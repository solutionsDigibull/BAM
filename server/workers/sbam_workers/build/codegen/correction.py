"""Corrections (TD §12.4, E11.4): findings -> `sbam-corrector` -> ISATVON patch and/or new bodies.

* The model gets the failing findings (at most MAX_FINDINGS, each field capped), the harness trace
  (at most MAX_TRACE_BYTES), the structured ISATVON, the current bodies, the skeleton and the import
  allowlist, each in a `<data>` block; attempt 2 also gets attempt 1's diff and findings. Finding
  messages and traces are data: nothing in them widens the allowlist or the patchable paths.
* `CorrectorOutput` forbids extra keys and is schema-checked by the client (re-asked when invalid).
  What it may *do* is checked here, after the schema, so a forbidden or invalid patch is a recorded
  outcome of the attempt (a finding), never a re-ask:
  - every patch op must target `/isatvon_definition` (PATCH_FORBIDDEN: skill_metadata ids,
    governance, sbam_build_record, srim_runtime_definition, anything else), and the whole patch
    is rejected if one op is;
  - ops are an RFC 6902 subset (add/remove/replace/test) applied to a copy (PATCH_INVALID);
  - the patched Sub-Skill must pass the ISATVON 1.4 rules again, the runtime library registry
    included (ISATVON_CONTRACT), and its loop spec must compile.
* New bodies replace the current ones as a whole and go through the same pre-check as generated
  bodies (`bodies.precheck`). The model writes only slot bodies, so skeleton regions are out of its
  reach; the AST gate re-checks the assembled files.
"""

import copy
import difflib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from sbam_contracts.canonical import sha256_of
from sbam_contracts.isatvon14 import DEFAULT_LIBRARY_REGISTRY, SubSkill, validate_sub_skill

from sbam_workers.build.codegen.bodies import (
    BodyFinding,
    CodeBodies,
    import_allowlist,
    precheck,
)
from sbam_workers.build.codegen.render import RenderedSkeleton
from sbam_workers.build.codegen.store import LoadedTemplate
from sbam_workers.isatvon.loop_spec import compile_definition
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.store import LlmScope

ALIAS: Final = "sbam-corrector"
PROMPT_ID: Final = "correct-code"
MAX_FINDINGS: Final = 50
MAX_TRACE_BYTES: Final = 200 * 1024
MAX_FINDING_TEXT: Final = 2000
MAX_PATCH_OPS: Final = 50
PATCH_ROOT: Final = "isatvon_definition"
FINDING_KEYS: Final = ("gate", "code", "message", "file", "field", "path", "line", "col")
TRUNCATED: Final = "\n[... truncated]"


class PatchOp(BaseModel):
    """One RFC 6902 operation. `value` is ignored for `remove`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    op: Literal["add", "remove", "replace", "test"]
    path: Annotated[str, Field(max_length=512)]
    value: JsonValue = None


class CorrectorOutput(BaseModel):
    """What `sbam-corrector` may answer (TD §5 `CorrectionPatch`): a patch, bodies, and why."""

    model_config = ConfigDict(extra="forbid", strict=True)

    isatvon_patch: list[PatchOp] | None = Field(
        default=None,
        max_length=MAX_PATCH_OPS,
        description="JSON Patch on the Sub-Skill; every path starts with /isatvon_definition/.",
    )
    bodies: CodeBodies | None = Field(
        default=None, description="The complete corrected bodies, or null to keep the current ones."
    )
    rationale: Annotated[str, Field(min_length=1, max_length=4000)]


class PatchFindingCode(StrEnum):
    """Stable codes for a rejected ISATVON patch. Add new ones here; never rename or reuse one."""

    PATCH_FORBIDDEN = "PATCH_FORBIDDEN"  # an op outside /isatvon_definition
    PATCH_INVALID = "PATCH_INVALID"  # a bad pointer, a missing target or a failed `test`
    ISATVON_CONTRACT = "ISATVON_CONTRACT"  # the patched Sub-Skill breaks the ISATVON 1.4 rules


@dataclass(frozen=True)
class PatchFinding:
    code: PatchFindingCode
    path: str  # the op's JSON Pointer, or the violated ISATVON path
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "field": "isatvon_patch",
            "path": self.path,
            "message": self.message,
        }


class _PatchError(ValueError):
    pass


@dataclass(frozen=True)
class Correction:
    output: CorrectorOutput
    bodies: CodeBodies  # what the next pass assembles: the corrector's, or the current ones
    sub_skill: Mapping[str, Any] | None  # the patched Sub-Skill, only when a patch was accepted
    isatvon_sha256: str | None  # sha256 of the patched isatvon_definition, with `sub_skill`
    loop_spec: bytes | None  # its recompiled loop_spec.json, with `sub_skill`
    findings: tuple[BodyFinding | PatchFinding, ...]  # empty: the correction may be assembled
    llm_call_id: Any
    trace_id: str
    attempts: int
    cached: bool


# --- context -------------------------------------------------------------------------------------


def _cap(text: str, limit: int) -> str:
    """At most `limit` UTF-8 bytes, cut on a character boundary, marked when cut."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    keep = limit - len(TRUNCATED.encode("utf-8"))
    return raw[:keep].decode("utf-8", errors="ignore") + TRUNCATED


