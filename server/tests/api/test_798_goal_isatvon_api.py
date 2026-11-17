"""#798: under the FDE's goal the workbench shows a fixed ISATVON template, read-only and display
only. The backend serves it with the goal (`GET` / `PUT /projects/{id}/goal`): seven sections
I S A T V O N, the goal exactly as typed in I, nothing while no goal is set."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sbam_contracts.projects import GOAL_ISATVON_TEMPLATE, goal_isatvon
from test_720_project_goal_api import GOAL, path, put
from test_project_workspace import Harness


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_798_t01_no_goal_no_template(h: Harness) -> None:
    assert h.call("GET", path(h), h.fde()).json()["isatvon"] == []
    assert goal_isatvon(None) == [] and goal_isatvon("") == []


def test_798_t02_the_template_comes_with_the_goal_seven_sections_in_order(h: Harness) -> None:
    res = put(h, {"goal": GOAL})
    assert res.status_code == 200, res.text
    sections = res.json()["isatvon"]
    assert [s["key"] for s in sections] == ["I", "S", "A", "T", "V", "O", "N"]
    assert [s["label"] for s in sections] == [
        "Instructions",
        "Source",
        "Automation",
        "Tech Stack (T_tech_stack)",
        "Variables",
        "Outcome",
        "Notification",
    ]
    assert h.call("GET", path(h), h.fde()).json()["isatvon"] == sections


def test_798_t03_instructions_carry_the_goal_exactly_as_typed(h: Harness) -> None:
    [instructions, *_] = put(h, {"goal": GOAL}).json()["isatvon"]
    assert instructions["text"] == (
        f"You are an FDE Agent operating under ISATVON constraints.\nGoal: {GOAL}"
    )


def test_798_t04_the_other_sections_are_the_fixed_template(h: Harness) -> None:
    sections = {s["key"]: s["text"] for s in put(h, {"goal": GOAL}).json()["isatvon"]}
    assert "Missing Data Guard" in sections["S"] and "never assume" in sections["S"]
    assert "success_gate" in sections["A"] and "human approval" in sections["A"]
    assert (
        "FastMCP tool bindings" in sections["T"] and "nothing else may be called" in sections["T"]
    )
    assert "budget_limit (USD)" in sections["V"] and "timeout_seconds" in sections["V"]
    assert "PostgreSQL JSONB" in sections["O"] and "validate before handoff" in sections["O"]
    assert "budget exceeded" in sections["N"] and "last valid state" in sections["N"]
    assert all(GOAL not in text for key, text in sections.items() if key != "I")


def test_798_t05_a_goal_with_braces_is_shown_as_typed() -> None:
    odd = "Automate {quoting} for {who} in India"
    [instructions] = [s for s in goal_isatvon(odd) if s.key == "I"]
    assert instructions.text.endswith(f"Goal: {odd}")
    assert len(GOAL_ISATVON_TEMPLATE) == 7
