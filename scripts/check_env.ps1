# ===========================================================================
#  ShortVideoAI - development environment checklist
#  Usage:  npm run check      (or)   powershell -File scripts/check_env.ps1
# ===========================================================================
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
if (Test-Path "$root\.cache\cargo\bin\cargo.exe") {
    $env:CARGO_HOME = "$root\.cache\cargo"
    $env:RUSTUP_HOME = "$root\.cache\rustup"
    $env:PATH = "$root\.cache\cargo\bin;$root\resources\ffmpeg;$env:PATH"
}

$script:Fails = 0
$script:Warns = 0

function Write-Result {
    param(
        [string]$Name,
        [string]$Value,
        [ValidateSet("OK", "WARN", "FAIL")][string]$State,
        [string]$Hint = ""
    )

    switch ($State) {
        "OK"   { $color = "Green" }
        "WARN" { $color = "Yellow"; $script:Warns++ }
        "FAIL" { $color = "Red";    $script:Fails++ }
    }

    $label = $Name.PadRight(16)
    # NOTE: names are case-insensitive in PowerShell - do not reuse $State here.
    $tag = "[$State]".PadRight(7)
    Write-Host ("  {0} {1}" -f $label, $Value) -NoNewline
    Write-Host (" {0} {1}" -f $tag, $Hint) -ForegroundColor $color
}

function Get-CommandVersion {
    param([string]$Exe, [string[]]$CommandArgs)

    try {
        $output = & $Exe @CommandArgs 2>$null
        if (-not $output) { return $null }
        return ($output | Select-Object -First 1).ToString().Trim()
    }
    catch {
        return $null
    }
}

function Get-ExeVersion {
    param([string]$Path, [string[]]$CommandArgs)

    if (-not $Path) { return $null }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Path @CommandArgs 2>$null
    }
    catch {
        $output = $null
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if (-not $output) { return $null }
    return ($output | Select-Object -First 1).ToString().Trim()
}

# Resolve a tool from PATH, falling back to the install location used by
# winget/rustup. Winget only edits the user PATH, so a shell that was already
# open still reports "missing" for a correctly installed tool.
function Find-Tool {
    param([string]$Name, [string[]]$Fallback)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    foreach ($candidate in $Fallback) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }

    $wingetPackage = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $wingetPackage) {
        $hit = Get-ChildItem $wingetPackage -Recurse -Filter "$Name.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

Write-Host ""
Write-Host "=== ShortVideoAI :: environment check ===" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------- Node / npm
$node = Get-CommandVersion -Exe "node" -CommandArgs @("--version")
if ($node) {
    $major = 0
    if ($node -match "^v(\d+)") { $major = [int]$Matches[1] }
    if ($major -ge 20) { Write-Result "Node.js" $node "OK" } else { Write-Result "Node.js" $node "FAIL" "need >= 20.19" }
}
else {
    Write-Result "Node.js" "not found" "FAIL" "https://nodejs.org"
}

$npm = Get-CommandVersion -Exe "npm" -CommandArgs @("--version")
if ($npm) { Write-Result "npm" $npm "OK" } else { Write-Result "npm" "not found" "FAIL" "" }

# --------------------------------------------------------------- Python 3.12
$pyExe = $null
try {
    $pyExe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
}
catch { $pyExe = $null }

if ($pyExe -and (Test-Path $pyExe)) {
    $pyVer = & py -3.12 -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
    Write-Result "Python 3.12" "$pyVer ($pyExe)" "OK"
}
else {
    Write-Result "Python 3.12" "not found" "FAIL" "winget install Python.Python.3.12"
}

# --------------------------------------------------------------------- Rust
$cargoPath = Find-Tool -Name "cargo" -Fallback @("$root\.cache\cargo\bin\cargo.exe", "$env:USERPROFILE\.cargo\bin\cargo.exe")
$rustcPath = Find-Tool -Name "rustc" -Fallback @("$root\.cache\cargo\bin\rustc.exe", "$env:USERPROFILE\.cargo\bin\rustc.exe")

$rustc = Get-ExeVersion -Path $rustcPath -CommandArgs @("--version")
$cargo = Get-ExeVersion -Path $cargoPath -CommandArgs @("--version")

if ($rustc -and $cargo) {
    $onPath = [bool](Get-Command cargo -ErrorAction SilentlyContinue) -or (Test-Path "$root\.cache\cargo\bin\cargo.exe")
    if ($onPath) {
        Write-Result "Rust" "$rustc / $cargo" "OK"
    }
    else {
        Write-Result "Rust" "$rustc (installed)" "WARN" "reopen the terminal to load PATH"
    }
}
else {
    Write-Result "Rust" "not found" "FAIL" "winget install Rustlang.Rustup (then reopen the shell)"
}

# ------------------------------------------------------------------- FFmpeg
$ffmpegPath = Find-Tool -Name "ffmpeg" -Fallback @("$root\resources\ffmpeg\ffmpeg.exe")
$ffmpeg = Get-ExeVersion -Path $ffmpegPath -CommandArgs @("-version")
if ($ffmpeg) {
    if ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -or (Test-Path "$root\resources\ffmpeg\ffmpeg.exe")) {
        Write-Result "FFmpeg" $ffmpeg "OK"
    }
    else {
        Write-Result "FFmpeg" "$ffmpeg (installed)" "WARN" "reopen the terminal to load PATH"
    }
}
else {
    Write-Result "FFmpeg" "not found" "FAIL" "winget install Gyan.FFmpeg"
}

