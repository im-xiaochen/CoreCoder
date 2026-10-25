param(
    [int]$Port = 8765,
    [switch]$Build,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$taskPythonCandidates = @(
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    (Join-Path (Split-Path $PSScriptRoot -Parent) '.corecoder-review-py312\Scripts\python.exe')
)
$taskPython = $taskPythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $taskPython) {
    $taskPython = (Get-Command python -ErrorAction Stop).Source
}

if ($Build -or -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'frontend\dist\index.html'))) {
    Push-Location -LiteralPath (Join-Path $PSScriptRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            & npm.cmd ci --cache (Join-Path $PSScriptRoot '.tmp\npm-cache') --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally {
        Pop-Location
    }
}

$taskServerArgs = @('-m', 'web_backend.server', '--port', "$Port")
if (-not $NoBrowser) { $taskServerArgs += '--open' }
& $taskPython @taskServerArgs
