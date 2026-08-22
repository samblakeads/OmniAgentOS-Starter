# Post-start live-receipt smoke test (Windows PowerShell equivalent of smoke.sh).
#
# Starts a real `omniagentos serve` on an ephemeral port, confirms
# /api/health reports configured:true, submits one tiny live run, reads its
# SSE event stream (falling back to polling) to completion within 120s, and
# writes a redacted receipt to evidence/live-receipts/smoke-<ts>.json.
#
# Passing requires ALL of: run status == "done", run verified == true, a
# non-empty deliverable, and >=4 distinct roles (planner/worker/critic/
# verifier) observed in the events. A run that merely reaches status=done
# with an empty deliverable or missing roles is NOT success — exits 1.
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

# Mirrors scripts/drill.py's ROLE_EVENTS (literal copy, not an import — same
# low-coupling pattern as scripts/lint_skills.py's mirror of redact shapes).
$RoleEvents = [ordered]@{
    planner  = @("planner.plan")
    worker   = @("worker.started", "worker.finished", "worker.delta")
    critic   = @("critic.verdict")
    verifier = @("verifier.verdict")
}
$DeadlineSeconds = 120

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

    $RunId = $RunResponse.run_id
    if (-not $RunId) { $RunId = $RunResponse.id }
    if (-not $RunId) { Fail "could not extract a run id from POST /api/runs response" }
    Write-Host "==> run started: $RunId"

    # Read the SSE event stream directly (like curl -N in smoke.sh) so role
    # coverage is checked from the real events, not inferred from status alone.
    $EventTypes = New-Object System.Collections.Generic.HashSet[string]
    try {
        $HttpClient = New-Object System.Net.Http.HttpClient
        $HttpClient.Timeout = [TimeSpan]::FromSeconds($DeadlineSeconds + 5)
        $Stream = $HttpClient.GetStreamAsync("$BaseUrl/api/runs/$RunId/events").GetAwaiter().GetResult()
        $Reader = New-Object System.IO.StreamReader($Stream)
        while (-not $Reader.EndOfStream) {
            if (((Get-Date) - $StartTs).TotalSeconds -gt $DeadlineSeconds) { break }
            $line = $Reader.ReadLine()
            if ($null -eq $line -or -not $line.StartsWith("data:")) { continue }
            $payload = $line.Substring(5).Trim()
            if (-not $payload) { continue }
            try {
                $obj = $payload | ConvertFrom-Json
            } catch {
                continue
            }
            if ($obj.type) { [void]$EventTypes.Add($obj.type) }
            if ($obj.type -eq "run.done" -or $obj.type -eq "run.failed") { break }
        }
        $Reader.Dispose()
        $HttpClient.Dispose()
    } catch {
        Write-Host "==> event stream ended early: $($_.Exception.Message)"
    }

    # Poll fallback: authoritative final state from the run resource, tried
    # until terminal or the 120s budget runs out — same pattern as smoke.sh.
    $Status = "unknown"
    $Verified = $false
    $Deliverable = ""
    $Deadline = $StartTs.AddSeconds($DeadlineSeconds)
    while ($true) {
        try {
            $RunState = Invoke-RestMethod -Uri "$BaseUrl/api/runs/$RunId" -Method Get -TimeoutSec 10
            $Status = $RunState.status
            $Verified = [bool]$RunState.verified
            $Deliverable = if ($RunState.deliverable) { $RunState.deliverable } else { "" }
        } catch {
            $Status = "unknown"
        }
        if ($Status -eq "done" -or $Status -eq "failed" -or (Get-Date) -ge $Deadline) { break }
        Start-Sleep -Seconds 2
    }

    $ElapsedS = [math]::Round(((Get-Date) - $StartTs).TotalSeconds, 2)

    $RolesSeen = @()
    foreach ($role in $RoleEvents.Keys) {
        foreach ($marker in $RoleEvents[$role]) {
            if ($EventTypes.Contains($marker)) { $RolesSeen += $role; break }
        }
    }
    $RolesSeen = $RolesSeen | Sort-Object -Unique
    $MissingRoles = $RoleEvents.Keys | Where-Object { $_ -notin $RolesSeen } | Sort-Object

    $Problems = @()
    if ($Status -ne "done") { $Problems += "run status is '$Status', not done" }
    if (-not $Verified) { $Problems += "run.verified is not true" }
    if (-not $Deliverable.Trim()) { $Problems += "deliverable is empty" }
    if ($RolesSeen.Count -lt 4) { $Problems += "fewer than 4 roles seen in events; missing: $($MissingRoles -join ', ')" }

    $DeliverableBytes = [System.Text.Encoding]::UTF8.GetBytes($Deliverable)
    $Sha256 = [System.Security.Cryptography.SHA256]::Create()
    $DeliverableSha256 = ([BitConverter]::ToString($Sha256.ComputeHash($DeliverableBytes)) -replace '-', '').ToLower()

    $CommitSha = (git -C $RepoRoot rev-parse HEAD 2>$null)
    if (-not $CommitSha) { $CommitSha = "unknown" }

    $Receipt = [ordered]@{
        magic              = "OMNIAGENTOS-RECEIPT-1"
        kind               = "smoke"
        ts                 = $Ts
        commit_sha         = $CommitSha
        run_id             = $RunId
        status             = $Status
        verified           = $Verified
        roles_seen         = @($RolesSeen)
        event_types        = @($EventTypes | Sort-Object)
        deliverable_chars  = $Deliverable.Length
        deliverable_sha256 = $DeliverableSha256
        elapsed_s          = $ElapsedS
        problems           = $Problems
        ok                 = ($Problems.Count -eq 0)
    }
    $ReceiptText = $Receipt | ConvertTo-Json
    # Defensive redaction pass, matching smoke.sh's local fallback (this
    # script has no import path into the Python package's real redactor).
    $ReceiptText = $ReceiptText -replace 'Bearer\s+\S+', '[REDACTED]'
    $ReceiptText = $ReceiptText -replace 'sk-[A-Za-z0-9]{10,}', '[REDACTED]'
    $ReceiptText = $ReceiptText -replace 'xai-[A-Za-z0-9]{10,}', '[REDACTED]'
    Set-Content -Path $ReceiptFile -Value $ReceiptText

    Write-Host "==> receipt written: $ReceiptFile"

    if ($Problems.Count -gt 0) {
        Fail ("run did not satisfy the smoke contract: " + ($Problems -join "; "))
    }

    Write-Host "SMOKE OK: run $RunId done in ${ElapsedS}s, verified, $($RolesSeen.Count) roles ($($RolesSeen -join ',')), deliverable $($Deliverable.Length) chars"
} finally {
    Cleanup
}
