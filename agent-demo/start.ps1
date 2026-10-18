param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $demoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error 'Create agent-demo/.venv and install requirements.txt first. See README.md.'
    exit 1
}
$env:PYTHONUTF8 = '1'
$env:APS_DEMO_PORT = "$Port"
try {
    $healthResult = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    if ($healthResult.app -eq 'aps-feed-demo') {
        Write-Host "APS Demo is already running: http://127.0.0.1:$Port"
        exit 0
    }
} catch { }
Write-Host "APS Demo: http://127.0.0.1:$Port (Ctrl+C to stop)"
& $pythonPath (Join-Path $demoRoot 'app.py')
