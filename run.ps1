[CmdletBinding()]
param(
    [switch]$Discover,
    [switch]$DeadlinesOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$collector = Join-Path $projectRoot '.venv\Scripts\moodle-lms-automation.exe'

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$Program,
        [Parameter()][string[]]$Arguments = @()
    )
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

Push-Location $projectRoot
try {
    Write-Host '[1/6] Preparing the local environment...'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            python -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
        }
    }
    if (-not (Test-Path -LiteralPath $collector -PathType Leaf)) {
        Invoke-Checked -Program $python -Arguments @('-m', 'pip', 'install', '-e', '.[dev]')
        Invoke-Checked -Program $python -Arguments @('-m', 'playwright', 'install', 'chromium')
    }
    if (-not (Test-Path -LiteralPath '.env' -PathType Leaf)) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
        Write-Host 'Created .env from .env.example.'
    }
    if (-not (Test-Path -LiteralPath 'config.yml' -PathType Leaf)) {
        Copy-Item -LiteralPath 'config.example.yml' -Destination 'config.yml'
        Write-Host 'Created config.yml from config.example.yml.'
    }

    Write-Host '[2/6] Checking configuration...'
    Invoke-Checked -Program $collector -Arguments @('doctor')

    Write-Host '[3/6] Checking LMS authentication...'
    & $collector auth-check
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Authentication is missing or expired. This unattended run will stop without opening a browser.'
        Invoke-Checked -Program $collector -Arguments @('notify-auth-required')
        throw 'Manual Moodle login required. Run the moodle-lms-automation login command for this environment.'
    }

    if ($Discover) {
        Write-Host '[4/6] Refreshing LMS structure discovery...'
        Invoke-Checked -Program $collector -Arguments @('discover')
    }
    else {
        Write-Host '[4/6] Structure discovery not requested.'
    }

    if ($DeadlinesOnly) {
        Write-Host '[5/6] Refreshing assignments...'
        Invoke-Checked -Program $collector -Arguments @('deadlines')
    }
    else {
        Write-Host '[5/6] Synchronizing transcripts, captions, notes, and assignments...'
        Invoke-Checked -Program $collector -Arguments @('sync')
    }

    Write-Host '[6/7] Sending the Moodle deadline and live-lecture digest...'
    Invoke-Checked -Program $collector -Arguments @('notify-deadlines')

    Write-Host '[7/7] Refreshing the clean Resources export...'
    Invoke-Checked -Program $collector -Arguments @('export-resources')
    Write-Host 'Everything completed successfully.' -ForegroundColor Green
}
finally {
    Pop-Location
}
