"""Fail when a fixture file is not described in README.md (issue E0.8, test E0.8-T03).

Run:  python server/tests/fixtures/check_fixture_readme.py
A file counts as documented when its path relative to this folder appears in the README.
The README, SHA256SUMS, the helpers and this script are infrastructure and are not listed.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INFRASTRUCTURE = {"README.md", "SHA256SUMS", "__init__.py", "check_fixture_readme.py"}


def fixture_files(root: Path = ROOT) -> list[str]:
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and "__pycache__" not in relative and relative not in INFRASTRUCTURE:
            files.append(relative)
    return files


def undocumented(root: Path = ROOT) -> list[str]:
    readme = (root / "README.md").read_text(encoding="utf-8")
    return [name for name in fixture_files(root) if f"`{name}`" not in readme]


def main() -> int:
    missing = undocumented()
    if missing:
        sys.stderr.write("undocumented fixture files:\n" + "".join(f"  {m}\n" for m in missing))
        return 1
    sys.stdout.write(f"all {len(fixture_files())} fixture files are documented\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
