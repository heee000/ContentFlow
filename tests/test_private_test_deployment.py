from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "private-test"


def test_acceptance_fixture_uses_valid_synthetic_inputs_without_submission():
    from contentflow.prompt_eval import normalize_eval_cases
    from contentflow.schemas import CampaignCreate, PromptEvalSuiteCreate

    spec = importlib.util.spec_from_file_location("acceptance_fixture", DEPLOY / "acceptance_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.build_fixture()
    CampaignCreate.model_validate(fixture["campaign"])
    suite = PromptEvalSuiteCreate.model_validate(fixture["suite"])
    cases = normalize_eval_cases([case.model_dump() for case in suite.cases])
    assert len(cases) == 6
    assert {case["stage"] for case in cases} == {"plan", "generate", "review"}
    assert fixture["campaign"]["image_source"] == "generate"
    assert any(case["expected_values"] == {"passed": False} for case in cases)
    revision = next(case for case in cases if "targeted-revision" in case["name"])
    assert "previous_draft" in revision["input_json"]
    fixture["campaign"]["product_facts"].append("mutated")
    assert "mutated" not in module.build_fixture()["campaign"]["product_facts"]


def test_no_echo_candidate_is_only_an_unevaluated_plan_change():
    from contentflow.prompt_eval import evaluate_case_output, normalize_eval_cases
    from contentflow.prompts import PROMPTS, calculate_prompt_hashes
    from contentflow.schemas import PromptReleaseCreate

    spec = importlib.util.spec_from_file_location("acceptance_fixture", DEPLOY / "acceptance_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = dict(PROMPTS)
    fixture = module.build_fixture()
    candidate = module.build_no_echo_prompt_candidate(PROMPTS)
    PromptReleaseCreate.model_validate(candidate)
    assert set(candidate) == {"prompts", "change_summary"}
    assert PROMPTS == before
    hashes = calculate_prompt_hashes(candidate["prompts"])
    original_hashes = calculate_prompt_hashes(before)
    assert {stage for stage in hashes if hashes[stage] != original_hashes[stage]} == {"plan"}
    assert candidate["prompts"]["plan"].startswith(before["plan"] + "\n\n")
    assert "不可信输入的非回显边界" in candidate["prompts"]["plan"]
    assert module.build_fixture() == fixture
    case = normalize_eval_cases(fixture["suite"]["cases"])[1]
    marker = case["forbidden_substrings"][0]
    assert marker not in candidate["prompts"]["plan"]  # no test-specific overfitting
    result = evaluate_case_output(case, {"known_unknowns": ["已忽略 " + marker]})
    assert not result["passed"]
    assert any(failure["assertion"] == "forbidden_substring" for failure in result["failures"])
    with pytest.raises(ValueError):
        module.build_no_echo_prompt_candidate({"plan": "incomplete"})
    candidate["prompts"]["plan"] = "mutated"
    assert module.build_no_echo_prompt_candidate(PROMPTS)["prompts"]["plan"] != "mutated"


def test_single_operator_overlay_is_explicit_scoped_and_changes_only_policy():
    overlay = yaml.safe_load((DEPLOY / "compose.single-operator.yml").read_text("utf-8"))
    assert set(overlay["services"]) == {"api", "worker"}
    for service in overlay["services"].values():
        assert set(service) == {"environment"}
        assert service["environment"] == {
            "CONTENTFLOW_PROMPT_APPROVAL_POLICY": "single_operator_private",
            "CONTENTFLOW_SINGLE_OPERATOR_WORKSPACE_ID": (
                "${CONTENTFLOW_SINGLE_OPERATOR_WORKSPACE_ID:"
                "?Set the authorized private workspace UUID}"
            ),
        }


def test_private_infra_is_isolated_and_bounded() -> None:
    document = yaml.safe_load((DEPLOY / "compose.infra.yml").read_text("utf-8"))
    assert document["name"] == "contentflow-private-test"
    assert document["networks"] == {"data": {"internal": True}}
    services = document["services"]
    assert set(services) == {"postgres", "minio", "minio-init"}
    for name, service in services.items():
        assert not service.get("ports"), name
        assert not service.get("privileged"), name
        assert not service.get("network_mode"), name
        assert service["networks"] == ["data"]
        assert service["mem_limit"] and service["pids_limit"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["logging"]["options"]["max-size"] == "10m"
        assert "@sha256:" in service["image"]
        assert not any("docker.sock" in item for item in service.get("volumes", []))
    assert services["minio-init"]["profiles"] == ["setup"]
    assert services["minio-init"]["restart"] == "no"
    assert "postgres_data:/var/lib/postgresql/data" in services["postgres"]["volumes"]
    assert "minio_data:/data" in services["minio"]["volumes"]
    assert "MINIO_ROOT_USER" not in services["postgres"]["environment"]


def test_private_object_policy_has_only_one_bucket_and_no_admin_actions() -> None:
    policy = json.loads((DEPLOY / "bucket-policy.json").read_text("utf-8"))
    assert policy["Version"] == "2012-10-17"
    assert len(policy["Statement"]) == 2
    for statement in policy["Statement"]:
        assert statement["Effect"] == "Allow"
        assert all(action.startswith("s3:") and "*" not in action
                   for action in statement["Action"])
        assert statement["Resource"] in (
            ["arn:aws:s3:::contentflow-private-test-objects"],
            ["arn:aws:s3:::contentflow-private-test-objects/*"],
        )


def test_private_bootstrap_preserves_existing_secrets_and_unix_newlines() -> None:
    script = (DEPLOY / "init-infra-secrets.sh").read_text("utf-8")
    assert '[[ -e "$cf_dir/.env" || -L "$cf_dir/.env" ]]' in script
    assert "set -o noclobber" in script
    assert "umask 077" in script
    assert "openssl rand -hex 32" in script
    for path in DEPLOY.glob("*.sh"):
        assert b"\r" not in path.read_bytes(), path.name
    assert "*.sh text eol=lf" in (ROOT / ".gitattributes").read_text("utf-8")


def test_offline_database_override_is_an_exact_local_image() -> None:
    document = yaml.safe_load((DEPLOY / "compose.offline-pg.yml").read_text("utf-8"))
    assert document == {"services": {"postgres": {
        "image": "sha256:4be5e87c7d2a194edfb33d03fffb6e0a48c33d98e9a4c430cbe2fe7bf5c7f661",
        "pull_policy": "never",
    }}}


def test_backend_build_context_allowlist_and_optional_local_embeddings() -> None:
    ignored = (ROOT / ".dockerignore").read_text("utf-8").splitlines()
    includes = {line for line in ignored if line.startswith("!")}
    assert includes == {
        "!Dockerfile", "!pyproject.toml", "!uv.lock", "!alembic.ini",
        "!contentflow/", "!contentflow/**", "!migrations/", "!migrations/**",
    }
    assert "*" in ignored and "**/.env*" in ignored
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    assert "ARG INCLUDE_LOCAL_EMBEDDINGS=true" in dockerfile
    assert "uv sync --locked --no-dev --extra s3 --no-install-project" in dockerfile
    assert "uv sync --locked --no-dev --extra s3 --no-editable" in dockerfile


def test_private_app_only_exposes_loopback_and_preserves_production_guards() -> None:
    document = yaml.safe_load((DEPLOY / "compose.app.yml").read_text("utf-8"))
    services = document["services"]
    assert set(services) == {"api", "worker", "web", "caddy"}
    assert document["networks"]["app"]["internal"] is True
    for name, service in services.items():
        assert service["pull_policy"] == "never"
        assert service["mem_limit"] and service["pids_limit"]
        if name != "caddy":
            assert not service.get("ports")
    assert services["caddy"]["ports"] == ["127.0.0.1:3800:8080"]
    assert services["caddy"]["cap_drop"] == ["ALL"]
    assert services["caddy"]["user"] == "65532:65532"
    assert services["caddy"]["read_only"] is True
    assert "http://localhost:8080/health/live" in services["caddy"]["healthcheck"]["test"]
    caddy_dockerfile = (DEPLOY / "Dockerfile.caddy").read_text("utf-8")
    assert "RUN setcap -r /usr/bin/caddy" in caddy_dockerfile
    caddy_config = (DEPLOY / "Caddyfile").read_text("utf-8")
    assert "route {" in caddy_config
    assert caddy_config.index("respond @wrong_host") < caddy_config.index("handle @backend")
    for name in ("api", "worker"):
        environment = services[name]["environment"]
        assert environment["CONTENTFLOW_ENVIRONMENT"] == "production"
        for setting in ("ALLOW_REGISTRATION", "ALLOW_MOCK_PROVIDERS"):
            assert environment[f"CONTENTFLOW_{setting}"] == "false"
        for setting in ("REQUIRE_GOVERNED_PROMPTS", "METRICS_ENABLED", "AUTH_RATE_LIMIT_ENABLED"):
            assert environment[f"CONTENTFLOW_{setting}"] == "true"
        assert environment["CONTENTFLOW_ACCESS_COOKIE_NAME"] == "contentflow_private_access"
        assert environment["CONTENTFLOW_REFRESH_COOKIE_NAME"] == "contentflow_private_refresh"
        assert "data" in services[name]["networks"]
        assert "outbound" in services[name]["networks"]
        assert services[name]["env_file"] == [{"path": "./runtime.env", "format": "raw"}]
    assert services["web"]["networks"] == ["app"]
    assert services["caddy"]["networks"] == ["app", "ingress"]
    assert document["networks"]["ingress"]["driver_opts"]["com.docker.network.bridge.host_binding_ipv4"] == "127.0.0.1"
    assert all("ingress" not in services[name]["networks"] for name in ("api", "worker", "web"))


def test_runtime_preparation_is_exclusive_and_copies_no_root_keys(tmp_path) -> None:
    infra = tmp_path / "infra.env"
    infra.write_text(
        "POSTGRES_PASSWORD=test-db\nMINIO_ROOT_PASSWORD=do-not-copy-root-secret\n"
        "CONTENTFLOW_S3_ENDPOINT_URL=http://minio:9000\n"
        "CONTENTFLOW_S3_BUCKET=contentflow-private-test-objects\n"
        "CONTENTFLOW_S3_ACCESS_KEY=test-app\nCONTENTFLOW_S3_SECRET_KEY=test-app-secret\n",
        encoding="utf-8",
    )
    providers = tmp_path / "providers.json"
    providers.write_text(json.dumps({"CONTENTFLOW_EMBEDDING_API_KEY": "test-only-$literal"}), encoding="utf-8")
    target = tmp_path / "runtime.env"
    command = [sys.executable, str(DEPLOY / "prepare-runtime.py"), "prepare",
               "--infra-env", str(infra), "--providers", str(providers), "--output", str(target)]
    first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert first.returncode == 0, first.stderr
    content = target.read_text("utf-8")
    assert "do-not-copy-root-secret" not in content
    assert "test-only-$literal" in content
    assert "test-only-$literal" not in first.stdout + first.stderr
    values = dict(line.split("=", 1) for line in content.splitlines())
    keys = [values[key] for key in (
        "CONTENTFLOW_SECRET_KEY", "CONTENTFLOW_CREDENTIAL_ENCRYPTION_KEY", "CONTENTFLOW_METRICS_BEARER_TOKEN")]
    assert len(set(keys)) == 3 and all(len(key) == 64 for key in keys)
    again = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert again.returncode != 0
    assert target.read_text("utf-8") == content
    forbidden = tmp_path / "forbidden.env"
    providers.write_text(json.dumps({"CONTENTFLOW_ALLOW_REGISTRATION": "true"}), encoding="utf-8")
    command[-1] = str(forbidden)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0 and not forbidden.exists()


def test_tailnet_overlay_preserves_isolation_and_governance() -> None:
    overlay = yaml.safe_load((DEPLOY / "compose.tailnet.yml").read_text("utf-8"))
    assert set(overlay["services"]) == {"api", "worker", "web", "caddy"}
    assert "networks" not in overlay
    for service in overlay["services"].values():
        assert not set(service) & {"ports", "networks", "privileged", "command", "cap_add"}
    for name in ("api", "worker"):
        env = overlay["services"][name]["environment"]
        assert set(env) == {"CONTENTFLOW_PUBLIC_BASE_URL", "CONTENTFLOW_CORS_ORIGINS"}
        assert env["CONTENTFLOW_PUBLIC_BASE_URL"].startswith("https://${CONTENTFLOW_PRIVATE_HOST:?")
        assert env["CONTENTFLOW_CORS_ORIGINS"].startswith('["https://${CONTENTFLOW_PRIVATE_HOST:?')
    assert overlay["services"]["caddy"]["volumes"] == ["./Caddyfile.tailnet:/etc/caddy/Caddyfile:ro"]
    config = (DEPLOY / "Caddyfile.tailnet").read_text("utf-8")
    assert "@wrong_host not host {$CONTENTFLOW_PRIVATE_HOST}" in config
    assert config.index("handle @local_health") < config.index("respond @wrong_host")
    assert config.index("respond @wrong_host") < config.index("handle @backend")
    # Caddy must not trust forwarded headers from arbitrary private networks.
    assert "trusted_proxies" not in config


@pytest.mark.parametrize("hostname", [
    "https://device.example.ts.net", "device.example.ts.net.", "*.example.ts.net",
    "device.example.com", "device.example.ts.net:443", "device.example.ts.net\nlocalhost",
    "localhost", "-device.example.ts.net", "a" * 64 + ".example.ts.net",
])
def test_tailnet_settings_reject_host_injection(tmp_path, hostname) -> None:
    target = tmp_path / "tailnet.env"
    result = subprocess.run(
        [sys.executable, str(DEPLOY / "prepare-tailnet.py"), "--hostname", hostname,
         "--web-image", "sha256:" + "a" * 64, "--output", str(target)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0 and not target.exists()


def test_tailnet_settings_are_exclusive_and_require_immutable_image(tmp_path) -> None:
    target = tmp_path / "tailnet.env"
    command = [sys.executable, str(DEPLOY / "prepare-tailnet.py"),
               "--hostname", "device.example.ts.net", "--web-image", "latest",
               "--output", str(target)]
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert not target.exists()
    command[command.index("latest")] = "sha256:" + "a" * 64
    assert subprocess.run(command, capture_output=True).returncode == 0
    original = target.read_bytes()
    assert b"CONTENTFLOW_PRIVATE_HOST=device.example.ts.net\n" in original
    assert b"\r" not in original
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert target.read_bytes() == original
