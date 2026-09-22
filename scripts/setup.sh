#!/usr/bin/env bash
# BuildArena one-command setup for macOS and Linux.
# Windows keeps scripts/setup.ps1.
#
# Usage from the repository root:
#   bash scripts/setup.sh
#   bash scripts/setup.sh --besiege-data "/path/to/Besiege_Data"
#   bash scripts/setup.sh --non-interactive
# On macOS, --besiege-data points at Besiege.app/Contents.

set -u

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

echo "================================================================"
echo "  BuildArena 一键配置 / one-command setup"
echo "  仓库 / repo: $repo_root"
echo "================================================================"

if ! command -v uv >/dev/null 2>&1; then
    echo "[uv] 未检测到 uv，正在安装 / uv not found, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv 安装后仍不可用，请打开新的终端后重跑本脚本。 / uv is still unavailable; open a new terminal and re-run." >&2
    exit 1
fi
echo "[uv] $(uv --version)"

echo "[deps] uv sync ..."
uv sync
sync_exit=$?
if [[ "$sync_exit" -ne 0 ]]; then
    echo "uv sync 失败 / uv sync failed." >&2
    exit "$sync_exit"
fi

uv run python scripts/setup.py "$@"
setup_exit=$?

echo
if [[ "$setup_exit" -eq 0 ]]; then
    echo "================================================================"
    echo "  全部就绪 / all set"
    echo "================================================================"
elif [[ "$setup_exit" -eq 2 ]]; then
    echo "================================================================"
    echo "  需要购买 DLC 或订阅 Workshop / DLC or Workshop subscription required"
    echo "  见上方提示后重跑 / do that step, then re-run this script"
    echo "================================================================"
else
    echo "================================================================"
    echo "  自动配置失败 / automated setup failed — 见上方错误"
    echo "================================================================"
fi

exit "$setup_exit"
