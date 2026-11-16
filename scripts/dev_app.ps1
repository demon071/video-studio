# ===========================================================================
#  ShortVideoAI - start the Tauri desktop shell in development
#  Keeps CARGO_HOME, RUSTUP_HOME, TEMP/TMP and SVA_DATA_DIR on D: drive
# ===========================================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root "frontend"
$tmpDir = Join-Path $root ".tmp"
$cacheDir = Join-Path $root ".cache"
$cargoHome = Join-Path $cacheDir "cargo"
$rustupHome = Join-Path $cacheDir "rustup"
$ffmpegDir = Join-Path $root "resources\ffmpeg"

New-Item -ItemType Directory -Force -Path $tmpDir, $cacheDir | Out-Null

$env:TEMP = $tmpDir
$env:TMP = $tmpDir
if (-not $env:SVA_DATA_DIR) { $env:SVA_DATA_DIR = Join-Path $cacheDir "ShortVideoAI" }

if (Test-Path (Join-Path $cargoHome "bin\cargo.exe")) {
    $env:CARGO_HOME = $cargoHome
    $env:RUSTUP_HOME = $rustupHome
    $env:PATH = "$(Join-Path $cargoHome 'bin');$ffmpegDir;$env:PATH"
} else {
    $env:PATH = "$ffmpegDir;$env:PATH"
}

# Ensure Tauri resource directories exist so tauri-build does not fail in dev mode
$tauriResEngine = Join-Path $frontend "src-tauri\resources\engine"
$tauriResFfmpeg = Join-Path $frontend "src-tauri\resources\ffmpeg"
New-Item -ItemType Directory -Force -Path $tauriResEngine, $tauriResFfmpeg | Out-Null
$keepEngine = Join-Path $tauriResEngine ".keep"
$keepFfmpeg = Join-Path $tauriResFfmpeg ".keep"
if (-not (Test-Path $keepEngine)) { Set-Content -Path $keepEngine -Value "" }
if (-not (Test-Path $keepFfmpeg)) { Set-Content -Path $keepFfmpeg -Value "" }

# Free port 1420 if an older Vite dev instance is still holding it
$existingVite = Get-NetTCPConnection -LocalPort 1420 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pidToKill in $existingVite) {
    if ($pidToKill -and $pidToKill -ne $PID) {
        Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
    }
}

Push-Location $frontend
try {
    & npx tauri dev
}
finally {
    Pop-Location
}
