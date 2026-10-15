#!/usr/bin/env python3
"""Print the runtime registry of THIS Python environment (TD §20.2, E12.1).

Runs inside the skill-runtime image, at build time and in CI. It reads what is really installed
(importlib.metadata), never the lock file, so the registry can't drift from the image. Stdlib only:
the image has no SBAM packages. The output validates as `sbam_contracts.runtime_registry`:

    {"content_sha256": "sha256:…", "generated_at": "…", "image_digest": null | "sha256:…",
     "packages": [{"allowed": true, "name": "psycopg", "top_level_modules": ["psycopg"],
                   "version": "3.3.6"}, …],
     "python_version": "3.12.15"}

`allowed` (E3.12): the package is one of the allowed libraries in requirements.in (copied into the
image); everything else installed is a transitive dependency that Sub-Skills must not import.

Usage: sbam-runtime-registry [--image-digest sha256:…] [--generated-at ISO-8601] [--output FILE]
                             [--allowed requirements.in]
The digest only exists after the build, so CI passes it in; `content_sha256` ignores generated_at.
"""

import argparse
import hashlib
import json
import platform
import re
import sys
from datetime import UTC, datetime
from importlib.metadata import Distribution, distributions

TOOLING = {"pip", "setuptools", "wheel"}  # the venv's own tools, not libraries for Sub-Skills
ALLOWED_FILE = "/opt/runtime_allowed_libraries.in"  # requirements.in, copied in by the Dockerfile
REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize(name: str) -> str:
    """PEP 503 (same rule as sbam_contracts.isatvon14.normalize_package)."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def allowed_names(path: str) -> set[str]:
    """Normalised names from a requirements.in: `psycopg[binary]>=3 ; extra` -> `psycopg`."""
    names = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            match = REQUIREMENT_NAME.match(line.split("#", 1)[0])
            if match:
                names.add(normalize(match.group(1)))
    if not names:
        raise ValueError(f"no allowed libraries in {path}")
    return names


def top_level_modules(dist: Distribution) -> list[str]:
    """Import names a distribution provides: top_level.txt if present, else its installed files.

    Handles dist names that differ from import names (psycopg-binary -> psycopg_binary,
    python-dateutil -> dateutil, pyjwt -> jwt) and single-file modules (six.py -> six).
    """
    modules: set[str] = set()
    text = dist.read_text("top_level.txt")
    if text:
        modules = {line.strip().replace("/", ".").split(".")[0] for line in text.splitlines()}
    else:
        for file in dist.files or []:
            parts = file.parts
            if not parts or parts[0] == ".." or parts[0].endswith((".dist-info", ".data")):
                continue
            if len(parts) > 1:
                modules.add(parts[0])
            elif parts[0].endswith((".py", ".so", ".pyd")):
                modules.add(parts[0].split(".")[0])
    return sorted(m for m in modules if MODULE.match(m) and m != "__pycache__")


def packages(allowed: set[str]) -> list[dict[str, object]]:
    seen: dict[str, dict[str, object]] = {}
    for dist in distributions():
        name = normalize(dist.metadata["Name"] or "")
        if not name or name in TOOLING or name in seen:
            continue
        seen[name] = {
            "name": name,
            "version": dist.version,
            "top_level_modules": top_level_modules(dist),
            "allowed": name in allowed,
        }
    return [seen[n] for n in sorted(seen)]


def content_sha256(registry: dict[str, object]) -> str:
    """RFC 8785-compatible for this data (ASCII keys/strings, no floats): sorted keys, no spaces."""
    hashed = {k: v for k, v in registry.items() if k not in {"generated_at", "content_sha256"}}
    text = json.dumps(hashed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(
    image_digest: str | None = None,
    generated_at: str | None = None,
    allowed_file: str = ALLOWED_FILE,
) -> dict[str, object]:
    if image_digest is not None and not DIGEST.match(image_digest):
        raise ValueError(f"image digest must look like sha256:<64 hex>, got {image_digest!r}")
    try:
        allowed = allowed_names(allowed_file)
    except FileNotFoundError:
        raise ValueError(f"allowed libraries file not found: {allowed_file}") from None
    except OSError as exc:  # e.g. permissions: say what really happened
        raise ValueError(f"can't read allowed libraries file {allowed_file}: {exc}") from None
    registry: dict[str, object] = {
        "image_digest": image_digest,
        "python_version": platform.python_version(),
        "packages": packages(allowed),
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
    }
    registry["content_sha256"] = content_sha256(registry)
    return registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sbam-runtime-registry", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--image-digest", help="sha256:<64 hex> of the built image (CI)")
    parser.add_argument("--generated-at", help="fixed timestamp (tests); default: now, UTC")
    parser.add_argument("--output", help="write here instead of stdout")
    parser.add_argument("--allowed", default=ALLOWED_FILE, help="the allowed requirements.in")
    args = parser.parse_args(argv)
    try:
        registry = build(args.image_digest, args.generated_at, args.allowed)
    except ValueError as exc:
        print(f"sbam-runtime-registry: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(registry, indent=2, sort_keys=True) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
