#!/usr/bin/env python3
# ruff: noqa: E501, S310, S104
"""Recording proxy for the LLM mock fixtures (#914, WG-33). Stdlib only.

It sits between the workers and Bifrost, forwards every `/v1/chat/completions` call unchanged and writes the
prompt and the answer as a fixture the mock (`compose/docker/llm-mock/server.py`) can replay for free:

    <LLM_RECORD_DIR>/<alias>/<prompt_id>_<hash12>.json   (same key the mock uses: alias + prompt_id + input hash)

Recording is the only paid step, so it is OFF by default and refuses to start unless BOTH are set:
    LLM_RECORD=1                   explicit opt-in
    LLM_RECORD_SPEND_CAP_USD=<n>   hard cap; once the cost Bifrost reports for the recorded calls reaches it, every
                                   further call is refused with HTTP 402 (the proxy never retries by itself)
Optional: LLM_RECORD_UPSTREAM (default http://bifrost:8080), LLM_RECORD_DIR (default /record),
LLM_RECORD_PORT (default 8099).

A fixture holds the messages and the model's text, never headers, keys or the request metadata (tenant and project
ids). A re-ask appends turns to the original prompt; the mock finds the fixture by the ORIGINAL prompt, so the
answer of the deepest re-ask replaces the earlier ones of that prompt.
"""

import hashlib
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# WG-44 (#932): the one place a document id enters a prompt is the header of a rendered chunk or entity,
# `[chunk <ref> doc=<uuid> <where>]` (sow/context.py). The same documents uploaded twice get new ids, so the key
# hashes those headers with the ids replaced. Strict on purpose: only the `doc=` value (and a ref that is itself
# a uuid) inside such a header; a uuid anywhere else in a prompt still changes the hash.
DOC_HEADER = re.compile(
    rf"(\[(?:chunk|entity) )(?:{UUID}|[A-Za-z0-9_.:-]+)( doc=){UUID}(?=[ \]])"
)


def normalise_ids(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        ref = m.group(0)[len(m.group(1)) :].split(" doc=", 1)[0]
        keep = "<REF_UUID>" if re.fullmatch(UUID, ref) else ref
        return f"{m.group(1)}{keep}{m.group(2)}<DOC_UUID>"

    return DOC_HEADER.sub(repl, text)


def input_hash(messages: Any) -> str:
    """Identical to llm-mock/server.py input_hash (a test pins the two together)."""
    canonical = json.dumps(
        messages, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(normalise_ids(canonical).encode()).hexdigest()


def original_prompt(messages: list[Any]) -> list[Any]:
    reasks = sum(
        1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant"
    )
    return messages[: max(len(messages) - 2 * reasks, 1)]


def reasks_in(messages: list[Any]) -> int:
    return sum(
        1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant"
    )


def check_opt_in(env: dict[str, str]) -> float:
    """The spend cap in USD, or SystemExit: recording never starts by accident or without a cap."""
    if env.get("LLM_RECORD") != "1":
        raise SystemExit(
            "llm_record_proxy: set LLM_RECORD=1 to record (it is off by default)"
        )
    raw = env.get("LLM_RECORD_SPEND_CAP_USD", "")
    try:
        cap = float(raw)
    except ValueError:
        cap = 0.0
    if cap <= 0:
        raise SystemExit(
            "llm_record_proxy: LLM_RECORD_SPEND_CAP_USD must be set to a number above 0"
        )
    return cap


class Recorder:
    def __init__(self, out: Path, cap_usd: float) -> None:
        self.out, self.cap, self.spent = out, cap_usd, 0.0
        self.depth: dict[Path, int] = {}
        self.lock = threading.Lock()

    def over_cap(self) -> bool:
        with self.lock:
            return self.spent >= self.cap

    def record(self, body: dict[str, Any], reply: dict[str, Any]) -> Path | None:
        usage = reply.get("usage") or {}
        with self.lock:
            try:
                self.spent += float(usage.get("cost") or 0)
            except (TypeError, ValueError):
                pass
        alias = str(body.get("model", ""))
        messages = body.get("messages")
        try:
            text = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        if (
            not alias.startswith("sbam-")
            or not isinstance(messages, list)
            or not isinstance(text, str)
        ):
            return None
        prompt_id = str((body.get("metadata") or {}).get("prompt_id", "default"))
        digest = input_hash(original_prompt(messages))
        path = self.out / alias / f"{prompt_id}_{digest[:12]}.json"
        reasks = reasks_in(messages)
        with self.lock:
            if self.depth.get(path, -1) > reasks:
                return path
            self.depth[path] = reasks
        path.parent.mkdir(parents=True, exist_ok=True)
        fixture = {
            "prompt_id": prompt_id,
            "input_hash": digest,
            "messages": original_prompt(messages),
            "valid": True,
            "response_text": text,
            "note": f"#914 recorded live ({reply.get('model', '?')}, re-asks: {reasks}).",
        }
        path.write_text(
            json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return path


def make_handler(rec: Recorder, upstream: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._forward(None)

        def do_POST(self) -> None:
            self._forward(self.rfile.read(int(self.headers.get("Content-Length") or 0)))

        def _forward(self, data: bytes | None) -> None:
            chat = self.path.endswith("/chat/completions")
            if chat and rec.over_cap():
                msg = {
                    "error": {
                        "message": "LLM_RECORD_SPEND_CAP_USD reached",
                        "code": "RECORD_CAP",
                    }
                }
                print(
                    json.dumps({"msg": "cap reached", "spent": round(rec.spent, 4)}),
                    flush=True,
                )
                return self._send(402, json.dumps(msg).encode())
            headers = {
                "Content-Type": self.headers.get("Content-Type", "application/json")
            }
            req = urllib.request.Request(
                upstream + self.path, data=data, headers=headers, method=self.command
            )
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    status, payload = resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                status, payload = exc.code, exc.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                return self._send(
                    502, json.dumps({"error": {"message": str(exc)}}).encode()
                )
            if chat and status == 200 and data:
                try:
                    path = rec.record(json.loads(data), json.loads(payload))
                    print(
                        json.dumps(
                            {
                                "msg": "recorded",
                                "file": path.name if path else None,
                                "spent_reported": round(rec.spent, 4),
                            }
                        ),
                        flush=True,
                    )
                except (ValueError, OSError) as exc:
                    print(
                        json.dumps(
                            {"msg": "record failed", "error": type(exc).__name__}
                        ),
                        flush=True,
                    )
            elif chat:
                print(
                    json.dumps({"msg": "upstream error", "status": status}), flush=True
                )
            self._send(status, payload)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    return Handler


def main() -> None:
    cap = check_opt_in(dict(os.environ))
    rec = Recorder(Path(os.environ.get("LLM_RECORD_DIR", "/record")), cap)
    upstream = os.environ.get("LLM_RECORD_UPSTREAM", "http://bifrost:8080").rstrip("/")
    port = int(os.environ.get("LLM_RECORD_PORT", "8099"))
    print(json.dumps({"msg": "recording", "cap_usd": cap, "port": port}), flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(rec, upstream)).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
