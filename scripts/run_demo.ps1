param(
    [int]$Port = 8000,
    [ValidateRange(6, 100000)]
    [int]$ChannelCount = 12000,
    [switch]$SyntheticCatalog,
    [switch]$RefreshCatalog,
    [switch]$LanAccess,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$OutLog = Join-Path $ProjectRoot ".demo-server.out.log"
$ErrLog = Join-Path $ProjectRoot ".demo-server.err.log"
$PidFile = Join-Path $ProjectRoot ".demo-server.pid"
$BindHost = if ($LanAccess) { "0.0.0.0" } else { "127.0.0.1" }

if (-not (Test-Path $Python)) {
    throw "Virtual environment not found at $Python. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

if (Test-Path $PidFile) {
    $ExistingState = Get-Content $PidFile -Raw -ErrorAction SilentlyContinue
    $ExistingPid = $null
    try {
        $ParsedState = $ExistingState | ConvertFrom-Json
        $ExistingPid = if ($ParsedState.launcher_pid) { $ParsedState.launcher_pid } else { $ParsedState }
    } catch {
        $ExistingPid = $ExistingState
    }
    if ($ExistingPid -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
        Write-Host "Demo is already running at http://127.0.0.1:$Port" -ForegroundColor Green
        if ($LanAccess) { Write-Warning "The existing server may only listen on localhost. Stop it first, then restart with -LanAccess for TV access." }
        if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
        exit 0
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$ExistingListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($ExistingListener) {
    throw "Port $Port is already in use by process $($ExistingListener.OwningProcess). Choose another port with -Port."
}

$env:DATABASE_URL = "sqlite+aiosqlite:///./demo_iptv.sqlite3"
$env:REDIS_URL = ""
$env:CREATE_TABLES_ON_STARTUP = "true"
$env:DEMO_MODE = if ($SyntheticCatalog) { "true" } else { "false" }
$env:SEED_DEMO_DATA = if ($SyntheticCatalog) { "true" } else { "false" }
$env:SEED_PUBLIC_CATALOG = if ($SyntheticCatalog) { "false" } elseif ($RefreshCatalog -or -not (Test-Path (Join-Path $ProjectRoot 'demo_iptv.sqlite3'))) { "true" } else { "false" }
$env:DEMO_CHANNEL_COUNT = "$ChannelCount"
$env:CORS_ORIGINS = '["*"]'
$LanIp = if ($LanAccess) {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.InterfaceAlias -notmatch 'vEthernet|Loopback|VPN|VMware|VirtualBox' } |
        Sort-Object @{ Expression = { if ($_.InterfaceAlias -match 'Wi-Fi|Ethernet') { 0 } else { 1 } } } |
        Select-Object -First 1 -ExpandProperty IPAddress
} else { $null }
$env:STREAM_VALIDATION_ORIGIN = if ($LanIp) { "http://${LanIp}:$Port" } else { "http://127.0.0.1:$Port" }
# Keep an explicitly configured public base URL (environment or private .env).
# Otherwise issue links reachable from the network selected for this run.
$EnvFile = Join-Path $ProjectRoot ".env"
$EnvBaseUrlLine = if (Test-Path $EnvFile) {
    Get-Content $EnvFile | Where-Object { $_ -match '^\s*SUBSCRIBER_BASE_URL\s*=' } | Select-Object -Last 1
} else { $null }
if (-not $env:SUBSCRIBER_BASE_URL -and -not $EnvBaseUrlLine) {
    $env:SUBSCRIBER_BASE_URL = if ($LanIp) { "http://${LanIp}:$Port" } else { "http://127.0.0.1:$Port" }
}
$CatalogProfile = if ($SyntheticCatalog) {
    "$ChannelCount synthetic capacity-test records"
} else {
    "IPTV-org worldwide browser-validated live directory"
}

Remove-Item $OutLog, $ErrLog -Force -ErrorAction SilentlyContinue
$Server = Start-Process -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "main:app", "--host", $BindHost, "--port", "$Port", "--no-access-log") `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

$Ready = $false
for ($Attempt = 0; $Attempt -lt 1800; $Attempt++) {
    Start-Sleep -Milliseconds 500
    if ($Server.HasExited) { break }
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($Health.status -eq "ok") { $Ready = $true; break }
    } catch { }
}

if (-not $Ready) {
    $FailedListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($FailedListener) {
        Stop-Process -Id $FailedListener.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue
    $Details = if (Test-Path $ErrLog) { Get-Content $ErrLog -Raw } else { "No server log was written." }
    throw "Demo server failed to start.`n$Details"
}

$Listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
@{
    launcher_pid = $Server.Id
    server_pid = if ($Listener) { $Listener.OwningProcess } else { $Server.Id }
    port = $Port
} | ConvertTo-Json | Set-Content $PidFile

Write-Host "NexaStream demo is running" -ForegroundColor Green
Write-Host "Dashboard: http://127.0.0.1:$Port"
Write-Host "Customer administration: http://127.0.0.1:$Port/admin"
if ($LanAccess) {
    if ($LanIp) {
        Write-Host "TV browser (same network): http://${LanIp}:$Port"
        Write-Host "TV IPTV playlist URL: http://${LanIp}:$Port/playlist.m3u"
        if ($env:SUBSCRIBER_BASE_URL) { Write-Host "Customer link base: $env:SUBSCRIBER_BASE_URL" }
        else { Write-Host "Customer link base: configured in .env" }
    } else {
        Write-Warning "No physical LAN IP found. Determine this computer's reachable address before configuring a TV."
    }
    Write-Warning "LAN access also exposes unauthenticated catalog and import/management endpoints. Use only on a trusted private network, not the public Internet; allow the port through your local firewall if needed. Customer links on HTTP are not confidential."
}
Write-Host "API docs: http://127.0.0.1:$Port/docs"
Write-Host "Catalog profile: $CatalogProfile"
Write-Host "Stop with: .\scripts\stop_demo.ps1"

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:$Port"
}