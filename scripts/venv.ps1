# Create or recreate the local Python virtualenv (.venv). Never committed.
# Usage: .\scripts\venv.ps1           # create if missing, then pip install
#        .\scripts\venv.ps1 -Recreate # delete .venv and make a fresh one
param(
  [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
Set-Location $Root

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "python not found. Install Python 3.11+ and add it to PATH." }

if ($Recreate -and (Test-Path -LiteralPath $Venv)) {
  Write-Host "Removing $Venv"
  Remove-Item -LiteralPath $Venv -Recurse -Force
}

if (-not (Test-Path -LiteralPath $Venv)) {
  Write-Host "Creating $Venv"
  & python -m venv $Venv
}

$VenvPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPy)) {
  throw "venv python missing: $VenvPy"
}

& $VenvPy -m pip install -U pip
& $VenvPy -m pip install -e ".[dev]"
Write-Host "OK. Activate: .\.venv\Scripts\Activate.ps1"
Write-Host "This folder is gitignored and is not part of the repository."
