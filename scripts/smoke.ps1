# Post-start live-receipt smoke test (Windows PowerShell equivalent of smoke.sh).
#
# Starts a real `omniagentos serve` on an ephemeral port, confirms
# /api/health reports configured:true, submits one tiny live run, polls it
# to completion (done|failed) within 120s, and writes a redacted receipt to
# evidence/live-receipts/smoke-<ts>.json. Exits non-zero on any failure.
#
# This is a LIVE test: it spends real provider credits and requires a real
# key (XAI_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY) in the environment.
# It is never run in CI — see .github/workflows/ci.yml.
#
# Usage: .\scripts\smoke.ps1
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$ReceiptDir = Join-Path $RepoRoot "evidence\live-receipts"
New-Item -ItemType Directory -Force -Path $ReceiptDir | Out-Null

$Ts = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$DataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("omniagentos-smoke-data-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$ReceiptFile = Join-Path $ReceiptDir "smoke-$Ts.json"

$ServerProcess = $null

function Cleanup {
    if ($ServerProcess -and -not $ServerProcess.HasExited) {
        Stop-Process -Id $ServerProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $DataDir
}

function Fail($msg) {
    Write-Error "SMOKE FAIL: $msg"
    Cleanup
    exit 1
}

try {
    # Make sure `omniagentos` is on PATH; bootstrap a venv if this is a bare checkout.
    $omniagentos = Get-Command omniagentos -ErrorAction SilentlyContinue
    if (-not $omniagentos -and (Test-Path (Join-Path $RepoRoot ".venv\Scripts\omniagentos.exe"))) {
        $env:Path = (Join-Path $RepoRoot ".venv\Scripts") + ";" + $env:Path
        $omniagentos = Get-Command omniagentos -ErrorAction SilentlyContinue
    }
    if (-not $omniagentos) {
        Write-Host "==> omniagentos not on PATH, bootstrapping .venv"
        python -m venv (Join-Path $RepoRoot ".venv")
        & (Join-Path $RepoRoot ".venv\Scripts\pip.exe") install --quiet -e $RepoRoot
        $env:Path = (Join-Path $RepoRoot ".venv\Scripts") + ";" + $env:Path
    }

    $Listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $Listener.Start()
    $Port = $Listener.LocalEndpoint.Port
    $Listener.Stop()
    $BaseUrl = "http://127.0.0.1:$Port"

    Write-Host "==> Starting omniagentos serve on port $Port (data-dir $DataDir)"
    $LogFile = Join-Path ([System.IO.Path]::GetTempPath()) ("omniagentos-smoke-log-" + [System.Guid]::NewGuid().ToString("N") + ".txt")
    $ServerProcess = Start-Process -FilePath "omniagentos" `
        -ArgumentList @("serve", "--port", "$Port", "--host", "127.0.0.1", "--data-dir", "$DataDir") `
        -RedirectStandardOutput $LogFile -RedirectStandardError "$LogFile.err" -PassThru -NoNewWindow

    Write-Host "==> Waiting for /api/health"
    $Health = $null
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $Health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -Method Get -TimeoutSec 2
            break
        } catch {
            if ($ServerProcess.HasExited) { Fail "server process exited before becoming healthy" }
            Start-Sleep -Seconds 1
        }
    }
    if (-not $Health) { Fail "server never answered /api/health within 30s" }
    if (-not $Health.configured) { Fail "/api/health did not report configured:true — set XAI_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY" }
    Write-Host "==> health OK, configured:true"

    $Goal = "Write a 3-bullet summary of why agent orchestration beats a chatbox."
    $StartTs = Get-Date

    $RunResponse = Invoke-RestMethod -Uri "$BaseUrl/api/runs" -Method Post `
        -ContentType "application/json" -Body (@{ goal = $Goal } | ConvertTo-Json)

    $RunId = $RunResponse.id
    if (-not $RunId) { $RunId = $RunResponse.run_id }
    if (-not $RunId) { Fail "could not extract a run id from POST /api/runs response" }
    Write-Host "==> run started: $RunId"

    $Status = "unknown"
    $Deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $Deadline) {
        try {
            $RunState = Invoke-RestMethod -Uri "$BaseUrl/api/runs/$RunId" -Method Get -TimeoutSec 5
            $Status = $RunState.status
        } catch {
            $Status = "unknown"
        }
        if ($Status -eq "done" -or $Status -eq "failed") { break }
        Start-Sleep -Seconds 2
    }

    $ElapsedS = [math]::Round(((Get-Date) - $StartTs).TotalSeconds, 2)

    if ($Status -ne "done" -and $Status -ne "failed") {
        Fail "run $RunId did not reach done|failed within 120s (last status: '$Status')"
    }

    $CommitSha = (git -C $RepoRoot rev-parse HEAD 2>$null)
    if (-not $CommitSha) { $CommitSha = "unknown" }

    $Receipt = [ordered]@{
        ts         = $Ts
        commit_sha = $CommitSha
        run_id     = $RunId
        status     = $Status
        elapsed_s  = $ElapsedS
    }
    $ReceiptText = $Receipt | ConvertTo-Json
    # Defensive redaction pass, matching smoke.sh, even though these are all control fields.
    $ReceiptText = $ReceiptText -replace 'Bearer\s+\S+', '[REDACTED]'
    $ReceiptText = $ReceiptText -replace 'sk-[A-Za-z0-9]{10,}', '[REDACTED]'
    $ReceiptText = $ReceiptText -replace 'xai-[A-Za-z0-9]{10,}', '[REDACTED]'
    Set-Content -Path $ReceiptFile -Value $ReceiptText

    Write-Host "==> receipt written: $ReceiptFile"

    if ($Status -eq "failed") {
        Fail "run $RunId finished with status=failed"
    }

    Write-Host "SMOKE OK: run $RunId done in ${ElapsedS}s"
} finally {
    Cleanup
}
