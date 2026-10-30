"""Copy the in-VM runner and its import closure into one directory (ADR 0023, decision 7).

    python stage.py <repo-root> <out-dir>

`<out-dir>` becomes the contents of /opt/sbam/harness: `run_cases.py` plus the packages it imports,
laid out the way `python -I run_cases.py` finds them. The runtime image's registry is generated from
what is installed, so none of this is installed: it is only copied.
"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOTS = ("server/contracts/", "server/workers/")
# files the VM cannot use as they are, and what stands in for them (see each stand-in's docstring)
STAND_INS = {
    "server/contracts/sbam_contracts/_jsonschema.py": "vendored_jsonschema.py",
    "server/contracts/sbam_contracts/canonical.py": "vendored_canonical.py",
}


def listed() -> list[str]:
    lines = (HERE / "files.txt").read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def destination(source: str) -> str:
    for root in ROOTS:
        if source.startswith(root):
            return source[len(root) :]
    raise SystemExit(f"{source}: not under {' or '.join(ROOTS)}")


def stage(repo: Path, out: Path) -> list[str]:
    copied = []
    for source in listed():
        target = out / destination(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / STAND_INS[source] if source in STAND_INS else repo / source, target)
        copied.append(str(target.relative_to(out)))
    shutil.copyfile(HERE / "run_cases.py", out / "run_cases.py")
    copied.append("run_cases.py")
    return sorted(copied)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    for name in stage(Path(sys.argv[1]), Path(sys.argv[2])):
        print(name)
