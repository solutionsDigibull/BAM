"""The pinned dependencies of a repository, read as text (nothing is installed or run).

requirements*.txt and pyproject.toml (`name==version` only), package-lock.json (every package of
lockfileVersion 2 and 3). An unpinned dependency has no version to check a database against, so
it is
skipped here.
"""

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*)(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9._!+\-]+)")
MAX_DEPS = 500


@dataclass(frozen=True)
class Dependency:
    ecosystem: str  # PyPI or npm, as OSV names them
    name: str
    version: str
    spdx: str | None = None  # known already (a lockfile may carry it)


def _pins(lines: list[str]) -> list[Dependency]:
    found = []
    for line in lines:
        pin = _PIN.match(line.split("#", 1)[0].split(";", 1)[0])
        if pin:
            found.append(Dependency("PyPI", pin.group(1).lower().replace("_", "-"), pin.group(2)))
    return found


def read_dependencies(files: Mapping[str, bytes]) -> list[Dependency]:
    out: dict[tuple[str, str, str], Dependency] = {}

    def add(deps: list[Dependency]) -> None:
        for d in deps:
            out.setdefault((d.ecosystem, d.name, d.version), d)

    for path, data in sorted(files.items()):
        name = path.rsplit("/", 1)[-1]
        text = data.decode("utf-8", "replace")
        if re.fullmatch(r"requirements[\w.\-]*\.txt", name):
            add(_pins(text.splitlines()))
        elif name == "pyproject.toml":
            try:
                project = tomllib.loads(text).get("project", {})
            except tomllib.TOMLDecodeError:
                continue
            add(_pins([str(d) for d in project.get("dependencies", [])]))
        elif name == "package-lock.json":
            try:
                packages = json.loads(text).get("packages", {})
            except (ValueError, AttributeError):
                continue
            for key, meta in packages.items():
                if key and isinstance(meta, dict) and meta.get("version"):
                    pkg = key.rsplit("node_modules/", 1)[-1]
                    lic = meta.get("license")
                    spdx = lic if isinstance(lic, str) else None
                    add([Dependency("npm", pkg, str(meta["version"]), spdx)])
    return sorted(out.values(), key=lambda d: (d.ecosystem, d.name, d.version))[:MAX_DEPS]
