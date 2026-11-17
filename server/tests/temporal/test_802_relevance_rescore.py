"""#802: `RelevanceRescore` scores every parsed document of a project against its current goal; a
document that cannot be scored is skipped and the others go on."""

import asyncio
import uuid
from dataclasses import dataclass, field

from sbam_api.core.ids import uuid7
from sbam_contracts.pipeline import IdsResult, StageRef
from sbam_contracts.runs import RunInput, TaskQueue
from sbam_workers.ingest.relevance_rescore import RelevanceRescore
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

QUEUE = TaskQueue.INGEST.value


@dataclass
class Fakes:
    ids: list[uuid.UUID] = field(default_factory=list)
    failing: set[uuid.UUID] = field(default_factory=set)
    calls: list[uuid.UUID] = field(default_factory=list)


def run(fakes: Fakes) -> list[uuid.UUID]:
    @activity.defn(name="parsed_documents")
    async def parsed_documents(inp: RunInput) -> IdsResult:
        return IdsResult(ids=fakes.ids)

    @activity.defn(name="score_relevance")
    async def score_relevance(inp: StageRef) -> IdsResult:
        fakes.calls.append(inp.ref_id)
        if inp.ref_id in fakes.failing:
            raise ConnectionError("Bifrost is down")
        return IdsResult(ids=[inp.ref_id])

    inp = RunInput(
        tenant_id=uuid.uuid4(), project_id=uuid.uuid4(), run_id=uuid7(), correlation_id="t802"
    )

    async def scenario() -> list[uuid.UUID]:
        env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
        async with env:
            async with Worker(
                env.client,
                task_queue=QUEUE,
                workflows=[RelevanceRescore],
                activities=[parsed_documents, score_relevance],
            ):
                result = await env.client.execute_workflow(
                    RelevanceRescore.run, inp, id=f"relevance-{inp.project_id}", task_queue=QUEUE
                )
                return result.ids

    return asyncio.run(scenario())


def test_802_t20_every_parsed_document_is_scored() -> None:
    ids = [uuid7(), uuid7(), uuid7()]
    fakes = Fakes(ids=ids)
    assert run(fakes) == ids
    assert fakes.calls == ids


def test_802_t21_a_document_that_cannot_be_scored_is_skipped() -> None:
    ids = [uuid7(), uuid7(), uuid7()]
    fakes = Fakes(ids=ids, failing={ids[1]})
    assert run(fakes) == [ids[0], ids[2]]
    assert fakes.calls.count(ids[1]) == 2  # retried once, then left unscored


def test_802_t22_no_parsed_documents_scores_nothing() -> None:
    fakes = Fakes()
    assert run(fakes) == [] and fakes.calls == []
