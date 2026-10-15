"""Append-only guard for `master_entry_versions` and `master_snapshots` (E3.2), like `audit_log`.

A row trigger rejects UPDATE and DELETE, a statement trigger rejects TRUNCATE, both with SQLSTATE
`SBAPO`. The migration installs it; `models.py` adds the SQLite equivalent for test databases.
"""

from sbam_api.db.base import SCHEMA

APPEND_ONLY_SQLSTATE = "SBAPO"
APPEND_ONLY_TABLES = ("master_entry_versions", "master_snapshots")


def create_append_only_guard(schema: str = SCHEMA) -> list[str]:
    """Postgres: reject UPDATE/DELETE (row) and TRUNCATE (statement), SQLSTATE SBAPO."""
    function = f'"{schema}".forbid_masters_change()'
    statements = [f"""
CREATE OR REPLACE FUNCTION {function} RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only (%)', TG_TABLE_NAME, TG_OP
    USING ERRCODE = '{APPEND_ONLY_SQLSTATE}';
END $$;
"""]
    for table in APPEND_ONLY_TABLES:
        target = f'"{schema}"."{table}"'
        statements += [
            f'CREATE TRIGGER "{table}_append_only" BEFORE UPDATE OR DELETE ON {target} '
            f"FOR EACH ROW EXECUTE FUNCTION {function}",
            f'CREATE TRIGGER "{table}_no_truncate" BEFORE TRUNCATE ON {target} '
            f"FOR EACH STATEMENT EXECUTE FUNCTION {function}",
        ]
    return statements


def drop_append_only_guard(schema: str = SCHEMA) -> list[str]:
    statements = []
    for table in reversed(APPEND_ONLY_TABLES):
        target = f'"{schema}"."{table}"'
        statements += [
            f'DROP TRIGGER IF EXISTS "{table}_no_truncate" ON {target}',
            f'DROP TRIGGER IF EXISTS "{table}_append_only" ON {target}',
        ]
    return [*statements, f'DROP FUNCTION IF EXISTS "{schema}".forbid_masters_change()']
