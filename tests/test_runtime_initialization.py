"""Imports must not initialize a deployment or consume local credentials."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run_isolated(code, cwd):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("CONTENTFLOW_", "NEXT_PUBLIC_CONTENTFLOW_"))
    }
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "tests")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_imports_do_not_read_settings_create_directories_or_engines(tmp_path):
    # A tripwire blocks even attempted dotenv access; no real secrets are read
    # if the old import-time initialization is accidentally reintroduced.
    run_isolated(
        """
from unittest.mock import patch
from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource
with patch.object(DotEnvSettingsSource, "_read_env_files", side_effect=AssertionError("dotenv access")), \\
     patch("pathlib.Path.mkdir", side_effect=AssertionError("directory creation")), \\
     patch("sqlalchemy.create_engine", side_effect=AssertionError("engine creation")):
    import contentflow.db as db
    import contentflow.api
    import contentflow.worker
    import contentflow.bootstrap_admin
    from contentflow.settings import get_settings
    assert db.engine is None
    assert get_settings.cache_info().currsize == 0
""",
        tmp_path,
    )


def test_test_bootstrap_ignores_dotenv_and_live_environment(tmp_path):
    (tmp_path / ".env").write_text(
        "CONTENTFLOW_SECRET_KEY=TEST-ONLY-DOTENV-TRIPWIRE\n"
        "CONTENTFLOW_TEXT_PROVIDER=openai-compatible\n",
        encoding="utf-8",
    )
    run_isolated(
        """
import os
os.environ["CONTENTFLOW_SECRET_KEY"] = "TEST-ONLY-ENV-TRIPWIRE"
os.environ["CONTENTFLOW_IMAGE_PROVIDER"] = "http"
os.environ["CONTENTFLOW_TEST_POSTGRES_URL"] = "TEST-ONLY-explicit-test-target"
from isolation import isolate_test_settings
isolate_test_settings()
from contentflow.settings import Settings
settings = Settings()
assert settings.secret_key == "change-this-in-production"
assert settings.text_provider == "mock"
assert settings.image_provider == "mock"
assert os.environ["CONTENTFLOW_TEST_POSTGRES_URL"] == "TEST-ONLY-explicit-test-target"
assert "CONTENTFLOW_SECRET_KEY" not in os.environ
""",
        tmp_path,
    )


def test_factory_and_worker_use_only_explicit_settings(tmp_path):
    run_isolated(
        """
from pathlib import Path
from unittest.mock import patch
from contentflow.settings import Settings
from contentflow.api import create_app
from contentflow.worker import Worker
from fastapi.testclient import TestClient
from contentflow import db
settings = Settings(_env_file=None, database_url="sqlite:///synthetic.db",
    local_storage_dir=Path("synthetic-storage"), secret_key="test-only-secret",
    metrics_enabled=False)
with patch("contentflow.settings.get_settings", side_effect=AssertionError("implicit settings")), \\
     patch("contentflow.api.get_settings", side_effect=AssertionError("implicit settings")), \\
     patch("contentflow.worker.get_settings", side_effect=AssertionError("implicit settings")):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200
    with Worker(settings=settings) as worker:
        assert str(worker.session_factory.kw["bind"].url) == "sqlite:///synthetic.db"
    db.engine.dispose()
""",
        tmp_path,
    )


def test_worker_closes_only_its_owned_pool_even_on_error(tmp_path):
    run_isolated(
        """
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import text
from contentflow.settings import Settings
from contentflow.worker import Worker
from contentflow import db
settings = Settings(_env_file=None, database_url="sqlite:///owned.db",
    local_storage_dir=Path("synthetic-storage"), secret_key="test-only-secret")
worker = Worker(settings=settings)
owned = worker.session_factory.kw["bind"]
with patch.object(owned, "dispose", wraps=owned.dispose) as dispose:
    try:
        with worker:
            with worker.session_factory() as session:
                assert session.scalar(text("SELECT 1")) == 1
            raise RuntimeError("TEST-ONLY execution error")
    except RuntimeError:
        pass
    dispose.assert_called_once_with()
    worker.close()
    dispose.assert_called_once_with()
    assert worker.run_once() is False
# A checked-in SQLite handle would prevent this rename on Windows.
Path("owned.db").rename("closed.db")
shared = db.configure_database("sqlite:///caller-owned.db")
try:
    with patch.object(shared, "dispose", wraps=shared.dispose) as dispose:
        with Worker(settings=settings, session_factory=db.SessionLocal) as borrowed:
            with borrowed.session_factory() as session:
                assert session.scalar(text("SELECT 1")) == 1
        dispose.assert_not_called()
finally:
    shared.dispose()
""",
        tmp_path,
    )
