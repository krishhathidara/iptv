$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".demo-server.pid"

if (-not (Test-Path $PidFile)) {
    Write-Host "No demo PID file was found. The demo is not running." -ForegroundColor Yellow
    exit 0
}

$RawState = Get-Content $PidFile -Raw -ErrorAction SilentlyContinue
$ProcessIds = @()
try {
    $State = $RawState | ConvertFrom-Json
    if ($State.launcher_pid) { $ProcessIds += [int]$State.launcher_pid }
    if ($State.server_pid) { $ProcessIds += [int]$State.server_pid }
    if (-not $State.launcher_pid -and -not $State.server_pid -and $State) {
        $ProcessIds += [int]$State
    }
} catch {
    if ($RawState) { $ProcessIds += [int]$RawState }
}

foreach ($ProcessId in ($ProcessIds | Select-Object -Unique)) {
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "NexaStream demo stopped." -ForegroundColor Green
exit 0