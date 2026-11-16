# ===========================================================================
#  ShortVideoAI - one-shot Windows development environment setup
#
#  Usage:
#    npm run setup                          # full offline-AI install (torch + models stack)
#    npm run setup -- -CoreOnly             # server deps only, no torch / AI wheels
#    npm run setup -- -ForceCpu             # force the CPU torch build
#    npm run setup -- -SkipAi               # torch, but skip faster-whisper / TTS / LLM
#    npm run setup -- -OcrGpu               # DirectML ONNX build: GPU OCR on any DX12 card
#    npm run setup -- -OcrCuda              # CUDA ONNX build: GPU OCR on NVIDIA + cuDNN 9
#
#  Idempotent: every step is skipped when it is already satisfied.
# ===========================================================================
[CmdletBinding()]
param(
    [switch]$CoreOnly,
    [switch]$SkipAi,
    [switch]$SkipRust,
    [switch]$SkipFfmpeg,
    [switch]$ForceCpu,
    [switch]$OcrGpu,
    [switch]$OcrCuda
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\python.exe"

# Keep temp and caches inside project folder to avoid bloating C: drive
$tmpDir = Join-Path $root ".tmp"
$cacheDir = Join-Path $root ".cache"
New-Item -ItemType Directory -Force -Path $tmpDir, $cacheDir | Out-Null
$env:TEMP = $tmpDir
$env:TMP = $tmpDir
$env:PIP_NO_CACHE_DIR = "1"
$env:npm_config_cache = Join-Path $cacheDir "npm"
$env:HF_HOME = Join-Path $cacheDir "huggingface"
$env:TORCH_HOME = Join-Path $cacheDir "torch"

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host ("--- {0}" -f $Text) -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Text)
    Write-Host ("    [ok]   {0}" -f $Text) -ForegroundColor Green
}

function Write-Skip {
    param([string]$Text)
    Write-Host ("    [skip] {0}" -f $Text) -ForegroundColor DarkGray
}

function Write-Warn {
    param([string]$Text)
    Write-Host ("    [warn] {0}" -f $Text) -ForegroundColor Yellow
}

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# Resolve a Python 3.12+ interpreter. The `py` launcher is preferred, but a
# bare `python` is accepted when it is new enough. Native stderr must never
# abort the probe, so the caller's error preference is restored afterwards.
function Find-Python {
    $candidates = @(
        @{ Exe = "py";         Pre = @("-3.12") },
        @{ Exe = "py";         Pre = @("-3.13") },
        @{ Exe = "python";     Pre = @() },
        @{ Exe = "python3.12"; Pre = @() }
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }

        $script = "import sys;print('%d.%d|%s' % (sys.version_info[0], sys.version_info[1], sys.executable))"
        $probeArgs = @($candidate.Pre) + @("-c", $script)

        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $probe = & $candidate.Exe @probeArgs 2>$null
        }
        catch {
            $probe = $null
        }
        finally {
            $ErrorActionPreference = $previous
        }

        if (-not $probe) { continue }

        $line = ($probe | Select-Object -First 1).ToString().Trim()
        $parts = $line.Split("|")
        if ($parts.Count -ne 2) { continue }

        $major, $minor = $parts[0].Split(".")
        if ([int]$major -eq 3 -and [int]$minor -ge 12) {
            return $parts[1].Trim()
        }
    }
    return $null
}

# Which ONNX Runtime build is installed: the CPU wheel, a GPU build, or nothing.
# `onnxruntime`, `onnxruntime-directml` and `onnxruntime-gpu` all install the
# same `onnxruntime` module, so this answer decides which one to restore after
# the AI stack reinstalls the CPU wheel (requirements-ai.txt installs it).
function Get-OnnxPack {
    param([string]$Python)
    $script = "import importlib.metadata as m;print(next((p for p in ('onnxruntime-directml','onnxruntime-gpu') if p in {d.metadata['Name'].lower() for d in m.distributions()}),''))"
    $probe = & $Python -c $script 2>$null
    if ($probe) { return ($probe | Select-Object -First 1).ToString().Trim() }
    return ""
}

