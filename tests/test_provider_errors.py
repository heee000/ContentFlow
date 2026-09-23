import io
import json
import socket
import ssl
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from contentflow.ai_provenance import AIProvenanceRecorder
from contentflow.providers import (
    OpenAICompatibleProvider,
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderResponseError,
)


SECRET = "PRIVATE-UPSTREAM-SECRET"


def recorder():
    provider = OpenAICompatibleProvider("https://provider.test/v1", SECRET, "test-model")
    return AIProvenanceRecorder(provider, embedding_provider="not_used", embedding_model="not_used")


def failed_call(call_recorder, error_class):
    with pytest.raises(error_class) as captured:
        call_recorder.complete_json("plan", {"secret": SECRET})
    error = captured.value
    snapshot = error.ai_provenance
    assert SECRET not in json.dumps(snapshot)
    assert SECRET not in str(error)
    assert snapshot["failed_invocations"] == 1
    return snapshot["invocations"][0]


@pytest.mark.parametrize("status", [400, 401, 403, 408, 429, 500, 502, 503])
def test_http_status_is_preserved_without_upstream_body_or_url(status):
    error = urllib.error.HTTPError(
        "https://provider.test/" + SECRET, status, SECRET,
        {"x-request-id": "known-request"}, io.BytesIO(SECRET.encode()),
    )
    call_recorder = recorder()
    with patch("contentflow.providers.open_model_request", side_effect=error) as send:
        invocation = failed_call(call_recorder, ProviderHTTPError)
    assert send.call_count == 1
    assert invocation["error_type"] == f"ProviderHTTPError:{status}"
    assert invocation["error_diagnostics"] == {"category": "http", "http_status": status}
    assert call_recorder.provider.last_call_metadata["provider_request_id"] == "known-request"
    assert error.fp.tell() == 0  # Do not even read an HTTP error's potentially sensitive body.


@pytest.mark.parametrize("error,kind", [
    (TimeoutError(SECRET), "timeout"),
    (ConnectionResetError(SECRET), "connection"),
    (ssl.SSLError(SECRET), "tls"),
    (urllib.error.URLError(socket.gaierror(SECRET)), "dns"),
    (urllib.error.URLError(TimeoutError(SECRET)), "timeout"),
    (urllib.error.URLError(ssl.SSLError(SECRET)), "tls"),
    (urllib.error.URLError(SECRET), "network"),
])
def test_network_errors_have_bounded_non_secret_categories(error, kind):
    with patch("contentflow.providers.open_model_request", side_effect=error) as send:
        invocation = failed_call(recorder(), ProviderNetworkError)
    assert send.call_count == 1
    assert invocation["error_type"] == f"ProviderNetworkError:{kind}"
    assert invocation["error_diagnostics"] == {"category": "network", "kind": kind}


@pytest.mark.parametrize("body,kind", [
    (b"<html>PRIVATE-UPSTREAM-SECRET</html>", "json"),
    (b"\xff", "encoding"),
    (b"{}", "structure"),
    (b'{"choices":[]}', "structure"),
    (b'{"choices":null}', "structure"),
    (b'{"choices":[{"message":{"content":null}}]}', "structure"),
    (b'{"choices":[{"message":{"content":"[]"}}]}', "not_object"),
])
def test_invalid_response_is_classified_and_header_id_survives_parsing(body, kind):
    response = MagicMock()
    response.read.return_value = body
    response.headers = {"x-request-id": "response-header-id"}
    call_recorder = recorder()
    with patch("contentflow.providers.open_model_request") as send:
        send.return_value.__enter__.return_value = response
        invocation = failed_call(call_recorder, ProviderResponseError)
    assert send.call_count == 1
    assert invocation["error_diagnostics"] == {"category": "response", "kind": kind}
    assert call_recorder.provider.last_call_metadata["provider_request_id"] == "response-header-id"


def test_usage_is_preserved_when_model_content_is_not_json():
    response = MagicMock()
    response.headers = {}
    response.read.return_value = json.dumps({
        "id": "valid-provider-request", "model": "reported-model",
        "choices": [{"message": {"content": SECRET}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }).encode()
    with patch("contentflow.providers.open_model_request") as send:
        send.return_value.__enter__.return_value = response
        invocation = failed_call(recorder(), ProviderResponseError)
    assert invocation["usage"]["total_tokens"] == 15
    assert invocation["response_model"] == "reported-model"
    assert invocation["error_diagnostics"] == {"category": "response", "kind": "json"}
