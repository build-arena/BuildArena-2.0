# BuildArena one-command setup bootstrap / 一键配置引导脚本
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

$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
chcp 65001 > $null

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $RepoRoot

Write-Host "================================================================"
Write-Host "  BuildArena 一键配置 / one-command setup"
Write-Host "  仓库 / repo: $RepoRoot"
Write-Host "================================================================"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[uv] 未检测到 uv，正在安装 / uv not found, installing..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

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

Write-Host "[deps] uv sync ..."
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Error "uv sync 失败 / uv sync failed."
    exit $LASTEXITCODE
}

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
    Write-Host "  全部就绪 / all set"
    Write-Host "================================================================"
} elseif ($setupExit -eq 2) {
    Write-Host "================================================================"
    Write-Host "  需要购买 DLC 或订阅 Workshop / DLC or Workshop subscription required"
    Write-Host "  见上方提示后重跑 / do that step, then re-run this script"
    Write-Host "================================================================"
} else {
    Write-Host "================================================================"
    Write-Host "  自动配置失败 / automated setup failed — 见上方错误"
    Write-Host "================================================================"
}

exit $setupExit
