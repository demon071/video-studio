# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the ShortVideoAI engine.

Build it through ``scripts/build_backend.ps1`` which sets ``SVA_BUNDLE_PROFILE``:

* ``core`` — server, FFmpeg pipeline, model manager. ~120 MB, starts in a
  second, no CUDA runtime. AI stages report a friendly "AI stack not bundled"
  error because every heavy import in ``app/`` is lazy.
* ``full`` — adds the offline AI stack (torch/CUDA, CTranslate2, llama.cpp,
  ONNX Runtime, audio-separator). ~3.5 GB onedir; the NSIS installer for this
  profile is close to the 2 GB format limit.

The output is a *onedir* folder (``--onedir`` equivalent: EXE + COLLECT) and
``console=False`` (``--noconsole``): the Tauri sidecar never shows a console.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

PROFILE = (os.environ.get("SVA_BUNDLE_PROFILE") or "core").strip().lower()
if PROFILE not in {"core", "full"}:
    raise SystemExit(f"SVA_BUNDLE_PROFILE must be 'core' or 'full', got {PROFILE!r}")

BACKEND = Path(SPECPATH).parent
ICON = BACKEND.parent / "frontend" / "src-tauri" / "icons" / "icon.ico"
RTHOOK = BACKEND / "packaging" / "rthook_console.py"

hiddenimports = [
    # uvicorn resolves its loop/protocol/lifespan implementations by string.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
    "websockets.legacy.server",
    "websockets.legacy.client",
    "python_multipart",
    "email.mime.multipart",
]
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("httpcore")

datas = []
binaries = []

# Every module here is imported lazily at call time, so PyInstaller can only
# see them when we ask for them explicitly.
AI_PACKAGES = {
    "core": [],
    "full": [
        "torch",
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "audio_separator",
        "soundfile",
        "av",
        "sherpa_onnx",
        "rapidocr",
        "piper",
        "vieneu",
        "vietnormalizer",
        "sea_g2p",
        "numpy",
        "scipy",
        "librosa",
    ],
}

# Cloud translation providers (OpenAI, Gemini) are lazy imports as well and are
# not part of the offline stack, but shipping them costs a few MB and keeps
# provider=openai / provider=gemini usable in the frozen engine. Groq and
# VoiceStudio are plain httpx calls, so they need no SDK.
CLOUD_SDKS = ("openai", "google.genai")
# Optional extras: bundled when the build machine has them, skipped otherwise.
# `omnivoice` is the offline dubbing engine (`pip install omnivoice`).
OPTIONAL_PACKAGES = (*CLOUD_SDKS, "omnivoice")

excludes = [
    "tkinter",
    "matplotlib",
    "IPython",
    "pytest",
    "PySide2",
    "PySide6",
    "PyQt5",
    "PyQt6",
    "notebook",
    "tensorboard",
    "torchvision",
    "torchaudio",
]

if PROFILE == "core":
    # Keep the small installer honest: nothing from the AI stack leaks in.
    excludes += [
        "torch",
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "audio_separator",
        "soundfile",
        "av",
        "sherpa_onnx",
        "rapidocr",
        "piper",
        "vieneu",
        "omnivoice",
        "scipy",
        "librosa",
    ]
else:
    for package in AI_PACKAGES["full"]:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden

    import importlib.util

    for package in OPTIONAL_PACKAGES:
        try:
            installed = importlib.util.find_spec(package) is not None
        except (ImportError, ValueError):  # broken install / shadowed namespace
            installed = False
        if not installed:
            print(f"[engine.spec] {package} is not installed - skipping")
            continue
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden

a = Analysis(
    [str(BACKEND / "packaging" / "entry.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(RTHOOK)],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="shortvideoai-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="engine",
)
