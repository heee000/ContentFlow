"""Real PostgreSQL generation acceptance races; isolated database only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import event, func, select, text

from contentflow.entities import AuditLog, Campaign, GenerationIntent, Job, WorkflowRun
from contentflow.routers.runs import create_run
from contentflow.schemas import WorkflowRunRequest
import test_postgres_sessions as auth_pg

postgres_harness = auth_pg.postgres_harness
pytestmark = pytest.mark.skipif(
    not auth_pg.TEST_DATABASE_URL, reason="Dedicated PostgreSQL test URL is required"
)


def fixture(harness):
    sid, uid, workspaces, _token, _context = auth_pg.fixture(harness)
    with harness.sessions() as session:
        campaigns = [
            Campaign(
                workspace_id=workspaces[0],
                created_by=uid,
                name=f"TEST-ONLY {i}",
                product_name="TEST",
                objective="Verify generation intent",
                audience="Tests",
                platforms=["wechat"],
            )
            for i in range(2)
        ]
        session.add_all(campaigns)
        session.commit()
        return sid, uid, workspaces[0], [(row.id, row.updated_at) for row in campaigns]


@pytest.mark.parametrize(
    "variant", ["same", "different_payload", "different_campaign", "different_key"]
)
def test_postgres_concurrent_generation_acceptance(postgres_harness, variant):
    harness = postgres_harness
    sid, uid, wid, campaigns = fixture(harness)
    key = str(uuid.uuid4())
    barrier = Barrier(2)

    def submit(index):
        campaign_id, updated = campaigns[
            index if variant == "different_campaign" else 0
        ]
        payload = WorkflowRunRequest(
            expected_campaign_updated_at=updated,
            provider="mock" if variant == "different_payload" and index else None,
        )
        request_id = str(uuid.uuid4()) if variant == "different_key" else key
        with harness.sessions() as session:
            session.execute(text("SET statement_timeout = '15s'"))
            principal = auth_pg.principal(session, sid, uid, wid)
            barrier.wait(timeout=10)
            try:
                run = create_run(
                    campaign_id,
                    payload,
                    principal,
                    session,
                    harness.settings.model_copy(
                        update={"require_governed_prompts": False}
                    ),
                    request_id,
                )
                session.commit()
                return 202, run.id
            except HTTPException as error:
                session.rollback()
                return error.status_code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    if variant in {"different_payload", "different_campaign"}:
        assert sorted(code for code, _ in results) == [202, 409]
        expected = 1
    else:
        assert [code for code, _ in results] == [202, 202]
        expected = 2 if variant == "different_key" else 1
        assert len({identifier for _, identifier in results}) == expected
    with harness.sessions() as session:
        for model in (WorkflowRun, GenerationIntent, Job):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.workspace_id == wid)
                )
                == expected
            )
        assert (
            session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.workspace_id == wid, AuditLog.action == "workflow.enqueue"
                )
            )
            == expected
        )


def test_postgres_generation_rechecks_cached_brief_after_row_lock(postgres_harness):
    harness = postgres_harness
    sid, uid, wid, campaigns = fixture(harness)
    campaign_id, updated = campaigns[0]
    ready, changed, lock_attempted = Event(), Event(), Event()

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if "campaigns" in statement and "FOR UPDATE" in statement:
            lock_attempted.set()

    def stale_request():
        with harness.sessions() as session:
            session.execute(text("SET statement_timeout = '15s'"))
            cached = session.get(Campaign, campaign_id)
            principal = auth_pg.principal(session, sid, uid, wid)
            ready.set()
            assert changed.wait(10)
            try:
                create_run(
                    campaign_id,
                    WorkflowRunRequest(expected_campaign_updated_at=updated),
                    principal,
                    session,
                    harness.settings.model_copy(
                        update={"require_governed_prompts": False}
                    ),
                    str(uuid.uuid4()),
                )
                pytest.fail("Changed Brief unexpectedly generated")
            except HTTPException as error:
                assert cached.objective == "Changed under lock"
                session.rollback()
                return error.status_code

    with harness.sessions() as owner, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale_request)
        try:
            assert ready.wait(10)
            row = owner.scalar(
                select(Campaign).where(Campaign.id == campaign_id).with_for_update()
            )
            row.objective = "Changed under lock"
            row.updated_at = updated + timedelta(seconds=1)
            owner.flush()
            event.listen(harness.engine, "before_cursor_execute", observe)
            changed.set()
            assert lock_attempted.wait(10)
            owner.commit()
            assert future.result(timeout=15) == 409
        finally:
            owner.rollback()
            changed.set()
            if event.contains(harness.engine, "before_cursor_execute", observe):
                event.remove(harness.engine, "before_cursor_execute", observe)
    with harness.sessions() as session:
        assert (
            session.scalar(
                select(func.count(WorkflowRun.id)).where(
                    WorkflowRun.workspace_id == wid
                )
            )
            == 0
        )
