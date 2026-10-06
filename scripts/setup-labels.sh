#!/usr/bin/env bash
# setup-labels.sh — create the status labels the agent workflow (AGENTS.md, /next-issue, /ship) relies on.
# phase:*, area:*, stream:*, priority:*, size:*, type:* and `blocked` already exist (created with the issues).
# Safe to re-run (--force updates colour/description). Needs gh with triage rights on the repo.
set -euo pipefail
REPO="${REPO:-solutionsDigibull/BAM}"
mk() { gh label create "$1" -R "$REPO" --color "$2" --description "$3" --force >/dev/null && echo "✓ $1"; }

mk "status:in-progress" "FBCA04" "Claimed; someone is working on it (set by /next-issue)"
mk "status:in-review"   "1D76DB" "PR open (set by /ship)"
mk "blocked"            "B60205" "Waiting on an open dependency or a decision"