# A GPU dist-info on disk proves nothing: both distributions install the same
# `onnxruntime` package, so a later `pip install onnxruntime` (requirements-ai.txt)
# overwrites the files with the CPU build while the `-gpu` metadata stays
# behind. Only the providers the module really loads tell the two apart.
function Test-OnnxPackWorking {
    param([string]$Python, [string]$Pack)
    $provider = if ($Pack -eq "onnxruntime-gpu") { "CUDAExecutionProvider" } else { "DmlExecutionProvider" }
    $script = "import onnxruntime as o;print(1 if '$provider' in o.get_available_providers() else '')"
    $probe = & $Python -c $script 2>$null
    return [bool]($probe | Select-Object -First 1)
}

Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host " ShortVideoAI - environment setup" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. Python 3.12 interpreter
# ---------------------------------------------------------------------------
Write-Step "Python 3.12"

$pyLauncher = Find-Python

if (-not $pyLauncher -or -not (Test-Path $pyLauncher)) {
    Write-Warn "Python 3.12 not found. Install it first:"
    Write-Host "           winget install -e --id Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
Write-Ok "interpreter: $pyLauncher"

# ---------------------------------------------------------------------------
# 2. Virtual environment
# ---------------------------------------------------------------------------
Write-Step "Virtual environment (.venv)"

if (Test-Path $python) {
    Write-Skip "$venv already exists"
}
else {
    & $pyLauncher -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtual environment." }
    Write-Ok "created $venv"
}

& $python -m pip install --upgrade pip setuptools wheel --quiet
Write-Ok "pip upgraded"

# ---------------------------------------------------------------------------
# 3. Core engine dependencies
# ---------------------------------------------------------------------------
Write-Step "Core dependencies (requirements.txt + requirements-dev.txt)"

Push-Location $backend
try {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Core requirements failed to install." }
    & $python -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw "Dev requirements failed to install." }
}
finally {
    Pop-Location
}
Write-Ok "FastAPI stack installed"

# The OCR device choice must survive a re-run: step 4 puts the CPU ONNX wheel
# back (requirements-ai.txt depends on it), so a GPU build is restored in step 6.
$ocrPack = ""
if ($OcrCuda) { $ocrPack = "onnxruntime-gpu" }
elseif ($OcrGpu) { $ocrPack = "onnxruntime-directml" }
elseif (-not $CoreOnly -and -not $SkipAi) { $ocrPack = Get-OnnxPack $python }

if ($CoreOnly) {
    Write-Warn "CoreOnly: skipping torch and the AI stack."
}

# ---------------------------------------------------------------------------
# 4. AI stack (faster-whisper, SenseVoice, LLM, vocal isolation)
#    Runs BEFORE torch on purpose: several packages depend on torch, and a bare
#    `pip install torch` resolves to the CPU-only PyPI wheel on Windows. PyTorch
#    is therefore re-installed afterwards from the CUDA index so it wins.
# ---------------------------------------------------------------------------
if (-not $CoreOnly -and -not $SkipAi) {
    Write-Step "AI stack (this downloads 2-4 GB on first run)"

    Push-Location $backend
    try {
        & $python -m pip install -r requirements-ai.txt
        $aiOk = ($LASTEXITCODE -eq 0)
    }
    finally {
        Pop-Location
    }

    if ($aiOk) {
        Write-Ok "AI stack installed"
    }
    else {
        Write-Warn "Bulk install failed - retrying group by group (some packages are optional)."

        & $python -m pip install numpy scipy soundfile faster-whisper ctranslate2 sherpa-onnx onnxruntime openai google-genai
        if ($LASTEXITCODE -eq 0) { Write-Ok "STT / API clients / ONNX installed" } else { Write-Warn "STT group failed" }

        & $python -m pip install audio-separator
        if ($LASTEXITCODE -eq 0) { Write-Ok "audio-separator installed" } else { Write-Warn "audio-separator failed - vocal isolation disabled" }

        & $python -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --only-binary=:all:
        if ($LASTEXITCODE -eq 0) { Write-Ok "llama-cpp-python installed" } else { Write-Warn "llama-cpp-python failed - offline translation falls back to the Gemini API" }
    }
}

