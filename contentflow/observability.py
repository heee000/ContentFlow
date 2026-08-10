from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .entities import Job, PromptEvalRun, PublishJob, WorkerNode, WorkflowRun
from .settings import Settings


JOB_STATUSES = ("queued", "retry", "running", "failed", "succeeded")
WORKFLOW_STATUSES = ("queued", "running", "awaiting_review", "failed", "error")
PROMPT_EVAL_STATUSES = ("queued", "running", "passed", "failed", "error")
HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class DatabaseOperationalCollector:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    @staticmethod
    def _status_counts(
        session: Session,
        entity,
        statuses: tuple[str, ...],
    ) -> dict[str, int]:
        counts = {status: 0 for status in statuses}
        for item_status, count in session.execute(
            select(entity.status, func.count(entity.id))
            .where(entity.status.in_(statuses))
            .group_by(entity.status)
        ):
            counts[str(item_status)] = int(count)
        counts["unknown"] = int(
            session.scalar(
                select(func.count(entity.id)).where(entity.status.not_in(statuses))
            )
            or 0
        )
        return counts

    def collect(self) -> Iterator[GaugeMetricFamily]:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            job_counts = self._status_counts(session, Job, JOB_STATUSES)
            workflow_counts = self._status_counts(
                session,
                WorkflowRun,
                WORKFLOW_STATUSES,
            )
            prompt_eval_counts = self._status_counts(
                session,
                PromptEvalRun,
                PROMPT_EVAL_STATUSES,
            )
            ready_filter = (
                Job.status.in_(("queued", "retry")),
                Job.run_at <= now,
            )
            ready_jobs = int(
                session.scalar(select(func.count(Job.id)).where(*ready_filter)) or 0
            )
            oldest_ready_at = session.scalar(
                select(func.min(Job.run_at)).where(*ready_filter)
            )
            oldest_ready_age = (
                max(0.0, (now - _aware(oldest_ready_at)).total_seconds())
                if oldest_ready_at is not None
                else 0.0
            )
            stale_cutoff = now - timedelta(seconds=self.settings.worker_stale_seconds)
            worker_counts = {
                "active": int(
                    session.scalar(
                        select(func.count(WorkerNode.id)).where(
                            WorkerNode.status != "stopped",
                            WorkerNode.heartbeat_at >= stale_cutoff,
                        )
                    )
                    or 0
                ),
                "stale": int(
                    session.scalar(
                        select(func.count(WorkerNode.id)).where(
                            WorkerNode.status != "stopped",
                            WorkerNode.heartbeat_at < stale_cutoff,
                        )
                    )
                    or 0
                ),
                "stopped": int(
                    session.scalar(
                        select(func.count(WorkerNode.id)).where(
                            WorkerNode.status == "stopped"
                        )
                    )
                    or 0
                ),
            }
            reconciliation_required = int(
                session.scalar(
                    select(func.count(PublishJob.id)).where(
                        PublishJob.status == "reconciliation_required"
                    )
                )
                or 0
            )

        queue = GaugeMetricFamily(
            "contentflow_queue_jobs",
            "Database queue jobs by controlled status.",
            labels=["status"],
        )
        for item_status, count in job_counts.items():
            queue.add_metric([item_status], count)
        yield queue

        ready = GaugeMetricFamily(
            "contentflow_queue_ready_jobs",
            "Queued or retry jobs eligible to be claimed now.",
            value=ready_jobs,
        )
        yield ready
        yield GaugeMetricFamily(
            "contentflow_queue_oldest_ready_age_seconds",
            "Age in seconds of the oldest ready queue job, or zero when empty.",
            value=oldest_ready_age,
        )

        workers = GaugeMetricFamily(
            "contentflow_worker_nodes",
            "Persisted worker nodes classified by heartbeat state.",
            labels=["state"],
        )
        for state, count in worker_counts.items():
            workers.add_metric([state], count)
        yield workers

        workflows = GaugeMetricFamily(
            "contentflow_workflow_runs",
            "Workflow runs by controlled status.",
            labels=["status"],
        )
        for item_status, count in workflow_counts.items():
            workflows.add_metric([item_status], count)
        yield workflows

        eval_runs = GaugeMetricFamily(
            "contentflow_prompt_eval_runs",
            "Prompt evaluation runs by controlled status.",
            labels=["status"],
        )
        for item_status, count in prompt_eval_counts.items():
            eval_runs.add_metric([item_status], count)
        yield eval_runs

        yield GaugeMetricFamily(
            "contentflow_publish_reconciliation_required",
            "Publish jobs requiring manual reconciliation.",
            value=reconciliation_required,
        )


class ObservabilityMetrics:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Session],
    ) -> None:
        self.api_prefix = settings.api_prefix.rstrip("/") or "/"
        self.registry = CollectorRegistry()
        self.build_info = Gauge(
            "contentflow_build_info",
            "ContentFlow build and runtime information.",
            labelnames=("version", "environment"),
            registry=self.registry,
        )
        self.build_info.labels(version="0.2.0", environment=settings.environment).set(1)
        self.http_requests = Counter(
            "contentflow_http_requests_total",
            "HTTP requests by method, route template, and status class.",
            labelnames=("method", "route", "status_class"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "contentflow_http_request_duration_seconds",
            "HTTP request duration in seconds by method and route template.",
            labelnames=("method", "route"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=self.registry,
        )
        self.http_in_flight = Gauge(
            "contentflow_http_requests_in_flight",
            "HTTP requests currently in flight by method.",
            labelnames=("method",),
            registry=self.registry,
        )
        self.registry.register(DatabaseOperationalCollector(session_factory, settings))

    @staticmethod
    def method_label(method: str) -> str:
        normalized = method.upper()
        return normalized if normalized in HTTP_METHODS else "OTHER"

    def route_label(self, route: object, request_path: str) -> str:
        value = getattr(route, "path", None)
        if not isinstance(value, str) or not value or len(value) > 240:
            return "unmatched"
        if (
            request_path == self.api_prefix
            or request_path.startswith(f"{self.api_prefix}/")
        ) and not value.startswith(self.api_prefix):
            value = f"{self.api_prefix}{value}"
        return value

    def request_started(self, method: str) -> None:
        self.http_in_flight.labels(method=self.method_label(method)).inc()

    def request_finished(
        self,
        *,
        method: str,
        route: object,
        request_path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        method_label = self.method_label(method)
        route_label = self.route_label(route, request_path)
        status_class = (
            f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"
        )
        self.http_in_flight.labels(method=method_label).dec()
        self.http_requests.labels(
            method=method_label,
            route=route_label,
            status_class=status_class,
        ).inc()
        self.http_duration.labels(method=method_label, route=route_label).observe(
            max(0.0, duration_seconds)
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
