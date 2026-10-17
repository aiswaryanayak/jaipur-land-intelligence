$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$pipeline = Join-Path $repo "refresh_pipeline.py"
$logDir = Join-Path $repo "data\refresh_pipeline\logs"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logDir "refresh_$stamp.log"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Push-Location $repo
try {
    & $python $pipeline --retries 2 --interval 1 --cache-ttl 900 *>&1 | Tee-Object -FilePath $log
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    exit $exitCode
}
catch {
    $_ | Out-File -FilePath $log -Append
    exit 1
}
finally {
    Pop-Location
}
