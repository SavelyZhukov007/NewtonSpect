Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location "$PSScriptRoot\..\backend"
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}
python worker.py

