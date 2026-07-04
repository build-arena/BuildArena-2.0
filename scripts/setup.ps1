# BuildArena one-command setup bootstrap.
#
# Usage from the repository root:
#   powershell -ExecutionPolicy ByPass -File scripts\setup.ps1
#   powershell -ExecutionPolicy ByPass -File scripts\setup.ps1 -BesiegeData "D:\Games\Besiege\Besiege_Data"

[CmdletBinding()]
param(
    [string]$BesiegeData = "",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

# Make console output UTF-8 friendly.
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
chcp 65001 > $null

# Always operate from the repository root.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $RepoRoot

Write-Host "================================================================"
Write-Host "  BuildArena one-command setup"
Write-Host "  repo: $RepoRoot"
Write-Host "================================================================"

# Step 1: ensure uv is installed.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[uv] uv not found, installing..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

    $uvBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path $uvBin) {
        $env:Path = "$uvBin;$env:Path"
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Error "uv is still unavailable; open a new terminal and re-run this script."
        exit 1
    }
}
Write-Host "[uv] $(uv --version)"

# Step 2: sync dependencies.
Write-Host "[deps] uv sync ..."
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Error "uv sync failed."
    exit $LASTEXITCODE
}

# Hand off to the Python orchestrator.
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
    Write-Host "  all set - next: visual check in README Step 8"
    Write-Host "================================================================"
} else {
    Write-Host "================================================================"
    Write-Host "  a human step is still needed - see the instruction above, then re-run"
    Write-Host "================================================================"
}

exit $setupExit
