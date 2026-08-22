# One-command bootstrap for Windows: create a venv if missing, install the
# package, and serve. Any extra arguments are passed straight through to
# `omniagentos serve` (e.g. .\start.ps1 --port 9000, .\start.ps1 --host 0.0.0.0).
$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "==> Creating virtual environment in .venv"
    python -m venv .venv
}

Write-Host "==> Installing OmniAgentOS Starter (editable)"
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\pip.exe install --quiet -e .

Write-Host "==> Starting OmniAgentOS Starter"
& .\.venv\Scripts\omniagentos.exe serve --open @args
