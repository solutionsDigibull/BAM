#!/usr/bin/env python3
"""Policy check for the skill-runtime lock (E12.1): every requirement is pinned with hashes.

    python scripts/check_runtime_lock.py [compose/docker/skill-runtime/requirements.lock]

Fails (exit 1, one line per problem) when a requirement is not `name==version`, has no
`--hash=sha256:…`, or uses a URL / editable / index option, so `pip install --require-hashes`
in the image build can never silently pick something else. Stdlib only; run by `make lint`.
"""

import re
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "compose/docker/skill-runtime/requirements.lock"
PINNED = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9,._-]+\])?==[^\s;]+(\s*;.*)?$"
)
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def requirements(text: str) -> list[tuple[int, str]]:
    """(line number, logical requirement) with backslash continuations joined, comments dropped."""
    entries: list[tuple[int, str]] = []
    current, start = "", 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line and not current:
            continue
        if not current:
            start = number
        current += " " + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            entries.append((start, current.strip()))
            current = ""
    if current:
        entries.append((start, current.strip()))
    return entries


def problems(text: str) -> list[str]:
    found = []
    for number, entry in requirements(text):
        spec, *options = entry.split(" --")
        options = ["--" + o for o in options]
        if spec.startswith("-") or "://" in spec or spec.startswith("."):
            found.append(f"line {number}: {spec!r} is not a pinned requirement")
            continue
        if not PINNED.match(spec.strip()):
            found.append(f"line {number}: {spec.strip()!r} is not pinned with ==")
        if not any(HASH.fullmatch(o.strip()) for o in options):
            found.append(f"line {number}: {spec.strip()!r} has no --hash=sha256")
        bad = [o for o in options if not HASH.fullmatch(o.strip())]
        if bad:
            found.append(f"line {number}: {spec.strip()!r} has unsupported options {bad}")
    if not requirements(text):
        found.append("lock file has no requirements")
    return found


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    issues = problems(path.read_text(encoding="utf-8"))
    for issue in issues:
        print(f"{path}: {issue}", file=sys.stderr)
    if not issues:
        print(f"{path.name}: {len(requirements(path.read_text(encoding='utf-8')))} pinned, hashed")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
