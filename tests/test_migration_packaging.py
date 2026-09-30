from pathlib import Path
import shutil
import tomllib

from alembic import command
import pytest
from sqlalchemy import create_engine, text

from contentflow import migrate


ROOT = Path(__file__).resolve().parents[1]


def test_wheel_declares_all_migration_assets():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    files = config["tool"]["setuptools"]["data-files"]
    assert files["share/contentflow"] == ["alembic.ini"]
    assert files["share/contentflow/migrations"] == ["migrations/env.py", "migrations/script.py.mako"]
    assert files["share/contentflow/migrations/versions"] == ["migrations/versions/*.py"]


def test_installed_migrations_ignore_cwd_and_support_percent_paths(tmp_path, monkeypatch):
    prefix = tmp_path / "installed%prefix"
    assets = prefix / "share" / "contentflow"
    assets.mkdir(parents=True)
    shutil.copy2(ROOT / "alembic.ini", assets)
    shutil.copytree(ROOT / "migrations", assets / "migrations", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(migrate, "PROJECT_ROOT", tmp_path / "site-packages")
    monkeypatch.setattr(migrate.sys, "prefix", str(prefix))
    monkeypatch.chdir(tmp_path)
    # An unrelated CWD config must not redirect the migration.
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = missing\n", encoding="utf-8")
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            config = migrate._alembic_config(connection)
            assert Path(config.get_main_option("script_location")) == assets / "migrations"
            command.upgrade(config, "head")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == migrate.HEAD_REVISION
    finally:
        engine.dispose()


def test_missing_migration_assets_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "PROJECT_ROOT", tmp_path / "missing-source")
    monkeypatch.setattr(migrate.sys, "prefix", str(tmp_path / "missing-install"))
    with pytest.raises(RuntimeError, match="migration assets are missing"):
        migrate._alembic_config(None)
