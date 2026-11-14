$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
$DashboardDir = Join-Path $ProjectRoot 'dashboard'
$VenvDir = Join-Path $ProjectRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Requirements = Join-Path $ProjectRoot 'requirements.txt'
$DashboardDist = Join-Path $DashboardDir 'dist\index.html'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $($LASTEXITCODE): $File $($Arguments -join ' ')"
    }
}

if (-not (Test-Path $VenvPython)) {
    Write-Host 'Creating the Python virtual environment...'
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    $AnacondaPython = Join-Path $env:USERPROFILE 'anaconda3\python.exe'
    $SystemPython = Get-Command python -ErrorAction SilentlyContinue

    if ($PyLauncher) {
        Invoke-Checked -File $PyLauncher.Source -Arguments @('-3', '-m', 'venv', $VenvDir)
    } elseif (Test-Path $AnacondaPython) {
        Invoke-Checked -File $AnacondaPython -Arguments @('-m', 'venv', $VenvDir)
    } elseif ($SystemPython) {
        Invoke-Checked -File $SystemPython.Source -Arguments @('-m', 'venv', $VenvDir)
    } else {
        throw 'Python was not found. Install Python 3, then run this file again.'
    }
}

if (-not (Test-Path $DashboardDir)) {
    throw "Dashboard folder was not found: $DashboardDir"
}

& $VenvPython -c 'import uvicorn' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing Python requirements...'
    Invoke-Checked -File $VenvPython -Arguments @('-m', 'pip', 'install', '-r', $Requirements)
}

$Npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $Npm) {
    throw 'npm was not found. Install Node.js, then run this file again.'
}

$PackageJson = Join-Path $DashboardDir 'package.json'
$PackageLock = Join-Path $DashboardDir 'package-lock.json'
$NodeModules = Join-Path $DashboardDir 'node_modules'
$InstalledLock = Join-Path $NodeModules '.package-lock.json'
$NeedsNpmInstall = -not (Test-Path $NodeModules)
if (-not $NeedsNpmInstall -and (Test-Path $PackageLock) -and (Test-Path $InstalledLock)) {
    $NeedsNpmInstall = (Get-Item $PackageLock).LastWriteTimeUtc -gt (Get-Item $InstalledLock).LastWriteTimeUtc
}
if ($NeedsNpmInstall) {
    Write-Host 'Installing dashboard packages...'
    Push-Location $DashboardDir
    try {
        Invoke-Checked -File $Npm.Source -Arguments @('ci')
    } finally {
        Pop-Location
    }
}

$SourceFiles = @()
if (Test-Path (Join-Path $DashboardDir 'src')) {
    $SourceFiles += Get-ChildItem (Join-Path $DashboardDir 'src') -File -Recurse
}
$SourceFiles += Get-Item $PackageJson
if (Test-Path $PackageLock) {
    $SourceFiles += Get-Item $PackageLock
}
$LatestSource = $SourceFiles | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
$NeedsBuild = -not (Test-Path $DashboardDist)
if (-not $NeedsBuild) {
    $NeedsBuild = (Get-Item $DashboardDist).LastWriteTimeUtc -lt $LatestSource.LastWriteTimeUtc
}

if ($NeedsBuild) {
    Write-Host 'Building the React dashboard...'
    Push-Location $DashboardDir
    try {
        Invoke-Checked -File $Npm.Source -Arguments @('run', 'build')
    } finally {
        Pop-Location
    }
}

$Port = 8127
if ($env:FRESHGATE_PORT) {
    $ConfiguredPort = 0
    if (-not [int]::TryParse($env:FRESHGATE_PORT, [ref]$ConfiguredPort) -or $ConfiguredPort -lt 1 -or $ConfiguredPort -gt 65535) {
        throw 'FRESHGATE_PORT must be a valid TCP port from 1 to 65535.'
    }
    $Port = $ConfiguredPort
}

$HostAddress = '127.0.0.1'
if ($env:FRESHGATE_HOST) {
    $HostAddress = $env:FRESHGATE_HOST
}

$InitialPort = $Port
while (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    if ($env:FRESHGATE_PORT) {
        throw "Port $Port is already in use. Set FRESHGATE_PORT to another free port and retry."
    }
    $Port++
    if ($Port -gt 65535) {
        throw "No available port found starting at $InitialPort. Set FRESHGATE_PORT to a free port and retry."
    }
}

$DashboardUrl = "http://${HostAddress}:$Port"
Write-Host "Starting FreshGate at $DashboardUrl"
Write-Host 'Press Ctrl+C to stop the server.'
Push-Location $ProjectRoot
try {
    & $VenvPython -m uvicorn server.app:app --reload --host $HostAddress --port $Port
} finally {
    Pop-Location
}
