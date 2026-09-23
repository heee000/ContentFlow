"""UTC response contract, independent of SQLite's naive datetime decoding."""

from datetime import datetime, timedelta, timezone

import pytest

from contentflow.schemas import ORMModel
from contentflow import schemas
import test_publication_confirmation as publication

application = publication.application


class TimestampResponse(ORMModel):
    recorded_at: datetime
    optional_at: datetime | None = None


def test_all_typed_response_timestamps_use_the_utc_contract():
    for name, model in vars(schemas).items():
        if not name.endswith("Response") or not isinstance(model, type) or not hasattr(model, "model_fields"):
            continue
        if any(field.annotation in (datetime, datetime | None) for field in model.model_fields.values()):
            assert issubclass(model, schemas.UTCResponseModel), name


@pytest.mark.parametrize("value", [
    datetime(2026, 1, 1, 12, 30),
    datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc),
    datetime(2026, 1, 1, 20, 30, tzinfo=timezone(timedelta(hours=8))),
    datetime(2026, 1, 1, 7, 30, tzinfo=timezone(timedelta(hours=-5))),
    "2026-01-01T20:30:00+08:00",
])
def test_response_normalizes_to_explicit_utc(value):
    result = TimestampResponse(recorded_at=value).model_dump(mode="json")
    assert result == {"recorded_at": "2026-01-01T12:30:00Z", "optional_at": None}


@pytest.mark.parametrize("immediate", [True, False])
def test_publication_first_replay_and_list_agree_on_aware_times(application, immediate):
    intent = publication.new_intent(application)
    intent["publish_now"] = immediate
    if not immediate:
        intent["scheduled_at"] = (datetime.now(timezone(timedelta(hours=8))) + timedelta(hours=1)).isoformat()
    intent["preview_token"] = publication.preview(application, intent)["preview_token"]
    first = application.client.post("/api/v1/publishing/jobs", headers=application.headers, json=intent)
    replay = application.client.post("/api/v1/publishing/jobs", headers=application.headers, json=intent)
    assert first.status_code == replay.status_code == 202
    records = application.client.get("/api/v1/publishing/jobs", headers=application.headers).json()
    listed = next(item for item in records if item["id"] == first.json()["id"])
    for key in ("scheduled_at", "created_at", "updated_at"):
        values = [record[key] for record in (first.json(), replay.json(), listed)]
        assert len(set(values)) == 1
        assert datetime.fromisoformat(values[0]).utcoffset() == timedelta(0)
    if not immediate:
        assert datetime.fromisoformat(listed["scheduled_at"]) == datetime.fromisoformat(intent["scheduled_at"])
