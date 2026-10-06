#!/usr/bin/env python3
"""Container HEALTHCHECK for sbam-api and sbam-worker images (stdlib only).

api:    GET /health/live answers 200 (Compose additionally gates dependants on /health/ready).
worker: the heartbeat file was touched in the last 30 s.
"""

import os
import sys
import time
import urllib.request

HEARTBEAT = "/tmp/sbam/heartbeat"  # noqa: S108 - container-private dir owned by uid 10001
MAX_AGE_SECONDS = 30


def main() -> int:
    if os.environ.get("SBAM_ROLE") == "api":
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health/live", timeout=3) as r:
                return 0 if r.status == 200 else 1
        except OSError:
            return 1
    try:
        return 0 if time.time() - os.path.getmtime(HEARTBEAT) < MAX_AGE_SECONDS else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
