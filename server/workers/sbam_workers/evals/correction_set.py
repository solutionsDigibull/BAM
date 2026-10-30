"""The correction golden set (E15.5): twenty flawed code bodies for `sbam-corrector` to repair.

Every item starts from the MS01.1 sample's recorded, working bodies and puts one flaw in them, of a
kind the build's own pre-check (`bodies.precheck`) refuses: a manual loop, a forbidden import, a
forbidden call, SQL built by hand, or an import outside the allowlist. The findings the model is
shown are not written by hand: they are what `precheck` reports for the flawed bodies, so they are
the ones a real build would hand to the corrector. Building the set checks that every flaw raises
the code the item says, and that the unflawed bodies are clean.

`correction-base.json` holds the Sub-Skill, manifest and bodies the items start from (a copy of the
repo's MS01.1 fixtures; a test fails if they drift apart), and `correction.json` is this module's
output:

    uv run python -m sbam_workers.evals.correction_set
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sbam_contracts.manifest import SkillManifest

from sbam_workers.build.codegen import params_from_manifest
from sbam_workers.build.codegen.bodies import CodeBodies, import_allowlist, precheck
from sbam_workers.build.codegen.render import RenderedSkeleton, render_skeleton
from sbam_workers.build.codegen.store import LoadedTemplate, load_template
from sbam_workers.evals.golden import (
    GOLDEN_DIR,
    Defect,
    GoldenCorrectionItem,
    GoldenCorrectionSet,
    Task,
    apply_defect,
    correction_base,
)

TESTS: Final = Path(__file__).resolve().parents[3] / "tests"
SOURCES: Final = {
    "sub_skill": TESTS / "fixtures" / "isa14" / "ms01_1_structured.json",
    "manifest": TESTS / "fixtures" / "isa14" / "ms01_1_manifest.json",
}
BODIES_SOURCE: Final = TESTS / "llm-mock" / "sbam-codegen" / "e11_2_ms01_1.json"
TEMPLATE: Final = ("fastmcp-loopy", "1.0.0")
# What a corrected answer must still contain: the logic that makes this Sub-Skill do its job.
MUST_KEEP: Final = (
    "otp_match",
    "policy_violation",
    "state_handoff.append",
    "auth_status",
    "_within_device_limit",
)

_SQL: Final = "SELECT user_status FROM investor_auth WHERE mobile_number = "
_HELPER_LINE: Final = "    return int(current.active_devices or 0) <= limit"

# (description, expected pre-check code, defect)
DEFECTS: Final[tuple[tuple[str, str, Defect], ...]] = (
    (
        "a while loop in the check body",
        "MANUAL_LOOP",
        Defect(slot="check_body", text="while True:\n    break\n"),
    ),
    (
        "a retry loop around the action body",
        "MANUAL_LOOP",
        Defect(slot="action_body", text="attempts = 0\nwhile attempts < 3:\n    attempts += 1\n"),
    ),
    (
        "an unused helper that loops",
        "MANUAL_LOOP",
        Defect(
            slot="helpers",
            text=(
                "def _spin() -> int:\n    n = 0\n    while n < 3:\n        n += 1\n"
                "    return n\n\n\n"
            ),
        ),
    ),
    (
        "the device limit check rewritten as a countdown loop",
        "MANUAL_LOOP",
        Defect(
            slot="helpers",
            find=_HELPER_LINE,
            text=(
                "    devices = int(current.active_devices or 0)\n"
                "    while devices > limit:\n        devices -= 1\n"
                "    return devices == limit"
            ),
        ),
    ),
    (
        "pickle imported in the helpers",
        "FORBIDDEN_IMPORT",
        Defect(slot="helpers", text="import pickle\n\n_CACHE = pickle.dumps({})\n\n\n"),
    ),
    (
        "ctypes imported in the helpers",
        "FORBIDDEN_IMPORT",
        Defect(slot="helpers", text="import ctypes\n\n_LIB = ctypes.c_int(0)\n\n\n"),
    ),
    (
        "marshal imported in the helpers",
        "FORBIDDEN_IMPORT",
        Defect(slot="helpers", text="import marshal\n\n_BLOB = marshal.dumps(1)\n\n\n"),
    ),
    (
        "subprocess imported in the helpers",
        "FORBIDDEN_IMPORT",
        Defect(slot="helpers", text="import subprocess\n\n_RUN = subprocess.run\n\n\n"),
    ),
    (
        "eval called in the check body",
        "FORBIDDEN_CALL",
        Defect(slot="check_body", text='probe = eval("1 + 1")\n'),
    ),
    (
        "exec called in the action body",
        "FORBIDDEN_CALL",
        Defect(slot="action_body", text='exec("seen = True")\n'),
    ),
    (
        "__import__ called in a helper",
        "FORBIDDEN_CALL",
        Defect(slot="helpers", text="def _load(name: str):\n    return __import__(name)\n\n\n"),
    ),
    (
        "compile called in the check body",
        "FORBIDDEN_CALL",
        Defect(slot="check_body", text='code = compile("1", "x", "eval")\n'),
    ),
    (
        "SQL built with an f-string in the action body",
        "SQL_UNSAFE",
        Defect(slot="action_body", text=f"query = f\"{_SQL}'{{mobile_number}}'\"\n"),
    ),
    (
        "SQL built by concatenation in the action body",
        "SQL_UNSAFE",
        Defect(slot="action_body", text=f'query = "{_SQL}\'" + mobile_number + "\'"\n'),
    ),
    (
        "SQL built with % formatting in the action body",
        "SQL_UNSAFE",
        Defect(slot="action_body", text=f"query = \"{_SQL}'%s'\" % mobile_number\n"),
    ),
    (
        "SQL built with .format in the check body",
        "SQL_UNSAFE",
        Defect(slot="check_body", text=f"query = \"{_SQL}'{{}}'\".format(mobile_number)\n"),
    ),
    (
        "requests imported in the helpers",
        "IMPORT_NOT_ALLOWED",
        Defect(slot="helpers", text="import requests\n\n_SESSION = requests.Session\n\n\n"),
    ),
    (
        "numpy imported in the helpers",
        "IMPORT_NOT_ALLOWED",
        Defect(slot="helpers", text="import numpy\n\n_ZEROS = numpy.zeros\n\n\n"),
    ),
    (
        "pandas imported in the helpers",
        "IMPORT_NOT_ALLOWED",
        Defect(slot="helpers", text="import pandas\n\n_FRAME = pandas.DataFrame\n\n\n"),
    ),
    (
        "yaml imported in the helpers",
        "IMPORT_NOT_ALLOWED",
        Defect(slot="helpers", text="from yaml import safe_load\n\n_PARSE = safe_load\n\n\n"),
    ),
)


@dataclass(frozen=True)
class CorrectionEnv:
    """What `correct_code` needs besides the flawed bodies, built once from the base."""

    sub_skill: dict[str, Any]
    params: dict[str, Any]
    template: LoadedTemplate
    skeleton: RenderedSkeleton
    allowlist: tuple[str, ...]


def build_env(base: dict[str, Any]) -> CorrectionEnv:
    params = params_from_manifest(SkillManifest.model_validate(base["manifest"]))
    template = load_template(*TEMPLATE)
    return CorrectionEnv(
        sub_skill=base["sub_skill"],
        params=params,
        template=template,
        skeleton=render_skeleton(template, params),
        allowlist=tuple(import_allowlist(template.base_imports, ())),
    )


def flawed_bodies(item: GoldenCorrectionItem, base: dict[str, Any]) -> CodeBodies:
    return CodeBodies.model_validate(apply_defect(item, base["bodies"]))


def findings_of(bodies: CodeBodies, env: CorrectionEnv) -> list[dict[str, Any]]:
    """What the pre-check reports for the bodies, in the form the corrector is given."""
    return [
        {"gate": "BODY_PRECHECK", "code": f.code.value, "message": f.message, "field": f.field}
        for f in precheck(bodies, env.allowlist, env.params, env.skeleton)
    ]


def verify_items(items: list[GoldenCorrectionItem], base: dict[str, Any]) -> list[str]:
    """Problems with the items: the unflawed bodies must be clean, and every flaw must raise the
    pre-check code its item names (and the set may not name a code twice by accident)."""
    try:
        env = build_env(base)
        clean = precheck(
            CodeBodies.model_validate(base["bodies"]), env.allowlist, env.params, env.skeleton
        )
    except (KeyError, ValueError, OSError) as exc:
        return [f"correction: the base cannot be loaded ({type(exc).__name__})"]
    problems = [f"correction: the base bodies are not clean: {f.code.value}" for f in clean]
    for item in items:
        try:
            raised = {f["code"] for f in findings_of(flawed_bodies(item, base), env)}
        except ValueError as exc:
            problems.append(f"{item.id}: {exc}")
            continue
        if item.expect_code not in raised:
            problems.append(
                f"{item.id}: raises {sorted(raised) or 'nothing'}, not {item.expect_code}"
            )
    return problems


def build_correction_set() -> GoldenCorrectionSet:
    items = [
        GoldenCorrectionItem(
            id=f"correction-{number:02d}",
            description=description,
            synthetic=True,
            expect_code=code,
            defect=defect,
        )
        for number, (description, code, defect) in enumerate(DEFECTS, start=1)
    ]
    return GoldenCorrectionSet(task=Task.CORRECTION, version=1, items=items)


def render_json() -> str:
    return build_correction_set().model_dump_json(indent=2) + "\n"


def base_from_sources() -> dict[str, Any]:
    """The base as the repo's fixtures hold it now."""
    base: dict[str, Any] = {
        name: json.loads(path.read_text(encoding="utf-8")) for name, path in SOURCES.items()
    }
    base["bodies"] = json.loads(BODIES_SOURCE.read_text(encoding="utf-8"))["response"]
    return base


def render_base() -> str:
    return json.dumps(base_from_sources(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    (GOLDEN_DIR / "correction-base.json").write_text(render_base(), encoding="utf-8", newline="\n")
    (GOLDEN_DIR / "correction.json").write_text(render_json(), encoding="utf-8", newline="\n")
    problems = verify_items(build_correction_set().items, correction_base())
    print("\n".join(problems) or "20 flaws verified")
