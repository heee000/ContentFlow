from __future__ import annotations

import ipaddress
import re


_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_exact_host(value: str) -> str | None:
    """Normalize one exact DNS/IP host; reject wildcards and URL-like values."""

    if not isinstance(value, str):
        return None
    host = value.strip().lower()
    if (
        not host
        or host != value.lower()
        or len(host) > 253
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in host)
        or any(char in host for char in "/*@?#")
        or host.endswith(".")
    ):
        return None

    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return ipaddress.ip_address(literal).compressed.lower()
    except ValueError:
        pass

    if ":" in host:
        return None
    labels = host.split(".")
    if any(not _DNS_LABEL.fullmatch(label) for label in labels):
        return None
    return host