# Windows entry: .\packaging\build.ps1 win|linux|all
param(
  [Parameter(Position = 0)]
  [ValidateSet("win", "linux", "all")]
  [string]$Target = "win"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "python not found. Install Python 3.11+ and retry." }
& python "$Root\packaging\build.py" $Target
exit $LASTEXITCODE
