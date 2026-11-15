# ===========================================================================
#  ShortVideoAI - start the Python engine in development
#  Usage:  npm run dev:backend      (or)   powershell -File scripts/dev_backend.ps1 -Port 8756
# ===========================================================================
[CmdletBinding()]
param(
    [int]$Port = 8756
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "  [FAIL] .venv not found. Run: npm run setup" -ForegroundColor Red
    exit 1
}

# UTF-8 everywhere: subtitles are Vietnamese/Japanese/Chinese, not ASCII.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $backend
$env:SVA_PORT = "$Port"
$env:SVA_PACKAGED = "0"

Write-Host ""
Write-Host ("  ShortVideoAI engine -> http://127.0.0.1:{0}" -f $Port) -ForegroundColor Cyan
Write-Host ("  docs               -> http://127.0.0.1:{0}/docs" -f $Port) -ForegroundColor DarkGray
Write-Host ""

Push-Location $backend
try {
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload --reload-dir app
}
finally {
    Pop-Location
}
