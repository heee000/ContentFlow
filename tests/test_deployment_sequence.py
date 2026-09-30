"""Run the real deployment shell against verified no-op tools, never Docker."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


def bash_path():
    if os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parents[1] / "bin" / "bash.exe" if git else None
        return str(candidate) if candidate and candidate.is_file() else None
    return shutil.which("bash")


@pytest.fixture
def deployment_runner(tmp_path):
    bash = bash_path()
    if bash is None:
        pytest.skip("A native Bash or Git Bash is required for the isolated shell harness")
    root = tmp_path / "checkout"
    deploy = root / "deploy" / "public-test"
    deploy.mkdir(parents=True)
    script = deploy / "deploy.sh"
    script.write_text(Path("deploy/public-test/deploy.sh").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    (deploy / "backup.sh").write_text(
        '#!/usr/bin/env sh\nprintf "backup\\n" >> "$CF_TEST_LOG"\n[ "$CF_TEST_FAIL" != backup ]\n',
        encoding="utf-8", newline="\n")
    (deploy / "backup.sh").chmod(0o700)
    bin_dir = root / "fake-bin"
    bin_dir.mkdir()
    tools = {
        "stat": "printf '600\\n'\n",
        "df": "printf 'Filesystem blocks used available capacity mounted\\nfake 99999999 0 99999999 0 /\\n'\n",
        "flock": '[ "$CF_TEST_FAIL" != lock ]\n',
        "sleep": "exit 0\n",
        "python3": 'printf "validate\\n" >> "$CF_TEST_LOG"\n[ "$CF_TEST_FAIL" != preflight ] || exit 20\nprintf "%s\\n" "$CF_TEST_MODE"\n',
        "docker": '''printf '%s\\n' "$*" >> "$CF_TEST_LOG"
case "$*" in
  *"pull postgres"*) [ "$CF_TEST_FAIL" != pull ] || exit 20 ;;
  *"stop worker api"*) [ "$CF_TEST_FAIL" != stop ] || exit 21 ;;
  *"--wait-timeout 120 postgres"*) [ "$CF_TEST_FAIL" != database ] || exit 22 ;;
  *"SELECT count(*) FROM pg_tables"*) [ "$CF_TEST_FAIL" != inspect ] || exit 22; printf '%s\\n' "$CF_TEST_TABLES" ;;
  *"contentflow-migrate"*) [ "$CF_TEST_FAIL" != migrate ] || exit 23 ;;
  *"up -d --remove-orphans"*) [ "$CF_TEST_FAIL" != startup ] || exit 24 ;;
  *"contentflow.deployment_checks api"*) [ "$CF_TEST_FAIL" != api ] || exit 25 ;;
  *"contentflow.deployment_checks worker"*) [ "$CF_TEST_FAIL" != worker ] || exit 26 ;;
  *"contentflow-prepare-embedding-cache verify"*)
    [ "$CF_TEST_FAIL" != cache ] || exit 27
    if [ "$CF_TEST_COLD" = true ] && [ ! -f "$CF_TEST_LOG.cache" ]; then
      : > "$CF_TEST_LOG.cache"; exit 28
    fi ;;
  *"contentflow-prepare-embedding-cache prepare"*) [ "$CF_TEST_FAIL" != cache ] || exit 27 ;;
  *) echo 'Unrecognized stub Docker command' >&2; exit 98 ;;
esac
exit 0
''',
    }
    for name, body in tools.items():
        path = bin_dir / name
        path.write_text("#!/usr/bin/env sh\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o700)
    env_file = root / "synthetic.env"
    env_file.write_text("# TEST-ONLY no credentials\n", encoding="utf-8")
    log = root / "calls.log"

    def run(*, tables="35", failure="", mode="openai-compatible", cold=False):
        (deploy / "release.env").write_text("PREVIOUS", encoding="utf-8")
        (deploy / "release-success.txt").write_text("PREVIOUS", encoding="utf-8")
        env = os.environ.copy()
        env.update(CF_TEST_LOG=log.as_posix(), CF_TEST_FAIL=failure, CF_TEST_TABLES=tables,
            CF_TEST_MODE=mode, CF_TEST_COLD=str(cold).lower())
        result = subprocess.run([bash, "-c",
            'test_bin=$(cd -- "$1" && pwd); export PATH="$test_bin:$PATH"; '
            'for tool in stat df python3 docker flock sleep; do case "$(command -v "$tool")" in "$test_bin"/*) ;; *) exit 97;; esac; done; '
            'exec bash "$2" "$3" "$4" "$5" "$6"',
            "test-deploy", bin_dir.as_posix(), script.as_posix(), env_file.as_posix(),
            "test/backend@sha256:" + "1" * 64, "test/web@sha256:" + "2" * 64, "3" * 40],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=40)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls, deploy
    return run


@pytest.mark.parametrize("tables,failure", [("0", ""), ("35", ""), ("35", "backup"),
    ("35", "migrate"), ("35", "stop"), ("unknown", "")])
def test_schema_deployment_stops_writers_and_fails_closed(deployment_runner, tables, failure):
    result, calls, deploy = deployment_runner(tables=tables, failure=failure)
    stops = [i for i, line in enumerate(calls) if "stop worker api" in line]
    migrations = [i for i, line in enumerate(calls) if "contentflow-migrate" in line]
    startups = [i for i, line in enumerate(calls) if "up -d --remove-orphans" in line]
    assert len(stops) == 1, result.stderr + repr(calls)
    if failure or tables == "unknown":
        assert result.returncode != 0
        assert not startups
        assert (deploy / "release.env").read_text() == "PREVIOUS"
        if failure != "migrate":
            assert not migrations
    else:
        assert result.returncode == 0, result.stderr + repr(calls)
        assert len(migrations) == len(startups) == 1
        assert stops[0] < migrations[0] < startups[0]
        assert not any("ps --status running" in line for line in calls)
        if tables != "0":
            assert stops[0] < calls.index("backup") < migrations[0]
        else:
            assert "backup" not in calls
        assert (deploy / "release-success.txt").read_text().strip() == "3" * 40


@pytest.mark.parametrize("mode,cold,expected", [("openai-compatible", False, 0),
    ("bge-m3-local", False, 1), ("bge-m3-local", True, 3)])
def test_embedding_api_never_loads_local_model_and_local_cache_is_verified(deployment_runner, mode, cold, expected):
    result, calls, _ = deployment_runner(mode=mode, cold=cold)
    assert result.returncode == 0, result.stderr
    cache = [line for line in calls if "embedding-bootstrap" in line]
    assert len(cache) == expected
    if cache:
        assert calls.index(cache[-1]) < next(i for i, line in enumerate(calls) if "stop worker api" in line)


@pytest.mark.parametrize("failure", ["lock", "preflight", "pull", "cache", "database", "inspect",
    "startup", "api", "worker"])
def test_failed_deployment_does_not_promote_or_leave_unverified_writers(deployment_runner, failure):
    result, calls, deploy = deployment_runner(failure=failure, mode="bge-m3-local")
    assert result.returncode != 0, repr(calls)
    assert (deploy / "release.env").read_text() == "PREVIOUS"
    assert (deploy / "release-success.txt").read_text() == "PREVIOUS"
    if failure in {"startup", "api", "worker"}:
        assert "stop worker api" in calls[-1]
    else:
        assert not any("contentflow-migrate" in line for line in calls)
        assert not any("up -d --remove-orphans" in line for line in calls)


def test_missing_validated_mode_never_stops_running_services(deployment_runner):
    result, calls, _ = deployment_runner(mode="hash")
    assert result.returncode != 0
    assert calls == ["validate"]
