"""The review golden set (#759, WG-11): twenty cases for `sbam-reviewer`, 12 flawed and 8 clean.

Every case is the MS01.1 sample (an investor login: OTP and device limit) written out as code
bodies. The sample's recorded bodies are not a clean reference: they answer `DENIED` where the spec
allows AUTHENTICATED, REJECTED or LOCKED, and read tables the spec does not name. `CLEAN_EDITS` fix
that once, so the base the cases start from does what its spec says.

* A **flawed** case puts one defect into the clean bodies: an inverted condition, a field that
  records the wrong value, a limit that is not enforced. These are defects no gate can see, so only
  the reviewer can catch them. Building the set checks that every case, flawed or clean, passes the
  body pre-check and the AST and static gates (`verify_items`), so a flawed case is never one a gate
  would already have refused.
* A **clean** case is the same behaviour written differently (a comment, a local variable, an
  equivalent condition). A reviewer that flags one is wrong.

`server/evals/golden/review.json` is this module's output, and a test checks the two agree:

    uv run python -m sbam_workers.evals.review_set

Nothing here calls a model.
"""

import ast
import copy
import json
from dataclasses import dataclass
from typing import Any, Final

from sbam_contracts.build_dashboard import GateStatus
from sbam_contracts.manifest import SkillManifest

from sbam_workers.build.codegen import params_from_manifest
from sbam_workers.build.codegen.bodies import CodeBodies, import_allowlist, precheck
from sbam_workers.build.codegen.bundle import assemble_bundle
from sbam_workers.build.codegen.render import RenderedSkeleton, render_skeleton
from sbam_workers.build.codegen.store import LoadedTemplate, load_template
from sbam_workers.build.gates.ast_checks import CheckResult, run_ast_checks
from sbam_workers.build.gates.ast_rules import run_rule_checks
from sbam_workers.build.gates.static_tools import TOOLS, ToolResult
from sbam_workers.build.review import review_context
from sbam_workers.evals.golden import (
    GOLDEN_DIR,
    GoldenReviewItem,
    GoldenReviewSet,
    ReviewEdit,
    Task,
    correction_base,
)

TEMPLATE: Final = ("fastmcp-loopy", "1.0.1")
ACTION: Final = "action_tool.py"
CHECK: Final = "check_tool.py"
STATE: Final = "state.py"

_LOCKED_BLOCK: Final = (
    'if current.user_status == "LOCKED":\n    status = "LOCKED"\nelif allowed:\n'
    '    status = "AUTHENTICATED"\nelse:\n    status = "REJECTED"\n'
)
_PROFILES: Final = "FROM investor_profiles WHERE mobile_number = %s"

# The base's two flaws against its own spec, fixed once: the answer is one of the three allowed
# `auth_status` values (a locked profile is LOCKED), and the state is read from the Sub-Skill's
# own table.
CLEAN_EDITS: Final[tuple[ReviewEdit, ...]] = (
    ReviewEdit(
        slot="action_body",
        find='return {\n    "auth_status": "AUTHENTICATED" if allowed else "DENIED",\n',
        text=f'{_LOCKED_BLOCK}return {{\n    "auth_status": status,\n',
    ),
    ReviewEdit(
        slot="state_queries",
        find="SELECT failed_login_attempts FROM investor_auth WHERE mobile_number = %s",
        text=f"SELECT failed_login_attempts {_PROFILES}",
    ),
    ReviewEdit(
        slot="state_queries",
        find="SELECT user_status FROM investor_auth WHERE mobile_number = %s",
        text=f"SELECT user_status {_PROFILES}",
    ),
    ReviewEdit(
        slot="state_queries",
        find="SELECT count(*) FROM investor_devices WHERE mobile_number = %s",
        text=f"SELECT active_devices {_PROFILES}",
    ),
)

_OTP: Final = 'otp_match = current.user_status == "active" and bool(otp_payload)'
_COUNT: Final = "iteration_pass=int(current.failed_login_attempts or 0) + 1,"
_SCOPE: Final = 'access_scope="investor" if allowed else "none",'
_CHECK_RETURN: Final = (
    'return {"otp_match": otp_match, "policy_violation": not _within_device_limit(current)}'
)
_LOCKED_FIRST: Final = (
    'if allowed:\n    status = "AUTHENTICATED"\nelif current.user_status == "LOCKED":\n'
    '    status = "LOCKED"\nelse:\n    status = "REJECTED"\n'
)

