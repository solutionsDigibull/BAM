#!/usr/bin/env bash
# E1.23: should compose-smoke (13–28 min, the full Docker stack) run for this CI event?
#
#   pull request           no (a job skipped by `if:` still satisfies a required check)
#   push to dev            every SMOKE_EVERY-th first-parent commit, or when the push touches the stack
#   push to main / a tag   yes
#   nightly, manual run    yes
#
# Writes run=true|false to $GITHUB_OUTPUT and the reason to the job summary (stdout when run locally).
# Dry run over dev history: EVENT=push REF=refs/heads/dev BEFORE=<sha^> scripts/ci-smoke-gate.sh <sha>
set -euo pipefail

sha=${1:-HEAD}
every=${SMOKE_EVERY:-10}
# What the stack test exists to catch: compose/realm/seed, migrations, images, dependency locks, CI.
# Markdown under these paths is ignored.
risky='^(compose/|server/api/sbam_api/migrations/|Makefile$|\.github/|(.*/)?[^/]*Dockerfile[^/]*$|(.*/)?pyproject\.toml$|(.*/)?uv\.lock$|(.*/)?pnpm-lock\.yaml$)'

case "${EVENT:?}" in
  pull_request)
    run=false
    reason="pull request; runs after merge (every ${every}th dev merge, stack changes, nightly)" ;;
  push)
    if [ "${REF:?}" != refs/heads/dev ]; then
      run=true reason="push to ${REF#refs/*/}"
    else
      n=$(git rev-list --count --first-parent "$sha")
      if [ -n "${BEFORE:-}" ] && [[ ! $BEFORE =~ ^0+$ ]] && git cat-file -e "$BEFORE^{commit}" 2>/dev/null; then
        stack=$(git diff --name-only "$BEFORE" "$sha" | grep -E "$risky" | grep -v '\.md$' | head -5 |
          paste -sd, - || true)
      else
        stack="unknown range" # new branch or rewritten history: be safe
      fi
      if [ -n "$stack" ]; then
        run=true reason="dev merge #$n touches the stack: $stack"
      elif (( n % every == 0 )); then
        run=true reason="dev merge #$n (every ${every}th)"
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
