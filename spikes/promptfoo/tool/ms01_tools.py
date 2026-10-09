"""Sample FastMCP tools for Spike E0.12 (#30), shaped like Sub-Skill MS01.1 (investor login).

Two tools, as ISATVON 1.4 requires (rule 4, decoupling):
  - auth_verify_otp  = the action tool      (T.action_tool:      "Auth_MCP")
  - rbac_check_policy = the fixed check tool (T.fixed_check_tool: "RBAC_MCP")

State lives in an in-memory fake store seeded per test scenario. Synthetic data only.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

mcp = FastMCP("ms01-1-login")

# user_id -> fake profile. The harness seeds this before each scenario (fresh state, TD §15.2).
USERS: dict[str, dict[str, Any]] = {}


def seed(users: dict[str, dict[str, Any]]) -> None:
    USERS.clear()
    USERS.update({k: dict(v) for k, v in users.items()})


@mcp.tool
def auth_verify_otp(user_id: str, otp: str, device_fingerprint: str) -> dict[str, Any]:
    """Action tool: compare the submitted OTP with the expected one and count failures."""
    user = USERS[user_id]
    otp_match = otp == user["expected_otp"]
    if not otp_match:
        user["failed_login_attempts"] += 1
    return {
        "otp_match": otp_match,
        "device_fingerprint": device_fingerprint,
        "failed_login_attempts": user["failed_login_attempts"],
    }


@mcp.tool
def rbac_check_policy(user_id: str, device_fingerprint: str) -> dict[str, Any]:
    """Fixed check tool: independent policy check (role + device limit); never trusts the action."""
    user = USERS[user_id]
    devices = set(user["known_devices"]) | {device_fingerprint}
    policy_violation = len(devices) > user["max_device_limit"]
    return {
        "policy_violation": policy_violation,
        "user_role": user["role"],
        "access_scope": "none" if policy_violation else user["scope"],
    }
