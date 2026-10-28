"""The insert-only guard every handoff table gets (E9.4, TD §12.6): a platform template, never LLM
output. A row-level trigger rejects UPDATE and DELETE; TRUNCATE skips row triggers, so a
statement-level trigger rejects it too. Both raise `sbam_handoff_insert_only`.

`CREATE OR REPLACE` (PostgreSQL 14+): an uploaded base that already guards the table, or a second
handoff table, re-creates the same function and triggers instead of failing.
"""

from collections.abc import Iterable
from typing import Final

GUARD_ERROR: Final = "sbam_handoff_insert_only"
GUARD_FUNCTION: Final = "sbam_handoff_insert_only"
ROW_TRIGGER: Final = "sbam_handoff_insert_only_row"
TRUNCATE_TRIGGER: Final = "sbam_handoff_insert_only_truncate"

FUNCTION_SQL: Final = f"""CREATE OR REPLACE FUNCTION {GUARD_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '{GUARD_ERROR}: % on %', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'insufficient_privilege';
END;
$$"""


def guard_sql(tables: Iterable[str]) -> list[str]:
    """The function once, then both triggers per table (sorted, so the output is stable)."""
    ordered = sorted(set(tables))
    if not ordered:
        return []
    statements = [FUNCTION_SQL]
    for table in ordered:
        statements += [
            f"CREATE OR REPLACE TRIGGER {ROW_TRIGGER} BEFORE UPDATE OR DELETE ON {table}\n"
            f"FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}()",
            f"CREATE OR REPLACE TRIGGER {TRUNCATE_TRIGGER} BEFORE TRUNCATE ON {table}\n"
            f"FOR EACH STATEMENT EXECUTE FUNCTION {GUARD_FUNCTION}()",
        ]
    return statements
