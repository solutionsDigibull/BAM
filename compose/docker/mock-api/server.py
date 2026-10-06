#!/usr/bin/env python3
"""sbam-mock-api stub: answers GET /health and 404 problem+json for everything else (stdlib only)."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    server_version = "sbam-mock-api"
    sys_version = ""

    def _send(self, status: int, body: dict[str, object], content_type: str) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path == "/health":
            self._send(200, {"status": "ok"}, "application/json")
        else:
            problem = {"type": "about:blank", "title": "Not found", "status": 404, "code": "NOT_FOUND"}
            self._send(404, problem, "application/problem+json")

    def log_message(self, fmt: str, *args: object) -> None:
        print(json.dumps({"level": "INFO", "logger": "sbam_mock_api", "msg": fmt % args}), flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()  # noqa: S104 (container port)
