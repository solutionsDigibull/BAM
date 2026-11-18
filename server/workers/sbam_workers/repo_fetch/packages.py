"""Dependencies: their licences (package indexes) and their known vulnerabilities (OSV).

Both answer from a pinned name and version, through the injectable `JsonHttp`. A lookup that finds
nothing is not "fine": a dependency without a licence is UNKNOWN, and one the database cannot find
has no known vulnerabilities (OSV has no row for it), which is recorded as is.
"""

from collections.abc import Sequence
from urllib.parse import quote

from sbam_contracts.repo_scan import DependencyLicence, Severity, VulnFinding

from sbam_workers.repo_fetch.deps import Dependency
from sbam_workers.repo_fetch.http import JsonHttp
from sbam_workers.repo_fetch.licence import normalise, verdict_of

OSV = "https://api.osv.dev/v1"
MAX_DETAILS = 50
_SEVERITY = {
    "LOW": Severity.LOW,
    "MODERATE": Severity.MEDIUM,
    "MEDIUM": Severity.MEDIUM,
    "HIGH": Severity.HIGH,
    "CRITICAL": Severity.CRITICAL,
}


def registry_licence(http: JsonHttp, dep: Dependency) -> str | None:
    """The licence a package index states for exactly this version, or None."""
    name, version = quote(dep.name, safe="@/"), quote(dep.version, safe="")
    if dep.ecosystem == "npm":
        doc = http.get(f"https://registry.npmjs.org/{name}/{version}")
        lic = (doc or {}).get("license")
        return normalise(lic) if isinstance(lic, str) else None
    doc = http.get(f"https://pypi.org/pypi/{name}/{version}/json")
    info = (doc or {}).get("info") or {}
    for key in ("license_expression", "license"):
        value = info.get(key)
        if isinstance(value, str) and 0 < len(value) <= 100:
            return normalise(value)
    for classifier in info.get("classifiers") or []:
        if str(classifier).startswith("License :: OSI Approved :: "):
            return normalise(str(classifier).rsplit(" :: ", 1)[-1].removesuffix(" License"))
    return None


def dependency_licences(http: JsonHttp, deps: Sequence[Dependency]) -> list[DependencyLicence]:
    out = []
    for dep in deps:
        spdx = dep.spdx or registry_licence(http, dep)
        out.append(
            DependencyLicence(
                ecosystem=dep.ecosystem,
                name=dep.name[:300],
                version=dep.version[:300],
                spdx=spdx[:300] if spdx else None,
                verdict=verdict_of(spdx),
            )
        )
    return out


def vulnerabilities(http: JsonHttp, deps: Sequence[Dependency]) -> list[VulnFinding]:
    """Known vulnerabilities of the pinned versions, from one OSV batch query."""
    if not deps:
        return []
    queries = [
        {"package": {"name": d.name, "ecosystem": d.ecosystem}, "version": d.version} for d in deps
    ]
    answer = http.post(f"{OSV}/querybatch", {"queries": queries}) or {}
    results = answer.get("results") or []
    found: list[VulnFinding] = []
    details = 0
    for dep, result in zip(deps, results, strict=False):
        for vuln in (result or {}).get("vulns") or []:
            vid = str(vuln.get("id", ""))[:300]
            if not vid:
                continue
            summary, severity = "", Severity.UNKNOWN
            if details < MAX_DETAILS:
                details += 1
                doc = http.get(f"{OSV}/vulns/{quote(vid, safe='')}") or {}
                summary = str(doc.get("summary") or "")[:300]
                label = str((doc.get("database_specific") or {}).get("severity", "")).upper()
                severity = _SEVERITY.get(label, Severity.UNKNOWN)
            found.append(
                VulnFinding(
                    ecosystem=dep.ecosystem,
                    package=dep.name[:300],
                    version=dep.version[:300],
                    id=vid,
                    summary=summary,
                    severity=severity,
                )
            )
    return found
