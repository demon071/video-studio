# ===========================================================================
#  ShortVideoAI - build the optional "AI pack" (the full engine as a .zip)
#
#  The NSIS installer ships the *core* engine (~110 MB). The heavy AI stack
#  cannot fit in a 2 GB installer, so it is distributed as this archive and
#  installed from the app (Model tab -> Engine AI -> Download / From file).
#
#  Usage:
#    npm run build:pack                                   # use build\dist-full
#    powershell -File scripts/make_ai_pack.ps1 -BuildEngine
#    powershell -File scripts/make_ai_pack.ps1 -Level 9 -Force
#
#  Output:
#    release\ShortVideoAI_<version>_ai-pack-full.zip
# ===========================================================================
[CmdletBinding()]
param(
    [string]$Engine = "build\dist-full\engine",
    [string]$Out = "release",
    [int]$Level = 6,
    [switch]$BuildEngine,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$engineDir = if ([System.IO.Path]::IsPathRooted($Engine)) { $Engine } else { Join-Path $root $Engine }
$outDir = if ([System.IO.Path]::IsPathRooted($Out)) { $Out } else { Join-Path $root $Out }
$exePath = Join-Path $engineDir "shortvideoai-engine.exe"

function Write-Stage([string]$Text) {
    Write-Host ""
    Write-Host "  $Text" -ForegroundColor Cyan
}

$version = (Get-Content (Join-Path $root "package.json") -Raw | ConvertFrom-Json).version
$zipPath = Join-Path $outDir ("ShortVideoAI_{0}_ai-pack-full.zip" -f $version)

Write-Host ""
Write-Host "  ShortVideoAI AI pack" -ForegroundColor White
Write-Host ("  version : {0}" -f $version) -ForegroundColor DarkGray
Write-Host ("  engine  : {0}" -f $engineDir) -ForegroundColor DarkGray
Write-Host ("  output  : {0}" -f $zipPath) -ForegroundColor DarkGray

if (-not (Test-Path $python)) {
    Write-Host "  [FAIL] .venv not found. Run: npm run setup" -ForegroundColor Red
    exit 1
}

# --- 1. Optionally build the full engine into build\dist-full --------------
if ($BuildEngine -or -not (Test-Path $exePath)) {
    Write-Stage "1/2  Building the full engine (5-20 min)"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build_backend.ps1") `
        -Flavor full -DistPath $engineDir -SkipSmokeTest -NoFfmpeg
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $exePath)) {
        Write-Host "  [FAIL] the full engine bundle is missing - see the PyInstaller log" -ForegroundColor Red
        exit 1
    }
}
else {
    Write-Stage "1/2  Reusing the existing full engine"
    Write-Host "       $exePath" -ForegroundColor DarkGray
}

# --- 2. Zip it --------------------------------------------------------------
Write-Stage "2/2  Packing engine\ -> ai-pack.zip"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$pyArgs = @((Join-Path $PSScriptRoot "make_ai_pack.py"), "--engine", $engineDir, "--out", $zipPath,
    "--version", $version, "--level", $Level)
if ($Force) { $pyArgs += "--force" }

& $python @pyArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] packing failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

if (-not (Test-Path $zipPath)) {
    Write-Host "  [FAIL] $zipPath was not produced" -ForegroundColor Red
    exit 1
}

$mb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
$sha = (Get-FileHash $zipPath -Algorithm SHA256).Hash
Write-Host ""
Write-Host ("  AI pack ready: {0} MB" -f $mb) -ForegroundColor Green
Write-Host ("  file : {0}" -f $zipPath) -ForegroundColor DarkGray
Write-Host ("  sha  : {0}" -f $sha) -ForegroundColor DarkGray
Write-Host "  Serve it from any HTTPS host and set SVA_AI_PACK_URL, or ship it next to the installer." -ForegroundColor DarkGray
Write-Host ""
