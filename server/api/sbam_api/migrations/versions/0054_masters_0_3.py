"""masters: BAM 0.2 payloads -> Masters 0.3 (the approved Admin Masters UI)

Revision ID: 0054_masters_0_3
Revises: 0053_project_goal
Create Date: 2026-11-16

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#781 (ADR 0029): every Organization, Rules, Agents, Skills and Tooling entry gets a new version
whose payload uses the Masters 0.3 columns (`sbam_contracts.masters_v03`), mapped as in
docs/research/admin-masters-ui-mapping.md §3. Nothing is lost: a 0.2 value with no 0.3 column goes
under `legacy_0_2`, with the entry's 0.2 row label and stable id, and a Restricted RAG Reference
keeps the entry Restricted (`security_access_level`).

Versions are append-only (a trigger refuses UPDATE / DELETE), so both directions append:
  * upgrade: version n+1 holds the 0.3 payload; the row label becomes the id column, upper-cased
    like every stable id (the 0.2 sample rows were labelled `Unit #1`, ...);
  * downgrade: an entry whose current version is still this migration's gets version n+2 with its
    0.2 payload back, and its 0.2 row label and stable id. An entry edited since keeps its 0.3
    payload: it can't be mapped back without losing the edit.

The mapping is frozen here on purpose: it must not change when the contracts move on.
"""

import json
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0054_masters_0_3"
down_revision: str | Sequence[str] | None = "0053_project_goal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTOR = "migration-0054"
TYPES = ("ORGANIZATION", "RULES", "AGENTS", "SKILLS", "TOOLING")
# master -> (0.3 key, 0.2 key or None). Keys not listed here (other than the id) start empty.
COLUMNS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "ORGANIZATION": (
        ("company_type", None),  # org_name and type, joined below
        ("location", "location"),
        ("certifications", "certifications"),
        ("segment_offerings", "offerings"),
        ("customers_audience", "customers"),
        ("employees_roles", "employees"),
        ("data_sources", "data_sources"),
        ("systems_tools", "systems_tools"),
        ("assets", "assets"),
        ("fde_notes", "misc_audit"),
    ),
    "RULES": (
        ("rule_type_description", "domain_rule"),
        ("record_status", None),  # the entry's status, so the Status column agrees with it
    ),
    "AGENTS": (
        ("agent_name", "agent_name"),
        ("reference", "repo"),
        ("model", "model"),
    ),
    "SKILLS": (
        ("skill", "skill"),
        ("references", "documents"),
    ),
    "TOOLING": (
        ("tool_name", "examples"),
        ("category", "category"),
        ("links_path", "links_path"),
        ("owner_access", "owner_access"),
        ("record_status", "record_status"),
        ("source_version_checked_at", "source_version_checked_at"),
        ("refresh_frequency", "refresh_frequency"),
        ("record_type", "entity_node_type"),
    ),
}
ID_KEY = {
    "ORGANIZATION": "org_id",
    "RULES": "rule_id",
    "AGENTS": "agent_id",
    "SKILLS": "skill_id",
    "TOOLING": "tooling_id",
}
# Every 0.3 field per master (defaults), so a migrated payload validates as written.
FIELDS: dict[str, tuple[str, ...]] = {
    "ORGANIZATION": (
        "org_id", "company_type", "brand_properties", "location", "certifications",
        "segment_offerings", "customers_audience", "employees_roles", "data_sources",
        "systems_tools", "assets", "fde_notes", "documents", "artifacts",
    ),
    "RULES": (
        "rule_id", "rule_type_description", "source_evidence", "mapping_enforcement",
        "stop_review_condition", "owner_version", "record_status", "effective_expiry", "skill_ref",
        "applies_to", "fde_use", "client_use", "acts_on",
    ),
    "AGENTS": (
        "agent_id", "agent_name", "type", "reference", "source", "tools", "model",
    ),
    "SKILLS": (
        "skill_id", "skill", "instruction", "source_data", "automation", "tech_stack", "variables",
        "outcome", "next", "references",
    ),
    "TOOLING": (
        "tooling_id", "tool_name", "category", "links_path", "owner_access", "record_status",
        "source_version_checked_at", "refresh_frequency", "record_type", "source_tab",
        "bullm_badge",
    ),
}  # fmt: skip


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def to_v03(
    master_type: str, old: dict[str, Any], status: str, stable_id: str, row_label: str
) -> dict[str, Any]:
    """One 0.2 payload as a 0.3 payload. Pure, so the round trip is unit-tested."""
    new: dict[str, Any] = dict.fromkeys(FIELDS[master_type], "")
    new[ID_KEY[master_type]] = stable_id.upper()
    used: set[str] = set()
    for key, source in COLUMNS[master_type]:
        if source is not None:
            new[key] = _text(old.get(source))
            used.add(source)
    if master_type == "ORGANIZATION":
        name, kind = _text(old.get("org_name")).strip(), _text(old.get("type")).strip()
        new["company_type"] = f"{name} - {kind}" if name and kind else name or kind
        used |= {"org_name", "type"}
        if _text(old.get("code")).strip().upper() == stable_id.upper():
            used.add("code")  # the id column already holds it
    if master_type == "RULES":
        new["record_status"] = status.capitalize()
        new["prompts"] = {"system": "", "micro": []}
    if master_type == "AGENTS":
        new["kind"] = "Agent"
    legacy = {k: _text(v) for k, v in old.items() if k not in used and _text(v).strip()}
    rag = old.get("rag_reference")
    if isinstance(rag, dict) and str(rag.get("Security / Access Level", "")).strip():
        legacy["security_access_level"] = str(rag["Security / Access Level"])
    legacy["_row_label"] = row_label
    legacy["_stable_id"] = stable_id
    new["legacy_0_2"] = legacy
    return new


