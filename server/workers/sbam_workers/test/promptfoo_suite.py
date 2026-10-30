"""The `build_promptfoo_suite` build stage (E13.6, TD §15.1): this build's suite, built and checked.

It loads the Sub-Skill as the corrections left it, the newest test dataset and its cases, and builds
the Promptfoo suite from them (`suite.build_suite`): the manifest's output schema and V bounds, the
deterministic assertions for every case, and the assertions the cases carry. The suite is a pure
function of those inputs, so this stage stores nothing: it proves that a suite can be built (and
records its `suite_sha256` in the log) before the sandbox spends a run on it, and the run stage
builds the same suite again and gets the same hash.

A suite that cannot be built from stored data (an assertion type the suite does not know, which the
API and the testgen check refuse, so it means the row was written another way) is `SuiteInvalid`, a
platform defect: BUILD_ERROR for an operator, never a correction. No test data at all is the same.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sbam_api.db.repository import TenantContext
from sbam_api.testgen.repository import CaseRepository, DatasetRepository
from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import IdResult
from sbam_contracts.promptfoo import suite_sha256
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.build.gates.errors import SUITE_INVALID
from sbam_workers.test.suite import SuiteBuildError, build_suite

STAGE = "build_promptfoo_suite"


def _invalid(message: str) -> ApplicationError:
    return ApplicationError(message, type=SUITE_INVALID, non_retryable=True)


class BuildPromptfooSuite:
    """`build_promptfoo_suite` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        isatvon_of: Callable[[BuildStageInput], Awaitable[Mapping[str, Any]]],
        sessions: sessionmaker[Session],
    ) -> None:
        self.isatvon_of = isatvon_of
        self.sessions = sessions

    def _build(self, inp: BuildStageInput, isatvon: Mapping[str, Any]) -> uuid.UUID:
        ctx = TenantContext(inp.tenant_id, inp.project_id)
        with self.sessions() as session:
            dataset = DatasetRepository(session, ctx).latest(inp.sub_skill_id)
            if dataset is None:
                raise _invalid("the Sub-Skill has no test data to build a suite from")
            cases = [
                {
                    "name": row.name,
                    "kind": row.kind,
                    "input": row.input,
                    "expected_terminal": row.expected_terminal,
                    "expected_output": row.expected_output,
                    "assertions": row.assertions,
                }
                for row in CaseRepository(session, ctx).of_dataset(dataset.id)
            ]
            dataset_id = dataset.id
        try:
            suite = build_suite(isatvon, cases)
        except (SuiteBuildError, ValueError) as exc:  # pydantic's ValidationError is a ValueError
            raise _invalid(f"the suite cannot be built: {type(exc).__name__}") from None
        activity.logger.info(
            "promptfoo suite built",
            extra={
                "suite_sha256": suite_sha256(suite),
                "cases": len(suite.cases),
                "dataset_id": str(dataset_id),
                "tenant_id": str(inp.tenant_id),
                "project_id": str(inp.project_id),
                "workflow_id": str(inp.workflow_id),
                "sub_skill_id": str(inp.sub_skill_id),
                "correlation_id": inp.correlation_id,
            },
        )
        return dataset_id

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        isatvon = await self.isatvon_of(inp)
        return IdResult(id=await asyncio.to_thread(self._build, inp, isatvon))
