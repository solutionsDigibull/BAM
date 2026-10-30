"""#536: `scripts/unblocked_issues.py` must see a 'Blocked by' line however the issue writes it."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "unblocked_issues", REPO / "scripts/unblocked_issues.py"
)
assert _spec and _spec.loader
script: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


@pytest.mark.parametrize(
    "line",
    [
        "Blocked by: #119",
        "blocked by: #119",
        "**Blocked by:** #119",
        "**Blocked by**: #119",
        "- Blocked by: #119 (PR for the gate). Lane D2 owns the Compose block.",  # #522's shape
        "* Blocked by: #119",
        "+ Blocked by: #119",
        "> Blocked by: #119",
        "  - **Blocked by:** #119",
        "- - Blocked by: #119",
        "Depends on: #119",
        "- depends on: #119",
    ],
)
def test_536_every_way_of_writing_the_line_counts(line: str) -> None:
    assert script.body_deps(f"Some text\n\n{line}\n") == {119}


@pytest.mark.parametrize(
    "line",
    [
        "It was blocked by #119 for a while",  # a sentence, not the dependency line
        "Not blocked by: #119",
        "See Blocked by: #119 in the other issue",
        "- Related to #119 (blocked by nothing)",
        "Blocked by #119",  # no colon: not the convention
        "",
    ],
)
def test_536_a_mention_inside_a_sentence_does_not(line: str) -> None:
    assert script.body_deps(line) == set()


def test_536_all_issue_numbers_on_the_line_are_read_and_other_lines_ignored() -> None:
    body = "Intro #5\n- Blocked by: #12 (`E1.6`), #15 and #16\nNotes mention #99\n"
    assert script.body_deps(body) == {12, 15, 16}


def test_536_522s_real_body_blocks_it() -> None:
    body = "### Notes\n- Blocked by: #119 (PR for the gate). Lane D2 (compose) owns the block.\n"
    assert script.body_deps(body) == {119}