def _append(conn: sa.Connection, entry: Any, number: int, payload: dict[str, Any]) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO sbam.master_entry_versions"
            " (id, tenant_id, entry_id, version, payload, created_at, created_by)"
            " VALUES (:id, :tenant, :entry, :version, CAST(:payload AS jsonb), now(), :actor)"
        ),
        {
            "id": uuid.uuid4(),
            "tenant": entry.tenant_id,
            "entry": entry.id,
            "version": number,
            "payload": json.dumps(payload, ensure_ascii=False),
            "actor": ACTOR,
        },
    )


def _current(conn: sa.Connection) -> Sequence[Any]:
    return conn.execute(
        sa.text(
            "SELECT e.id, e.tenant_id, e.master_type, e.stable_id, e.row_label, e.status,"
            " e.current_version, v.payload, v.created_by"
            " FROM sbam.master_entries e JOIN sbam.master_entry_versions v"
            " ON v.entry_id = e.id AND v.version = e.current_version"
            " WHERE e.master_type IN :types"
        ).bindparams(sa.bindparam("types", expanding=True)),
        {"types": list(TYPES)},
    ).all()


def _move(conn: sa.Connection, entry: Any, number: int, stable_id: str, label: str) -> None:
    conn.execute(
        sa.text(
            "UPDATE sbam.master_entries SET current_version = :n, stable_id = :sid,"
            " row_label = :label, row_version = row_version + 1, updated_at = now()"
            " WHERE id = :id"
        ),
        {"n": number, "sid": stable_id, "label": label, "id": entry.id},
    )


def upgrade() -> None:
    conn = op.get_bind()
    for entry in _current(conn):
        payload = to_v03(
            entry.master_type, dict(entry.payload), entry.status, entry.stable_id, entry.row_label
        )
        number = entry.current_version + 1
        _append(conn, entry, number, payload)
        new_id = payload[ID_KEY[entry.master_type]]
        _move(conn, entry, number, new_id, new_id)


def downgrade() -> None:
    conn = op.get_bind()
    for entry in _current(conn):
        if entry.created_by != ACTOR:
            continue  # edited in 0.3 since: keep it
        legacy = dict(entry.payload).get("legacy_0_2") or {}
        old = conn.execute(
            sa.text(
                "SELECT payload FROM sbam.master_entry_versions"
                " WHERE entry_id = :id AND version = :n"
            ),
            {"id": entry.id, "n": entry.current_version - 1},
        ).scalar_one()
        number = entry.current_version + 1
        _append(conn, entry, number, dict(old))
        _move(
            conn,
            entry,
            number,
            legacy.get("_stable_id", entry.stable_id),
            legacy.get("_row_label", entry.row_label),
        )
