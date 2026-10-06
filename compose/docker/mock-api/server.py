#!/usr/bin/env python3
"""sbam-mock-api stub: GET /health answers 200; everything else is a 404 problem+json (stdlib)."""

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

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"}, "application/json")
        else:
            problem = {
                "type": "about:blank",
                "title": "Not found",
                "status": 404,
                "code": "NOT_FOUND",
            }
            self._send(404, problem, "application/problem+json")

    def log_message(self, fmt: str, *args: object) -> None:
        line = {"level": "INFO", "logger": "sbam_mock_api", "msg": fmt % args}
        print(json.dumps(line), flush=True)


HOST = "0.0.0.0"  # noqa: S104 - container port, internal network only

if __name__ == "__main__":
    ThreadingHTTPServer((HOST, 8090), Handler).serve_forever()
