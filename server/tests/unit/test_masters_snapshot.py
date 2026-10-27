"""E3.2 snapshot rules, without a database: override, exclusions and a reproducible hash."""

import uuid
from typing import Any

from sbam_api.masters.snapshot import Candidate, resolve, snapshot_hash
from sbam_contracts.canonical import sha256_of
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_api import SnapshotDocument

PROJECT, OTHER = uuid.uuid4(), uuid.uuid4()


def entry(
    stable_id: str,
    version: int = 1,
    *,
    master_type: str = "RULES",
    project_id: uuid.UUID | None = None,
    status: str = "PROPOSED",
    payload: dict[str, Any] | None = None,
) -> Candidate:
    return Candidate(
        entry_id=uuid.uuid4(),
        project_id=project_id,
        master_type=master_type,
        stable_id=stable_id,
        status=status,
        version=version,
        payload=payload
        or ({"domain_rule": f"{stable_id}: policy"} if master_type == "RULES" else {}),
    )


def test_e3_2_t04_snapshot_is_reproducible() -> None:
    rows = [entry("RULE-BOM", 2), entry("RULE-OFFER"), entry("SK-BOM", master_type="SKILLS")]
    first = resolve(PROJECT, rows)
    second = resolve(PROJECT, list(reversed(rows)))
    assert first == second
    assert snapshot_hash(PROJECT, first) == snapshot_hash(PROJECT, second)
    assert snapshot_hash(PROJECT, first).startswith("sha256:")


def test_e3_2_t04_a_new_version_changes_the_hash() -> None:
    rule = entry("RULE-BOM", 2)
    before = snapshot_hash(PROJECT, resolve(PROJECT, [rule]))
    bumped = Candidate(**{**rule.__dict__, "version": 3})
    assert snapshot_hash(PROJECT, resolve(PROJECT, [bumped])) != before


def test_e3_2_t05_project_entry_overrides_the_tenant_entry() -> None:
    tenant = entry("RULE-BOM", 2)
    own = entry("RULE-BOM", 1, project_id=PROJECT)
    pinned = resolve(PROJECT, [tenant, own])
    assert [(e.entry_id, e.version) for e in pinned] == [(own.entry_id, 1)]
    # whatever order the rows arrive in
    assert resolve(PROJECT, [own, tenant]) == pinned


def test_e3_2_other_projects_entries_never_reach_a_snapshot() -> None:
    assert resolve(PROJECT, [entry("RULE-BOM", project_id=OTHER)]) == []


def test_e3_2_every_entry_is_pinned_with_its_status_and_restricted_flag() -> None:
    """E3.6 decision: the snapshot pins every live entry; consumers decide inclusion from these."""
    restricted_tool = entry(
        "TOOL-MFG-02",
        master_type="TOOLING",
        payload={"category": "API", "security_access_level": "Restricted"},
    )
    rows = [
        entry("Rule #1", status="ILLUSTRATIVE"),
        entry("RULE-OLD", status="RETIRED"),
        restricted_tool,
        entry("RULE-BOM"),
        entry("TOOL-MFG-01", master_type="TOOLING", payload={"category": "MCP"}),
    ]
    pinned = {e.stable_id: (e.status.value, e.restricted) for e in resolve(PROJECT, rows)}
    assert pinned == {
        "Rule #1": ("ILLUSTRATIVE", False),
        "RULE-OLD": ("RETIRED", False),
        "RULE-BOM": ("PROPOSED", False),
        "TOOL-MFG-01": ("PROPOSED", False),
        "TOOL-MFG-02": ("PROPOSED", True),
    }


def test_e3_2_empty_snapshot_is_valid_and_hashed() -> None:
    assert resolve(PROJECT, []) == []
    assert snapshot_hash(PROJECT, []).startswith("sha256:")


# --- E3.8b: parsed master documents pinned with the entries ---------------------------------


def pinned_document(title: str = "costing.pdf", sha: str = "b" * 64) -> SnapshotDocument:
    return SnapshotDocument(
        document_id=uuid.uuid4(),
        title=title,
        master_type=MasterType.RULES,
        intent_class="Policy / rules",
        sha256=sha,
        derived_md_key=f"t/p/derived/{sha}",
    )


def test_e3_8b_a_snapshot_without_documents_keeps_the_hash_it_always_had() -> None:
    """The `documents` key is only hashed when there are some, so a snapshot taken before master
    documents were pinned still passes its integrity check."""
    pinned = resolve(PROJECT, [entry("RULE-BOM", 2), entry("SK-BOM", master_type="SKILLS")])
    legacy = sha256_of(
        {
            "project_id": str(PROJECT),
            "entries": [e.model_dump(mode="json") for e in pinned],
        }
    )
    assert snapshot_hash(PROJECT, pinned) == legacy
    assert snapshot_hash(PROJECT, pinned, []) == legacy
    assert snapshot_hash(PROJECT, pinned, None) == legacy


def test_e3_8b_pinning_a_document_changes_the_hash_and_the_order_does_not() -> None:
    pinned = resolve(PROJECT, [entry("RULE-BOM")])
    one, two = pinned_document("a.pdf"), pinned_document("b.pdf", "c" * 64)
    plain = snapshot_hash(PROJECT, pinned)
    assert snapshot_hash(PROJECT, pinned, [one]) != plain
    assert snapshot_hash(PROJECT, pinned, [one]) != snapshot_hash(PROJECT, pinned, [two])
    assert snapshot_hash(PROJECT, pinned, [one, two]) == snapshot_hash(PROJECT, pinned, [two, one])


def test_e3_8b_a_changed_document_changes_the_hash() -> None:
    pinned = resolve(PROJECT, [entry("RULE-BOM")])
    doc = pinned_document()
    key = f"t/p/derived/{'d' * 64}"
    changed = doc.model_copy(update={"sha256": "d" * 64, "derived_md_key": key})
    assert snapshot_hash(PROJECT, pinned, [doc]) != snapshot_hash(PROJECT, pinned, [changed])
