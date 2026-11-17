"""The software bill of materials that ships with the export (WG-9, #757).

One CycloneDX 1.5 document listing every component of a Sub-Skill's code with its version: each
vendored repository (its pinned commit, licence, the digest of its untouched tree and the hash of
every patch on it), the dependencies the scan of each repository found, and the libraries the
Sub-Skill declares. It is deterministic: no timestamp and no random serial number, the components
are sorted and the JSON is canonical, so the same inputs always give the same bytes (and the same
hash in the export).
"""

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from sbam_contracts.isatvon14 import Library
from sbam_contracts.repo_overlay import RepoOverlay
from sbam_contracts.repo_scan import DependencyLicence

from sbam_workers.repo_fetch.overlay import repo_name

_ECOSYSTEM = {"pypi": "pypi", "npm": "npm"}


def _purl(ecosystem: str, name: str, version: str | None, qualifiers: str = "") -> str:
    kind = _ECOSYSTEM.get(ecosystem.lower(), "generic")
    suffix = f"@{quote(version, safe='')}" if version else ""
    return f"pkg:{kind}/{quote(name, safe='/')}{suffix}{qualifiers}"


def _licences(spdx: str | None) -> list[dict[str, Any]]:
    return [{"license": {"id": spdx}}] if spdx else []


def _repo_component(overlay: RepoOverlay) -> dict[str, Any]:
    purl = _purl(
        "generic", repo_name(overlay.url), overlay.commit, f"?vcs_url={quote(overlay.url, safe='')}"
    )
    properties = [{"name": "sbam:patched-tree-sha256", "value": overlay.patched_tree_sha256}]
    properties += [
        {"name": "sbam:patch", "value": f"{patch.sha256} {patch.path}"} for patch in overlay.patches
    ]
    return {
        "type": "library",
        "bom-ref": purl,
        "name": repo_name(overlay.url),
        "version": overlay.commit,
        "purl": purl,
        "licenses": _licences(overlay.licence),
        "hashes": [{"alg": "SHA-256", "content": overlay.vendor_tree_sha256}],
        "properties": properties,
    }


def _dependency_component(dep: DependencyLicence) -> dict[str, Any]:
    purl = _purl(dep.ecosystem, dep.name, dep.version)
    return {
        "type": "library",
        "bom-ref": purl,
        "name": dep.name,
        "version": dep.version,
        "purl": purl,
        "licenses": _licences(dep.spdx),
    }


def _library_component(lib: Library) -> dict[str, Any]:
    purl = _purl("pypi", lib.package, None)
    constraint = lib.version_constraint
    return {
        "type": "library",
        "bom-ref": f"{purl}#{constraint or 'unspecified'}",
        "name": lib.package,
        "version": constraint or "unspecified",  # a constraint such as ">=1.2", not a resolved pin
        "purl": purl,
        "properties": [{"name": "sbam:declared-by", "value": "sub-skill"}],
    }


def build_sbom(
    *,
    subject: str,
    version: str,
    overlays: Sequence[RepoOverlay] = (),
    dependencies: Sequence[DependencyLicence] = (),
    libraries: Sequence[Library] = (),
) -> dict[str, Any]:
    """The SBOM of one Sub-Skill (or Workflow): `subject` and `version` name what it describes."""
    components: dict[str, dict[str, Any]] = {}
    for component in (
        *(_repo_component(o) for o in overlays),
        *(_dependency_component(d) for d in dependencies),
        *(_library_component(lib) for lib in libraries),
    ):
        components.setdefault(component["bom-ref"], component)  # the same package once
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": subject, "version": version}},
        "components": [components[ref] for ref in sorted(components)],
    }


def sbom_bytes(sbom: dict[str, Any]) -> bytes:
    """Canonical JSON bytes of the SBOM, the form that is hashed and shipped."""
    return json.dumps(sbom, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
