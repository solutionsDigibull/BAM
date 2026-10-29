"""Mock definitions for a Sub-Skill's API references (E13.5, TD §15.2).

`mock_definitions` turns each `S.external_data` entry of kind API into a `MockDefinition` for
`sbam-mock-api`: the route the reference names (`GET /crm/accounts/{id}`) answered 200 for every
case, 503 then 200 for a PASS_AFTER_RETRY case (the first pass fails, the second succeeds) and a
persistent 500 for an ESCALATION case (the check never passes, so the loop runs to its bound).

Nothing here is a model's output: the routes come from the reference text, the bodies are a fixed
marker, and the contract (`sbam_contracts.mocks`) refuses anything that could forward or record. A
reference that names no route gets no stub (the tool's call then answers MOCK_UNMATCHED and the
journal says so) rather than a guessed one. `MockApiClient` is the worker's side of the service's
control protocol, for the sandbox adapter to install a run's stubs, read its journal and remove it.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

import httpx
from sbam_contracts.canonical import canonical_json
from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.mocks import (
    CASE_HEADER,
    RUN_HEADER,
    MockDefinition,
    MockJournal,
    MockRunSetup,
)
from sbam_contracts.testgen import CaseBody, CaseKind

ROUTE: Final = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9._~!$&'()*+,;=:@%/{}-]*)", re.IGNORECASE
)
OK_BODY: Final = {"mock": True}
CONTROL_TIMEOUT: Final = 10.0


def route_of(ref: str) -> tuple[str, str] | None:
    """`(method, path)` for the first `METHOD /path` in an API reference, or None."""
    found = ROUTE.search(ref)
    return None if found is None else (found.group(1).upper(), found.group(2))


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:40] or "api"


def mock_definitions(
    isatvon: Mapping[str, Any], cases: Sequence[tuple[CaseKind, CaseBody]]
) -> list[MockDefinition]:
    """One definition per API reference that names a route, in reference order."""
    refs = SubSkillModel.model_validate(isatvon).isatvon_definition.s_source.external_data or []
    ordered = sorted(cases, key=lambda pair: pair[1].name)
    out: list[MockDefinition] = []
    seen_routes: set[tuple[str, str]] = set()
    for index, ref in enumerate(r for r in refs if r.kind.value == "API"):
        route = route_of(ref.ref)
        if route is None or route in seen_routes:  # no route to answer, or already answered
            continue
        seen_routes.add(route)
        method, path = route
        prefix = f"{_slug(ref.name)}-{index}"
        body = {**OK_BODY, "api": ref.name}
        stubs: list[dict[str, Any]] = [
            {
                "id": f"{prefix}-default",
                "method": method,
                "path": path,
                "responses": [{"status": 200, "body": body}],
            }
        ]
        for position, (kind, case) in enumerate(ordered):
            if kind is CaseKind.PASS_AFTER_RETRY:
                responses = [
                    {"status": 503, "body": {"error": "service_unavailable", "api": ref.name}},
                    {"status": 200, "body": body},
                ]
            elif kind is CaseKind.ESCALATION:
                responses = [
                    {"status": 500, "body": {"error": "upstream_failure", "api": ref.name}}
                ]
            else:
                continue
            stubs.append(
                {
                    "id": f"{prefix}-c{position}",
                    "method": method,
                    "path": path,
                    "case": case.name,
                    "responses": responses,
                }
            )
        out.append(MockDefinition.model_validate({"name": ref.name, "stubs": stubs}))
    return out


def definition_bytes(content: Mapping[str, Any]) -> bytes:
    """The stored form of a definition: canonical JSON of the validated model, so the same
    definition is always the same bytes and the same content-addressed key."""
    return canonical_json(MockDefinition.model_validate(content).model_dump(mode="json"))


def definition_hash(content: Mapping[str, Any]) -> str:
    return hashlib.sha256(definition_bytes(content)).hexdigest()


class MockApiClient:
    """The control side of `sbam-mock-api`: install a run's stubs, read its journal, remove it."""

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=CONTROL_TIMEOUT)
        self.headers = {"Authorization": f"Bearer {token}"}

    def _run(self, run_id: str) -> str:
        return f"{self.base}/_control/runs/{run_id}"

    def install(self, run_id: str, definitions: Sequence[MockDefinition]) -> None:
        """Validated here with the contract, and again by the service. Replaces an earlier setup."""
        setup = MockRunSetup(definitions=list(definitions))
        res = self.client.put(
            self._run(run_id),
            headers=self.headers,
            content=json.dumps(setup.model_dump(mode="json")),
        )
        res.raise_for_status()

    def journal(self, run_id: str) -> MockJournal:
        res = self.client.get(f"{self._run(run_id)}/journal", headers=self.headers)
        res.raise_for_status()
        return MockJournal.model_validate(res.json())

    def remove(self, run_id: str) -> None:
        res = self.client.delete(self._run(run_id), headers=self.headers)
        if res.status_code != 404:  # already gone is as good as removed
            res.raise_for_status()


def tool_headers(run_id: str, case: str | None = None) -> dict[str, str]:
    """What the sandbox gives a tool so its calls land in this run's namespace."""
    return {RUN_HEADER: run_id, **({CASE_HEADER: case} if case else {})}
