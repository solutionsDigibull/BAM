"""Licence check against the allowlist (ADR 0028, decision 2). Pure: text in, verdict out.

MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause and ISC pass. A recognised licence that is not on the
list
(GPL, AGPL, LGPL, MPL, ...) is REFUSED. Text nobody can classify, or no licence at all, is UNKNOWN.
Both are refused unless a person overrides with a recorded reason (done by the caller).
"""

import json
import re
import tomllib
from collections.abc import Mapping

from sbam_contracts.repo_scan import LICENCE_ALLOWLIST, LicenceFinding, LicenceVerdict

_ALIASES = {
    "mit": "MIT",
    "mit license": "MIT",
    "apache-2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "isc": "ISC",
    "isc license": "ISC",
}
# Recognised, and not on the allowlist.
_REFUSED_PREFIXES = (
    "gpl",
    "agpl",
    "lgpl",
    "mpl",
    "epl",
    "eupl",
    "cddl",
    "sspl",
    "osl",
    "cc-by-sa",
    "cc-by-nc",
    "busl",
    "proprietary",
    "commercial",
)
_LICENCE_FILE = re.compile(r"^(licen[sc]e|copying|unlicense)([._-][\w.-]*)?$", re.I)
_CLASSIFIER = re.compile(r"License :: OSI Approved :: (.+?)( License)?$")


def normalise(raw: str) -> str | None:
    """An SPDX id for what a package or a file says, or the cleaned text if it is not known."""
    text = " ".join(raw.strip().split())
    if not text:
        return None
    return _ALIASES.get(text.lower(), text)


def _rank(verdict: LicenceVerdict) -> int:
    return {LicenceVerdict.ALLOWED: 0, LicenceVerdict.UNKNOWN: 1, LicenceVerdict.REFUSED: 2}[
        verdict
    ]


def verdict_of(spdx: str | None) -> LicenceVerdict:
    if not spdx:
        return LicenceVerdict.UNKNOWN
    if " OR " in spdx:  # a choice: one allowed term is enough
        parts = [verdict_of(p.strip("() ")) for p in spdx.split(" OR ")]
        return LicenceVerdict.ALLOWED if LicenceVerdict.ALLOWED in parts else max(parts, key=_rank)
    if " AND " in spdx:  # all terms apply
        return max((verdict_of(p.strip("() ")) for p in spdx.split(" AND ")), key=_rank)
    normal = _ALIASES.get(spdx.lower(), spdx)
    if normal in LICENCE_ALLOWLIST:
        return LicenceVerdict.ALLOWED
    if normal.lower().startswith(_REFUSED_PREFIXES):
        return LicenceVerdict.REFUSED
    return LicenceVerdict.UNKNOWN


def spdx_from_text(text: str) -> str | None:
    """Recognise a licence file by its standard wording. Unrecognised text gives None (custom)."""
    t = " ".join(text.lower().split())
    if "gnu affero general public license" in t:
        return "AGPL-3.0-only"
    if "gnu lesser general public license" in t:
        return "LGPL-3.0-only" if "version 3" in t else "LGPL-2.1-only"
    if "gnu general public license" in t:
        return "GPL-3.0-only" if "version 3" in t else "GPL-2.0-only"
    if "mozilla public license" in t:
        return "MPL-2.0"
    if "apache license" in t and "version 2.0" in t:
        return "Apache-2.0"
    if "permission is hereby granted, free of charge" in t:
        return "MIT"
    if "permission to use, copy, modify, and/or distribute this software for any purpose" in t:
        return "ISC"
    if "redistribution and use in source and binary forms" in t:
        return "BSD-3-Clause" if "neither the name" in t else "BSD-2-Clause"
    return None


def finding_for(source: str, spdx: str | None) -> LicenceFinding:
    return LicenceFinding(spdx=spdx, source=source[:300], verdict=verdict_of(spdx))


def licence_of_repo(files: Mapping[str, bytes]) -> LicenceFinding:
    """The licence of the repository: its LICENSE file and its package metadata. The worst wins."""
    found: list[LicenceFinding] = []
    for path, data in sorted(files.items()):
        if "/" not in path and _LICENCE_FILE.match(path):
            found.append(finding_for(path, spdx_from_text(data.decode("utf-8", "replace"))))
    meta = _from_metadata(files)
    if meta:
        found.append(finding_for(meta[0], meta[1]))
    if not found:
        return finding_for("none", None)
    return max(found, key=lambda f: _rank(f.verdict))


def _from_metadata(files: Mapping[str, bytes]) -> tuple[str, str | None] | None:
    pkg = files.get("package.json")
    if pkg:
        try:
            value = json.loads(pkg).get("license")
        except (ValueError, AttributeError):
            value = None
        if isinstance(value, str):
            return "package.json", normalise(value)
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            return "package.json", normalise(value["type"])
    py = files.get("pyproject.toml")
    if py:
        try:
            project = tomllib.loads(py.decode("utf-8", "replace")).get("project", {})
        except tomllib.TOMLDecodeError:
            return None
        lic = project.get("license")
        if isinstance(lic, str):
            return "pyproject.toml", normalise(lic)
        if isinstance(lic, dict) and isinstance(lic.get("text"), str):
            return "pyproject.toml", normalise(lic["text"])
        for classifier in project.get("classifiers", []):
            found = _CLASSIFIER.match(str(classifier))
            if found:
                return "pyproject.toml", normalise(found.group(1))
    return None