# (description, files, edits)
FLAWED: Final[tuple[tuple[str, tuple[str, ...], tuple[ReviewEdit, ...]], ...]] = (
    (
        "the rejection answers DENIED, which is not an allowed auth_status",
        (ACTION,),
        (ReviewEdit(slot="action_body", find='status = "REJECTED"', text='status = "DENIED"'),),
    ),
    (
        "a LOCKED profile is reported as REJECTED",
        (ACTION,),
        (
            ReviewEdit(
                slot="action_body",
                find='if current.user_status == "LOCKED":\n    status = "LOCKED"\nelif allowed:\n',
                text="if allowed:\n",
            ),
        ),
    ),
    (
        "policy_violation is reported the wrong way round",
        (CHECK,),
        (
            ReviewEdit(
                slot="check_body",
                find='"policy_violation": not _within_device_limit(current)',
                text='"policy_violation": _within_device_limit(current)',
            ),
        ),
    ),
    (
        "the check never reports a device-limit violation",
        (CHECK,),
        (
            ReviewEdit(
                slot="check_body",
                find='"policy_violation": not _within_device_limit(current)',
                text='"policy_violation": False',
            ),
        ),
    ),
    (
        "the check accepts the OTP of a profile that is not active",
        (CHECK,),
        (ReviewEdit(slot="check_body", find=_OTP, text="otp_match = bool(otp_payload)"),),
    ),
    (
        "access is granted past the device limit",
        (ACTION,),
        (
            ReviewEdit(
                slot="action_body",
                find="allowed = otp_match and _within_device_limit(current)",
                text="allowed = otp_match",
            ),
        ),
    ),
    (
        "a rejected user is given the investor role",
        (ACTION,),
        (
            ReviewEdit(
                slot="action_body",
                find='"user_role": "investor" if allowed else None,',
                text='"user_role": "investor",',
            ),
        ),
    ),
    (
        "the handoff records full access scope even when access was denied",
        (ACTION,),
        (ReviewEdit(slot="action_body", find=_SCOPE, text='access_scope="investor",'),),
    ),
    (
        "the handoff records the OTP as matched whatever the result",
        (ACTION,),
        (ReviewEdit(slot="action_body", find="otp_match=otp_match,", text="otp_match=True,"),),
    ),
    (
        "the handoff pass counter is always 1",
        (ACTION,),
        (ReviewEdit(slot="action_body", find=_COUNT, text="iteration_pass=1,"),),
    ),
    (
        "the one-time code is written into the handoff record",
        (ACTION,),
        (
            ReviewEdit(
                slot="action_body",
                find="device_fingerprint=device_fingerprint,",
                text='device_fingerprint=f"{device_fingerprint}:{otp_payload}",',
            ),
        ),
    ),
    (
        "the failed-attempts state reads the device count column",
        (STATE,),
        (
            ReviewEdit(
                slot="state_queries",
                find=f"SELECT failed_login_attempts {_PROFILES}",
                text=f"SELECT active_devices {_PROFILES}",
            ),
        ),
    ),
)

CLEAN: Final[tuple[tuple[str, tuple[ReviewEdit, ...]], ...]] = (
    ("the clean bodies as they are", ()),
    (
        "a comment explains the access rule",
        (
            ReviewEdit(
                slot="action_body",
                text="# access needs a matching OTP and a device count within the limit\n",
            ),
        ),
    ),
    (
        "the failed-attempts count is read into a local variable",
        (
            ReviewEdit(slot="action_body", find=_COUNT, text="iteration_pass=attempts + 1,"),
            ReviewEdit(
                slot="action_body", text="attempts = int(current.failed_login_attempts or 0)\n"
            ),
        ),
    ),
    (
        "the check names the device-limit result before returning it",
        (
            ReviewEdit(
                slot="check_body",
                find=_CHECK_RETURN,
                text=(
                    "within_limit = _within_device_limit(current)\n"
                    'return {"otp_match": otp_match, "policy_violation": not within_limit}'
                ),
            ),
        ),
    ),
    (
        "the device limit is compared with a negated greater-than",
        (
            ReviewEdit(
                slot="helpers",
                find="return int(current.active_devices or 0) <= limit",
                text="return not int(current.active_devices or 0) > limit",
            ),
        ),
    ),
    (
        "the status is decided with the granted case first",
        (ReviewEdit(slot="action_body", find=_LOCKED_BLOCK, text=_LOCKED_FIRST),),
    ),
    (
        "the status query is written in lower case",
        (
            ReviewEdit(
                slot="state_queries",
                find=f"SELECT user_status {_PROFILES}",
                text="select user_status from investor_profiles where mobile_number = %s",
            ),
        ),
    ),
    (
        "the access scope is named before the handoff",
        (
            ReviewEdit(slot="action_body", find=_SCOPE, text="access_scope=scope,"),
            ReviewEdit(
                slot="action_body",
                find="allowed = otp_match and _within_device_limit(current)\n",
                text=(
                    "allowed = otp_match and _within_device_limit(current)\n"
                    'scope = "investor" if allowed else "none"\n'
                ),
            ),
        ),
    ),
)

