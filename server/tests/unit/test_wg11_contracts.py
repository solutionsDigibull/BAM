"""#759: the reviewer's and the repo scout's answer contracts."""

import pytest
from pydantic import ValidationError
from sbam_contracts.build import BuildStage
from sbam_contracts.build_dashboard import GateName
from sbam_contracts.repo_catalog import RepoScoutAnswer
from sbam_contracts.review import ReviewAnswer


def finding(severity: str = "HIGH") -> dict[str, object]:
    return {
        "category": "WRONG_OUTPUT",
        "severity": severity,
        "message": "the check tool never returns `success`",
        "file": "check_tool.py",
        "line": 12,
    }


def test_wg11_review_is_a_gate_and_a_stage() -> None:
    assert GateName("REVIEW") is GateName.REVIEW
    assert BuildStage("review_code") is BuildStage.REVIEW


def test_wg11_a_fail_needs_a_high_finding_and_a_pass_may_not_have_one() -> None:
    assert ReviewAnswer.model_validate({"verdict": "FAIL", "findings": [finding()]})
    assert ReviewAnswer.model_validate({"verdict": "PASS", "findings": [finding("LOW")]})
    assert ReviewAnswer.model_validate({"verdict": "PASS"}).findings == []
    with pytest.raises(ValidationError):
        ReviewAnswer.model_validate({"verdict": "FAIL", "findings": []})
    with pytest.raises(ValidationError):
        ReviewAnswer.model_validate({"verdict": "FAIL", "findings": [finding("MEDIUM")]})
    with pytest.raises(ValidationError):
        ReviewAnswer.model_validate({"verdict": "PASS", "findings": [finding()]})


def test_wg11_a_review_answer_refuses_extra_fields_and_too_many_findings() -> None:
    with pytest.raises(ValidationError):
        ReviewAnswer.model_validate({"verdict": "PASS", "note": "x"})
    with pytest.raises(ValidationError):
        ReviewAnswer.model_validate({"verdict": "FAIL", "findings": [finding()] * 21})


def test_wg11_a_scout_answer_scores_each_candidate_once_within_zero_and_one() -> None:
    ok = {"scores": [{"candidate_id": "a", "score": 0.8, "reasons": ["same stack"]}]}
    assert RepoScoutAnswer.model_validate(ok).scores[0].score == 0.8
    with pytest.raises(ValidationError):
        RepoScoutAnswer.model_validate({"scores": [{"candidate_id": "a", "score": 1.2}]})
    with pytest.raises(ValidationError):
        RepoScoutAnswer.model_validate(
            {"scores": [{"candidate_id": "a", "score": 0.1}, {"candidate_id": "a", "score": 0.2}]}
        )
