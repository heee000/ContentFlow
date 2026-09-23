"""Existing flow tests now explicitly perform the preview/confirm protocol."""

import uuid


def confirm_publish(client, *, headers, json, api="/api/v1"):
    # Retain the actual intent/token in the caller's test payload so replay
    # tests resend the same confirmation instead of manufacturing a new one.
    payload = json
    payload.setdefault("request_id", str(uuid.uuid4()))
    if "preview_token" not in payload:
        preview = client.post(f"{api}/publishing/preview", headers=headers, json=payload)
        if preview.status_code != 200:
            payload["preview_token"] = "invalid-preview-token-" * 4
        else:
            payload["preview_token"] = preview.json()["preview_token"]
    return client.post(f"{api}/publishing/jobs", headers=headers, json=payload)