# The order of the cases in the file: flawed and clean mixed, so a position says nothing.
ORDER: Final[tuple[str, ...]] = tuple(
    "F0 C0 F1 F2 C1 F3 F4 F5 C2 F6 F7 C3 F8 F9 C4 F10 C5 F11 C6 C7".split()
)


def apply_edits(
    bodies: dict[str, Any], edits: tuple[ReviewEdit, ...] | list[ReviewEdit]
) -> dict[str, Any]:
    """The bodies with the edits put in, in order. Raises `ValueError` if a `find` is not there
    exactly once, so an edit can never silently do nothing."""
    out = copy.deepcopy(bodies)
    for edit in edits:
        if edit.slot == "state_queries":
            queries: dict[str, str] = out["state_queries"]
            if edit.find is None:
                raise ValueError("a state query edit needs `find`")
            hits = [k for k, v in queries.items() if edit.find in v]
            if len(hits) != 1 or sum(v.count(edit.find) for v in queries.values()) != 1:
                raise ValueError(
                    f"the text to replace is not in state_queries exactly once: {edit.find!r}"
                )
            queries[hits[0]] = queries[hits[0]].replace(edit.find, edit.text, 1)
            continue
        current: str = out[edit.slot]
        if edit.find is None:
            out[edit.slot] = edit.text + current
        else:
            if current.count(edit.find) != 1:
                raise ValueError(
                    f"the text to replace is not in {edit.slot} exactly once: {edit.find!r}"
                )
            out[edit.slot] = current.replace(edit.find, edit.text, 1)
    return out


def clean_bodies(base: dict[str, Any]) -> dict[str, Any]:
    return apply_edits(base["bodies"], CLEAN_EDITS)


def item_bodies(item: GoldenReviewItem, base: dict[str, Any]) -> dict[str, Any]:
    """The bodies of one case: the clean bodies with its edits."""
    return apply_edits(clean_bodies(base), item.edits)


@dataclass(frozen=True)
class ReviewEnv:
    """What every case needs besides its bodies, built once from the base."""

    sub_skill: dict[str, Any]
    params: dict[str, Any]
    template: LoadedTemplate
    skeleton: RenderedSkeleton
    allowlist: tuple[str, ...]
    loop_spec: bytes


def build_env(base: dict[str, Any]) -> ReviewEnv:
    from sbam_workers.build.wiring import loop_spec_of  # the loop spec a build would compile

    params = params_from_manifest(SkillManifest.model_validate(base["manifest"]))
    template = load_template(*TEMPLATE)
    return ReviewEnv(
        sub_skill=base["sub_skill"],
        params=params,
        template=template,
        skeleton=render_skeleton(template, params),
        allowlist=tuple(import_allowlist(template.base_imports, ())),
        loop_spec=loop_spec_of(base["sub_skill"]),
    )


@dataclass(frozen=True)
class ReviewCase:
    """What the reviewer is given for one item, and what the item is."""

    item: GoldenReviewItem
    files: dict[str, str]  # the assembled, formatted .py files
    context: dict[str, str]  # the request's data blocks: spec and diff


def assembled_files(bodies: dict[str, Any], env: ReviewEnv) -> dict[str, bytes]:
    out = assemble_bundle(
        env.template, env.skeleton, bodies, env.params.get("fresh_state", []), env.loop_spec
    )
    if out.bundle is None:
        raise ValueError(f"the bodies do not assemble: {[f.message for f in out.findings]}")
    return dict(out.bundle.files)


