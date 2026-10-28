#!/usr/bin/env bash
# E1.23: should compose-smoke (13–28 min, the full Docker stack) run for this CI event?
#
#   pull request           no (a job skipped by `if:` still satisfies a required check)
#   push to dev            every SMOKE_EVERY-th first-parent commit, or when the push touches the stack
#                          (judged since the last dev run that wasn't cancelled: a queued run cancelled by
#                          a newer push leaves its changes to the next one, #375)
#   push to main / a tag   yes
#   nightly, manual run    yes
#
# Writes run=true|false to $GITHUB_OUTPUT and the reason to the job summary (stdout when run locally).
# Dry run over dev history: EVENT=push REF=refs/heads/dev BEFORE=<sha^> scripts/ci-smoke-gate.sh <sha>
# (with GH_TOKEN and GITHUB_REPOSITORY set it also looks up cancelled runs, as in CI).
set -euo pipefail

sha=${1:-HEAD}
every=${SMOKE_EVERY:-10}
# What the stack test exists to catch: compose/realm/seed, migrations, images, dependency locks, CI.
# Markdown under these paths is ignored.
risky='^(compose/|server/api/sbam_api/migrations/|Makefile$|\.github/|(.*/)?[^/]*Dockerfile[^/]*$|(.*/)?pyproject\.toml$|(.*/)?uv\.lock$|(.*/)?pnpm-lock\.yaml$)'

# The newest dev commit an earlier, not-cancelled `ci` push run covered (in progress counts): a strict
# ancestor of $1. Empty without gh/GH_TOKEN or when none is found; the caller then uses BEFORE.
covered_base() {
  [ -n "${GH_TOKEN:-}" ] && command -v gh >/dev/null || return 0
  local head candidate
  local query=".[] | select(.databaseId != ${GITHUB_RUN_ID:-0} and .conclusion != \"cancelled\") | .headSha"
  head=$(git rev-parse "$1")
  # newest first; `|| true`: an API hiccup falls back to BEFORE, today's behaviour
  for candidate in $(gh run list -R "${GITHUB_REPOSITORY:?}" --workflow ci --branch dev \
    --event push --limit 50 --json databaseId,headSha,conclusion --jq "$query" 2>/dev/null || true); do
    if [ "$candidate" != "$head" ] && git merge-base --is-ancestor "$candidate" "$head" 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
}

case "${EVENT:?}" in
  pull_request)
    run=false
    reason="pull request; runs after merge (every ${every}th dev merge, stack changes, nightly)" ;;
  push)
    if [ "${REF:?}" != refs/heads/dev ]; then
      run=true reason="push to ${REF#refs/*/}"
    else
      n=$(git rev-list --count --first-parent "$sha")
      base=$(covered_base "$sha")
      since=""
      if [ -n "$base" ] && [ "$base" != "$(git rev-parse "${BEFORE:-}^{commit}" 2>/dev/null)" ]; then
        since=" (since ${base:0:7}, the last dev run not cancelled)"
      fi
      base=${base:-${BEFORE:-}}
      if [ -n "$base" ] && [[ ! $base =~ ^0+$ ]] && git cat-file -e "$base^{commit}" 2>/dev/null; then
        stack=$(git diff --name-only "$base" "$sha" | grep -E "$risky" | grep -v '\.md$' | head -5 |
          paste -sd, - || true)
        m=$(git rev-list --count --first-parent "$base")
      else
        stack="unknown range" # new branch or rewritten history: be safe
        m=$(( n - 1 ))
      fi
      if [ -n "$stack" ]; then
        run=true reason="dev merge #$n touches the stack$since: $stack"
      elif (( n / every > m / every )); then # a multiple of SMOKE_EVERY in (m, n]
        run=true reason="dev merge #$n (every ${every}th$since)"
      else
        run=false reason="dev merge #$n; next scheduled at #$(( (n / every + 1) * every )), on a stack change, or nightly"
      fi
    fi ;;
  *)
    run=true reason="$EVENT" ;;
esac

echo "run=$run" >> "${GITHUB_OUTPUT:-/dev/stdout}"
verdict=$([ "$run" = true ] && echo "runs" || echo "skipped")
echo "compose-smoke $verdict: $reason" >> "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
