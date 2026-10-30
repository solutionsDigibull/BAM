"""The code-generation golden set (E15.5): the five testgen Sub-Skills at four iteration bounds.

The Sub-Skills are the testgen set's own (`DomainProfile`, one per invented business process), so
there is one source for them: a login with a one-time code, invoice matching, stock reservation,
ticket triage and leave requests. Each item is one of them at one bound (1, 2, 3 or 5), and the
model is asked, as in a build, for the code bodies that fill the `fastmcp-loopy` skeleton.

No manifest is needed: `params_from_parts` takes the three things a manifest supplies, which here
come from the Sub-Skill and its contracts. Building the set checks that every item yields render
parameters the template accepts and a skeleton that renders.

`server/evals/golden/codegen.json` is this module's output, and a test checks the two agree:

    uv run python -m sbam_workers.evals.codegen_set
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError
from sbam_contracts.isatvon14 import SubSkill, build_contracts

from sbam_workers.build.codegen.errors import TemplateError
from sbam_workers.build.codegen.params import params_from_parts, validate_params
from sbam_workers.build.codegen.render import RenderedSkeleton, render_skeleton
from sbam_workers.build.codegen.store import LoadedTemplate, load_template
from sbam_workers.evals.golden import (
    GOLDEN_DIR,
    DomainProfile,
    GoldenCodegenItem,
    GoldenCodegenSet,
    GoldenTestgenItem,
    Task,
    build_testgen_isatvon,
    load_testgen,
)
from sbam_workers.evals.testgen_set import BOUNDS

TEMPLATE: Final = ("fastmcp-loopy", "1.0.0")


@dataclass(frozen=True)
class CodegenCase:
    """What `generate_bodies` needs for one item."""

    item: GoldenCodegenItem
    sub_skill: dict[str, Any]
    params: dict[str, Any]
    skeleton: RenderedSkeleton


def build_case(
    item: GoldenCodegenItem,
    base: dict[str, Any],
    domains: dict[str, DomainProfile],
    template: LoadedTemplate,
) -> CodegenCase:
    sub_skill = build_testgen_isatvon(
        GoldenTestgenItem(
            id="testgen-00",
            description=item.description,
            synthetic=True,
            domain=item.domain,
            max_iterations=item.max_iterations,
        ),
        base,
        domains,
    )
    contracts = build_contracts(SubSkill.model_validate(sub_skill))
    params = params_from_parts(
        sub_skill["skill_metadata"]["skill_id"], sub_skill["isatvon_definition"], contracts
    )
    validate_params(template, params)
    return CodegenCase(item, sub_skill, params, render_skeleton(template, params))


def load_codegen_template() -> LoadedTemplate:
    return load_template(*TEMPLATE)


def build_cases(
    items: list[GoldenCodegenItem], base: dict[str, Any], domains: dict[str, DomainProfile]
) -> list[CodegenCase]:
    template = load_codegen_template()
    return [build_case(item, base, domains, template) for item in items]


def verify_items(
    items: list[GoldenCodegenItem], base: dict[str, Any], domains: dict[str, DomainProfile]
) -> list[str]:
    """Problems with the items: each must give render parameters the template accepts, a skeleton
    that renders, and gate outputs that are plain names (a `null` among them means a gate
    expression the template cannot use, as `is not null` once produced)."""
    try:
        template = load_codegen_template()
    except (TemplateError, OSError) as exc:
        return [f"codegen: the template cannot be loaded ({type(exc).__name__})"]
    problems: list[str] = []
    for item in items:
        try:
            case = build_case(item, base, domains, template)
        except (TemplateError, ValidationError, KeyError, ValueError) as exc:
            problems.append(f"{item.id}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        names = case.params["gate_outputs"]
        if not names or {"null", "none", "true", "false"} & {n.lower() for n in names}:
            problems.append(f"{item.id}: odd gate outputs {names}")
    return problems


def build_codegen_set() -> GoldenCodegenSet:
    items: list[GoldenCodegenItem] = []
    for name in load_testgen().domains:
        for bound in BOUNDS:
            items.append(
                GoldenCodegenItem(
                    id=f"codegen-{len(items) + 1:02d}",
                    description=f"{name}, max_iterations {bound}",
                    synthetic=True,
                    domain=name,
                    max_iterations=bound,
                )
            )
    return GoldenCodegenSet(task=Task.CODEGEN, version=1, items=items)


def render_json() -> str:
    return build_codegen_set().model_dump_json(indent=2) + "\n"


if __name__ == "__main__":
    (GOLDEN_DIR / "codegen.json").write_text(render_json(), encoding="utf-8", newline="\n")
    shared = load_testgen()
    from sbam_workers.evals.golden import load_codegen, testgen_base

    problems = verify_items(load_codegen().items, testgen_base(), shared.domains)
    print("\n".join(problems) or f"{len(shared.domains) * len(BOUNDS)} items verified")
    print(f"wrote {Path(GOLDEN_DIR) / 'codegen.json'}")
