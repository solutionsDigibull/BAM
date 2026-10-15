"""E3.6 master context for Cognee: snapshot entries -> masters.jsonl nodes + inclusion report."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sbam_api.masters.context import Version, build_master_context
from sbam_contracts.masters import MasterStatus, MasterType
from sbam_contracts.masters_api import SnapshotEntry

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def rag(**keys: str) -> dict[str, str]:
    base = {
        "Example": "",
        "Links / Path": "",
        "Entity / Node Type": "",
        "Security / Access Level": "",
        "Refresh Frequency": "",
        "ID Field": "",
        "Parent Entity": "",
    }
    return {**base, **keys}


def pin(
    stable_id: str,
    master_type: MasterType,
    payload: dict[str, Any],
    *,
    status: MasterStatus = MasterStatus.PROPOSED,
    restricted: bool = False,
    written: datetime = NOW,
) -> tuple[SnapshotEntry, Version]:
    entry = SnapshotEntry(
        entry_id=uuid.uuid4(),
        master_type=master_type,
        stable_id=stable_id,
        version=1,
        status=status,
        restricted=restricted,
    )
    return entry, Version(payload=payload, created_at=written)


def build(*rows: tuple[SnapshotEntry, Version], allow_restricted: bool = False) -> Any:
    return build_master_context(
        [e for e, _ in rows],
        {e.entry_id: v for e, v in rows},
        allow_restricted=allow_restricted,
        now=NOW,
    )


def org(code: str) -> tuple[SnapshotEntry, Version]:
    return pin(code, MasterType.ORGANIZATION, {"code": code, "org_name": f"Org {code}"})


RULE_BOM = pin(
    "RULE-BOM",
    MasterType.RULES,
    {
        "domain_rule": "RULE-BOM: applies to BOM.",
        "rag_reference": rag(
            **{"Entity / Node Type": "DomainKnowledge", "ID Field": "DOM-PCBA-01"},
            **{"Parent Entity": "ORG-001"},
        ),
    },
)


def test_e3_6_t01_typed_node_with_an_edge_to_its_parent() -> None:
    ctx = build(org("ORG-001"), RULE_BOM)
    node = next(n for n in ctx.nodes if n.stable_id == "RULE-BOM")
    assert (node.node_id, node.node_type, node.parent_id) == (
        "DOM-PCBA-01",
        "DomainKnowledge",
        "ORG-001",
    )
    assert ctx.warnings == []


def test_e3_6_t02_organization_rows_are_roots() -> None:
    ctx = build(org("ORG-EMS"), org("ORG-CAB"))
    assert [(n.node_id, n.node_type, n.parent_id) for n in ctx.nodes] == [
        ("ORG-CAB", "ORGANIZATION", None),
        ("ORG-EMS", "ORGANIZATION", None),
    ]


def test_e3_6_t03_restricted_is_excluded_by_default() -> None:
    secret = pin("RULE-COST", MasterType.RULES, {"domain_rule": "x"}, restricted=True)
    ctx = build(secret)
    assert ctx.nodes == []
    assert [(x.stable_id, x.reason) for x in ctx.excluded] == [("RULE-COST", "RESTRICTED")]


def test_e3_6_t04_restricted_is_included_when_the_project_allows_it() -> None:
    secret = pin("RULE-COST", MasterType.RULES, {"domain_rule": "x"}, restricted=True)
    ctx = build(secret, allow_restricted=True)
    assert [n.stable_id for n in ctx.nodes] == ["RULE-COST"] and ctx.excluded == []


def test_e3_6_t05_illustrative_and_retired_never_reach_cognee() -> None:
    sample = pin("Skill #1", MasterType.SKILLS, {"skill": "x"}, status=MasterStatus.ILLUSTRATIVE)
    old = pin("SK-OLD", MasterType.SKILLS, {"skill": "x"}, status=MasterStatus.RETIRED)
    ctx = build(sample, old, allow_restricted=True)
    assert ctx.nodes == []
    assert sorted((x.stable_id, x.reason) for x in ctx.excluded) == [
        ("SK-OLD", "RETIRED"),
        ("Skill #1", "ILLUSTRATIVE"),
    ]


def test_e3_6_t06_same_snapshot_same_bytes() -> None:
    rows = (org("ORG-001"), RULE_BOM, pin("SK-BOM", MasterType.SKILLS, {"skill": "SK-BOM: x"}))
    one = build(*rows).jsonl
    two = build(*reversed(rows)).jsonl
    assert one == two and one
    assert hashlib.sha256(one).hexdigest() == hashlib.sha256(two).hexdigest()


def test_e3_6_counts_sum_to_the_snapshot_size() -> None:
    rows = (
        org("ORG-001"),
        RULE_BOM,
        pin("Rule #1", MasterType.RULES, {"domain_rule": "x"}, status=MasterStatus.ILLUSTRATIVE),
        pin("RULE-COST", MasterType.RULES, {"domain_rule": "x"}, restricted=True),
    )
    ctx = build(*rows)
    assert len(ctx.nodes) + len(ctx.excluded) == len(rows)


def test_e3_6_unknown_parent_attaches_to_the_root_with_a_warning() -> None:
    ctx = build(RULE_BOM)  # ORG-001 isn't in the snapshot
    assert ctx.nodes[0].parent_id is None
    assert ctx.warnings == ["RULE-BOM: unknown parent ORG-001; attached to the graph root"]


def test_e3_6_empty_entity_type_falls_back_to_the_master_type() -> None:
    ctx = build(pin("SK-BOM", MasterType.SKILLS, {"skill": "SK-BOM: x", "rag_reference": rag()}))
    assert (ctx.nodes[0].node_id, ctx.nodes[0].node_type) == ("SK-BOM", "SKILL")


def test_e3_6_duplicate_id_field_gets_the_stable_id_suffix() -> None:
    a = pin("RULE-A", MasterType.RULES, {"rag_reference": rag(**{"ID Field": "DOM-1"})})
    b = pin("RULE-B", MasterType.RULES, {"rag_reference": rag(**{"ID Field": "DOM-1"})})
    ctx = build(a, b)
    assert sorted(n.node_id for n in ctx.nodes) == ["DOM-1", "DOM-1-RULE-B"]
    assert ctx.warnings == ["duplicate node id DOM-1: RULE-B uses DOM-1-RULE-B"]


def test_e3_6_elapsed_refresh_frequency_is_flagged_not_rebuilt() -> None:
    stale = pin(
        "SK-FX",
        MasterType.SKILLS,
        {"rag_reference": rag(**{"Refresh Frequency": "Weekly"})},
        written=NOW - timedelta(days=8),
    )
    fresh = pin(
        "SK-BOM",
        MasterType.SKILLS,
        {"rag_reference": rag(**{"Refresh Frequency": "Monthly"})},
        written=NOW - timedelta(days=3),
    )
    ctx = build(stale, fresh)
    assert ctx.refresh_due == ["SK-FX"]
    assert len(ctx.nodes) == 2  # still included


def test_e3_6_links_path_stays_pointer_text() -> None:
    link = "s3://domain-rules/pcba_costing_v1.json"
    row = pin("RULE-BOM", MasterType.RULES, {"rag_reference": rag(**{"Links / Path": link})})
    line = json.loads(build(row).jsonl.decode("utf-8").splitlines()[0])
    assert line["payload"]["rag_reference"]["Links / Path"] == link


def test_e3_6_one_node_per_line_with_the_documented_fields() -> None:
    lines = build(org("ORG-001"), RULE_BOM).jsonl.decode("utf-8").splitlines()
    assert len(lines) == 2
    assert set(json.loads(lines[0])) == {
        "node_id",
        "node_type",
        "master_type",
        "stable_id",
        "entry_id",
        "version",
        "parent_id",
        "payload",
    }
