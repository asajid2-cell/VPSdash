$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m PyInstaller --noconfirm --clean VPSDASH.spec

$distExe = Join-Path $root "dist\VPSDASH\VPSDASH.exe"
$buildExe = Join-Path $root "build\VPSDASH\VPSDASH.exe"

if (Test-Path $buildExe) {
  Remove-Item $buildExe -Force
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Run this executable:"
Write-Host "  $distExe"
Write-Host ""
Write-Host "Do not launch the intermediate file under build\."