# ---------------------------------------------------------------------------
# 5. PyTorch - MUST run after the AI stack (see note in step 4)
# ---------------------------------------------------------------------------
if (-not $CoreOnly) {
    Write-Step "PyTorch (CUDA fp16 on NVIDIA, CPU int8 otherwise)"

    $hasNvidia = $false
    if (-not $ForceCpu) {
        try {
            $null = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null
            $hasNvidia = ($LASTEXITCODE -eq 0)
        }
        catch { $hasNvidia = $false }
    }

    if ($hasNvidia) {
        # cu130 is the newest Windows CUDA channel; cu128 tops out at torch 2.9.
        $indexUrl = "https://download.pytorch.org/whl/cu130"
        $torchPins = @("torch==2.14.1+cu130", "torchvision==0.29.1+cu130")
        Write-Host "    NVIDIA GPU detected - installing CUDA 13.0 build" -ForegroundColor DarkGray
    }
    else {
        $indexUrl = "https://download.pytorch.org/whl/cpu"
        $torchPins = @("torch")
        Write-Host "    No NVIDIA GPU - installing CPU build" -ForegroundColor DarkGray
    }

    $probeScript = "import torch;print(torch.__version__ + '|' + str(torch.cuda.is_available()))"
    $torchProbe = & $python -c $probeScript 2>$null
    $torchState = if ($torchProbe) { ($torchProbe | Select-Object -First 1).ToString().Trim() } else { "" }

    $needsTorch = $true
    if ($LASTEXITCODE -eq 0 -and $torchState) {
        $bits = $torchState.Split("|")
        $isCudaBuild = $bits[0] -like "*+cu*"
        $cudaWorks = ($bits.Count -gt 1) -and ($bits[1] -eq "True")

        if ($hasNvidia) {
            # A CPU wheel silently installed by a dependency must be replaced.
            $needsTorch = -not ($isCudaBuild -and $cudaWorks)
            if ($needsTorch) { Write-Warn "found $torchState - replacing with the CUDA build" }
        }
        else {
            $needsTorch = $false
        }
    }

    if (-not $needsTorch) {
        Write-Skip "torch $torchState already correct"
    }
    else {
        & $python -m pip install --force-reinstall @torchPins --index-url $indexUrl --extra-index-url https://pypi.org/simple
        if ($LASTEXITCODE -ne 0) { throw "PyTorch install failed." }
        Write-Ok "torch installed from $indexUrl"

        $verify = & $python -c $probeScript 2>$null
        if ($verify) { Write-Ok "verify: $($verify | Select-Object -First 1)" }
    }
}

# ---------------------------------------------------------------------------
# 5b. OmniVoice TTS (offline dubbing)
#     torchaudio is frozen at 2.11 and its PyPI wheel pins torch==2.11.1, so it
#     must go in with --no-deps: installed normally it drags the CUDA build above
#     back to a CPU 2.11 one. omnivoice imports torchaudio at module level, so
#     skipping it breaks `import omnivoice` even though we never call it. The
#     weights (3.3 GB) arrive on the first dubbed line, not here.
# ---------------------------------------------------------------------------
if (-not $CoreOnly) {
    Write-Step "OmniVoice TTS (offline dubbing)"

    & $python -m pip install "torchaudio==2.11.1" --no-deps --quiet
    if ($LASTEXITCODE -ne 0) { Write-Warn "torchaudio 2.11.1 failed - omnivoice cannot import" }

    & $python -m pip install omnivoice --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "omnivoice failed - dubbing stays on CapCut / VoiceStudio"
    }
    else {
        Write-Ok "omnivoice installed (weights download on the first dubbed line)"
    }
}

