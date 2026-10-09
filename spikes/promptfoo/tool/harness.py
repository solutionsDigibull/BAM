"""Mini loop harness for Spike E0.12 (#30).

Runs the MS01.1 tools for 3 scenarios and saves what Promptfoo checks.

A cut-down version of the TD §15.2 harness (no CubeSandbox, no Postgres):
  if no_op_condition: record NO_OP, stop
  for pass in 1..max_iterations: action tool -> check tool -> append handoff row -> ui_update
      success_gate        -> SUCCESS, stop
      escalation_condition -> ESCALATED, stop

For each case it writes out/outputs/<CASE>.json = output + harness trace + handoff rows.

Break modes (to produce the failing cases), via the SPIKE_BREAK env var:
  missing_handoff  PASS_FIRST output has no state_handoff_id   (E0.12-T02)
  plain_text       PASS_FIRST prints plain text instead of JSON (E0.12-T03)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import ms01_tools
from fastmcp import Client

MAX_ITERATIONS = 3  # V.max_iterations of MS01.1
HANDOFF_NS = uuid.UUID("6f1d3c2a-0e0b-4c55-9d1e-30e012000000")  # fixed, so reruns give the same ids

# Fresh state per scenario + the OTP the "investor" types on each pass (scripted, synthetic).
SCENARIOS: dict[str, dict[str, Any]] = {
    "PASS_FIRST": {
        "user": {"user_status": "ACTIVE", "expected_otp": "424242"},
        "otps": ["424242"],
    },
    "ESCALATION": {  # never meets success_gate -> stops exactly at max_iterations
        "user": {"user_status": "ACTIVE", "expected_otp": "424242"},
        "otps": ["000000", "111111", "222222"],
    },
    "NO_OP": {  # no_op_condition: user_status is 'LOCKED' -> no tool is called
        "user": {"user_status": "LOCKED", "expected_otp": "424242"},
        "otps": [],
    },
}
BASE_USER = {
    "failed_login_attempts": 0,
    "role": "investor",
    "scope": "portfolio:read",
    "known_devices": ["dev-laptop-01"],
    "max_device_limit": 3,
}


async def run_case(case: str, spec: dict[str, Any]) -> dict[str, Any]:
    user_id = f"spike-{case.lower()}"
    ms01_tools.seed({user_id: {**BASE_USER, **spec["user"]}})
    trace: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    ui = ["Status: Verifying"]
    out: dict[str, Any] = {"otp_match": False, "policy_violation": False, "user_role": "investor"}
    started = time.perf_counter()

    if ms01_tools.USERS[user_id]["user_status"] == "LOCKED":
        terminal, auth_status = "NO_OP", "LOCKED"
        ui.append("No-op: account already locked")
    else:
        terminal, auth_status = "ESCALATED", "LOCKED"  # bound reached unless a gate stops us first
        async with Client(ms01_tools.mcp) as mcp:
            for p in range(1, MAX_ITERATIONS + 1):
                otp = spec["otps"][p - 1]
                act = (
                    await mcp.call_tool(
                        "auth_verify_otp",
                        {"user_id": user_id, "otp": otp, "device_fingerprint": "dev-laptop-01"},
                    )
                ).data
                chk = (
                    await mcp.call_tool(
                        "rbac_check_policy",
                        {"user_id": user_id, "device_fingerprint": "dev-laptop-01"},
                    )
                ).data
                trace += [
                    {"pass": p, "tool": "auth_verify_otp", "result": act},
                    {"pass": p, "tool": "rbac_check_policy", "result": chk},
                ]
                out = {
                    "otp_match": act["otp_match"],
                    "policy_violation": chk["policy_violation"],
                    "user_role": chk["user_role"],
                }
                rows.append(
                    {
                        "iteration_pass": p,
                        "device_fingerprint": act["device_fingerprint"],
                        "otp_match": act["otp_match"],
                        "access_scope": chk["access_scope"],
                    }
                )
                ui.append(f"Looping [Pass {p}/{MAX_ITERATIONS}]")
                if act["otp_match"] and not chk["policy_violation"]:  # success_gate
                    terminal, auth_status = "SUCCESS", "AUTHENTICATED"
                    ui.append("Authenticated")
                    break
                if act["failed_login_attempts"] >= MAX_ITERATIONS:  # escalation_condition
                    break
        if terminal == "ESCALATED":
            ui.append("Status: Escalated - Account Locked")

    return {
        "case": case,
        "synthetic": True,
        "terminal_state": terminal,
        "iterations": len(rows),
        "max_iterations": MAX_ITERATIONS,
        "output": {
            **out,
            "auth_status": auth_status,
            "jwt_token": "synthetic-token" if auth_status == "AUTHENTICATED" else "",
            "state_handoff_id": str(uuid.uuid5(HANDOFF_NS, f"{case}:{len(rows)}")),
        },
        "handoff_rows": rows,
        "ui_updates": ui,
        "trace": trace,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }


async def main() -> int:
    out_dir = (
        Path(os.environ.get("OUT_DIR", Path(__file__).resolve().parent.parent / "out")) / "outputs"
    )
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    brk = os.environ.get("SPIKE_BREAK", "")
    for case, spec in SCENARIOS.items():
        result = await run_case(case, spec)
        if case == "PASS_FIRST" and brk == "missing_handoff":
            del result["output"]["state_handoff_id"]
        text = json.dumps(result, indent=2) + "\n"
        if case == "PASS_FIRST" and brk == "plain_text":
            text = "Authenticated OK\n"
        (out_dir / f"{case}.json").write_text(text, encoding="utf-8")
        print(
            f"{case:<11} {result['terminal_state']:<9} "
            f"iterations={result['iterations']}/{MAX_ITERATIONS}"
        )
    print(f"wrote {out_dir}" + (f" (SPIKE_BREAK={brk})" if brk else ""))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
