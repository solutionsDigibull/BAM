"""gate_results: the REVIEW gate

Revision ID: 0055_gate_result_review
Revises: 0054_document_relevance
Create Date: 2026-11-17

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#759 (WG-11): the reviewer's verdict is a `gate_results` row with gate `REVIEW`, so the corrector
reads its findings like any gate's. This widens `ck_gate_results_gate_valid` to
`build_dashboard.GateName` as of this revision. Downgrade deletes the REVIEW rows first (the old
constraint would refuse them), then restores the old list.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0055_gate_result_review"
down_revision: str | Sequence[str] | None = "0054_document_relevance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_gate_results_gate_valid"  # op.f() in _set: needs a running migration
_OLD = (
    "'ISATVON_CONTRACT', 'LIBRARIES', 'SCHEMA_SQL', 'CODEGEN', 'AST_SYNTAX', 'AST_SKELETON', "
    "'AST_CONFORMANCE', 'AST_DECOUPLING', 'AST_NO_MANUAL_LOOP', 'AST_HANDOFF', "
    "'AST_IMPORTS', 'AST_SQL_SAFETY', 'RUFF', 'BLACK', 'MYPY', 'CLAMAV', 'BANDIT', "
    "'IMPORT_RESOLUTION', 'LOOP_TESTS'"
)
_NEW = (
    "'ISATVON_CONTRACT', 'LIBRARIES', 'SCHEMA_SQL', 'CODEGEN', 'AST_SYNTAX', 'AST_SKELETON', "
    "'AST_CONFORMANCE', 'AST_DECOUPLING', 'AST_NO_MANUAL_LOOP', 'AST_HANDOFF', "
    "'AST_IMPORTS', 'AST_SQL_SAFETY', 'RUFF', 'BLACK', 'MYPY', 'CLAMAV', 'BANDIT', "
    "'IMPORT_RESOLUTION', 'REVIEW', 'LOOP_TESTS'"
)


def _set(values: str) -> None:
    name = op.f(_NAME)  # already the full name, no convention prefix
    op.drop_constraint(name, "gate_results", schema="sbam", type_="check")
    op.create_check_constraint(name, "gate_results", f"gate IN ({values})", schema="sbam")


def upgrade() -> None:
    _set(_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM sbam.gate_results WHERE gate = 'REVIEW'")
    _set(_OLD)
