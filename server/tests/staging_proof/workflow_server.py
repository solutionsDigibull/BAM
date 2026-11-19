"""TEST ONLY (WG-31, #908): a stand-in Workflow MCP server for the Staging proof.

It is NOT a generated Workflow server. It answers the three JSON-RPC calls the proof needs on
`POST /mcp` (port 8000), so the real Staging runtime has a real container to start, health-check and
forward to:

* `tools/list`, `initialize`: the tool names the manifest declares.
* `tools/call` `echo`: returns the server's VERSION, so the proof can tell v1 from v2.
* `tools/call` `probe`: fetches `http://<host>/` through the proxy in HTTP_PROXY and reports the
  status code: the egress policy, seen from the inside.

Standard library only: the image is `python:3.12-slim` plus this file.
"""

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

VERSION = os.environ.get("PROOF_VERSION", "unknown")
TOOLS = ["MS01_1__action", "MS01_1__check"]


def probe(host: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"http://{host}/", timeout=8) as answer:
            return {"host": host, "status": answer.status}
    except urllib.error.HTTPError as error:
        return {"host": host, "status": error.code}
    except OSError as error:  # no route, no DNS: the internal network has no way out
        return {"host": host, "status": 0, "error": type(error).__name__}


def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "probe":
        return probe(str(arguments.get("host", "")))
    return {"version": VERSION, "tool": name, "arguments": arguments}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._send(406, {"error": "POST JSON-RPC to /mcp"})  # below 500: the server is up

    def do_POST(self) -> None:
        size = int(self.headers.get("content-length", "0"))
        try:
            request = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            self._send(400, {"error": "bad json"})
            return
        method, params = request.get("method"), request.get("params") or {}
        if method == "tools/list":
            result: dict[str, Any] = {"tools": [{"name": n} for n in TOOLS]}
        elif method == "tools/call":
            result = {"content": [call(str(params.get("name")), params.get("arguments") or {})]}
        else:
            result = {"serverInfo": {"name": "wf_proof", "version": VERSION}}
        self._send(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": result})

    def log_message(self, format: str, *args: Any) -> None:
        return None


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()  # noqa: S104
