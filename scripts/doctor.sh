#!/usr/bin/env bash
# Checks that the tools needed to build SBAM are installed. Run via `make doctor`.
set -u
fail=0

need() { # name, install hint
  if command -v "$1" >/dev/null 2>&1; then
    printf 'ok    %-8s %s\n' "$1" "$("$1" --version 2>&1 | head -n1)"
  else
    printf 'MISS  %-8s install: %s\n' "$1" "$2"
    fail=1
  fi
}

need git  "https://git-scm.com/downloads"
need uv   "https://docs.astral.sh/uv/getting-started/installation/"
need node "Node 22 LTS, https://nodejs.org (or nvm use, see client/.nvmrc)"
need pnpm "corepack enable pnpm"

if command -v node >/dev/null 2>&1; then
  major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" = "22" ] || printf 'warn  node %s found; client/.nvmrc pins 22\n' "$major"
fi

if command -v docker >/dev/null 2>&1; then
  printf 'ok    %-8s %s\n' docker "$(docker --version)"
else
  printf 'warn  docker not found; needed for `make up` (Docker Desktop with WSL2 on Windows)\n'
fi

if [ "$(git config --get core.longpaths)" != "true" ] && [ -n "${WINDIR:-}" ]; then
  printf 'warn  run `git config --global core.longpaths true` to avoid Windows path-length errors\n'
fi

exit "$fail"
