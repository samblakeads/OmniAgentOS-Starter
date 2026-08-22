# One-command bootstrap for Windows: create a venv if missing, install the
# package, and serve. Any extra arguments are passed straight through to
# `omniagentos serve` (e.g. .\start.ps1 --port 9000). Passing --host 0.0.0.0
# requires OMNIAGENTOS_TOKEN to be set first (the server refuses to bind
# off-loopback without one — see SECURITY.md).
$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

# Fail fast with a clear message rather than a wall of pip/setuptools text
# from a `requires-python = ">=3.11"` mismatch discovered mid-install.
$PythonBin = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$PythonCmd = Get-Command $PythonBin -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
    Write-Error "OmniAgentOS Starter needs Python 3.11+. No '$PythonBin' found on PATH."
    exit 1
}
$VersionOk = & $PythonBin -c "import sys; print('OK' if sys.version_info >= (3, 11) else 'OLD')"
if ($VersionOk -ne "OK") {
    $PyVersion = & $PythonBin -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    Write-Error "OmniAgentOS Starter needs Python 3.11+, found $PyVersion ($PythonBin). Install Python 3.11+, or set `$env:PYTHON to its path and re-run."
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "==> Creating virtual environment in .venv"
    & $PythonBin -m venv .venv
}

# Skip the network round-trip on every launch: only (re)install when
# pyproject.toml actually changed since the last successful install (or the
# stamp is missing), unless $env:OMNIAGENTOS_FORCE_INSTALL = "1". This is
# what makes a second .\start.ps1 come up in seconds on flaky stage wifi.
$StampFile = ".venv\.omniagentos-install-stamp"
$CurrentHash = (Get-FileHash -Path "pyproject.toml" -Algorithm SHA256).Hash
$StampHash = if (Test-Path $StampFile) { Get-Content $StampFile -Raw } else { "" }

if ($env:OMNIAGENTOS_FORCE_INSTALL -eq "1" -or $CurrentHash -ne $StampHash.Trim()) {
    Write-Host "==> Installing OmniAgentOS Starter (editable)"
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\pip.exe install --quiet -e .
    Set-Content -Path $StampFile -Value $CurrentHash -NoNewline
} else {
    Write-Host "==> OmniAgentOS Starter already installed and up to date, skipping pip (set `$env:OMNIAGENTOS_FORCE_INSTALL = '1' to force)"
}

Write-Host "==> Starting OmniAgentOS Starter"
& .\.venv\Scripts\omniagentos.exe serve --open @args
