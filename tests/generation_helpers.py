"""Explicit new-operation helper for existing non-idempotency flow tests."""

import uuid


def request_run(client, path, *, headers, json):
    campaign = client.get(path.removesuffix("/runs"), headers=headers)
    assert campaign.status_code == 200, campaign.text
    return client.post(
        path,
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"expected_campaign_updated_at": campaign.json()["updated_at"], **json},
    )
