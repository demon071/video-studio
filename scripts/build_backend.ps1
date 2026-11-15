# ===========================================================================
#  ShortVideoAI - freeze the Python engine with PyInstaller (onedir, windowed)
#
#  Usage:
#    npm run build:backend                                  # core profile
#    powershell -File scripts/build_backend.ps1 -Flavor full
#    powershell -File scripts/build_backend.ps1 -Clean -SkipSmokeTest
#
#  Profiles:
#    core  ~120 MB   server + FFmpeg pipeline + model manager (no CUDA runtime)
#    full  ~3.5 GB   adds torch/CUDA, CTranslate2, llama.cpp, ONNX, TTS stack
#
#  Output:
#    frontend/src-tauri/resources/engine/shortvideoai-engine.exe (+ _internal)
#    frontend/src-tauri/resources/ffmpeg/{ffmpeg,ffprobe}.exe  (bundled tools)
# ===========================================================================
[CmdletBinding()]
param(
    [ValidateSet("core", "full")]
    [string]$Flavor = "core",
    [switch]$Clean,
    [switch]$SkipSmokeTest,
    [switch]$NoFfmpeg,
    # Where the onedir bundle is written. `npm run build:backend` targets the
    # Tauri resources; a `full` engine destined for the AI pack can go to
    # build\dist-full instead (see scripts/make_ai_pack.ps1).
    [string]$DistPath = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $root ".venv\Scripts\python.exe"
$spec = Join-Path $backend "packaging\engine.spec"
if (-not $DistPath) { $DistPath = Join-Path $root "frontend\src-tauri\resources" }
$distRoot = if ([System.IO.Path]::IsPathRooted($DistPath)) { $DistPath } else { Join-Path $root $DistPath }
$engineDir = Join-Path $distRoot "engine"
$exePath = Join-Path $engineDir "shortvideoai-engine.exe"
$workRoot = Join-Path $root ("build\pyinstaller-" + $Flavor)
$ffmpegTarget = Join-Path $distRoot "ffmpeg"

function Write-Stage([string]$Text) {
    Write-Host ""
    Write-Host "  $Text" -ForegroundColor Cyan
}

function Get-FolderMb([string]$Path) {
    if (-not (Test-Path $Path)) { return 0 }
    $bytes = (Get-ChildItem $Path -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Sum Length).Sum
    return [math]::Round($bytes / 1MB, 1)
}

Write-Host ""
Write-Host "  ShortVideoAI engine build" -ForegroundColor White
Write-Host ("  profile : {0}" -f $Flavor) -ForegroundColor DarkGray
Write-Host ("  python  : {0}" -f $python) -ForegroundColor DarkGray
Write-Host ("  output  : {0}" -f $engineDir) -ForegroundColor DarkGray

if (-not (Test-Path $python)) {
    Write-Host "  [FAIL] .venv not found. Run: npm run setup" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $spec)) {
    Write-Host "  [FAIL] PyInstaller spec not found: $spec" -ForegroundColor Red
    exit 1
}

& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] PyInstaller is missing. Run: .venv\Scripts\python.exe -m pip install pyinstaller" -ForegroundColor Red
    exit 1
}

# --- 1. Freeze ------------------------------------------------------------
Write-Stage "1/3  Freezing the engine (PyInstaller onedir, noconsole)"
if ($Flavor -eq "full") {
    Write-Host "       full profile copies ~3.5 GB of CUDA/torch DLLs - expect 5-20 min" -ForegroundColor Yellow
}

$env:SVA_BUNDLE_PROFILE = $Flavor
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Force -Path $distRoot, $workRoot | Out-Null

$pyArgs = @("-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
    "--distpath", $distRoot, "--workpath", $workRoot)
if ($Clean) { $pyArgs += "--clean" }
$pyArgs += $spec

