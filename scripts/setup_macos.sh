#!/usr/bin/env bash
# BuildArena one-command setup bootstrap for macOS.
#
# Usage from the repository root:
#   bash scripts/setup_macos.sh
#   bash scripts/setup_macos.sh --besiege-data "$HOME/Library/Application Support/Steam/steamapps/common/Besiege/Besiege.app/Contents/Resources/Data"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONUTF8=1

echo "================================================================"
echo "  BuildArena one-command setup for macOS"
echo "  repo: $REPO_ROOT"
echo "================================================================"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This bootstrap is for macOS. On Windows, run scripts\\setup.ps1." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "[uv] uv not found, installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh

  if [[ -d "$HOME/.local/bin" ]]; then
    export PATH="$HOME/.local/bin:$PATH"
  fi

  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is still unavailable; open a new terminal and re-run this script." >&2
    exit 1
  fi
fi

echo "[uv] $(uv --version)"

echo "[deps] uv sync ..."
uv sync

setup_args=(run python "$SCRIPT_DIR/setup.py")
if [[ "$#" -gt 0 ]]; then
  setup_args+=("$@")
fi

set +e
uv "${setup_args[@]}"
setup_exit=$?
set -e

echo ""
if [[ "$setup_exit" -eq 0 ]]; then
  echo "================================================================"
  echo "  all set - next: visual check in README Step 8"
  echo "================================================================"
else
  echo "================================================================"
  echo "  a human step is still needed - see the instruction above, then re-run"
  echo "================================================================"
fi

exit "$setup_exit"
