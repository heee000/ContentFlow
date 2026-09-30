"""Verify the local node's private HTTPS entry without publishing or invoking AI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-file", type=Path, required=True)
    args = parser.parse_args()
    status = json.loads(subprocess.check_output(["tailscale", "status", "--json"], timeout=15))
    assert status["BackendState"] == "Running", "Tailscale is not connected"
    node = status["Self"]
    hostname = node["DNSName"].removesuffix(".")
    assert re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net", hostname), "Unexpected node DNS name"
    address = next(value for value in node["TailscaleIPs"] if ":" not in value)
    origin = "https://" + hostname
    # Resolve only this node's own HTTPS endpoint; normal CA/hostname checks stay on.
    account = json.loads(args.account_file.read_text("utf-8"))
    with tempfile.TemporaryDirectory(prefix="contentflow-tailnet-check-") as directory:
        root = Path(directory)
        cookie_file = root / "cookies.txt"

        def request(path, *, body=None, headers=None, cookies=False):
            output = root / "body"
            response_headers = root / "headers"
            command = ["curl", "--silent", "--show-error", "--noproxy", "*",
                       "--connect-timeout", "10", "--max-time", "30",
                       "--resolve", f"{hostname}:443:{address}",
                       "--output", str(output), "--dump-header", str(response_headers),
                       "--write-out", "%{http_code}", origin + path]
            if cookies:
                command += ["--cookie", str(cookie_file), "--cookie-jar", str(cookie_file)]
            for name, value in (headers or {}).items():
                command += ["--header", f"{name}: {value}"]
            payload = None
            if body is not None:
                command += ["--header", "Content-Type: application/json", "--data-binary", "@-"]
                payload = json.dumps(body)
            result = subprocess.run(command, input=payload, capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, "HTTPS request failed (details suppressed)"
            return int(result.stdout), output.read_text("utf-8"), response_headers.read_text("utf-8")

        code, body, _ = request("/health/ready")
        assert code == 200 and json.loads(body)["database"] == "ok", "Readiness failed"
        assert json.loads(body)["storage"] == "ok", "Storage not ready"
        assert request("/api/v1/knowledge/documents")[0] == 401, "Anonymous request was not rejected"
        assert request("/health/ready", headers={"Host": "untrusted.example"})[0] == 403, "Host guard failed"
        code, _, headers = request("/")
        assert code == 200, "Web entry failed"
        csp = next(line for line in headers.splitlines() if line.lower().startswith("content-security-policy:"))
        assert origin in csp and "http://localhost:3600" not in csp, "Web API origin mismatch"
        session_headers = {"Origin": origin, "X-ContentFlow-Session-Mode": "cookie"}
        code, _, headers = request("/api/v1/auth/login", cookies=True, headers=session_headers,
                                   body={key: account[key] for key in ("email", "password")})
        assert code == 200, "Login failed"
        cookie_headers = [line.lower() for line in headers.splitlines() if line.lower().startswith("set-cookie:")]
        assert len(cookie_headers) == 2, "Expected two session cookies"
        assert all(all(flag in line for flag in ("secure", "httponly", "samesite=lax"))
                   for line in cookie_headers), "Cookie protection failed"
        try:
            code, body, _ = request("/api/v1/knowledge/documents", cookies=True)
            assert code == 200, "Authenticated read failed"
            documents = json.loads(body)
            code, body, _ = request("/api/v1/admin/prompt-releases", cookies=True)
            assert code == 200 and json.loads(body)["governance_required"] is True, "Governance guard failed"
            assert request("/api/v1/auth/refresh", body={}, cookies=True, headers=session_headers)[0] == 200, "Refresh failed"
        finally:
            assert request("/api/v1/auth/logout", body={}, cookies=True, headers=session_headers)[0] == 204, "Logout failed"
        assert request("/api/v1/knowledge/documents", cookies=True)[0] == 401, "Logout did not clear session"
        print(json.dumps({"tls_verified": True, "readiness": "ok", "host_guard": "ok",
                          "web_https_origin": "ok", "secure_httponly_cookies": "ok",
                          "login_refresh_logout": "ok", "governance_required": True,
                          "existing_documents": len(documents), "ai_calls": 0,
                          "scope": "server self-check; not cross-device/browser acceptance"}))


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        # Assertion messages above are fixed strings, never response/credential data.
        raise SystemExit(f"Private HTTPS check failed: {error}") from None
    except (KeyError, ValueError, OSError, StopIteration, subprocess.SubprocessError) as error:
        raise SystemExit(f"Private HTTPS check failed: {type(error).__name__}; secrets suppressed") from None
