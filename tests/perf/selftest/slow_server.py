"""E15.4-T06 (#138) self-test target: a local stand-in for the SBAM API that answers every
`GET /api/v1/...` after a fixed delay, so `run_t06.sh` can prove the k6 thresholds fail the run.

Python stdlib only. Binds to 127.0.0.1. Synthetic data only.

    python tests/perf/selftest/slow_server.py --port 8765 --latency-ms 800
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def tree(sub_skills: int) -> dict[str, object]:
    """A WorkbenchTree-shaped body: `macros[].sub_skills[]`, 50 Sub-Skills per Macro Skill."""
    macros = []
    for m in range(0, sub_skills, 50):
        subs = [{"id": str(uuid.uuid4())} for _ in range(min(50, sub_skills - m))]
        macros.append({"id": str(uuid.uuid4()), "sub_skills": subs})
    return {"macros": macros}


def handler(latency_s: float, sub_skills: int) -> type[BaseHTTPRequestHandler]:
    class Slow(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            time.sleep(latency_s)
            if not self.path.startswith("/api/v1/"):
                return self.reply(404, {"title": "Not Found", "status": 404})
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                return self.reply(401, {"title": "Unauthorized", "status": 401})
            body: object = {"items": []}
            if self.path.split("?")[0].endswith("/macro-skills"):
                body = tree(sub_skills)
            self.reply(200, body)

        def reply(self, status: int, body: object) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_: object) -> None:  # keep k6 output readable
            pass

    return Slow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--latency-ms", type=int, default=800)
    parser.add_argument("--sub-skills", type=int, default=500)
    args = parser.parse_args()
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), handler(args.latency_ms / 1000, args.sub_skills)
    )
    print(f"slow_server: 127.0.0.1:{args.port} latency={args.latency_ms}ms", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
