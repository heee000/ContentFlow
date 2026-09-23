"""Real urllib redirect machinery with offline transports, never sockets."""

import io
import json
import traceback
import urllib.request
from email.message import Message
from unittest.mock import patch

import pytest

from contentflow.providers import OpenAICompatibleProvider, ProviderHTTPError


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "destination",
    [
        "https://provider.test/another-endpoint",
        "https://other.test/PRIVATE-REDIRECT-SENTINEL",
        "http://127.0.0.1/PRIVATE-REDIRECT-SENTINEL",
    ],
)
def test_model_redirect_is_refused_before_second_request(status, destination):
    calls = []

    def offline_open(_handler, request):
        calls.append(request)
        headers = Message()
        if len(calls) == 1:
            headers["Location"] = destination
            body, code = b"PRIVATE-REDIRECT-SENTINEL", status
        else:
            body = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
            code = 200
        response = urllib.response.addinfourl(
            io.BytesIO(body), headers, request.full_url, code
        )
        response.msg = "PRIVATE-REDIRECT-SENTINEL"
        return response

    provider = OpenAICompatibleProvider(
        "https://provider.test/v1", "PRIVATE-REDIRECT-SENTINEL", "synthetic"
    )
    with (
        patch("urllib.request.getproxies", return_value={}),
        patch("urllib.request._opener", None),
        patch.object(urllib.request.HTTPHandler, "http_open", offline_open),
        patch.object(urllib.request.HTTPSHandler, "https_open", offline_open),
        pytest.raises(ProviderHTTPError) as caught,
    ):
        provider.complete_json("plan", {})
    assert caught.value.status == status
    assert len(calls) == 1
    assert "PRIVATE-REDIRECT-SENTINEL" not in "".join(
        traceback.format_exception(caught.value)
    )
