"""E0.15-T03: the drift helper reports a state the machine gained or lost without a migration.

The database side runs in tests/integration/test_state_check_constraints_db.py (needs PostgreSQL).
"""

from sbam_api.db.base import status_check
from sbam_contracts.state_machines import MachineName, check_constraint_values
from state_checks import check_values, drift, stateful_tables

# What PostgreSQL gives back for `state IN ('A', 'B')` on a varchar column (pg_get_constraintdef).
PG_SPELLING = (
    "CHECK (((state)::text = ANY ((ARRAY['DRAFT'::character varying, "
    "'IT''S'::character varying])::text[])))"
)


def test_e0_15_t03_a_machine_with_one_more_state_is_reported() -> None:
    machine = check_constraint_values(MachineName.SOW_VERSION)
    found = drift(machine, (*machine, "ON_HOLD"))
    assert found and found.missing == ("ON_HOLD",) and found.extra == ()


def test_e0_15_t03_a_machine_with_one_state_less_is_reported() -> None:
    machine = check_constraint_values(MachineName.SOW_VERSION)
    found = drift(machine, machine[1:])
    assert found and found.extra == (machine[0],) and found.missing == ()


def test_e0_15_no_drift_is_falsy() -> None:
    values = check_constraint_values(MachineName.WORKFLOW)
    assert not drift(values, values)


def test_e0_15_values_are_read_from_postgres_spelling_and_from_the_model() -> None:
    assert check_values(PG_SPELLING) == ("DRAFT", "IT'S")
    model_sql = str(status_check(("A", "B"), column="state").sqltext)
    assert check_values(model_sql) == ("A", "B")


def test_e0_15_every_state_check_in_the_models_is_found() -> None:
    """The discovery covers the models that call `state_check` (no hand-kept list)."""
    assert {t.table for t in stateful_tables()} >= {
        "projects",
        "document_versions",
        "knowledge_versions",
        "sow_versions",
        "workflows",
        "macro_skills",
        "sub_skills",
    }
