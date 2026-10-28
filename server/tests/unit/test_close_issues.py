"""#244: the PR-body keyword parser and the close-issues workflow that runs on merges into dev."""

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "close-issues.yml"
_spec = importlib.util.spec_from_file_location("closing_issues", REPO / "scripts/closing_issues.py")
assert _spec and _spec.loader
parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parser)


@pytest.mark.parametrize(
    ("body", "issues"),
    [
        ("Closes #244", [244]),
        ("closes #1\nFixes: #2 and RESOLVED #3, fixed #4. resolve #5", [1, 2, 3, 4, 5]),
        ("Close #7, close #7 again", [7]),  # once each
        ("Fixes #12.", [12]),
        ("Part of #141 (part 1 of 2)", []),
        ("Closes solutionsDigibull/BAM#9", []),  # another repository's reference
        ("Use `Closes #8` in the body", []),  # inline code
        ("```\nCloses #10\n```\nCloses #11", [11]),  # fenced code
        ("Discloses #13 and prefixes #14", []),  # not the keyword itself
        ("See #15; this closes nothing", []),
        ("", []),
    ],
)
def test_244_closing_keywords(body: str, issues: list[int]) -> None:
    assert parser.closing_issues(body) == issues


def test_244_cli_prints_one_number_per_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PR_BODY", "Closes #3\nfixes #4")
    parser.main()
    assert capsys.readouterr().out.split() == ["3", "4"]


def workflow() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads `on:` as True
    return data


def test_365_runs_on_pushes_to_dev_only() -> None:
    # not pull_request_target: GitHub reads that from the default branch (main) only (#365)
    wf = workflow()
    assert wf["on"] == {"push": {"branches": ["dev"]}}
    assert wf["permissions"] == {"contents": "read", "issues": "write", "pull-requests": "read"}
    find, close = workflow()["jobs"]["close"]["steps"][1:]
    assert "merged_at != null" in find["run"] and '.base.ref == "dev"' in find["run"]
    assert close["if"] == "steps.pr.outputs.number != ''"  # a direct push closes nothing


def test_244_never_runs_pr_code_and_never_interpolates_the_body() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "head.sha" not in text and "head.ref" not in text  # checkout = dev after the merge
    steps = workflow()["jobs"]["close"]["steps"]
    for step in steps:  # values reach the shell through env only
        assert "${{" not in step.get("run", "")
    assert "PR_BODY" not in steps[-1]["env"]  # read with gh at run time, never interpolated
    assert 'PR_BODY="$(gh pr view "$PR"' in steps[-1]["run"]
    for ref in re.findall(r"uses: (\S+)", text):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
