from uuid import UUID

import pytest

from contentflow.settings import Settings


WORKSPACE = "f6786f5f-c8d5-4fd4-9517-8627219d0781"
ORIGIN = "https://test.tailtest.ts.net"


def private_settings(**overrides):
    values = dict(
        prompt_approval_policy="single_operator_private",
        single_operator_workspace_id=WORKSPACE,
        require_governed_prompts=True,
        allow_registration=False,
        public_base_url=ORIGIN,
        cors_origins=[ORIGIN],
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_default_dual_and_exact_workspace_scope():
    assert Settings(_env_file=None).prompt_approval_policy_for(WORKSPACE) == "dual_control"
    settings = private_settings()
    settings.validate_runtime()
    assert settings.single_operator_workspace_id == UUID(WORKSPACE)
    assert settings.prompt_approval_policy_for(WORKSPACE) == "single_operator_private"
    assert settings.prompt_approval_policy_for("different-workspace") == "dual_control"


@pytest.mark.parametrize("override", [
    {"single_operator_workspace_id": None},
    {"require_governed_prompts": False},
    {"allow_registration": True},
    {"public_base_url": "https://public.example.com"},
    {"public_base_url": "http://test.tailtest.ts.net"},
    {"public_base_url": ORIGIN + "/unexpected"},
    {"public_base_url": ORIGIN + "?query=1"},
    {"public_base_url": "https://test.tailtest.ts.net.evil.example"},
    {"cors_origins": ["*"]},
    {"cors_origins": [ORIGIN, "https://other.example"]},
])
def test_private_policy_rejects_unsafe_or_incomplete_opt_in(override):
    with pytest.raises(ValueError, match="Single-operator approval requires"):
        private_settings(**override).validate_runtime()


def test_private_policy_does_not_disable_production_secret_checks():
    with pytest.raises(ValueError, match="at least 32"):
        private_settings(environment="production", secret_key="weak").validate_runtime()


def test_invalid_policy_and_workspace_fail_validation():
    with pytest.raises(ValueError):
        private_settings(prompt_approval_policy="disable-governance")
    with pytest.raises(ValueError):
        private_settings(single_operator_workspace_id="any-workspace")
