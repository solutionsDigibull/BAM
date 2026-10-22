"""Bounded structured-output re-ask (E1.15-T05; the same rules as E6.1-T02/T03, TD §5.4).

`run_structured` asks once, validates the reply against a Pydantic model, and on a schema error asks
again with the validation errors only, at most `MAX_REASKS` times. It knows nothing about HTTP: the
caller passes an async `LlmCall`, so the Bifrost client in E6.1 plugs in unchanged and writes one
`llm_calls` row per attempt from `on_attempt` (every attempt, failures included).

- Schema errors are the model's fault: they consume a re-ask and end in `SchemaInvalidAfterRetries`,
  which is non-retryable (a Temporal retry would only pay for the same bad answer).
- Transport errors raised by the caller propagate untouched. They are retryable and never count as
  a re-ask.
- The re-ask message carries `path: error type` only (E1.21). The path keeps only names the output
  schema defines, so a mapping key or an invented field shows as `<key>`; the error type is
  Pydantic's stable code (`missing`, `int_type`, ...), never the validator's message, which can name
  the offending value. It never echoes input values and never adds documents, so a re-ask cannot
  widen what the model sees, and `Attempt.errors` (written to `llm_calls`) holds no document text
  either.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

MAX_REASKS = 3  # one call + up to 3 re-asks = 4 calls at most

Message = dict[str, Any]
_FENCE = re.compile(r"\A```[A-Za-z0-9_-]*[ \t]*\n(.*?)\n?```\Z", re.S)


@dataclass(frozen=True)
class Reply:
    text: str
    model: str  # the model that answered; after a Bifrost failover this is the fallback
    tokens_in: int = 0  # usage as the gateway reported it (E6.1 writes it to llm_calls)
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


@dataclass(frozen=True)
class Attempt:
    number: int  # 1-based
    status: Literal["SUCCEEDED", "SCHEMA_INVALID"]
    model: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    output: T
    attempts: tuple[Attempt, ...]


LlmCall = Callable[[list[Message]], Awaitable[Reply]]
OnAttempt = Callable[[Attempt, Reply], Awaitable[None]]


class SchemaInvalidAfterRetries(Exception):  # noqa: N818 - fixed name in E6.1
    """Every attempt, including the re-asks, failed schema validation. Non-retryable."""

    retryable = False

    def __init__(self, attempts: tuple[Attempt, ...]) -> None:
        self.attempts = attempts
        last = "; ".join(attempts[-1].errors) if attempts else ""
        super().__init__(f"schema invalid after {len(attempts)} attempts: {last}")


def strip_fences(text: str) -> str:
    """A valid JSON answer wrapped in a markdown fence is still valid (E6.1 edge case)."""
    stripped = text.strip()
    match = _FENCE.match(stripped)
    return match.group(1).strip() if match else stripped


# Segments Pydantic adds for union members and similar; they name a type, never data.
_TYPE_SEGMENTS = frozenset(
    "str int float bool none list dict tuple set frozenset date datetime time timedelta bytes "
    "decimal uuid url".split()
)
# `ctx` entries that are constants of the schema (a limit, the allowed literals). Every other entry
# is left out: `value_error` carries the exception, which can hold the offending value.
_CONSTRAINT_CTX = ("ge", "gt", "le", "lt", "min_length", "max_length", "multiple_of", "expected")


def schema_names(output_type: type[BaseModel]) -> frozenset[str]:
    """Every property name the output schema defines, in the model and in the models it contains."""
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(str(name) for name in properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(output_type.model_json_schema())
    return frozenset(names)


def _path(loc: tuple[int | str, ...], names: frozenset[str]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"  # a position in a list, not data
            continue
        known = part in names or part in _TYPE_SEGMENTS
        out += ("." if out else "") + (part if known else "<key>")
    return out or "<root>"


def error_lines(exc: ValidationError, names: frozenset[str]) -> tuple[str, ...]:
    """`path: type (constraint)` per error, built only from what the schema defines (E1.21).

    Never `input` and never `msg`: both can hold document text or secrets (a validator that raises
    ``ValueError(f"duplicate id {value}")`` puts the value in the message). `loc` segments the
    schema does not define (mapping keys, fields the model invented) become `<key>`.
    """
    lines: list[str] = []
    for err in exc.errors():
        hint = ", ".join(
            f"{key}={err['ctx'][key]}"
            for key in _CONSTRAINT_CTX
            if key in (err.get("ctx") or {}) and isinstance(err["ctx"][key], int | float | str)
        )
        lines.append(f"{_path(err['loc'], names)}: {err['type']}" + (f" ({hint})" if hint else ""))
    return tuple(lines)


def reask_messages(previous: list[Message], reply: Reply, errors: tuple[str, ...]) -> list[Message]:
    feedback = (
        "Your previous answer did not match the required JSON schema. Fix these errors and "
        "answer again with the complete JSON only:\n" + "\n".join(f"- {line}" for line in errors)
    )
    return [
        *previous,
        {"role": "assistant", "content": reply.text},
        {"role": "user", "content": feedback},
    ]


async def run_structured[T: BaseModel](
    call: LlmCall,
    messages: list[Message],
    output_type: type[T],
    *,
    max_reasks: int = MAX_REASKS,
    on_attempt: OnAttempt | None = None,
    validate: Callable[[T], tuple[str, ...]] | None = None,
) -> StructuredResult[T]:
    """`validate` runs after the schema check and returns error lines (empty = valid). They go back
    to the model like schema errors, so they must be codes and ids only, never document text."""
    names = schema_names(output_type)
    attempts: list[Attempt] = []
    current = list(messages)
    for number in range(1, max_reasks + 2):
        reply = await call(current)
        try:
            # strict: the model must send the exact JSON types, no "12" -> 12 coercion.
            output = output_type.model_validate_json(strip_fences(reply.text), strict=True)
            errors = validate(output) if validate else ()
        except ValidationError as exc:
            errors = error_lines(exc, names)
        if errors:
            attempt = Attempt(number, "SCHEMA_INVALID", reply.model, errors)
            attempts.append(attempt)
            if on_attempt:
                await on_attempt(attempt, reply)
            current = reask_messages(current, reply, errors)
            continue
        attempt = Attempt(number, "SUCCEEDED", reply.model)
        attempts.append(attempt)
        if on_attempt:
            await on_attempt(attempt, reply)
        return StructuredResult(output, tuple(attempts))
    raise SchemaInvalidAfterRetries(tuple(attempts))
