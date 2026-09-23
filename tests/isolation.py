"""Test-process-only settings isolation, before importing application modules."""

import os
import sys


def isolate_test_settings() -> None:
    if "contentflow.db" in sys.modules or "contentflow.api" in sys.modules:
        raise RuntimeError("Test isolation must run before application imports")
    for key in list(os.environ):
        normalized = key.upper()
        if normalized.startswith("CONTENTFLOW_") and not normalized.startswith(
            "CONTENTFLOW_TEST_"
        ):
            del os.environ[key]
    # Do not add a production flag which disables validation. This override
    # exists only in the disposable test process; explicit synthetic env files
    # in settings tests remain available via _env_file=fixture_path.
    from contentflow.settings import Settings

    Settings.model_config = {**Settings.model_config, "env_file": None}
