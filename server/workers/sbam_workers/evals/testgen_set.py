"""The test-generation golden set (E15.5): five invented Sub-Skills at four iteration bounds.

Each domain is a complete, self-consistent Sub-Skill (`DomainProfile`): its prose, inputs, tables,
conditions, outputs and handoff all describe the same business process, so the model is never
shown two stories at once. The first domain is the base sample's own (a login with a one-time
code); the other four are invented (invoice matching, stock reservation, ticket triage, leave
requests). A bound of 1 takes `PASS_AFTER_RETRY` out of the kinds the model may use, so the set also
checks that it keeps to the allowed kinds. All names and figures are made up.

`server/evals/golden/testgen.json` is this module's output, and a test checks the two agree:

    uv run python -m sbam_workers.evals.testgen_set
"""

# ruff: noqa: E501 - prose data: one string per line reads better than strings wrapped over lines

from pathlib import Path
from typing import Any, Final

from sbam_workers.evals.golden import (
    GOLDEN_DIR,
    DomainProfile,
    GoldenTestgenItem,
    GoldenTestgenSet,
    Task,
)

COMPANY: Final = "(Alpha Dynamics Ltd)"


def _profile(**fields: Any) -> DomainProfile:
    return DomainProfile.model_validate(fields)


DOMAINS: Final[dict[str, DomainProfile]] = {
    "login with a one-time code": _profile(
        macro_category="MS01_Investor_Registration_Access",
        skill_id="MS01.1_Investor_Auth_Manager",
        skill_name="Investor OTP & Access Verifier",
        trigger="Investor submits a login request with an OTP",
        action="Validate OTP and enforce RBAC device limits",
        check="Verify OTP matches secure vault and device count is under limit",
        actor_level="External investor",
        actor_persona="Retail investor signing in to the portal",
        actor_authority="May access only their own profile and holdings",
        goal="Authenticate the investor or lock the profile",
        method="Bounded OTP verification with an independent RBAC policy check",
        background=f"Investor portal for a fictional fund {COMPANY}",
        topic="Investor registration and access",
        history="Device limits were introduced after credential-sharing incidents",
        required_inputs=["mobile_number", "otp_payload", "device_fingerprint"],
        fresh_state_parameters=["failed_login_attempts", "active_devices", "user_status"],
        action_step="Compare provided OTP against Auth_MCP memory and calculate device fingerprint limits.",
        acceptance_check="Programmatically evaluate if otp_match == True AND active_devices <= max_device_limit.",
        action_tool="Auth_MCP (Session & OTP Evaluator)",
        fixed_check_tool="RBAC_MCP (Policy & Limit Validator)",
        libraries=["fastmcp", "psycopg", "pyjwt"],
        success_gate="otp_match == True AND policy_violation == False",
        no_op_condition="user_status is 'LOCKED'",
        escalation_condition="failed_login_attempts >= max_iterations",
        tables=["investor_profiles", "auth_event_audit_handoff"],
        parameter_name="max_device_limit",
        parameter_value=3,
        secrets=["AUTH_VAULT_KEY", "JWT_SIGNING_KEY"],
        outputs={
            "auth_status": ["AUTHENTICATED", "REJECTED", "LOCKED"],
            "user_role": "string",
            "jwt_token": "string",
            "otp_match": "boolean",
            "policy_violation": "boolean",
        },
        handoff_fields=["device_fingerprint", "otp_match", "access_scope"],
        handoff_description="Log iteration_pass, device fingerprint used, OTP boolean match evidence and final access scope.",
        success_criteria="OTP matches and no RBAC policy violation",
        loop_update="Status: Verifying -> Looping [Pass 2/{n}]: Incorrect OTP -> Authenticated",
        escalated_update="Status: Escalated - Account Locked",
        db_update="Update investor_profiles with failed attempts or new active session state",
        alert="Notify platform ops on account lockout (Escalation)",
        next_skill="MS01.2_Investor_KYC_Verifier",
    ),
    "invoice matching": _profile(
        macro_category="MS07_Accounts_Payable",
        skill_id="MS07.2_Invoice_Matcher",
        skill_name="Supplier Invoice Matcher",
        trigger="A supplier invoice arrives with its purchase order reference",
        action="Match the invoice lines and total to the purchase order",
        check="Verify the invoice total equals the purchase order total and the supplier is active",
        actor_level="Accounts payable clerk",
        actor_persona="Clerk matching supplier invoices before payment",
        actor_authority="May approve matches up to the invoice limit and flag the rest",
        goal="Match the invoice to its order or escalate the mismatch",
        method="Bounded three-way matching with an independent supplier status check",
        background=f"Accounts payable team of a fictional manufacturer {COMPANY}",
        topic="Invoice matching before payment",
        history="Duplicate payments led to a mandatory match before release",
        required_inputs=["invoice_number", "supplier_code", "amount_cents"],
        fresh_state_parameters=["mismatch_count", "supplier_status", "invoice_status"],
        action_step="Compare the invoice lines and total with the purchase order held in Match_MCP.",
        acceptance_check="Programmatically evaluate if invoice_total == po_total AND supplier_active == True.",
        action_tool="Match_MCP (Invoice & Order Comparer)",
        fixed_check_tool="Supplier_MCP (Status & Limit Validator)",
        libraries=["fastmcp", "psycopg"],
        success_gate="invoice_total == po_total AND supplier_active == True",
        no_op_condition="invoice_status is 'PAID'",
        escalation_condition="mismatch_count >= max_iterations",
        tables=["invoices", "invoice_match_handoff"],
        parameter_name="max_variance_cents",
        parameter_value=500,
        secrets=["LEDGER_API_KEY"],
        outputs={
            "match_status": ["MATCHED", "MISMATCHED", "ON_HOLD"],
            "approver_role": "string",
            "invoice_total_matches": "boolean",
            "supplier_active": "boolean",
        },
        handoff_fields=["invoice_number", "invoice_total_matches", "approval_scope"],
        handoff_description="Log iteration_pass, the invoice number checked, the total match evidence and the final approval scope.",
        success_criteria="The invoice total matches the order and the supplier is active",
        loop_update="Status: Matching -> Looping [Pass 2/{n}]: Total differs -> Matched",
        escalated_update="Status: Escalated - Invoice On Hold",
        db_update="Update invoices with the mismatch count or the matched status",
        alert="Notify accounts payable on a held invoice (Escalation)",
        next_skill="MS07.3_Payment_Scheduler",
    ),
    "stock reservation": _profile(
        macro_category="MS04_Order_Fulfilment",
        skill_id="MS04.1_Stock_Reserver",
        skill_name="Warehouse Stock Reserver",
        trigger="A sales order asks for a quantity of a part",
        action="Reserve the requested quantity from a warehouse",
        check="Verify the reserved quantity equals the requested quantity",
        actor_level="Warehouse planner",
        actor_persona="Planner reserving stock for confirmed orders",
        actor_authority="May reserve stock only in the warehouses of their site",
        goal="Reserve the requested stock or escalate the shortage",
        method="Bounded reservation with an independent stock level check",
        background=f"Fulfilment centre of a fictional distributor {COMPANY}",
        topic="Stock reservation for orders",
        history="Overselling caused late deliveries, so reservations became mandatory",
        required_inputs=["sku", "warehouse_code", "quantity"],
        fresh_state_parameters=["reservation_attempts", "stock_on_hand", "order_status"],
        action_step="Reserve the requested quantity of the part in the warehouse through Stock_MCP.",
        acceptance_check="Programmatically evaluate if reserved_quantity == requested_quantity.",
        action_tool="Stock_MCP (Reservation Writer)",
        fixed_check_tool="Level_MCP (Stock Level Validator)",
        libraries=["fastmcp", "psycopg"],
        success_gate="reserved_quantity == requested_quantity",
        no_op_condition="requested_quantity == 0",
        escalation_condition="reservation_attempts >= max_iterations",
        tables=["stock_levels", "reservation_handoff"],
        parameter_name="max_reservation_units",
        parameter_value=1000,
        secrets=["WAREHOUSE_API_KEY"],
        outputs={
            "reservation_status": ["RESERVED", "PARTIAL", "REJECTED"],
            "warehouse_role": "string",
            "reserved_in_full": "boolean",
            "stock_available": "boolean",
        },
        handoff_fields=["sku", "reserved_in_full", "reservation_scope"],
        handoff_description="Log iteration_pass, the part reserved, the full reservation evidence and the final reservation scope.",
        success_criteria="The requested quantity is reserved in full",
        loop_update="Status: Reserving -> Looping [Pass 2/{n}]: Short of stock -> Reserved",
        escalated_update="Status: Escalated - Shortage",
        db_update="Update stock_levels with the reserved quantity or the shortage",
        alert="Notify the warehouse lead on a shortage (Escalation)",
        next_skill="MS04.2_Pick_List_Builder",
    ),
    "ticket triage": _profile(
        macro_category="MS09_Customer_Support",
        skill_id="MS09.1_Ticket_Triager",
        skill_name="Support Ticket Triager",
        trigger="A customer opens a support ticket",
        action="Assign the ticket to a queue that meets its response time",
        check="Verify the ticket has a queue and no response time is breached",
        actor_level="Support agent",
        actor_persona="Agent triaging incoming tickets",
        actor_authority="May assign tickets to the queues of their team",
        goal="Assign the ticket to a queue or escalate the breach",
        method="Bounded assignment with an independent response time check",
        background=f"Support desk of a fictional software vendor {COMPANY}",
        topic="Support ticket triage",
        history="Missed response times led to automatic queue assignment",
        required_inputs=["ticket_id", "customer_tier"],
        fresh_state_parameters=["assignment_attempts", "queue_load", "ticket_status"],
        action_step="Pick the least loaded queue for the customer tier and assign the ticket through Queue_MCP.",
        acceptance_check="Programmatically evaluate if assigned == True AND sla_breach == False.",
        action_tool="Queue_MCP (Ticket Assigner)",
        fixed_check_tool="Sla_MCP (Response Time Validator)",
        libraries=["fastmcp", "psycopg"],
        success_gate="assigned == True AND sla_breach == False",
        no_op_condition="ticket_status is 'CLOSED'",
        escalation_condition="assignment_attempts >= max_iterations",
        tables=["tickets", "triage_handoff"],
        parameter_name="max_queue_load",
        parameter_value=25,
        secrets=["HELPDESK_API_KEY"],
        outputs={
            "triage_status": ["ASSIGNED", "QUEUED", "ESCALATED"],
            "queue_name": "string",
            "assigned": "boolean",
            "sla_breach": "boolean",
        },
        handoff_fields=["ticket_id", "assigned", "queue_scope"],
        handoff_description="Log iteration_pass, the ticket triaged, the assignment evidence and the final queue scope.",
        success_criteria="The ticket has a queue and no response time is breached",
        loop_update="Status: Assigning -> Looping [Pass 2/{n}]: Queue full -> Assigned",
        escalated_update="Status: Escalated - Response Time Breached",
        db_update="Update tickets with the assigned queue or the failed attempt",
        alert="Notify the support lead on a breach (Escalation)",
        next_skill="MS09.2_Customer_Notifier",
    ),
    "leave request": _profile(
        macro_category="MS12_People_Operations",
        skill_id="MS12.1_Leave_Validator",
        skill_name="Leave Request Validator",
        trigger="An employee submits a leave request",
        action="Check the requested dates against the leave balance and other requests",
        check="Verify the balance stays at or above zero and the dates do not overlap",
        actor_level="HR coordinator",
        actor_persona="Coordinator validating leave requests",
        actor_authority="May approve requests inside the policy and refer the rest",
        goal="Validate the request or escalate the conflict",
        method="Bounded validation with an independent overlap check",
        background=f"People operations of a fictional company {COMPANY}",
        topic="Leave request validation",
        history="Overlapping absences left teams short, so requests are checked first",
        required_inputs=["employee_id", "start_date", "end_date", "leave_type"],
        fresh_state_parameters=["validation_attempts", "balance_days", "request_status"],
        action_step="Calculate the days requested and the balance after approval through Leave_MCP.",
        acceptance_check="Programmatically evaluate if balance_after >= 0 AND overlap == False.",
        action_tool="Leave_MCP (Balance Calculator)",
        fixed_check_tool="Calendar_MCP (Overlap Validator)",
        libraries=["fastmcp", "psycopg"],
        success_gate="balance_after >= 0 AND overlap == False",
        no_op_condition="request_status is 'APPROVED'",
        escalation_condition="validation_attempts >= max_iterations",
        tables=["leave_balances", "leave_handoff"],
        parameter_name="max_consecutive_days",
        parameter_value=14,
        secrets=["HR_API_KEY"],
        outputs={
            "leave_status": ["APPROVED", "REFERRED", "REJECTED"],
            "approver_role": "string",
            "balance_ok": "boolean",
            "overlap": "boolean",
        },
        handoff_fields=["employee_id", "balance_ok", "approval_scope"],
        handoff_description="Log iteration_pass, the employee checked, the balance evidence and the final approval scope.",
        success_criteria="The balance stays at or above zero and the dates do not overlap",
        loop_update="Status: Validating -> Looping [Pass 2/{n}]: Dates overlap -> Approved",
        escalated_update="Status: Escalated - Referred To HR",
        db_update="Update leave_balances with the days booked or the failed attempt",
        alert="Notify the HR lead on a referral (Escalation)",
        next_skill="MS12.2_Payroll_Notifier",
    ),
}
BOUNDS: Final = (1, 2, 3, 5)


def build_testgen_set() -> GoldenTestgenSet:
    items: list[GoldenTestgenItem] = []
    for name in DOMAINS:
        for bound in BOUNDS:
            items.append(
                GoldenTestgenItem(
                    id=f"testgen-{len(items) + 1:02d}",
                    description=f"{name}, max_iterations {bound}",
                    synthetic=True,
                    domain=name,
                    max_iterations=bound,
                )
            )
    return GoldenTestgenSet(task=Task.TESTGEN, version=3, domains=DOMAINS, items=items)


def render_json() -> str:
    return build_testgen_set().model_dump_json(indent=2) + "\n"


if __name__ == "__main__":
    target = Path(GOLDEN_DIR) / "testgen.json"
    target.write_text(render_json(), encoding="utf-8", newline="\n")
    print(f"wrote {target}")
