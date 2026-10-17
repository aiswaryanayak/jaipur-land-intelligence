param(
    [string]$TaskName = "JaipurLandIntelligence-Refresh",
    [string]$RepositoryRoot = $PSScriptRoot,
    [string]$Schedule = "DAILY",
    [string]$StartTime = "03:00",
    [switch]$Register
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path $RepositoryRoot).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
$pipeline = Join-Path $repo "refresh_pipeline.py"
$logDir = Join-Path $repo "data\refresh_pipeline\logs"
$runner = Join-Path $repo "run_refresh_task.ps1"

if (-not (Test-Path $python)) { throw "Virtual-environment Python was not found: $python" }
if (-not (Test-Path $pipeline)) { throw "Refresh pipeline was not found: $pipeline" }
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Output "Task already exists: $TaskName"
    if (-not $Register) { Write-Output "No changes made."; exit 0 }
    throw "Refusing to replace an existing task. Remove it explicitly before creating a replacement."
}

if (-not (Test-Path $runner)) {
    throw "Missing runner script: $runner"
}

if (-not $Register) {
    Write-Output "Preview only. No scheduled task was registered."
    Write-Output "Task: $TaskName"
    Write-Output "Schedule: $Schedule at $StartTime"
    Write-Output "Runner: $runner"
    Write-Output "Logs: $logDir"
    Write-Output "To register after review, rerun with -Register."
    exit 0
}

$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Parse($StartTime))
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description "Refresh Jaipur Land Intelligence official-source staging" | Out-Null
Write-Output "Registered scheduled task: $TaskName"
