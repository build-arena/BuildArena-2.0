# BuildArena one-command setup bootstrap / 一键配置引导脚本
#
# This thin bootstrap does the machine-side chores (Steps 0-2) and then hands
# off to scripts/setup.py, which auto-detects the game, fills in .env, imports
# the collider dump, and writes mcp.json — stopping with clear bilingual
# instructions whenever a human step is required.
#
# 这是一层很薄的引导脚本：它完成机器侧的准备（第 0-2 步），然后交给
# scripts/setup.py 去自动探测游戏、填 .env、导入碰撞数据、生成 mcp.json；
# 遇到必须人工的步骤会用双语提示停下来。
#
# Usage / 用法（在仓库根目录 PowerShell 运行）:
#   powershell -ExecutionPolicy ByPass -File scripts\setup.ps1
#   powershell -ExecutionPolicy ByPass -File scripts\setup.ps1 -BesiegeData "D:\Games\Besiege\Besiege_Data"

[CmdletBinding()]
param(
    [string]$BesiegeData = "",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

# Make the console UTF-8 so bilingual (CJK + emoji) output renders correctly.
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
chcp 65001 > $null

# Always operate from the repository root (this script lives in scripts/).
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $RepoRoot

Write-Host "================================================================"
Write-Host "  BuildArena 一键配置 / one-command setup"
Write-Host "  仓库 / repo: $RepoRoot"
Write-Host "================================================================"

# ── Step 1 — ensure uv is installed / 确保安装 uv ──────────────────────
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[uv] 未检测到 uv，正在安装 / uv not found, installing..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

    # Make uv visible in the current session without needing a new shell.
    $uvBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path $uvBin) {
        $env:Path = "$uvBin;$env:Path"
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Error "uv 安装后仍不可用，请打开新的终端后重跑本脚本。 / uv is still unavailable; open a new terminal and re-run."
        exit 1
    }
}
Write-Host "[uv] $(uv --version)"

# ── Step 2 — sync dependencies / 同步依赖 ──────────────────────────────
Write-Host "[deps] uv sync ..."
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Error "uv sync 失败 / uv sync failed."
    exit $LASTEXITCODE
}

# ── Hand off to the Python orchestrator / 交给 Python 编排器 ───────────
$SetupPy = Join-Path $PSScriptRoot "setup.py"
$setupArgs = @("run", "python", $SetupPy)
if ($BesiegeData -ne "") {
    $setupArgs += @("--besiege-data", $BesiegeData)
}
if ($NonInteractive) {
    $setupArgs += "--non-interactive"
}

uv @setupArgs
$setupExit = $LASTEXITCODE

Write-Host ""
if ($setupExit -eq 0) {
    Write-Host "================================================================"
    Write-Host "  全部就绪 / all set — 进行 README 第 8 步的目视检查"
    Write-Host "  next: visual check in README Step 8"
    Write-Host "================================================================"
} else {
    Write-Host "================================================================"
    Write-Host "  还差人工步骤 / a human step is still needed — 见上方提示后重跑"
    Write-Host "  see the instruction above, do it, then re-run this script"
    Write-Host "================================================================"
}

exit $setupExit
