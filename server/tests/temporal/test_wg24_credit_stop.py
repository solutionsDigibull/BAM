"""#777 (WG-24) on Temporal's time-skipping server: the real ProjectPipeline and `build_version`
(fake Cognee) when the provider has no credit. The refusal is simulated; no model is called."""

from pathlib import Path
from typing import Any

from knowledge_db import World, versions
from litellm.exceptions import BadGatewayError
from pipeline_db import PROJECT_ROW, new_project, state_of
from sbam_workers.llm.meter import StopCause
from test_knowledge_stage import docs, knowledge_world, real
from test_project_pipeline import TENANT, environment, finish, run, start

REFUSAL = '{"error": {"code": "upstream_credentials_exhausted"}}'


def refuse_cognify(world: World) -> None:
    async def cognify(who: Any, dataset: str) -> None:
        world.cognee._call("cognify")
        raise BadGatewayError(message=REFUSAL, model="sbam-knowledge", llm_provider="openai")

    world.cognee.cognify = cognify  # type: ignore[method-assign]


def test_wg24_t01_a_credit_refusal_fails_the_build_once_with_the_named_cause(
    tmp_path: Path,
) -> None:
    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        world = knowledge_world(tmp_path, project)
        docs(world, "RFQ")
        refuse_cognify(world)
        async with environment(real(world)) as env:
            status = await finish(await start(env, sessions, project))
        assert status.last_error == "build_knowledge: ModelCreditExhausted"
        assert world.cognee.calls.count("cognify") == 1  # non-retryable: one attempt, not five
        assert state_of(sessions, PROJECT_ROW, project) == "KNOWLEDGE_FAILED"
        [v1] = versions(world.sessions, project)
        assert (v1.state, v1.error_reason) == ("FAILED", StopCause.CREDIT_EXHAUSTED.value)

    run(scenario)
