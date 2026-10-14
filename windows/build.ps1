<#
  Builds MarbleTrack.exe (and optionally the installer) on Windows 10/11.

    powershell -ExecutionPolicy Bypass -File windows\build.ps1             # dist\MarbleTrack\MarbleTrack.exe
    powershell -ExecutionPolicy Bypass -File windows\build.ps1 -Installer  # + dist\MarbleTrack-Setup-1.0.0.exe

  Needs Python 3.10-3.12 (python.org installer, "Add to PATH") and, for -Installer, Inno Setup 6.
#>
param([switch]$Installer, [switch]$SkipTests, [switch]$SkipSelftest)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "== Python environment" -ForegroundColor Cyan
if (-not (Test-Path .venv-build)) { python -m venv .venv-build }
$Py = Join-Path $Root ".venv-build\Scripts\python.exe"
& $Py -m pip install --upgrade pip | Out-Null
& $Py -m pip install -r windows\requirements-windows.txt pytest hypothesis
if ($LASTEXITCODE) { throw "pip install failed" }

if (-not $SkipTests) {
  Write-Host "== Tests" -ForegroundColor Cyan
  & $Py -m pytest -q -m "not slow"
  if ($LASTEXITCODE) { throw "tests failed" }
}

Write-Host "== Icon + PyInstaller" -ForegroundColor Cyan
& $Py windows\make_icon.py
& $Py -m PyInstaller --noconfirm --clean --distpath dist --workpath build windows\MarbleTrack.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

if (-not $SkipSelftest) {
  Write-Host "== Self-test of the built exe (demo cameras, about 2 minutes)" -ForegroundColor Cyan
  $p = Start-Process -FilePath dist\MarbleTrack\MarbleTrack.exe -ArgumentList "--selftest", "--selftest-out", "dist\selftest.json" -Wait -PassThru
  Get-Content dist\selftest.json
  if ($p.ExitCode -ne 0) { throw "self-test failed (see dist\selftest.json and %APPDATA%\MarbleTrack\logs)" }
}

if ($Installer) {
  Write-Host "== Installer" -ForegroundColor Cyan
  $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
  if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
  & $iscc windows\installer.iss
  if ($LASTEXITCODE) { throw "installer build failed" }
}
Write-Host "Done: dist\MarbleTrack\MarbleTrack.exe" -ForegroundColor Green