$started = Get-Date
$pyLog = Join-Path $root "build\pyinstaller-$Flavor.log"
Push-Location $backend
# PyInstaller logs progress to stderr, and `*>` turns that into ErrorRecords.
# With $ErrorActionPreference = "Stop" (set at the top of this script) the first
# log line would abort the build, so relax it here and trust the exit code.
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $python @pyArgs *> $pyLog
    $code = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousPreference
    Pop-Location
}
if ($code -ne 0) {
    Write-Host "  [FAIL] PyInstaller exited with code $code - $pyLog" -ForegroundColor Red
    Get-Content $pyLog -Tail 30 | ForEach-Object { "       $_" }
    exit $code
}
$elapsed = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)

if (-not (Test-Path $exePath)) {
    Write-Host "  [FAIL] Build finished but $exePath is missing" -ForegroundColor Red
    exit 1
}
Write-Host ("       done in {0}s - {1} MB" -f $elapsed, (Get-FolderMb $engineDir)) -ForegroundColor Green

# --- 2. Bundle FFmpeg -----------------------------------------------------
if (-not $NoFfmpeg) {
    Write-Stage "2/3  Bundling ffmpeg/ffprobe"
    New-Item -ItemType Directory -Force -Path $ffmpegTarget | Out-Null
    foreach ($tool in @("ffmpeg", "ffprobe")) {
        $target = Join-Path $ffmpegTarget "$tool.exe"
        if (Test-Path $target) {
            Write-Host "       $tool.exe already bundled" -ForegroundColor DarkGray
            continue
        }
        $source = $null
        $provided = Join-Path $root "resources\ffmpeg\$tool.exe"
        if (Test-Path $provided) {
            $source = $provided
        }
        else {
            $command = Get-Command $tool -ErrorAction SilentlyContinue
            if ($command) { $source = $command.Source }
        }
        if (-not $source) {
            Write-Host "       [WARN] $tool.exe not found - the packaged app will rely on PATH" -ForegroundColor Yellow
            continue
        }
        Copy-Item $source $target -Force
        Write-Host ("       {0}.exe  <-  {1}" -f $tool, $source) -ForegroundColor DarkGray
    }
}

# --- 3. Smoke test --------------------------------------------------------
if (-not $SkipSmokeTest) {
    Write-Stage "3/3  Smoke test (packaged mode, /api/health)"
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()

    $smokeData = Join-Path $root "build\smoke-data"
    New-Item -ItemType Directory -Force -Path $smokeData | Out-Null
    $env:SVA_PORT = "$port"
    $env:SVA_PACKAGED = "1"
    $env:SVA_DATA_DIR = $smokeData
    if (Test-Path (Join-Path $ffmpegTarget "ffmpeg.exe")) { $env:SVA_FFMPEG_DIR = $ffmpegTarget }

    $engine = Start-Process -FilePath $exePath -PassThru -WindowStyle Hidden
    $health = $null
    try {
        $deadline = (Get-Date).AddSeconds(150)
        while ((Get-Date) -lt $deadline) {
            $engine.Refresh()
            if ($engine.HasExited) {
                $logPath = Join-Path $smokeData "logs\engine.log"
                Write-Host ("  [FAIL] engine exited (code {0}). Log: {1}" -f $engine.ExitCode, $logPath) -ForegroundColor Red
                if (Test-Path $logPath) { Get-Content $logPath -Tail 25 | ForEach-Object { "       $_" } }
                exit 1
            }
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2
                break
            }
            catch {
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $health) {
            Write-Host "  [FAIL] /api/health did not answer within 150 s" -ForegroundColor Red
            exit 1
        }
        Write-Host ("       http://127.0.0.1:{0}/api/health -> {1}" -f $port, ($health | ConvertTo-Json -Compress)) -ForegroundColor Green
    }
    finally {
        $engine.Refresh()
        if (-not $engine.HasExited) { Stop-Process -Id $engine.Id -Force }
        foreach ($name in @("SVA_PORT", "SVA_PACKAGED", "SVA_DATA_DIR", "SVA_FFMPEG_DIR", "SVA_BUNDLE_PROFILE")) {
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ""
Write-Host ("  Engine bundle ready: {0} MB" -f (Get-FolderMb $engineDir)) -ForegroundColor Green
Write-Host "  Next: npm run build:windows   (Tauri + NSIS installer)" -ForegroundColor DarkGray
Write-Host ""


