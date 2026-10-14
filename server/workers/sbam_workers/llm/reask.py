"""Bounded structured-output re-ask (E1.15-T05; the same rules as E6.1-T02/T03, TD §5.4).

`run_structured` asks once, validates the reply against a Pydantic model, and on a schema error asks
again with the validation errors only, at most `MAX_REASKS` times. It knows nothing about HTTP: the
caller passes an async `LlmCall`, so the Bifrost client in E6.1 plugs in unchanged and writes one
`llm_calls` row per attempt from `on_attempt` (every attempt, failures included).

- Schema errors are the model's fault: they consume a re-ask and end in `SchemaInvalidAfterRetries`,
  which is non-retryable (a Temporal retry would only pay for the same bad answer).
- Transport errors raised by the caller propagate untouched. They are retryable and never count as
  a re-ask.
- The re-ask message carries error locations and messages only. It never echoes input values and
  never adds documents, so a re-ask cannot widen what the model sees.
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


def error_lines(exc: ValidationError) -> tuple[str, ...]:
    """`path: message` per error. Never `input`: it can hold document text or secrets."""
    return tuple(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
    )


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
) -> StructuredResult[T]:
    attempts: list[Attempt] = []
    current = list(messages)
    for number in range(1, max_reasks + 2):
        reply = await call(current)
        try:
            # strict: the model must send the exact JSON types, no "12" -> 12 coercion.
            output = output_type.model_validate_json(strip_fences(reply.text), strict=True)
        except ValidationError as exc:
            errors = error_lines(exc)
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
