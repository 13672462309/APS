param(
    [string]$Message = "",
    [double]$Timeout = 90
)

$ErrorActionPreference = "Stop"
$bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$pythonExe = $null
if (Test-Path -LiteralPath $bundledPython) {
    $pythonExe = $bundledPython
} else {
    foreach ($name in @('python', 'python3', 'py')) {
        $candidate = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($candidate) {
            $pythonExe = $candidate.Source
            break
        }
    }
}
if (-not $pythonExe) {
    throw 'Python 3.9+ was not found. Install Python, then run this script again.'
}
$clientArgs = @((Join-Path $PSScriptRoot 'test_connection.py'), '--timeout', [string]$Timeout)
if ($Message) { $clientArgs += @('--message', $Message) }
& $pythonExe @clientArgs
exit $LASTEXITCODE
