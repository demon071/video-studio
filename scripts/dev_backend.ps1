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

# Keep all temp files, model caches, and app data inside the project on D: drive
$tmpDir = Join-Path $root ".tmp"
$cacheDir = Join-Path $root ".cache"
$torchLib = Join-Path $root ".venv\Lib\site-packages\torch\lib"
New-Item -ItemType Directory -Force -Path $tmpDir, $cacheDir | Out-Null
$env:TEMP = $tmpDir
$env:TMP = $tmpDir
$env:PIP_NO_CACHE_DIR = "1"
if (Test-Path $torchLib) { $env:PATH = "$torchLib;$env:PATH" }
if (-not $env:HF_HOME) { $env:HF_HOME = Join-Path $cacheDir "huggingface" }
if (-not $env:TORCH_HOME) { $env:TORCH_HOME = Join-Path $cacheDir "torch" }
if (-not $env:SVA_DATA_DIR) { $env:SVA_DATA_DIR = Join-Path $cacheDir "ShortVideoAI" }

# UTF-8 everywhere: subtitles are Vietnamese/Japanese/Chinese, not ASCII.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $backend
$env:SVA_PORT = "$Port"
$env:SVA_PACKAGED = "0"

# Free port if an older dev backend instance is still holding it
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pidToKill in $existing) {
    if ($pidToKill -and $pidToKill -ne $PID) {
        Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
    }
}

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
