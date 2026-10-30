"""The test-generation golden set (E15.5): five invented Sub-Skills at four iteration bounds.

Each item is the base Sub-Skill (`testgen-base.json`, the MS01.1 sample) with the inputs, state
parameters, tables and gate conditions of one domain, and one `max_iterations`. A bound of 1 takes
`PASS_AFTER_RETRY` out of the kinds the model may use, so the set also checks that it keeps to the
allowed kinds. Everything else in the document is the base's: the variants differ in what the
tests depend on (the closed input schema, the declared tables, the bound and the conditions), not
in the prose.

`server/evals/golden/testgen.json` is this module's output, and a test checks the two agree:

    uv run python -m sbam_workers.evals.testgen_set
"""

from pathlib import Path
from typing import Final

from sbam_workers.evals.golden import (
    GOLDEN_DIR,
    GoldenTestgenItem,
    GoldenTestgenOverrides,
    GoldenTestgenSet,
    Task,
)

# (description, required inputs, state parameters, tables, success gate, no-op, escalation)
DOMAINS: Final[
    tuple[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], str, str, str], ...]
] = (
    (
        "login with a one-time code",
        ("mobile_number", "otp_payload", "device_fingerprint"),
        ("failed_login_attempts", "active_devices", "user_status"),
        ("investor_profiles", "auth_event_audit_handoff"),
        "otp_match == True AND policy_violation == False",
        "user_status is 'LOCKED'",
        "failed_login_attempts >= max_iterations",
    ),
    (
        "invoice matching",
        ("invoice_number", "supplier_code", "amount_cents"),
        ("mismatch_count", "supplier_status", "invoice_status"),
        ("invoices", "supplier_master"),
        "invoice_total == po_total AND supplier_active == True",
        "invoice_status is 'PAID'",
        "mismatch_count >= max_iterations",
    ),
    (
        "stock reservation",
        ("sku", "warehouse_code", "quantity"),
        ("reservation_attempts", "stock_on_hand", "order_status"),
        ("stock_levels", "reservation_log"),
        "reserved_quantity == requested_quantity",
        "requested_quantity == 0",
        "reservation_attempts >= max_iterations",
    ),
    (
        "ticket triage",
        ("ticket_id", "customer_tier"),
        ("assignment_attempts", "queue_load", "ticket_status"),
        ("tickets", "sla_policies"),
        "assigned_queue is not null AND sla_breach == False",
        "ticket_status is 'CLOSED'",
        "assignment_attempts >= max_iterations",
    ),
    (
        "leave request",
        ("employee_id", "start_date", "end_date", "leave_type"),
        ("validation_attempts", "balance_days", "request_status"),
        ("leave_balances", "leave_requests"),
        "balance_after >= 0 AND overlap == False",
        "request_status is 'APPROVED'",
        "validation_attempts >= max_iterations",
    ),
)
BOUNDS: Final = (1, 2, 3, 5)


def build_testgen_set() -> GoldenTestgenSet:
    items: list[GoldenTestgenItem] = []
    for name, inputs, state, tables, success, no_op, escalation in DOMAINS:
        for bound in BOUNDS:
            items.append(
                GoldenTestgenItem(
                    id=f"testgen-{len(items) + 1:02d}",
                    description=f"{name}, max_iterations {bound}",
                    synthetic=True,
                    overrides=GoldenTestgenOverrides(
                        max_iterations=bound,
                        required_inputs=list(inputs),
                        fresh_state_parameters=list(state),
                        tables=list(tables),
                        success_gate=success,
                        no_op_condition=no_op,
                        escalation_condition=escalation,
                    ),
                )
            )
    return GoldenTestgenSet(task=Task.TESTGEN, version=1, items=items)


def render_json() -> str:
    return build_testgen_set().model_dump_json(indent=2) + "\n"


if __name__ == "__main__":
    target = Path(GOLDEN_DIR) / "testgen.json"
    target.write_text(render_json(), encoding="utf-8", newline="\n")
    print(f"wrote {target}")
