"""Postgres guard for `sow_versions` (E6.3): a version's content never changes once written.

A BEFORE UPDATE OR DELETE trigger raises (SQLSTATE SBSOW) on a DELETE, a TRUNCATE, or an UPDATE
that changes any content column. Lifecycle columns (state, approval, row_version, updated_at)
still change through the state-machine service. Migration 0020 installs it.
"""

from typing import Final

from sbam_api.db.base import SCHEMA

IMMUTABLE_SQLSTATE: Final = "SBSOW"
CONTENT_COLUMNS: Final = (
    "id",
    "tenant_id",
    "project_id",
    "sow_id",
    "version",
    "source",
    "run_id",
    "knowledge_version_id",
    "parent_version_id",
    "llm_call_id",
    "schema_version",
    "structured",
    "content_tiptap",
    "sha256",
    "error_reason",
    "created_at",
    "created_by",
)


def create_immutable_guard(schema: str = SCHEMA) -> list[str]:
    function = f'"{schema}".forbid_sow_version_change()'
    target = f'"{schema}"."sow_versions"'
    changed = " OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in CONTENT_COLUMNS)
    return [
        f"""
CREATE OR REPLACE FUNCTION {function} RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP <> 'UPDATE' OR {changed} THEN
    RAISE EXCEPTION 'sow_versions content is immutable (%)', TG_OP
      USING ERRCODE = '{IMMUTABLE_SQLSTATE}';
  END IF;
  RETURN NEW;
END $$;
""",
        f'CREATE TRIGGER "sow_versions_immutable" BEFORE UPDATE OR DELETE ON {target} '
        f"FOR EACH ROW EXECUTE FUNCTION {function}",
        f'CREATE TRIGGER "sow_versions_no_truncate" BEFORE TRUNCATE ON {target} '
        f"FOR EACH STATEMENT EXECUTE FUNCTION {function}",
    ]


def drop_immutable_guard(schema: str = SCHEMA) -> list[str]:
    target = f'"{schema}"."sow_versions"'
    return [
        f'DROP TRIGGER IF EXISTS "sow_versions_no_truncate" ON {target}',
        f'DROP TRIGGER IF EXISTS "sow_versions_immutable" ON {target}',
        f'DROP FUNCTION IF EXISTS "{schema}".forbid_sow_version_change()',
    ]