# ---------------------------------------------------------------- GPU / CUDA
try {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null | Select-Object -First 1)
}
catch { $gpu = $null }

if ($gpu) {
    Write-Result "NVIDIA GPU" $gpu.Trim() "OK" "(torch will use CUDA fp16)"
}
else {
    Write-Result "NVIDIA GPU" "none detected" "WARN" "engine falls back to CPU int8"
}

# ------------------------------------------------------------------- venv
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $torch = & $venvPython -c "import torch; print(torch.__version__ + ' cuda=' + str(torch.cuda.is_available()))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $torch) {
        Write-Result ".venv" "torch $torch" "OK"
    }
    else {
        Write-Result ".venv" "present, torch missing" "WARN" "npm run setup"
    }
}
else {
    Write-Result ".venv" "missing" "FAIL" "npm run setup"
}

# --------------------------------------------------------------- npm install
if (Test-Path (Join-Path $root "node_modules")) {
    Write-Result "node_modules" "present" "OK"
}
else {
    Write-Result "node_modules" "missing" "FAIL" "npm install"
}

# --------------------------------------------------------------- env files
$backendEnv = Join-Path $root "backend\.env"
if (Test-Path $backendEnv) { Write-Result "backend/.env" "present" "OK" } else { Write-Result "backend/.env" "missing" "WARN" "copy backend/.env.example" }

$frontendEnv = Join-Path $root "frontend\.env.local"
if (Test-Path $frontendEnv) { Write-Result "frontend/.env" "present" "OK" } else { Write-Result "frontend/.env" "missing" "WARN" "copy frontend/.env.example" }

# --------------------------------------------------------------- data folder
$dataDir = Join-Path $env:APPDATA "ShortVideoAI"
if (Test-Path $dataDir) { Write-Result "%APPDATA%\ShortVideoAI" "present" "OK" } else { Write-Result "%APPDATA%\ShortVideoAI" "created on first launch" "OK" }

Write-Host ""
if ($script:Fails -gt 0) {
    Write-Host ("  {0} blocking issue(s), {1} warning(s)." -f $script:Fails, $script:Warns) -ForegroundColor Red
    exit 1
}

Write-Host ("  Environment OK ({0} warning(s))." -f $script:Warns) -ForegroundColor Green
exit 0
