"""Create an exclusive, non-secret configuration for an authorized tailnet node."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re


LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--web-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Do not normalize arbitrary URLs or interpolate extra Caddyfile tokens.
    if not re.fullmatch(rf"{LABEL}\.{LABEL}\.ts\.net", args.hostname):
        parser.error("Use the exact device.tailnet.ts.net hostname, without a trailing dot or URL")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.web_image):
        parser.error("Use a verified immutable local Web image ID")
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"CONTENTFLOW_PRIVATE_HOST={args.hostname}\n")
            stream.write(f"CONTENTFLOW_TAILNET_WEB_IMAGE={args.web_image}\n")
    except OSError:
        parser.error("Cannot exclusively create output; do not overwrite existing configuration")
    print("Created private HTTPS settings; account and credential data are not included.")


if __name__ == "__main__":
    main()
