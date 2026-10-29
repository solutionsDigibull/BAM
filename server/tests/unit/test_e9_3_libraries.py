"""E9.3 `check_libraries`: a Sub-Skill's libraries against the runtime registry of its pinned image.

The check itself (`check`) is pure; `LibraryStore.run` is tested on SQLite with the real registry,
manifest and gate_results tables. The workflow's side (MISSING → DEPENDENCY_FAILED → correction →
the FDE notified) is #107's: tests/temporal/test_sub_skill_build.py::test_e10_2_t08_* and
::test_e10_2_missing_library_never_fixed_waits_in_dependency_failed.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sbam_api.builds.models
import sbam_api.isatvon.models  # noqa: F401 - sub_skills (and workflows), foreign-key targets
from registry_db import GENERATOR, sqlite_sessions
from sbam_api.core.ids import uuid7
from sbam_api.manifests.models import GateResultRow, SubSkillManifest
from sbam_api.registry.repository import registry_for_digest, upsert_registry
from sbam_contracts.build import REGISTRY_UNAVAILABLE, BuildStageInput, LibraryStatus
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.runtime_registry import RuntimeRegistry
from sbam_workers.build.libraries import LibraryStore, check
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

DIGEST = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32
TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()
MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "isa14" / "ms01_1_manifest.json"


def registry(digest: str = DIGEST, **packages: str) -> RuntimeRegistry:
    """A registry with `name=version` packages (all importable directly)."""
    raw: dict[str, Any] = {
        "image_digest": digest,
        "python_version": "3.12.13",
        "packages": [
            {"name": n, "version": v, "top_level_modules": [n.replace("-", "_")], "allowed": True}
            for n, v in sorted(packages.items())
        ],
        "generated_at": datetime(2026, 10, 28, tzinfo=UTC).isoformat(),
    }
    raw["content_sha256"] = GENERATOR.content_sha256(raw)
    return RuntimeRegistry.model_validate(raw)


REGISTRY = registry(
    httpx="0.27.2",
    psycopg="3.2.1",
    requests="2.32.3",
    duckdb="1.1.0",
    **{"pydantic-core": "2.27.2"},
)


def lib(package: str, constraint: str | None = None) -> dict[str, Any]:
    return {"package": package, **({"version_constraint": constraint} if constraint else {})}


def codes(outcome: Any) -> list[tuple[str | None, str]]:
    return [(f.package, f.code) for f in outcome.findings]


# --- the check ------------------------------------------------------------------------------------


def test_e9_3_t01_everything_present_is_pinned_to_the_registry_versions() -> None:
    outcome = check([lib("httpx", ">=0.27"), lib("psycopg")], REGISTRY)
    assert outcome.passed
    assert outcome.pins == [
        {"package": "httpx", "version_constraint": "==0.27.2"},
        {"package": "psycopg", "version_constraint": "==3.2.1"},
    ]


def test_e9_3_t02_a_package_the_registry_lacks_is_missing_with_guidance() -> None:
    outcome = check([lib("pandas_ta")], REGISTRY)
    (finding,) = outcome.findings
    assert (finding.package, finding.code, finding.actual) == ("pandas-ta", "MISSING", None)
    assert finding.expected == "pandas_ta"
    assert "ask the platform team to add pandas-ta" in finding.message
    assert "pip" not in finding.message  # templated guidance, never an install command


def test_e9_3_t03_a_constraint_no_registry_version_meets_is_a_version_conflict() -> None:
    (finding,) = check([lib("requests", "<2.0")], REGISTRY).findings
    assert (finding.code, finding.expected, finding.actual) == (
        "VERSION_CONFLICT",
        "requests<2.0",
        "2.32.3",
    )


def test_e9_3_t04_another_database_engine_is_forbidden_even_when_present() -> None:
    outcome = check([lib("duckdb"), lib("PyMySQL"), lib("psycopg")], REGISTRY)
    assert codes(outcome) == [("duckdb", "FORBIDDEN"), ("pymysql", "FORBIDDEN")]
    assert all("PostgreSQL only" in f.message for f in outcome.findings)


def test_e9_3_t05_names_match_after_pep_503_normalization_only() -> None:
    assert check([lib("Pydantic_Core")], REGISTRY).pins == [
        {"package": "pydantic-core", "version_constraint": "==2.27.2"}
    ]
    # no fuzzy matching: a near miss is a different (missing) package, never substituted
    assert codes(check([lib("requets")], REGISTRY)) == [("requets", "MISSING")]


def test_e9_3_t08_the_same_libraries_in_another_order_give_the_same_result() -> None:
    libs = [lib("psycopg"), lib("requests", "<2.0"), lib("pandas_ta"), lib("httpx")]
    first, second = check(libs, REGISTRY), check(list(reversed(libs)), REGISTRY)
    assert first == second
    assert [f.package for f in first.findings] == ["pandas-ta", "requests"]


def test_e9_3_no_libraries_is_valid() -> None:
    outcome = check([], REGISTRY)
    assert outcome.passed and outcome.pins == []


def test_e9_3_template_packages_are_always_there() -> None:
    assert check([lib("fastmcp"), lib("pydantic")], REGISTRY).passed


def test_e9_3_a_transitive_only_package_is_missing() -> None:
    reg = registry(urllib3="2.2.3")
    raw = reg.model_dump(mode="json")
    raw["packages"][0]["allowed"] = False
    raw["content_sha256"] = GENERATOR.content_sha256(raw)
    hidden = RuntimeRegistry.model_validate(raw)
    assert codes(check([lib("urllib3")], hidden)) == [("urllib3", "MISSING")]


def test_e9_3_a_bad_constraint_or_a_duplicate_is_a_version_conflict() -> None:
    assert codes(check([lib("httpx", "~~2")], REGISTRY)) == [("httpx", "VERSION_CONFLICT")]
    twice = check([lib("httpx"), lib("HTTPX", ">=0.1")], REGISTRY)
    assert codes(twice) == [("httpx", "VERSION_CONFLICT")]


# --- the stage on the database --------------------------------------------------------------------


class World:
    def __init__(self, libraries: list[dict[str, Any]], digest: str = DIGEST) -> None:
        self.sessions: sessionmaker[Session] = sqlite_sessions()
        bind = self.sessions.kw["bind"]
        for table in (SubSkillManifest, GateResultRow):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.sub_skill = uuid7()
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        data["sbam_build_record"]["build"]["libraries"] = libraries
        data["sbam_build_record"]["build"]["runtime_image_digest"] = digest
        sealed = SkillManifest.sealed(data)
        with self.sessions() as s:
            s.add(
                SubSkillManifest(
                    id=uuid7(),
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    sub_skill_id=self.sub_skill,
                    build_no=1,
                    cycle=0,
                    attempt=0,
                    manifest=sealed.dump(),
                    sha256=sealed.governance.deterministic_hash,
                    runtime_digest=digest,
                )
            )
            s.commit()
        self.before = sealed.governance.deterministic_hash

    def sync(self, reg: RuntimeRegistry, activate: bool = True) -> None:
        with self.sessions() as s:
            upsert_registry(s, reg, activate)
            s.commit()

    def inp(self) -> BuildStageInput:
        return BuildStageInput(
            tenant_id=TENANT,
            project_id=PROJECT,
            run_id=uuid.uuid4(),
            correlation_id="cid-e9-3",
            workflow_id=uuid.uuid4(),
            sub_skill_id=self.sub_skill,
            build_no=1,
            cycle=0,
            attempt=0,
        )

    def gates(self) -> list[GateResultRow]:
        with self.sessions() as s:
            rows = list(s.scalars(select(GateResultRow)))
            s.expunge_all()
            return rows

    def manifest(self) -> SubSkillManifest:
        with self.sessions() as s:
            row = s.scalars(select(SubSkillManifest)).one()
            s.expunge(row)
            return row


def test_e9_3_t01_passed_writes_the_gate_and_pins_the_manifest() -> None:
    w = World([lib("httpx", ">=0.27"), lib("psycopg")])
    w.sync(REGISTRY)
    result = LibraryStore(w.sessions).run(w.inp())
    assert result.status is LibraryStatus.OK and result.finding_ids == []
    (gate,) = w.gates()
    assert (gate.gate, gate.status, gate.findings, gate.tool_version) == (
        "LIBRARIES",
        "PASSED",
        [],
        DIGEST,
    )
    assert (gate.build_no, gate.cycle, gate.attempt) == (1, 0, 0)
    row = w.manifest()
    assert row.manifest["sbam_build_record"]["build"]["libraries"] == [
        {"package": "httpx", "version_constraint": "==0.27.2"},
        {"package": "psycopg", "version_constraint": "==3.2.1"},
    ]
    assert row.sha256 != w.before  # sealed again over the pins
    assert SkillManifest.model_validate(row.manifest).governance.deterministic_hash == row.sha256


def test_e9_3_t02_missing_fails_the_gate_and_answers_missing_with_its_id() -> None:
    w = World([lib("pandas_ta")])
    w.sync(REGISTRY)
    result = LibraryStore(w.sessions).run(w.inp())
    (gate,) = w.gates()
    assert result.status is LibraryStatus.MISSING and result.finding_ids == [gate.id]
    assert gate.status == "FAILED"
    assert gate.findings[0]["code"] == "MISSING" and gate.findings[0]["package"] == "pandas-ta"
    assert w.manifest().sha256 == w.before  # nothing pinned


def test_e9_3_t06_no_registry_for_the_pinned_digest_is_non_retryable_with_an_alert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w = World([lib("httpx")])
    w.sync(registry(OTHER, httpx="0.27.2"))  # only another image's registry exists
    with caplog.at_level(logging.ERROR), pytest.raises(ApplicationError) as err:
        LibraryStore(w.sessions).run(w.inp())
    assert err.value.type == REGISTRY_UNAVAILABLE and err.value.non_retryable
    (gate,) = w.gates()
    assert gate.status == "FAILED" and gate.findings[0]["code"] == "REGISTRY_UNAVAILABLE"
    alerts = [r for r in caplog.records if getattr(r, "alert", None)]
    assert alerts and alerts[0].__dict__["alert"] == "RUNTIME_REGISTRY_UNAVAILABLE"
    assert w.manifest().sha256 == w.before


def test_e9_3_the_check_uses_the_pinned_digest_not_the_active_registry() -> None:
    w = World([lib("httpx", ">=0.27")])
    w.sync(REGISTRY)  # the image the build pinned
    w.sync(registry(OTHER, httpx="0.20.0"))  # a newer registry activated mid-build
    assert LibraryStore(w.sessions).run(w.inp()).status is LibraryStatus.OK
    assert w.gates()[0].tool_version == DIGEST


def test_e9_3_t07_a_retried_check_keeps_one_row_and_the_same_findings() -> None:
    w = World([lib("pandas_ta"), lib("httpx")])
    w.sync(REGISTRY)
    store = LibraryStore(w.sessions)
    first = store.run(w.inp())
    findings = w.gates()[0].findings
    again = store.run(w.inp())
    assert again == first
    (gate,) = w.gates()
    assert gate.findings == findings


def test_e9_3_a_retried_registry_failure_raises_again_with_one_row() -> None:
    w = World([lib("httpx")])
    for _ in range(2):
        with pytest.raises(ApplicationError):
            LibraryStore(w.sessions).run(w.inp())
    assert len(w.gates()) == 1


def test_e9_3_registry_for_digest_finds_an_inactive_one() -> None:
    w = World([])
    w.sync(REGISTRY)
    w.sync(registry(OTHER, httpx="0.20.0"))
    with w.sessions() as s:
        found = registry_for_digest(s, DIGEST)
        assert found is not None and found.package("httpx") is not None
        assert registry_for_digest(s, "sha256:" + "ee" * 32) is None