# ---------------------------------------------------------------------------
# 6. ONNX Runtime build for OCR (CPU when nothing is asked for)
#    Settings → OCR → "Thiết bị chạy" can only pick the GPU when this wheel has
#    a GPU provider. The three distributions install the same module, so this
#    is a swap: uninstall whichever is there, install the one that is wanted.
# ---------------------------------------------------------------------------
if (-not $CoreOnly -and $ocrPack) {
    Write-Step "ONNX Runtime for OCR ($ocrPack)"

    $present = Get-OnnxPack $python
    if ($present -eq $ocrPack -and (Test-OnnxPackWorking $python $ocrPack)) {
        Write-Skip "$ocrPack already installed"
    }
    else {
        if ($present -eq $ocrPack) {
            Write-Warn "$ocrPack files were replaced by the CPU wheel - reinstalling"
        }
        & $python -m pip uninstall -y onnxruntime onnxruntime-directml onnxruntime-gpu --quiet
        & $python -m pip install --no-input $ocrPack
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "$ocrPack failed - OCR stays on the CPU (Settings → OCR → Thiết bị chạy)"
        }
    }

    $providerScript = "import onnxruntime as o;print(' '.join(o.get_available_providers()))"
    $providers = & $python -c $providerScript 2>$null
    if ($providers) { Write-Ok "providers: $($providers | Select-Object -First 1)" }
    if (-not (Test-OnnxPackWorking $python $ocrPack)) {
        Write-Warn "$ocrPack is installed but its provider does not load - check the CUDA/cuDNN runtime"
    }
}

# ---------------------------------------------------------------------------
# 7. Rust toolchain (required by Tauri)
# ---------------------------------------------------------------------------
Write-Step "Rust toolchain"

if (Test-CommandExists "cargo") {
    Write-Skip "cargo already on PATH"
}
elseif ($SkipRust) {
    Write-Warn "Skipped. Tauri cannot build without Rust."
}
elseif (Test-CommandExists "winget") {
    Write-Host "    installing Rustup via winget..." -ForegroundColor DarkGray
    & winget install -e --id Rustlang.Rustup --accept-package-agreements --accept-source-agreements
    Write-Warn "Rust installed - close and reopen the terminal so PATH picks up cargo."
}
else {
    Write-Warn "Install Rust manually: https://rustup.rs (MSVC toolchain required)."
}

if (-not (Test-Path (Join-Path $env:USERPROFILE ".cargo\bin"))) {
    Write-Warn "MSVC build tools are also required: winget install Microsoft.VisualStudio.2022.BuildTools"
}

# ---------------------------------------------------------------------------
# 8. FFmpeg
# ---------------------------------------------------------------------------
Write-Step "FFmpeg"

if (Test-CommandExists "ffmpeg") {
    Write-Skip "ffmpeg already on PATH"
}
elseif ($SkipFfmpeg) {
    Write-Warn "Skipped. The engine cannot extract audio or render video without FFmpeg."
}
elseif (Test-CommandExists "winget") {
    Write-Host "    installing FFmpeg via winget..." -ForegroundColor DarkGray
    & winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    Write-Warn "FFmpeg installed - reopen the terminal before running the engine."
}
else {
    Write-Warn "Install FFmpeg manually and add it to PATH."
}

# ---------------------------------------------------------------------------
# 9. Node dependencies
# ---------------------------------------------------------------------------
Write-Step "Node dependencies (npm install)"

Push-Location $root
try {
    & npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
}
finally {
    Pop-Location
}
Write-Ok "workspace dependencies installed"

# ---------------------------------------------------------------------------
# 10. Configuration files
# ---------------------------------------------------------------------------
Write-Step "Configuration"

$backendEnv = Join-Path $backend ".env"
if (Test-Path $backendEnv) {
    Write-Skip "backend/.env already exists"
}
else {
    Copy-Item (Join-Path $backend ".env.example") $backendEnv
    Write-Ok "created backend/.env"
}

$frontendEnv = Join-Path $root "frontend\.env.local"
if (Test-Path $frontendEnv) {
    Write-Skip "frontend/.env.local already exists"
}
else {
    Copy-Item (Join-Path $root "frontend\.env.example") $frontendEnv
    Write-Ok "created frontend/.env.local"
}

$dataDir = Join-Path $cacheDir "ShortVideoAI"
foreach ($sub in @("models", "models\separator", "cache", "projects", "renders", "logs", "temp")) {
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $dataDir $sub)
}
Write-Ok "data folders ready at $dataDir"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "===========================================" -ForegroundColor Green
Write-Host " Setup complete" -ForegroundColor Green
Write-Host "===========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  npm run check    verify the toolchain" -ForegroundColor White
Write-Host "  npm run dev      FastAPI engine + Tauri app" -ForegroundColor White
Write-Host ""
if (-not (Test-CommandExists "cargo")) {
    Write-Host "  Reopen your terminal first (Rust PATH)." -ForegroundColor Yellow
}
