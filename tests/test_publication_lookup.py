"""Read-only recovery, no worker or provider execution."""

import uuid

from sqlalchemy import select

from contentflow import db
from contentflow.entities import Job
import test_publication_confirmation as publication

application = publication.application


def test_lookup_is_readonly_scoped_and_survives_later_content_edits(application):
    intent = publication.new_intent(application)
    path = f"/api/v1/publishing/intents/{intent['request_id']}"
    before = publication.counts()
    assert application.client.get(path).status_code == 401
    assert application.client.get(path, headers=application.headers).status_code == 404
    assert publication.counts() == before
    intent["preview_token"] = publication.preview(application, intent)["preview_token"]
    accepted = application.client.post("/api/v1/publishing/jobs", headers=application.headers, json=intent)
    assert accepted.status_code == 202
    assert accepted.json()["request_id"] == intent["request_id"]
    count = publication.counts()
    changed = application.client.patch(f"/api/v1/contents/{intent['content_item_id']}", headers=application.headers,
        json={"expected_version": 1, "body": "TEST-ONLY changed after acceptance"})
    assert changed.status_code == 200
    found = application.client.get(path, headers=application.headers)
    assert found.status_code == 200
    assert found.json()["id"] == accepted.json()["id"]
    assert found.json()["request_id"] == intent["request_id"]
    assert "request_json" not in found.json() and "preview_token" not in found.json()
    assert publication.counts() == count
    other = application.client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex}@example.com", "password": "TEST-ONLY-long-password", "display_name": "Other", "workspace_name": "Other"})
    assert other.status_code == 201
    assert application.client.get(path, headers={"Authorization": f"Bearer {other.json()['access_token']}"}).status_code == 404


def test_lookup_rejects_incomplete_receipt_without_repairing_it(application):
    intent = publication.new_intent(application)
    intent["preview_token"] = publication.preview(application, intent)["preview_token"]
    accepted = application.client.post("/api/v1/publishing/jobs", headers=application.headers, json=intent)
    assert accepted.status_code == 202
    with db.SessionLocal() as session:
        session.delete(session.scalar(select(Job).where(Job.idempotency_key == f"publish.dispatch:{accepted.json()['id']}")))
        session.commit()
    before = publication.counts()
    result = application.client.get(f"/api/v1/publishing/intents/{intent['request_id']}", headers=application.headers)
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "publish_receipt_incomplete"
    assert publication.counts() == before
