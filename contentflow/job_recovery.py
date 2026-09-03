from __future__ import annotations

from enum import StrEnum

from .settings import Settings


class JobRecoveryPolicy(StrEnum):
    REPLAY_SAFE = "replay_safe"
    PROVIDER_IDEMPOTENT = "provider_idempotent"
    DOMAIN_GUARDED = "domain_guarded"
    CONFIGURATION_GUARDED = "configuration_guarded"
    MANUAL_REVIEW = "manual_review"


JOB_RECOVERY_POLICIES: dict[str, JobRecoveryPolicy] = {
    "knowledge.index": JobRecoveryPolicy.CONFIGURATION_GUARDED,
    "prompt_eval.execute": JobRecoveryPolicy.MANUAL_REVIEW,
    "workflow.execute": JobRecoveryPolicy.MANUAL_REVIEW,
    "connector.test": JobRecoveryPolicy.REPLAY_SAFE,
    "asset.generate": JobRecoveryPolicy.PROVIDER_IDEMPOTENT,
    "asset.search": JobRecoveryPolicy.REPLAY_SAFE,
    "asset.poll": JobRecoveryPolicy.REPLAY_SAFE,
    "publish.dispatch": JobRecoveryPolicy.DOMAIN_GUARDED,
    "publish.reconcile": JobRecoveryPolicy.DOMAIN_GUARDED,
    "storage.delete": JobRecoveryPolicy.DOMAIN_GUARDED,
    "storage.reconcile": JobRecoveryPolicy.REPLAY_SAFE,
    "metrics.pull": JobRecoveryPolicy.REPLAY_SAFE,
}

MANUAL_REVIEW_JOB_TYPES = frozenset(
    job_type
    for job_type, policy in JOB_RECOVERY_POLICIES.items()
    if policy == JobRecoveryPolicy.MANUAL_REVIEW
)


def manual_review_job_types(settings: Settings) -> frozenset[str]:
    job_types = set(MANUAL_REVIEW_JOB_TYPES)
    if settings.embedding_provider == "openai-compatible":
        job_types.add("knowledge.index")
    return frozenset(job_types)
