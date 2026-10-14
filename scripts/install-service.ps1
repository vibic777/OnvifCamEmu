# Register ONVIF emulator as a Windows service with the web GUI enabled.
# Do NOT pass --headless if you want to configure in the browser.
# Usage (Administrator):
#   .\scripts\install-service.ps1
#   .\scripts\install-service.ps1 -Exe .\dist\onvif-emulator-v1.0.0-win64.exe
#   .\scripts\install-service.ps1 -Uninstall
param(
  [string]$Name = "OnvifCamEmu",
  [string]$Exe = "",
  [string]$VideoDir = "",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ($Uninstall) {
  sc.exe stop $Name | Out-Null
  sc.exe delete $Name
  Write-Host "Removed service $Name"
  return
}

$args = @("--service")
if ($VideoDir) { $args += @("--video-dir", $VideoDir) }

if ($Exe) {
  $bin = (Resolve-Path -LiteralPath $Exe).Path
  $work = Split-Path -Parent $bin
} else {
  $wrapper = Join-Path $PSScriptRoot "run-service.cmd"
  if (-not (Test-Path -LiteralPath $wrapper)) { throw "missing $wrapper" }
  $bin = $wrapper
  $work = $Root
}

$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssm) {
  & nssm install $Name $bin
  & nssm set $Name AppDirectory $work
  if ($Exe) { & nssm set $Name AppParameters ($args -join " ") }
  & nssm set $Name Start SERVICE_AUTO_START
  & nssm start $Name
  Write-Host "Installed via nssm. Web GUI: http://127.0.0.1:8080/  (leave the service running)"
  return
}

$quoted = '"' + $bin + '"'
if ($Exe) {
  $binPath = "$quoted $($args -join ' ')"
} else {
  $binPath = "cmd.exe /c $quoted"
}
sc.exe create $Name binPath= $binPath start= auto
sc.exe start $Name
Write-Host "Installed via sc.exe. Working directory for the portable exe is the exe folder."
Write-Host "From sources, run-service.cmd cds to $Root so video/ is created there."
Write-Host "Web GUI: http://127.0.0.1:8080/  login admin/admin"
Write-Host "Prefer nssm if cwd must be set for a custom exe path."
