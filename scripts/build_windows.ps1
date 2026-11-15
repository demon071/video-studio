# ===========================================================================
#  ShortVideoAI - one-shot Windows release build
#
#  Usage:
#    npm run build:windows                     # core engine + NSIS installer
#    powershell -File scripts/build_windows.ps1 -Flavor full
#    powershell -File scripts/build_windows.ps1 -SkipBackend -SkipBundle
#
#  Pipeline:
#    1. freeze the Python engine (scripts/build_backend.ps1)
#    2. compile the Tauri shell in release mode (frontend => NSIS installer)
#    3. copy the installer to <repo>\release\ with a stable file name
#
#  NOTE on sizes: NSIS is a 32-bit container and cannot produce installers
#  larger than 2 GB. The `core` flavor (~110 MB engine) installs in seconds;
#  the `full` flavor bundles ~3.5 GB of CUDA/torch and is expected to exceed
#  that limit - ship it as a portable folder or download the AI stack on first
#  run instead.
# ===========================================================================
[CmdletBinding()]
param(
    [ValidateSet("core", "full")]
    [string]$Flavor = "core",
    [switch]$SkipBackend,
    [switch]$SkipBundle
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root "frontend"
$tauriDir = Join-Path $frontend "src-tauri"
$engineExe = Join-Path $tauriDir "resources\engine\shortvideoai-engine.exe"
$releaseDir = Join-Path $root "release"

function Write-Stage([string]$Text) {
    Write-Host ""
    Write-Host "  $Text" -ForegroundColor Cyan
}

function Get-FolderMb([string]$Path) {
    if (-not (Test-Path $Path)) { return 0 }
    $bytes = (Get-ChildItem $Path -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Sum Length).Sum
    return [math]::Round($bytes / 1MB, 1)
}

$conf = Get-Content (Join-Path $tauriDir "tauri.conf.json") -Raw | ConvertFrom-Json
$version = $conf.version

Write-Host ""
Write-Host "  ShortVideoAI $version - Windows release build" -ForegroundColor White
Write-Host ("  flavor  : {0}" -f $Flavor) -ForegroundColor DarkGray
Write-Host ("  release : {0}" -f $releaseDir) -ForegroundColor DarkGray

# --- 0. Preflight ---------------------------------------------------------
Write-Stage "0/3  Preflight"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "  [FAIL] Node.js is not on PATH" -ForegroundColor Red
    exit 1
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Host "  [FAIL] Rust/cargo is not on PATH - install rustup (1.77.2+)" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $root "node_modules"))) {
    Write-Host "  [FAIL] node_modules missing. Run: npm install" -ForegroundColor Red
    exit 1
}
Write-Host ("       node {0}" -f (node --version)) -ForegroundColor DarkGray
Write-Host ("       {0}" -f (cargo --version)) -ForegroundColor DarkGray

# --- 1. Engine ------------------------------------------------------------
if ($SkipBackend) {
    Write-Stage "1/3  Engine bundle - skipped"
}
else {
    Write-Stage "1/3  Engine bundle"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build_backend.ps1") -Flavor $Flavor
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] engine build failed" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}
if (-not (Test-Path $engineExe)) {
    Write-Host "  [FAIL] missing $engineExe - the installer would ship without an engine" -ForegroundColor Red
    exit 1
}

$engineMb = Get-FolderMb (Split-Path -Parent $engineExe)
$ffmpegMb = Get-FolderMb (Join-Path $tauriDir "resources\ffmpeg")
Write-Host ("       engine {0} MB + ffmpeg {1} MB" -f $engineMb, $ffmpegMb) -ForegroundColor DarkGray
if (($engineMb + $ffmpegMb) -gt 1900) {
    Write-Host "  [WARN] bundling more than 1.9 GB; NSIS cannot exceed 2 GB per installer." -ForegroundColor Yellow
    Write-Host "         Use -Flavor core, or ship the AI stack as an optional download." -ForegroundColor Yellow
}

# --- 2. Tauri -------------------------------------------------------------
Write-Stage "2/3  Tauri release build (this also runs the Vite production build)"
Push-Location $frontend
try {
    if ($SkipBundle) {
        & npx tauri build --no-bundle
    }
    else {
        & npx tauri build --bundles nsis
    }
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($code -ne 0) {
    Write-Host "  [FAIL] tauri build exited with code $code" -ForegroundColor Red
    exit $code
}

# --- 3. Collect -----------------------------------------------------------
Write-Stage "3/3  Collecting artifacts"
$appExe = Join-Path $tauriDir "target\release\short-video-ai.exe"
if (Test-Path $appExe) {
    Write-Host ("       app    : {0} ({1} MB)" -f $appExe, [math]::Round((Get-Item $appExe).Length / 1MB, 1)) -ForegroundColor DarkGray
}

$installer = Get-ChildItem (Join-Path $tauriDir "target\release\bundle\nsis") -Filter "*.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

if (-not $installer) {
    Write-Host "       [INFO] no NSIS installer produced (no-bundle build)" -ForegroundColor Yellow
}
else {
    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
    $target = Join-Path $releaseDir ("ShortVideoAI_{0}_x64-setup.exe" -f $version)
    Copy-Item $installer.FullName $target -Force
    $hash = (Get-FileHash $target -Algorithm SHA256).Hash
    $sizeMb = [math]::Round($installer.Length / 1MB, 1)
    Write-Host ""
    Write-Host ("  Installer: {0}" -f $target) -ForegroundColor Green
    Write-Host ("  Size     : {0} MB" -f $sizeMb) -ForegroundColor Green
    Write-Host ("  SHA256   : {0}" -f $hash) -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Build complete." -ForegroundColor Green
Write-Host ""
