Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location "$PSScriptRoot\..\backend"
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