def intake(findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The findings block: known keys only, text capped, at most MAX_FINDINGS (the rest counted)."""
    kept: list[dict[str, Any]] = []
    for finding in findings[:MAX_FINDINGS]:
        item: dict[str, Any] = {}
        for key in FINDING_KEYS:
            value = finding.get(key)
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, int):
                item[key] = value
            else:
                item[key] = _cap(str(value), MAX_FINDING_TEXT)
        kept.append(item)
    return {"findings": kept, "omitted": max(len(findings) - MAX_FINDINGS, 0)}


def bodies_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    """Unified diff of two `CodeBodies` dumps, field by field (empty when identical)."""

    def text(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(value, indent=1, sort_keys=True)

    lines: list[str] = []
    for field in CodeBodies.model_fields:
        old, new = text(before.get(field)), text(after.get(field))
        lines += difflib.unified_diff(
            old.splitlines(), new.splitlines(), f"a/{field}", f"b/{field}", lineterm=""
        )
    return "\n".join(lines)


def request_context(
    sub_skill: Mapping[str, Any],
    bodies: CodeBodies,
    skeleton: RenderedSkeleton,
    allowlist: Iterable[str],
    findings: Sequence[Mapping[str, Any]],
    trace: str = "",
    previous: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """The `<data>` blocks of the request. `previous` (attempt 2): attempt 1's diff and findings."""
    context = {
        "isatvon": json.dumps(sub_skill, indent=1, sort_keys=True, ensure_ascii=False),
        "bodies": json.dumps(bodies.model_dump(), indent=1, sort_keys=True, ensure_ascii=False),
        "allowed_imports": json.dumps(list(allowlist)),
    }
    for name, text in skeleton.files.items():
        context[f"skeleton.{name}"] = text
    context["findings"] = json.dumps(intake(findings), indent=1, ensure_ascii=False)
    if trace:
        context["harness_trace"] = _cap(trace, MAX_TRACE_BYTES)
    if previous is not None:
        context["previous_attempt"] = json.dumps(previous, indent=1, ensure_ascii=False)
    return context


# --- ISATVON patch -------------------------------------------------------------------------------


def _tokens(path: str) -> list[str]:
    if not path.startswith("/"):
        raise _PatchError(f"{path!r} is not a JSON Pointer")
    return [t.replace("~1", "/").replace("~0", "~") for t in path.split("/")[1:]]


def patchable(path: str) -> bool:
    try:
        tokens = _tokens(path)
    except _PatchError:
        return False
    return tokens[:1] == [PATCH_ROOT]


def _index(container: list[Any], token: str, *, append: bool) -> int:
    if append and token == "-":  # noqa: S105 -- JSON-Pointer "append" token, not a secret
        return len(container)
    if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        raise _PatchError(f"{token!r} is not an array index")
    index = int(token)
    if index > len(container) or (index == len(container) and not append):
        raise _PatchError(f"index {index} is out of range")
    return index


def _child(node: Any, token: str) -> Any:
    if isinstance(node, dict):
        if token not in node:
            raise _PatchError(f"no member {token!r}")
        return node[token]
    if isinstance(node, list):
        return node[_index(node, token, append=False)]
    raise _PatchError(f"cannot descend into a {type(node).__name__}")


def _apply(doc: dict[str, Any], op: PatchOp) -> None:
    *parents, last = _tokens(op.path)
    target: Any = doc
    for token in parents:
        target = _child(target, token)
    value = copy.deepcopy(op.value)
    if op.op == "test":
        if _child(target, last) != value:
            raise _PatchError("test failed")
    elif isinstance(target, dict):
        if op.op != "add" and last not in target:
            raise _PatchError(f"no member {last!r}")
        if op.op == "remove":
            del target[last]
        else:
            target[last] = value
    elif isinstance(target, list):
        index = _index(target, last, append=op.op == "add")
        if op.op == "add":
            target.insert(index, value)
        elif op.op == "remove":
            del target[index]
        else:
            target[index] = value
    else:
        raise _PatchError(f"cannot change a {type(target).__name__}")


def apply_isatvon_patch(
    sub_skill: Mapping[str, Any],
    ops: Sequence[PatchOp],
    library_registry: Iterable[str] = DEFAULT_LIBRARY_REGISTRY,
) -> tuple[dict[str, Any] | None, list[PatchFinding]]:
    """(patched copy, []) or (None, findings). `sub_skill` is never changed."""
    forbidden = [
        PatchFinding(PatchFindingCode.PATCH_FORBIDDEN, op.path, f"only /{PATCH_ROOT} is patchable")
        for op in ops
        if not patchable(op.path)
    ]
    if forbidden:
        return None, forbidden
    patched = copy.deepcopy(dict(sub_skill))
    for op in ops:
        try:
            _apply(patched, op)
        except _PatchError as exc:
            return None, [PatchFinding(PatchFindingCode.PATCH_INVALID, op.path, f"{op.op}: {exc}")]
    violations = validate_sub_skill(patched, library_registry=frozenset(library_registry))
    if violations:
        return None, [
            PatchFinding(PatchFindingCode.ISATVON_CONTRACT, v.path, f"rule {v.rule}: {v.message}")
            for v in violations
        ]
    return patched, []


def recompile(sub_skill: Mapping[str, Any]) -> tuple[bytes | None, list[PatchFinding]]:
    """The patched Sub-Skill's loop_spec.json (re-render input), or why it does not compile."""
    try:
        definition = SubSkill.model_validate(sub_skill).isatvon_definition
        return compile_definition(definition).canonical_bytes, []
    except (ValidationError, ValueError) as exc:
        message = _cap(str(exc), MAX_FINDING_TEXT)
        path = f"/{PATCH_ROOT}/v_variables"
        return None, [PatchFinding(PatchFindingCode.ISATVON_CONTRACT, path, message)]


# --- the call ------------------------------------------------------------------------------------


async def correct_code(
    client: LlmClient,
    scope: LlmScope,
    *,
    sub_skill: Mapping[str, Any],
    current: CodeBodies,
    template: LoadedTemplate,
    params: Mapping[str, Any],
    skeleton: RenderedSkeleton,
    modules: Iterable[str],
    findings: Sequence[Mapping[str, Any]],
    trace: str = "",
    previous: Mapping[str, Any] | None = None,
    library_registry: Iterable[str] = DEFAULT_LIBRARY_REGISTRY,
    prompt_version: str | None = None,
) -> Correction:
    """Ask `sbam-corrector` for a correction, then check the patch and pre-check the bodies.

    Raises what `LlmClient.run` raises (LlmTransportError, SchemaInvalidAfterRetries, ...); a
    rejected patch or rejected bodies come back as findings, for the next correction or the FDE.
    """
    allowlist = import_allowlist(template.base_imports, modules)
    result = await client.run(
        ALIAS,
        PROMPT_ID,
        CorrectorOutput,
        request_context(sub_skill, current, skeleton, allowlist, findings, trace, previous),
        scope,
        prompt_version=prompt_version,
        fresh=True,
        metadata={"template": f"{template.name}/{template.version}"},
    )
    out = result.output
    bodies = out.bodies or current
    patched: dict[str, Any] | None = None
    loop_spec: bytes | None = None
    problems: list[BodyFinding | PatchFinding] = []
    if out.isatvon_patch:
        patched, rejected = apply_isatvon_patch(sub_skill, out.isatvon_patch, library_registry)
        problems.extend(rejected)
        if patched is not None:
            loop_spec, broken = recompile(patched)
            problems.extend(broken)
            if broken:
                patched = None
    problems.extend(precheck(bodies, allowlist, params, skeleton))
    return Correction(
        output=out,
        bodies=bodies,
        sub_skill=patched,
        isatvon_sha256=None if patched is None else sha256_of(patched[PATCH_ROOT]),
        loop_spec=loop_spec,
        findings=tuple(problems),
        llm_call_id=result.llm_call_id,
        trace_id=result.trace_id,
        attempts=result.attempts,
        cached=result.cached,
    )
