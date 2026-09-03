from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY_ROOT = ROOT / "deploy" / "observability"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_prometheus_scrape_uses_secret_file_and_fixed_target():
    config = _yaml(OBSERVABILITY_ROOT / "prometheus" / "prometheus.yml")
    assert config["rule_files"] == ["/etc/prometheus/contentflow.rules.yml"]
    scrape = next(
        item
        for item in config["scrape_configs"]
        if item["job_name"] == "contentflow-api"
    )
    assert scrape["metrics_path"] == "/metrics"
    assert scrape["static_configs"] == [{"targets": ["api:8000"]}]
    assert scrape["authorization"] == {
        "type": "Bearer",
        "credentials_file": "/run/secrets/contentflow_metrics_bearer_token",
    }
    assert "credentials" not in scrape["authorization"]


def test_alert_rules_have_bounded_operations_contracts():
    config = _yaml(OBSERVABILITY_ROOT / "prometheus" / "contentflow.rules.yml")
    groups = config["groups"]
    assert {group["name"] for group in groups} == {
        "contentflow-recording",
        "contentflow-alerts",
    }
    alerts = [rule for group in groups for rule in group["rules"] if "alert" in rule]
    assert len(alerts) == 12
    assert {rule["labels"]["severity"] for rule in alerts} == {
        "warning",
        "critical",
    }
    assert all(rule.get("for") for rule in alerts)
    assert all(rule["annotations"].get("runbook_url") for rule in alerts)
    all_expressions = "\n".join(
        str(rule["expr"]) for group in groups for rule in group["rules"]
    )
    for forbidden_label in (
        "workspace_id",
        "campaign_id",
        "user_id",
        "publish_job_id",
    ):
        assert forbidden_label not in all_expressions
    assert "max(contentflow_queue_oldest_ready_age_seconds)" in all_expressions
    assert "sum(rate(contentflow_http_requests_total" in all_expressions
    assert "contentflow_storage_allocations" in all_expressions
    assert "contentflow_storage_reconciliation_overdue_workspaces" in all_expressions
    assert "contentflow_storage_delete_pending_oldest_age_seconds" in all_expressions
    assert "contentflow_job_manual_review_oldest_age_seconds" in all_expressions


def test_grafana_assets_are_immutable_and_use_safe_global_aggregation():
    datasource = _yaml(
        OBSERVABILITY_ROOT
        / "grafana"
        / "provisioning"
        / "datasources"
        / "contentflow.yml"
    )["datasources"][0]
    assert datasource["uid"] == "contentflow-prometheus"
    assert datasource["url"] == "http://prometheus:9090"
    assert datasource["editable"] is False

    provider = _yaml(
        OBSERVABILITY_ROOT
        / "grafana"
        / "provisioning"
        / "dashboards"
        / "contentflow.yml"
    )["providers"][0]
    assert provider["allowUiUpdates"] is False
    assert provider["updateIntervalSeconds"] > 10

    dashboard = json.loads(
        (
            OBSERVABILITY_ROOT / "grafana" / "dashboards" / "contentflow-overview.json"
        ).read_text(encoding="utf-8")
    )
    assert dashboard["uid"] == "contentflow-operations"
    assert dashboard["editable"] is False
    assert len(dashboard["panels"]) == 15
    expressions = [
        target["expr"] for panel in dashboard["panels"] for target in panel["targets"]
    ]
    assert all(
        panel["datasource"]["uid"] == "contentflow-prometheus"
        for panel in dashboard["panels"]
    )
    assert "max by (status) (contentflow_queue_jobs)" in expressions
    assert "max by (status) (contentflow_workflow_runs)" in expressions
    assert "max by (status) (contentflow_prompt_eval_runs)" in expressions
    assert "max by (status) (contentflow_storage_allocations)" in expressions
    assert (
        "max(contentflow_storage_reconciliation_overdue_workspaces)" in expressions
    )
    assert "max(contentflow_storage_reconciliation_failed_jobs)" in expressions
    assert (
        "max(contentflow_storage_delete_pending_oldest_age_seconds)" in expressions
    )
    assert "max(contentflow_job_manual_review_oldest_age_seconds)" in expressions
    assert any(
        "sum by (route, status_class)" in expression for expression in expressions
    )


def test_ci_mounts_placeholder_secret_for_promtool_config_validation():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert (
        'metrics_token_file="${RUNNER_TEMP}/contentflow_metrics_bearer_token"'
        in workflow
    )
    assert "umask 077" in workflow
    assert (
        '"${metrics_token_file}:/run/secrets/contentflow_metrics_bearer_token:ro"'
    ) in workflow
    assert "check config /etc/prometheus/prometheus.yml" in workflow
    assert "test rules /etc/prometheus/contentflow.rules.test.yml" in workflow


def test_compose_observability_profile_pins_images_and_secrets():
    compose = _yaml(ROOT / "docker-compose.yml")
    prometheus = compose["services"]["prometheus"]
    grafana = compose["services"]["grafana"]
    secret_check = compose["services"]["grafana-secret-check"]
    assert prometheus["profiles"] == ["observability"]
    assert grafana["profiles"] == ["observability"]
    assert prometheus["image"].endswith(
        "@sha256:214f8427c8fba80c327bb94a75feb802ae12f2d6ca30812aa6e7d22f09bbea80"
    )
    assert grafana["image"].endswith(
        "@sha256:121a7a9ece6dc10b969f1f96eed64b4f07dfac0d0b8abc070f7cb83bbde86f63"
    )
    assert prometheus["secrets"] == ["contentflow_metrics_bearer_token"]
    assert grafana["secrets"] == ["contentflow_grafana_admin_password"]
    assert secret_check["secrets"] == [
        "contentflow_grafana_admin_password",
        "contentflow_metrics_bearer_token",
    ]
    assert "wc -c" in secret_check["command"][0]
    assert "cmp -s" in secret_check["command"][0]
    assert grafana["depends_on"]["grafana-secret-check"]["condition"] == (
        "service_completed_successfully"
    )
    assert "ports" not in prometheus
    assert grafana["ports"] == [
        "${CONTENTFLOW_GRAFANA_BIND_ADDRESS:-127.0.0.1}:"
        "${CONTENTFLOW_GRAFANA_PORT:-3301}:3000"
    ]
