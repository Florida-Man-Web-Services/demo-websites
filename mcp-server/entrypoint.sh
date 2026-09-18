#!/bin/sh
# Start demo-mcp. Never print GH_TOKEN / GITHUB_TOKEN.
set -eu

REPO="${SITE_PR_GITHUB_REPO:-Florida-Man-Web-Services/demo-websites}"
ROOT="${SITE_PR_REPO_ROOT:-/data/demo-websites}"
export SITE_PR_REPO_ROOT="$ROOT"
export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-FMWS sitepr}"
export GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-sitepr@floridamanweb.online}"
export GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$GIT_AUTHOR_NAME}"
export GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$GIT_AUTHOR_EMAIL}"

if [ -n "${GH_TOKEN:-}" ] && [ -z "${GITHUB_TOKEN:-}" ]; then
  export GITHUB_TOKEN="$GH_TOKEN"
fi

_flag_on() {
  v=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
  [ "$v" = "1" ] || [ "$v" = "true" ] || [ "$v" = "yes" ] || [ "$v" = "on" ]
}

if command -v gh >/dev/null 2>&1 && { [ -n "${GH_TOKEN:-}" ] || [ -n "${GITHUB_TOKEN:-}" ]; }; then
  gh auth setup-git >/dev/null 2>&1 || true
fi

if _flag_on "${SITE_PR_ENABLED:-}" && command -v git >/dev/null 2>&1; then
  git config --global --add safe.directory "$ROOT" >/dev/null 2>&1 || true
  mkdir -p "$(dirname "$ROOT")"
  if [ ! -d "$ROOT/.git" ]; then
    git clone --depth 1 "https://github.com/${REPO}.git" "$ROOT" >/dev/null 2>&1 || \
      echo "sitepr: clone failed; MCP will start, publishes will fail closed" >&2
  else
    git -C "$ROOT" remote set-url origin "https://github.com/${REPO}.git" >/dev/null 2>&1 || true
    git -C "$ROOT" fetch --depth 1 origin main >/dev/null 2>&1 || true
  fi
fi

exec python mcp-server/server.py