def build_case(item: GoldenReviewItem, base: dict[str, Any], env: ReviewEnv) -> ReviewCase:
    files = {
        name: data.decode("utf-8")
        for name, data in assembled_files(item_bodies(item, base), env).items()
        if name.endswith(".py")
    }
    return ReviewCase(item, files, review_context(env.sub_skill, env.skeleton.files, files))


def build_cases(items: list[GoldenReviewItem], base: dict[str, Any]) -> list[ReviewCase]:
    env = build_env(base)
    return [build_case(item, base, env) for item in items]


def gate_problems(bodies: dict[str, Any], env: ReviewEnv, *, static: bool = True) -> list[str]:
    """Every way the bodies fail the body pre-check, the AST gates or (with `static`) the static
    tools. Empty means no gate would stop this code, so only the reviewer can judge it."""
    problems = [
        f"precheck {f.code.value}"
        for f in precheck(
            CodeBodies.model_validate(bodies), env.allowlist, env.params, env.skeleton
        )
    ]
    if problems:
        return problems
    files = assembled_files(bodies, env)
    results: list[CheckResult | ToolResult] = [
        *run_ast_checks(files, env.skeleton, env.template, env.params)
    ]
    trees = {name: ast.parse(data) for name, data in sorted(files.items()) if name.endswith(".py")}
    results += run_rule_checks(trees, env.params, env.template, ())
    if static:
        results += [tool(files) for tool in TOOLS]
    return [
        f"{r.gate.value} {r.status.value}" for r in results if r.status is not GateStatus.PASSED
    ]


def verify_items(
    items: list[GoldenReviewItem], base: dict[str, Any], *, static: bool = False
) -> list[str]:
    """Problems with the items. The clean bodies and every case must pass the pre-check and the AST
    gates (and the static tools when `static`): a flawed case that a gate refuses is not a defect
    only the reviewer can find. Every edit must apply exactly once, a flawed case must really change
    the bodies in the files it names, and a clean case may not change what the code does to a
    handoff or a status by accident (its edits apply and its diff is not empty)."""
    try:
        env = build_env(base)
        clean = clean_bodies(base)
        base_problems = gate_problems(clean, env, static=static)
    except (KeyError, ValueError, OSError) as exc:
        return [f"review: the base cannot be loaded ({type(exc).__name__})"]
    problems = [f"review: the clean base fails {p}" for p in base_problems]
    base_files = assembled_files(clean, env)
    for item in items:
        try:
            bodies = item_bodies(item, base)
            found = gate_problems(bodies, env, static=static)
            files = assembled_files(bodies, env)
        except ValueError as exc:
            problems.append(f"{item.id}: {exc}")
            continue
        problems += [f"{item.id}: a gate refuses it: {p}" for p in found]
        changed = sorted(n for n in files if files[n] != base_files.get(n))
        if item.flawed and sorted(item.files) != changed:
            problems.append(f"{item.id}: changes {changed}, not the files it names {item.files}")
    return problems


def build_review_set() -> GoldenReviewSet:
    items: list[GoldenReviewItem] = []
    for number, key in enumerate(ORDER, start=1):
        if key[0] == "F":
            description, files, edits = FLAWED[int(key[1:])]
            item = GoldenReviewItem(
                id=f"review-{number:02d}",
                description=description,
                synthetic=True,
                flawed=True,
                files=list(files),
                edits=list(edits),
            )
        else:
            description, edits = CLEAN[int(key[1:])]
            item = GoldenReviewItem(
                id=f"review-{number:02d}",
                description=description,
                synthetic=True,
                flawed=False,
                files=[],
                edits=list(edits),
            )
        items.append(item)
    return GoldenReviewSet(task=Task.REVIEW, version=1, items=items)


def render_json() -> str:
    return build_review_set().model_dump_json(indent=2) + "\n"


if __name__ == "__main__":
    (GOLDEN_DIR / "review.json").write_text(render_json(), encoding="utf-8", newline="\n")
    found = verify_items(build_review_set().items, correction_base(), static=True)
    print("\n".join(found) or "20 cases verified: no gate refuses any of them")
    print(json.dumps({"flawed": len(FLAWED), "clean": len(CLEAN)}))
