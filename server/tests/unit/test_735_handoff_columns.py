"""#735: a generated handoff table accepts the platform's `INSERT INTO <t> (skill_id, payload)`."""

from sbam_api.schema_sql.validate import check
from sbam_workers.build.schema_sql.merge import Declared, columns, merge

HANDOFF = "costing_module_input"
# What the model wrote for MS01.1 before #733: no platform columns, the required field NOT NULL.
BARE = (
    f"CREATE TABLE {HANDOFF} (\n"
    "    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n"
    "    created_at timestamptz NOT NULL DEFAULT now(),\n"
    "    normalized_bom_data jsonb NOT NULL\n"
    ")"
)
COMPLETE = (
    f"CREATE TABLE {HANDOFF} (id bigserial PRIMARY KEY, normalized_bom_data jsonb, "
    "skill_id text NOT NULL DEFAULT '', payload jsonb NOT NULL DEFAULT '{}'::jsonb)"
)
DECLARED = Declared(tables={HANDOFF: {"MS01.1"}}, handoff={HANDOFF: {"normalized_bom_data"}})


def merged(ddl: str, declared: Declared = DECLARED) -> tuple[str, dict[str, str]]:
    out = merge(declared, None, {"MS01.1": check(ddl)})
    assert out.problems == []
    script = check(out.sql)
    assert script.ok, script.problems  # still only allowlisted statements: no ALTER
    [table] = [s for s in script.statements if s.table is not None]
    return out.sql, {c.name.lower(): c.sql(dialect="postgres") for c in columns(table)}


def test_735_t01_a_handoff_table_without_the_platform_columns_gets_them() -> None:
    _, cols = merged(BARE)
    assert list(cols) == ["id", "created_at", "normalized_bom_data", "skill_id", "payload"]
    assert cols["skill_id"] == "skill_id TEXT NOT NULL DEFAULT ''"
    assert cols["payload"] == "payload JSONB NOT NULL DEFAULT CAST('{}' AS JSONB)"


def test_735_t01_a_column_the_insert_leaves_empty_loses_not_null_only() -> None:
    _, cols = merged(BARE)
    assert cols["normalized_bom_data"] == "normalized_bom_data JSONB"
    assert cols["created_at"].startswith("created_at TIMESTAMPTZ NOT NULL DEFAULT")  # a default
    assert cols["id"].startswith("id UUID PRIMARY KEY")


def test_735_t01_only_the_missing_one_is_added() -> None:
    _, cols = merged(BARE.replace("NOT NULL\n)", ",\n    skill_id text\n)"))
    assert list(cols)[-2:] == ["skill_id", "payload"]
    assert cols["skill_id"] == "skill_id TEXT"  # the model's own definition is kept


def test_735_t02_a_table_that_accepts_the_insert_is_unchanged() -> None:
    sql, _ = merged(COMPLETE)
    assert sql.startswith(COMPLETE + ";\n")  # verbatim: byte-identical to before #735


def test_735_t02_a_table_that_is_not_a_handoff_table_is_unchanged() -> None:
    ddl = "CREATE TABLE bom_lines (id bigserial PRIMARY KEY, part text NOT NULL)"
    sql, _ = merged(ddl, Declared(tables={"bom_lines": {"MS01.1"}}))
    assert sql.startswith(ddl + ";\n")


def test_735_merge_stays_deterministic() -> None:
    assert merged(BARE)[0] == merged(BARE)[0]
