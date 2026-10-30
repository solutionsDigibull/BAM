"""E15.5: the golden sets for the LLM tasks are sound (no model is called here)."""

import io
import json
from pathlib import Path

import pytest
import sbam_workers.llm as llm_package
from PIL import Image
from pydantic import ValidationError
from sbam_workers.evals.golden import (
    GOLDEN_DIR,
    MIN_ITEMS,
    OcrItem,
    RenderSpec,
    Task,
    check_golden,
    load_ocr,
    load_tasks,
)
from sbam_workers.evals.ocr_set import build_ocr_set, render_json
from sbam_workers.evals.render import PAGE, render_page


def prompt_ids() -> frozenset[str]:
    manifest = Path(llm_package.__file__).parent / "prompts" / "manifest.json"
    return frozenset(key.split("/")[0] for key in json.loads(manifest.read_text(encoding="utf-8")))


def copy_dir(tmp_path: Path) -> Path:
    for name in (
        "tasks.json",
        "ocr.json",
        "testgen.json",
        "testgen-base.json",
        "correction.json",
        "correction-base.json",
        "codegen.json",
    ):
        (tmp_path / name).write_text(
            (GOLDEN_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return tmp_path


def test_e15_5_the_committed_golden_sets_are_sound() -> None:
    assert check_golden(prompt_ids=prompt_ids()) == []


def test_e15_5_every_llm_task_is_listed_once_with_real_prompts() -> None:
    tasks = load_tasks()
    assert sorted(t.task for t in tasks) == sorted(Task)
    known = prompt_ids()
    assert all(p in known for t in tasks for p in t.prompt_ids)
    assert {t.task: t.alias for t in tasks} == {
        Task.OCR: "sbam-ocr",
        Task.SOW: "sbam-sow",
        Task.ISATVON: "sbam-isatvon",
        Task.CODEGEN: "sbam-codegen",
        Task.TESTGEN: "sbam-testgen",
        Task.CORRECTION: "sbam-corrector",
    }


def test_e15_5_a_task_is_ready_only_with_at_least_the_minimum_items() -> None:
    ready = [t for t in load_tasks() if t.status == "ready"]
    assert sorted(t.task for t in ready) == sorted(
        [Task.OCR, Task.TESTGEN, Task.CORRECTION, Task.CODEGEN]
    )
    assert len(load_ocr().items) >= MIN_ITEMS


def test_e15_5_the_committed_ocr_set_is_what_the_generator_writes() -> None:
    assert (GOLDEN_DIR / "ocr.json").read_text(encoding="utf-8") == render_json()


def test_e15_5_the_ocr_items_are_synthetic_and_each_has_identifiers_that_are_printed() -> None:
    items = load_ocr().items
    assert len({i.id for i in items}) == len(items) == 20
    for item in items:
        assert item.synthetic is True
        assert all(ident in "\n".join(item.render.lines) for ident in item.identifiers)


def test_e15_5_a_set_that_is_not_marked_synthetic_is_refused() -> None:
    item = build_ocr_set().items[0].model_dump(mode="json")
    with pytest.raises(ValidationError):
        OcrItem.model_validate({**item, "synthetic": False})


def test_e15_5_the_same_spec_draws_the_same_page() -> None:
    spec = build_ocr_set().items[2].render  # the simulated phone photo: every degradation on
    assert render_page(spec) == render_page(spec)


def test_e15_5_the_conditions_change_the_image() -> None:
    clean, jpeg, phone, half = (i.render for i in build_ocr_set().items[:4])
    sizes = {}
    for name, spec in {"clean": clean, "jpeg": jpeg, "phone": phone, "half": half}.items():
        data, media_type = render_page(spec)
        sizes[name] = (Image.open(io.BytesIO(data)).size, media_type)
    assert sizes["clean"] == (PAGE, "image/png")
    assert sizes["jpeg"] == (PAGE, "image/jpeg")
    assert sizes["half"] == ((round(PAGE[0] / 2), round(PAGE[1] / 2)), "image/png")
    assert sizes["phone"][0][0] > PAGE[0]  # rotated with expand: the canvas grew
    assert render_page(clean)[0] != render_page(phone)[0]


def test_e15_5_a_render_spec_with_no_text_or_a_wild_value_is_refused() -> None:
    with pytest.raises(ValidationError):
        RenderSpec.model_validate({"lines": []})
    with pytest.raises(ValidationError):
        RenderSpec.model_validate({"lines": ["x"], "rotate": 45})


def edit(directory: Path, change: dict[str, object]) -> None:
    data = json.loads((directory / "ocr.json").read_text(encoding="utf-8"))
    data.update(change)
    (directory / "ocr.json").write_text(json.dumps(data), encoding="utf-8")


def test_e15_5_too_few_items_is_a_problem(tmp_path: Path) -> None:
    directory = copy_dir(tmp_path)
    edit(directory, {"items": load_ocr().model_dump(mode="json")["items"][:19]})
    assert any("19 items, at least 20 needed" in p for p in check_golden(directory))


def test_e15_5_a_repeated_id_is_a_problem(tmp_path: Path) -> None:
    directory = copy_dir(tmp_path)
    items = load_ocr().model_dump(mode="json")["items"]
    items[1]["id"] = items[0]["id"]
    edit(directory, {"items": items})
    assert any("ocr-01 is used more than once" in p for p in check_golden(directory))


def test_e15_5_an_identifier_that_is_not_on_the_page_is_a_problem(tmp_path: Path) -> None:
    directory = copy_dir(tmp_path)
    items = load_ocr().model_dump(mode="json")["items"]
    items[0]["identifiers"].append("NOT-ON-THE-PAGE")
    edit(directory, {"items": items})
    assert any("'NOT-ON-THE-PAGE' is not printed" in p for p in check_golden(directory))


def test_e15_5_a_ready_task_without_a_loader_or_a_todo_task_with_a_set_is_a_problem(
    tmp_path: Path,
) -> None:
    directory = copy_dir(tmp_path)
    tasks = json.loads((directory / "tasks.json").read_text(encoding="utf-8"))
    for row in tasks:
        if row["task"] == "sow":
            row["status"] = "ready"  # claims a set that does not exist
    (directory / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    assert any("sow: listed as ready" in p for p in check_golden(directory))
    for row in tasks:
        if row["task"] == "sow":
            row["status"] = "todo"
    (directory / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    (directory / "sow.json").write_text("{}", encoding="utf-8")
    assert any("sow: has a set but is listed as todo" in p for p in check_golden(directory))


def test_e15_5_an_unknown_prompt_or_a_missing_task_is_a_problem(tmp_path: Path) -> None:
    directory = copy_dir(tmp_path)
    tasks = json.loads((directory / "tasks.json").read_text(encoding="utf-8"))
    tasks[0]["prompt_ids"] = ["no-such-prompt"]
    del tasks[-1]  # correction
    (directory / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    problems = check_golden(directory, prompt_ids())
    assert any("unknown prompt 'no-such-prompt'" in p for p in problems)
    assert any("correction is missing" in p for p in problems)


def test_e15_5_a_missing_task_file_is_reported_not_raised(tmp_path: Path) -> None:
    assert check_golden(tmp_path) == ["tasks.json: FileNotFoundError"]
