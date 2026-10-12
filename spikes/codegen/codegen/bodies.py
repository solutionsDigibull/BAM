"""LLM slot bodies for Spike E0.11 (#29), TD §12.4.

The LLM never returns files. It returns one `CodeBodies` object, which is schema-validated
before anything is assembled (E0.11-T08). Pydantic AI turns a validation error into a re-ask,
up to `RETRIES` times; after that `UnexpectedModelBehavior` is raised and no file is written.

Model:
  * BIFROST_URL set -> the Bifrost alias `sbam-codegen` (OpenAI-compatible API). Never a direct
    provider SDK call (golden rule).
  * otherwise       -> a mock (`FunctionModel`) that replays scripted responses from YAML
    fixtures, so everything runs in CI without a key.
"""

from __future__ import annotations

import ast
import os
import textwrap
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

RETRIES = 3
ALIAS = "sbam-codegen"
MARKER = "LLM_BODY"


def _parses(code: str, where: str, *, in_function: bool) -> str:
    if MARKER in code:
        raise ValueError(f"{where} must not contain slot markers ({MARKER})")
    src = "def _slot():\n" + textwrap.indent(code, "    ") if in_function else code
    try:
        ast.parse(src)
    except SyntaxError as e:
        raise ValueError(f"{where} is not valid Python: {e.msg} (line {e.lineno})") from e
    return code


class CodeBodies(BaseModel):
    """What the LLM returns. Each field fills one slot kind (templates' template.yaml)."""

    model_config = ConfigDict(extra="forbid")

    action_body: str = Field(min_length=1, description="Body of the action tool function")
    check_body: str = Field(min_length=1, description="Body of the fixed check tool function")
    state_queries: dict[str, str] = Field(
        min_length=1, description="fresh-state name -> parameterised SELECT with one ? placeholder"
    )
    helpers: str = Field(default="", description="Module-level pure helper functions, or empty")

    @field_validator("action_body", "check_body")
    @classmethod
    def _body(cls, v: str, info: Any) -> str:
        return _parses(v, info.field_name, in_function=True)

    @field_validator("helpers")
    @classmethod
    def _helpers(cls, v: str) -> str:
        tree = ast.parse(_parses(v, "helpers", in_function=False))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                raise ValueError(f"helpers may only define functions (line {node.lineno})")
        return v

    @field_validator("state_queries")
    @classmethod
    def _queries(cls, v: dict[str, str]) -> dict[str, str]:
        for name, sql in v.items():
            if not name.isidentifier():
                raise ValueError(f"state_queries key {name!r} is not an identifier")
            if not sql.lstrip().upper().startswith("SELECT") or sql.count("?") != 1:
                raise ValueError(f"state_queries[{name!r}] must be a SELECT with exactly one ?")
        return v


# --- prompt ---------------------------------------------------------------------------------------

INSTRUCTIONS = """You write Python function BODIES for a generated FastMCP Sub-Skill.
The files, imports, signatures and docstrings already exist; you fill only the slots.
Rules:
- action_body / check_body run inside the tool function. `current` (dict from state.read) and the
  tool's parameters are in scope. Return a dict. Local stdlib imports are allowed.
- check_body must not import or call the action tool; it re-derives everything from `current`.
- No while loops, no retry decorators (tenacity, backoff, retry). The runner owns iterations.
- action_body must call state_handoff.append(...) with exactly the required handoff fields.
- state_queries: one read-only SELECT per fresh-state name, with exactly one ? for the key.
- V parameters are in state.PARAMETERS. Never hard-code their values.
"""


def build_prompt(ctx: dict[str, Any], skeleton: dict[str, str]) -> str:
    return "\n".join(
        [
            f"Sub-Skill {ctx['skill_id']}",
            f"Action step: {ctx['action_step']}",
            f"Acceptance check: {ctx['acceptance_check']}",
            f"success_gate: {ctx['success_gate']}",
            f"escalation_condition: {ctx['escalation_condition']}",
            f"Check tool must return keys: {', '.join(ctx['gate_outputs'])}",
            f"Handoff fields: {', '.join(ctx['required_fields'])}",
            f"Fresh state (state_queries keys): {', '.join(ctx['fresh_state'])}",
            f"Tables: {', '.join(ctx['tables'])}",
            f"V parameters: {', '.join(p['name'] for p in ctx['parameters']) or '-'}",
            "",
            "--- action_tool.py (skeleton) ---",
            skeleton["action_tool.py"],
            "--- check_tool.py (skeleton) ---",
            skeleton["check_tool.py"],
        ]
    )


# --- models ---------------------------------------------------------------------------------------


def load_responses(path: Path) -> list[dict[str, Any]]:
    """A fixture is one response, or {responses: [...]}; a response may be {base, override}."""

    def resolve(item: dict[str, Any]) -> dict[str, Any]:
        if "base" not in item:
            return item
        base = yaml.safe_load((path.parent / item["base"]).read_text(encoding="utf-8"))
        return {**resolve(base), **item.get("override", {})}

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = data["responses"] if "responses" in data else [data]
    return [resolve(i) for i in items]


class MockModel:
    """Replays scripted responses as the output-tool call; the last one repeats."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls = 0

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        payload = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    def model(self) -> FunctionModel:
        return FunctionModel(self, model_name="mock-sbam-codegen")


def bifrost_model() -> Model:
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(
        base_url=os.environ["BIFROST_URL"], api_key=os.environ.get("BIFROST_API_KEY", "none")
    )
    return OpenAIChatModel(ALIAS, provider=provider)


def generate(model: Model, ctx: dict[str, Any], skeleton: dict[str, str]) -> CodeBodies:
    """One structured call; validation failures are re-asked up to RETRIES times."""
    agent = Agent(model, output_type=CodeBodies, instructions=INSTRUCTIONS, retries=RETRIES)
    return agent.run_sync(build_prompt(ctx, skeleton)).output
