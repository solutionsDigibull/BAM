"""Frozen rows (TD §7.1, E27): a BEFORE UPDATE OR DELETE trigger raises when `is_frozen` is true.

The baseline migration creates the shared trigger function. A table with an `is_frozen` column
opts in from its own migration:

    op.execute(create_frozen_trigger("workflow_versions"))   # upgrade
    op.execute(drop_frozen_trigger("workflow_versions"))     # downgrade

The repository maps the trigger's SQLSTATE to 423 RESOURCE_LOCKED; raw SQL gets the Postgres error.
"""

from sbam_api.db.base import SCHEMA

FROZEN_SQLSTATE = "SB423"  # custom SQLSTATE: five upper-case letters/digits


def create_frozen_function(schema: str = SCHEMA) -> str:
    return f"""
CREATE OR REPLACE FUNCTION "{schema}".forbid_frozen_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.is_frozen THEN
    RAISE EXCEPTION 'row is frozen (%.%)', TG_TABLE_SCHEMA, TG_TABLE_NAME
      USING ERRCODE = '{FROZEN_SQLSTATE}';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END $$;
"""


def drop_frozen_function(schema: str = SCHEMA) -> str:
    return f'DROP FUNCTION IF EXISTS "{schema}".forbid_frozen_change()'


def create_frozen_trigger(table: str, schema: str = SCHEMA) -> str:
    return (
        f'CREATE TRIGGER "{table}_frozen" BEFORE UPDATE OR DELETE ON "{schema}"."{table}" '
        f'FOR EACH ROW EXECUTE FUNCTION "{schema}".forbid_frozen_change()'
    )


def drop_frozen_trigger(table: str, schema: str = SCHEMA) -> str:
    return f'DROP TRIGGER IF EXISTS "{table}_frozen" ON "{schema}"."{table}"'
