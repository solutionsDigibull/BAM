"""#735: the Sub-Skill's newest build is found when Temporal's retention removed the older ones."""

import asyncio
import uuid
from typing import Any

from sbam_api.builds.service import latest_build_id
from sbam_contracts.runs import build_workflow_id
from temporalio.service import RPCError, RPCStatusCode

SUB = uuid.UUID("01a1a551-8b81-71f2-9a6e-8af1aedcc49b")


class Handle:
    def __init__(self, client: "Client", workflow_id: str) -> None:
        self.client, self.workflow_id = client, workflow_id

    async def describe(self, **_: Any) -> object:
        self.client.probed.append(self.workflow_id)
        if self.workflow_id not in self.client.existing:
            raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")
        return object()


class Client:
    def __init__(self, *build_nos: int) -> None:
        self.existing = {build_workflow_id(SUB, n) for n in build_nos}
        self.probed: list[str] = []

    def get_workflow_handle(self, workflow_id: str) -> Handle:
        return Handle(self, workflow_id)


def newest(client: Client) -> str | None:
    return asyncio.run(latest_build_id(client, SUB))  # type: ignore[arg-type]


def test_735_t06_build_1_removed_by_retention_still_finds_build_2() -> None:
    client = Client(2)
    assert newest(client) == build_workflow_id(SUB, 2)
    assert client.probed == [build_workflow_id(SUB, n) for n in (1, 2, 3)]  # stops after the gap


def test_735_t06_contiguous_builds_give_the_last_one() -> None:
    assert newest(Client(1, 2, 3)) == build_workflow_id(SUB, 3)


def test_735_t07_no_build_at_all_is_none() -> None:
    assert newest(Client()) is None
