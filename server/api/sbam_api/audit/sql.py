"""Append-only guard for `audit_log` (C-42, TD §16).

Triggers, not grants: the app role owns the schema (it could re-grant itself anything), and a
REVOKE UPDATE would also break the outbox's foreign key, whose check locks the referenced audit row
(`FOR KEY SHARE` needs UPDATE-level privilege). So:
  * a row trigger rejects every UPDATE and DELETE, and
  * a statement trigger rejects TRUNCATE (which skips row triggers),
both with SQLSTATE `SBAUD`.
"""

from sbam_api.db.base import SCHEMA

AUDIT_APPEND_ONLY_SQLSTATE = "SBAUD"


def create_audit_guard(schema: str = SCHEMA) -> list[str]:
    table = f'"{schema}"."audit_log"'
    function = f'"{schema}".forbid_audit_change()'
    return [
        f"""
CREATE OR REPLACE FUNCTION {function} RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only (%)', TG_OP
    USING ERRCODE = '{AUDIT_APPEND_ONLY_SQLSTATE}';
END $$;
""",
        f'CREATE TRIGGER "audit_log_append_only" BEFORE UPDATE OR DELETE ON {table} '
        f"FOR EACH ROW EXECUTE FUNCTION {function}",
        f'CREATE TRIGGER "audit_log_no_truncate" BEFORE TRUNCATE ON {table} '
        f"FOR EACH STATEMENT EXECUTE FUNCTION {function}",
    ]


def drop_audit_guard(schema: str = SCHEMA) -> list[str]:
    table = f'"{schema}"."audit_log"'
    return [
        f'DROP TRIGGER IF EXISTS "audit_log_no_truncate" ON {table}',
        f'DROP TRIGGER IF EXISTS "audit_log_append_only" ON {table}',
        f'DROP FUNCTION IF EXISTS "{schema}".forbid_audit_change()',
    ]
