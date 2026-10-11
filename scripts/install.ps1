# Flow installer for Windows. Run from the repository root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# Requires Python 3.11+ (https://www.python.org/downloads/) and a recent NVIDIA driver.
$ErrorActionPreference = "Stop"

if (-not (Test-Path .venv)) {
    py -3 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[all]"
& .\.venv\Scripts\python.exe -m flow init-config

Write-Host ""
Write-Host "Installed. Start Flow with:  .\.venv\Scripts\pythonw.exe -m flow"
Write-Host "Or with a console for logs: .\.venv\Scripts\python.exe -m flow -v"
Write-Host "Start with Windows:         .\.venv\Scripts\python.exe -m flow autostart on"
