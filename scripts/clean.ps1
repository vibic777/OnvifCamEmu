# Native Windows cleaner. Uses Python when available, otherwise PowerShell.
# Usage: .\scripts\clean.ps1 [-DryRun] [-All] [-Venv] [-KeepVendor]
param(
  [switch]$DryRun,
  [switch]$All,
  [switch]$Venv,
  [switch]$KeepVendor
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Get-Command python -ErrorAction SilentlyContinue
if ($Py) {
  $pyArgs = @("$PSScriptRoot\clean.py", "--root", $Root)
  if ($DryRun) { $pyArgs += "--dry-run" }
  if ($All -or $Venv) { $pyArgs += "--venv" }
  if ($KeepVendor) { $pyArgs += "--keep-vendor" }
  & python @pyArgs
  exit $LASTEXITCODE
}

function Remove-Target([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $rel = $Path.Substring($Root.Length).TrimStart("\", "/")
  if ($DryRun) { Write-Host "[dry-run] $rel" } else {
    Write-Host "[del] $rel"
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
  }
}

$skip = @(".git", "node_modules")
Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
  $p = $_.FullName
  foreach ($s in $skip) { if ($p -match [regex]::Escape([IO.Path]::DirectorySeparatorChar + $s + [IO.Path]::DirectorySeparatorChar) -or $p.EndsWith("\$s")) { return } }
  if ($_.PSIsContainer -and @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist", "htmlcov", ".eggs", "logs", "tmp", ".tox") -contains $_.Name) {
    Remove-Target $p
  } elseif (-not $_.PSIsContainer -and @(".pyc", ".pyo") -contains $_.Extension) {
    Remove-Target $p
  } elseif (-not $_.PSIsContainer -and $_.Name -like "_gen_*.spec") {
    Remove-Target $p
  }
}

@(
  "$Root\.coverage",
  "$Root\data\state.json",
  "$Root\packaging\_gen_win.spec",
  "$Root\packaging\_gen_linux.spec"
) | ForEach-Object { Remove-Target $_ }

if (-not $KeepVendor) {
  @("$Root\vendor\cache", "$Root\vendor\win64", "$Root\vendor\linux64") | ForEach-Object { Remove-Target $_ }
}
if ($All -or $Venv) {
  @("$Root\.venv", "$Root\venv") | ForEach-Object { Remove-Target $_ }
}
Write-Host "clean.ps1 done"
