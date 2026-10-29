"""Runs INSIDE the sbam-mock-api container (stdlib only): `compose exec -T sbam-mock-api python -c`.

Drives the real service over 127.0.0.1 with its own control token, prints one JSON object of results
for `test_e13_5_mock_api_stack.py` to assert on. Not a test module (no `test_` prefix).
"""

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:8090"
TOKEN = (
    os.environ.get("MOCK_CONTROL_TOKEN")
    or open(os.environ["MOCK_CONTROL_TOKEN_FILE"], encoding="utf-8").read().strip()
)
A, B, C = "run-integ-aaaa-0001", "run-integ-bbbb-0002", "run-integ-cccc-0003"


def call(
    method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(  # noqa: S310 - 127.0.0.1
        BASE + path, data=data, method=method, headers=headers or {}
    )
    try:
        res = urllib.request.urlopen(req, timeout=5)  # noqa: S310
    except urllib.error.HTTPError as err:
        res = err
    raw = res.read()
    return res.status, (json.loads(raw) if raw else None)


def control(method: str, run: str, body: Any = None, journal: bool = False) -> tuple[int, Any]:
    path = f"/_control/runs/{run}" + ("/journal" if journal else "")
    return call(method, path, body, {"Authorization": "Bearer " + TOKEN})


def tool(run: str, path: str) -> tuple[int, Any]:
    return call("GET", path, None, {"X-Sbam-Run-Id": run})


def setup(first: int) -> dict[str, Any]:
    responses = [{"status": first, "body": {"n": first}}, {"status": 200, "body": {"ok": True}}]
    stub = {"id": "s1", "method": "GET", "path": "/crm/accounts/{id}", "responses": responses}
    return {"definitions": [{"name": "crm", "stubs": [stub]}]}


out: dict[str, Any] = {"health": call("GET", "/health")[0]}
out["put_a"], out["put_b"] = control("PUT", A, setup(503))[0], control("PUT", B, setup(429))[0]
out["t01"] = tool(A, "/crm/accounts/42")
out["t03"] = tool(A, "/crm/unknown")[1]["code"]
seen: dict[str, list[int]] = {A: [], B: []}


def go(run: str) -> None:
    for _ in range(3):
        seen[run].append(tool(run, "/crm/accounts/1")[0])


threads = [threading.Thread(target=go, args=(r,)) for r in (A, B)]
for t in threads:
    t.start()
for t in threads:
    t.join()
out["t05"] = seen
out["journal_a"] = [
    (e["stub_id"], e["status"]) for e in control("GET", A, journal=True)[1]["entries"]
]
out["no_token"] = call("GET", f"/_control/runs/{A}/journal", None, {"X-Sbam-Run-Id": A})[0]
forwarding = {
    "definitions": [
        {
            "name": "x",
            "stubs": [
                {
                    "id": "p",
                    "method": "GET",
                    "path": "/x",
                    "proxy_base_url": "https://api.realcrm.com",
                    "responses": [{"status": 200}],
                }
            ],
        }
    ]
}
out["bad_stub"] = control("PUT", C, forwarding)[0]
out["cleanup"] = [control("DELETE", r)[0] for r in (A, B)]
print(json.dumps(out))
