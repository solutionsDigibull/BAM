"""Make `codegen` importable and share paths. Run from server/ (see spikes/codegen/Makefile)."""

from __future__ import annotations

import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
REPO = SPIKE.parents[1]
sys.path.insert(0, str(SPIKE))

MS01 = REPO / "server" / "tests" / "fixtures" / "isa14" / "ms01_1_manifest.json"
SK_BOM = SPIKE / "fixtures" / "sk_bom_scrub_manifest.json"
BODIES = SPIKE / "fixtures" / "mock_bodies"
