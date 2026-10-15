#!/usr/bin/env python3
"""Render compose/keycloak/realm-bullm.json for a deployed origin (E16.3, #142).

The committed realm file is the local-dev form: its redirect URIs and web origins are
`http://localhost:${WEB_PORT:3000}` (Keycloak fills the placeholder from the container env). A
deployed environment serves the portals at https://<SBAM_PORTAL_HOST>, so this rewrites every such
URL and writes the file that compose.deploy.yaml mounts through SBAM_REALM_FILE. The committed file
is not changed.

    python compose/keycloak/render_realm.py \\
        --origin https://portal.example.com --out /etc/sbam/realm-bullm.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

REALM = Path(__file__).with_name("realm-bullm.json")
LOCAL_ORIGIN = "http://localhost:${WEB_PORT:3000}"


def render(realm_text: str, origin: str) -> str:
    """Return the realm JSON text with the local origin replaced by `origin`."""
    parts = urlsplit(origin)
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(f"origin must be https://<host> with no path, got {origin!r}")
    if parts.hostname in ("localhost", "127.0.0.1") or not re.fullmatch(
        r"[A-Za-z0-9.-]+(:\d+)?", parts.netloc
    ):
        raise ValueError(f"origin must be a public hostname, got {origin!r}")
    rendered = realm_text.replace(LOCAL_ORIGIN, f"https://{parts.netloc}")
    if "localhost" in rendered:
        raise ValueError(
            "the realm still references localhost after rendering; update LOCAL_ORIGIN handling"
        )
    json.loads(rendered)  # fail here, not at Keycloak import
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--origin",
        required=True,
        help="public portal origin, e.g. https://portal.example.com",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="file to write (world-readable: it holds no secrets)",
    )
    args = parser.parse_args(argv)
    try:
        text = render(REALM.read_text(encoding="utf-8"), args.origin)
    except ValueError as exc:
        print(f"render_realm: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(text, encoding="utf-8")
    args.out.chmod(0o644)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
