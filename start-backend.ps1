# Launch the API with this project's geospatial runtime, without changing system settings.
$ErrorActionPreference = 'Stop'
$backendPython = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $backendPython)) {
    throw 'Create venv and install requirements.txt first (see README.md).'
}
$previousProjLib = [Environment]::GetEnvironmentVariable('PROJ_LIB', 'Process')
$previousProjData = [Environment]::GetEnvironmentVariable('PROJ_DATA', 'Process')
Push-Location $PSScriptRoot
try {
    # A system PostGIS installation can point rasterio at an incompatible proj.db.
    # PowerShell can marshal $null to an empty string in the .NET call.
    # PROJ treats an empty variable as a configured (invalid) search path.
    $env:PROJ_LIB = $null
    $env:PROJ_DATA = $null
    & $backendPython (Join-Path $PSScriptRoot 'start.py')
} finally {
    $env:PROJ_LIB = $previousProjLib
    $env:PROJ_DATA = $previousProjData
    Pop-Location
}
