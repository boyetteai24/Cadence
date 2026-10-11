# Cadence installer for Windows. Run from the repository root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# Requires Python 3.11+ (https://www.python.org/downloads/) and a recent NVIDIA driver.
$ErrorActionPreference = "Stop"

if (-not (Test-Path .venv)) {
    py -3 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
# Remove the package from before the rename to Cadence, if present.
& .\.venv\Scripts\python.exe -m pip uninstall -y flow-dictation 2>$null
& .\.venv\Scripts\python.exe -m pip install -e ".[all]"
& .\.venv\Scripts\python.exe -m cadence init-config

Write-Host ""
Write-Host "Installed. Start Cadence with:  .\.venv\Scripts\pythonw.exe -m cadence"
Write-Host "Or with a console for logs: .\.venv\Scripts\python.exe -m cadence -v"
Write-Host "Start with Windows:         .\.venv\Scripts\python.exe -m cadence autostart on"
