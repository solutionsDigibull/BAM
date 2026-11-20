"""WG-39 (#926): the end-to-end chain scorer. Pure functions over JSON; no model, no network."""

import json
from pathlib import Path
from typing import Any

from sbam_workers.evals import chain
from sbam_workers.evals.chain import SYNTHETIC_KEY, norm, score

KEY: dict[str, Any] = json.loads(SYNTHETIC_KEY.read_text(encoding="utf-8"))
ALL_OUTPUTS = ["order_total", "unpriced_items", "complete_flag"]


def build(*names: str, outputs: list[str] | None = None) -> dict[str, Any]:
    return {
        "sub_skills": [
            {"order": i + 1, "name": n, "outputs": outputs or [] if i == 0 else []}
            for i, n in enumerate(names)
        ]
    }


STEPS = ("Check Order", "price_items", "TOTAL-ORDER", "approve_and_send")


def test_wg39_t40_a_build_that_matches_the_key_scores_one() -> None:
    result = score(build(*STEPS, outputs=ALL_OUTPUTS), KEY)
    assert result.total == 1.0 and not result.missing_steps and not result.extra_steps


def test_wg39_t41_names_are_compared_without_case_or_punctuation() -> None:
    assert norm("Check Order") == norm("check-order") == "check_order"


def test_wg39_t42_a_missing_step_and_a_missing_output_lower_the_score_and_are_named() -> None:
    partial = build("check_order", "total_order", "approve_and_send", outputs=["order_total"])
    result = score(partial, KEY)
    assert result.missing_steps == ("price_items",)
    assert set(result.missing_outputs) == {"unpriced_items", "complete_flag"}
    assert result.total < 1.0


def test_wg39_t43_the_right_steps_in_the_wrong_order_lose_order_but_not_steps() -> None:
    swapped = build(
        "price_items", "check_order", "total_order", "approve_and_send", outputs=ALL_OUTPUTS
    )
    result = score(swapped, KEY)
    assert result.steps == 1.0 and result.order == 0.75


def test_wg39_t44_an_invented_step_costs_precision() -> None:
    extra = build(*STEPS, "ask_the_moon", outputs=ALL_OUTPUTS)
    result = score(extra, KEY)
    assert result.extra_steps == ("ask_the_moon",) and result.precision == 0.8


def test_wg39_t45_an_empty_build_scores_below_one_without_dividing_by_zero() -> None:
    result = score({"sub_skills": []}, KEY)
    assert result.steps == 0.0 and result.precision == 0.0 and result.total < 0.5


def test_wg39_t46_a_private_key_is_read_from_its_path_and_its_path_is_not_printed(
    tmp_path: Path, capsys: Any
) -> None:
    private = {"steps": [{"order": 1, "name": "customer_step"}], "outputs": ["customer_field"]}
    key_path, build_path = tmp_path / "key.json", tmp_path / "build.json"
    key_path.write_text(json.dumps(private), encoding="utf-8")
    build_path.write_text(json.dumps(build("customer_step")), encoding="utf-8")
    code = chain.main(["--build", str(build_path), "--key", str(key_path), "--min", "0.5"])
    printed = capsys.readouterr().out
    assert code == 0 and "chain score" in printed and str(key_path) not in printed


def test_wg39_t47_an_unreadable_input_is_exit_2_and_a_low_score_is_exit_1(tmp_path: Path) -> None:
    assert chain.main(["--build", str(tmp_path / "nope.json")]) == 2
    low = tmp_path / "low.json"
    low.write_text(json.dumps(build("unrelated")), encoding="utf-8")
    assert chain.main(["--build", str(low)]) == 1
