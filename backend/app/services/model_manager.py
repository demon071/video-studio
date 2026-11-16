"""On-demand model manager (``%APPDATA%\\ShortVideoAI\\models``).

Nothing heavy ships inside the installer. The engine exposes a catalogue, the
UI asks "what is already here?", and every download streams through this module
with byte-level progress published over ``/ws/progress``.

Layout (one folder per catalogue key, mirroring ``model_dir``)::

    models/
      whisper/<key>/            faster-whisper CTranslate2 snapshot
      sensevoice/<key>/         SenseVoice ONNX + tokens (sherpa-onnx)
      llm/<key>/<file>.gguf     Qwen2.5 / Gemma 3 GGUF for llama-cpp
      separator/<key>/<file>.onnx  MDX-Net vocal isolation
      ocr/<key>/<file>.onnx     OCR detector + recogniser pairs
      tts/<key>/                dubbing voices (OmniVoice downloads through torch)

Resolvers must go through ``model_dir()``: a flat ``<kind>/<file>`` lookup goes
stale as soon as ``download()`` puts the file in its key folder.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings, hf_token
from app.core.progress import NULL_REPORTER, Reporter

log = logging.getLogger(__name__)

HF_BASE = "https://huggingface.co"
CHUNK = 1 << 20  # 1 MiB
MARKER = ".sva-complete"
# faster-whisper refuses a directory without this file, so it doubles as the
# "snapshot is usable" probe.
WHISPER_CORE = "model.bin"


class ModelError(RuntimeError):
    """Raised when a model is missing or cannot be fetched."""


@dataclass(frozen=True)
class RemoteFile:
    filename: str                      # destination path relative to the model dir
    repo_id: str | None = None
    repo_path: str | None = None       # path inside the repo (defaults to filename)
    url: str | None = None
    size_mb: float = 0.0


@dataclass(frozen=True)
class ModelSpec:
    key: str
    kind: str                          # stt | llm | tts | separator | ocr
    label: str
    description: str
    size_mb: float
    files: tuple[RemoteFile, ...] = ()
    snapshot_repo: str | None = None   # whole HuggingFace repo (faster-whisper)
    requires: tuple[str, ...] = ()     # importable packages needed at runtime
    licence: str = ""
    # OCR engines look for their onnx files directly inside one folder, with no
    # per-key subdirectory, so those entries opt out of the key folder.
    flat: bool = False
    # Nothing to download: the runtime ships with the package (OmniVoice).
    pip_hint: str = ""
    # Speech engines other than whisper keep their own folder name.
    folder_name: str = ""

    @property
    def folder(self) -> str:
        if self.folder_name:
            return self.folder_name
        return {
            "stt": "whisper",
            "llm": "llm",
            "tts": "tts",
            "separator": "separator",
            "ocr": "ocr",
        }[self.kind]


CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        key="whisper-small",
        kind="stt",
        label="Whisper small",
        description="Offline speech-to-text, ~480 MB, the CPU default.",
        size_mb=480,
        snapshot_repo="Systran/faster-whisper-small",
        requires=("faster_whisper", "ctranslate2"),
        licence="MIT",
    ),
    ModelSpec(
        key="whisper-medium",
        kind="stt",
        label="Whisper medium",
        description="Best accuracy/speed balance on a 4 GB NVIDIA GPU (float16).",
        size_mb=1500,
        snapshot_repo="Systran/faster-whisper-medium",
        requires=("faster_whisper", "ctranslate2"),
        licence="MIT",
    ),
    ModelSpec(
        key="whisper-large-v3-turbo",
        kind="stt",
        label="Whisper large-v3-turbo",
        description="Highest accuracy, needs 6 GB+ VRAM (or patience on CPU).",
        size_mb=1620,
        snapshot_repo="Systran/faster-whisper-large-v3-turbo",
        requires=("faster_whisper", "ctranslate2"),
        licence="MIT",
    ),
    ModelSpec(
        key="sensevoice-zh-en-ja-ko-yue",
        kind="stt",
        label="SenseVoice (zh · en · ja · ko · yue)",
        description=(
            "Offline recogniser for Chinese drama on CPU: punctuation and numbers "
            "included, and it cannot answer with another language. Ported from CapCap."
        ),
        size_mb=240,
        files=(
            RemoteFile(
                filename="model.int8.onnx",
                repo_id="csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
                size_mb=239,
            ),
            RemoteFile(
                filename="tokens.txt",
                repo_id="csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
                size_mb=0.4,
            ),
        ),
        requires=("sherpa_onnx",),
        licence="Apache-2.0",
        folder_name="sensevoice",
    ),
    ModelSpec(
        key="qwen2.5-3b-instruct-q4_k_m",
        kind="llm",
        label="Qwen2.5 3B Instruct (Q4_K_M)",
        description="Offline subtitle translation with JSON-constrained output; the default translator.",
        size_mb=1980,
        files=(
            RemoteFile(
                filename="qwen2.5-3b-instruct-q4_k_m.gguf",
                repo_id="Qwen/Qwen2.5-3B-Instruct-GGUF",
                size_mb=1980,
            ),
        ),
        requires=("llama_cpp",),
        licence="Apache-2.0",
    ),
    ModelSpec(
        key="gemma3-4b-it-q4_k_m",
        kind="llm",
        label="Gemma 3 4B IT (Q4_K_M)",
        description=(
            "Second offline translator: Google's multilingual 4B, larger and slower "
            "per line than the 3B. Pick it in Settings → Dịch offline."
        ),
        size_mb=2375,
        files=(
            RemoteFile(
                filename="gemma-3-4b-it-q4_k_m.gguf",
                repo_id="unsloth/gemma-3-4b-it-GGUF",
                repo_path="gemma-3-4b-it-Q4_K_M.gguf",
                size_mb=2375,
            ),
        ),
        requires=("llama_cpp",),
        licence="Gemma",
    ),
    ModelSpec(
        key="uvr-mdx-inst-hq3",
        kind="separator",
        label="UVR MDX-Net Inst HQ3",
        description="Splits voice from music when a music-only bed is wanted (dubbing keeps the original track).",
        size_mb=110,
        files=(
            RemoteFile(
                filename="UVR-MDX-NET-Inst_HQ_3.onnx",
                url=(
                    "https://github.com/TRvlvr/model_repo/releases/download/"
                    "all_public_uvr_models/UVR-MDX-NET-Inst_HQ_3.onnx"
                ),
                size_mb=110,
            ),
        ),
        requires=("audio_separator",),
        licence="MIT",
    ),
    ModelSpec(
        key="omnivoice-v1",
        kind="tts",
        label="OmniVoice (k2-fsa)",
        description=(
            "Zero-shot cloning voice for 646 languages, Vietnamese included, on torch: "
            "`auto`, a described voice or a 3-10 s sample. `pip install omnivoice` brings "
            "the runtime; the 3.3 GB snapshot lands here on the first dubbed line."
        ),
        size_mb=3270,
        requires=("omnivoice",),
        licence="Apache-2.0",
        pip_hint="pip install omnivoice",
    ),
    ModelSpec(
        key="ocr-ppocrv6-tiny",
        kind="ocr",
        label="PP-OCRv6 Tiny — nhanh nhất",
        description=(
            "Small detector (9.9 MB) plus the standard recogniser (21 MB) for the "
            "fast OCR profile. Without it, OCR uses the pair bundled with rapidocr."
        ),
        size_mb=31,
        files=(
            RemoteFile(
                filename="PP-OCRv6_det_tiny.onnx",
                url=(
                    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/"
                    "v3.10.0/onnx/PP-OCRv6/det/PP-OCRv6_det_tiny.onnx"
                ),
                size_mb=9.9,
            ),
            RemoteFile(
                filename="PP-OCRv6_rec_small.onnx",
                url=(
                    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/"
                    "v3.10.0/onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx"
                ),
                size_mb=21,
            ),
        ),
        requires=("rapidocr",),
        licence="Apache-2.0",
        flat=True,
    ),
    ModelSpec(
        key="ocr-korean-v5",
        kind="ocr",
        label="PP-OCRv5 Korean — Hàn Quốc",
        description=(
            "PP-OCRv6 dropped Korean; this v5 pair keeps hardsub extraction "
            "working for Korean clips (detector + korean recogniser)."
        ),
        size_mb=26,
        files=(
            RemoteFile(
                filename="ch_PP-OCRv5_det_mobile.onnx",
                url=(
                    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/"
                    "v3.10.0/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx"
                ),
                size_mb=4.7,
            ),
            RemoteFile(
                filename="korean_PP-OCRv5_rec_mobile.onnx",
                url=(
                    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/"
                    "v3.10.0/onnx/PP-OCRv5/rec/korean_PP-OCRv5_rec_mobile.onnx"
                ),
                size_mb=21,
            ),
        ),
        requires=("rapidocr",),
        licence="Apache-2.0",
        flat=True,
    ),
    ModelSpec(
        key="pyannote-sherpa-diarization",
        kind="stt",
        label="Speaker Diarization (Pyannote + 3D-Speaker)",
        description=(
            "Offline speaker turn segmentation and embedding via sherpa-onnx. "
            "Tags subtitle lines with SPEAKER_00, SPEAKER_01, ... for multi-speaker dubbing."
        ),
        size_mb=42,
        files=(
            RemoteFile(
                filename="model.int8.onnx",
                repo_id="csukuangfj/sherpa-onnx-pyannote-segmentation-3-0",
                size_mb=1.5,
            ),
            RemoteFile(
                filename="3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
                url=(
                    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                    "speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
                ),
                size_mb=40.5,
            ),
        ),
        requires=("sherpa_onnx",),
        licence="MIT",
        folder_name="diarization",
    ),
)

MODEL_INDEX: dict[str, ModelSpec] = {spec.key: spec for spec in CATALOG}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def model_root(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    root = settings.models_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def model_dir(spec: ModelSpec, settings: Settings | None = None) -> Path:
    root = model_root(settings) / spec.folder
    return root if spec.flat else root / spec.key


def _expected_files(spec: ModelSpec, directory: Path | None = None) -> list[Path]:
    folder = directory or model_dir(spec)
    if spec.snapshot_repo:
        return [folder / WHISPER_CORE]
    return [folder / item.filename for item in spec.files]


def _package_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover - broken install
        return False


def size_on_disk(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        return round(path.stat().st_size / 1024**2, 1)
    total = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return round(total / 1024**2, 1)


@dataclass
class ModelStatus:
    key: str
    kind: str
    label: str
    description: str
    size_mb: float
    downloaded: bool
    installed_mb: float
    path: str
    missing_packages: list[str] = field(default_factory=list)
    licence: str = ""
    pip_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "key": self.key,
            "kind": self.kind,
            "label": self.label,
            "description": self.description,
            "size_mb": self.size_mb,
            "downloaded": self.downloaded,
            "installed_mb": self.installed_mb,
            "path": self.path,
            "missing_packages": list(self.missing_packages),
            "licence": self.licence,
            "pip_hint": self.pip_hint,
        }
        return data


def status(spec: ModelSpec, settings: Settings | None = None) -> ModelStatus:
    directory = model_dir(spec, settings)
    expected = _expected_files(spec, directory)
    if expected:
        downloaded = all(item.exists() for item in expected)
    else:
        # Nothing to fetch (OmniVoice): "installed" means the runtime is importable.
        downloaded = bool(spec.requires) and all(_package_available(pkg) for pkg in spec.requires)
    return ModelStatus(
        key=spec.key,
        kind=spec.kind,
        label=spec.label,
        description=spec.description,
        size_mb=spec.size_mb,
        downloaded=downloaded,
        installed_mb=size_on_disk(directory),
        path=str(directory),
        missing_packages=[pkg for pkg in spec.requires if not _package_available(pkg)],
        licence=spec.licence,
        pip_hint=spec.pip_hint,
    )


def list_models(settings: Settings | None = None) -> list[ModelStatus]:
    return [status(spec, settings) for spec in CATALOG]


def require(spec_key: str) -> ModelSpec:
    try:
        return MODEL_INDEX[spec_key]
    except KeyError as exc:
        raise ModelError(f"Unknown model {spec_key!r}") from exc


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def _headers() -> dict[str, str]:
    token = hf_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _hf_url(repo_id: str, path: str) -> str:
    return f"{HF_BASE}/{repo_id}/resolve/main/{quote(path)}?download=true"


def _snapshot_manifest(repo_id: str) -> list[tuple[str, str, float]]:
    """``[(remote_path, local_name, size_mb), ...]`` for a whole HF repo."""
    try:
        from huggingface_hub import HfApi  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - core dependency
        raise ModelError("huggingface_hub is required to download model snapshots") from exc

    info = HfApi().model_info(repo_id, files_metadata=True)
    manifest: list[tuple[str, str, float]] = []
    for sibling in info.siblings or []:
        name = sibling.rfilename
        if name in {".gitattributes"} or name.lower().startswith("readme"):
            continue
        size_mb = float(getattr(sibling, "size", 0) or 0) / 1024**2
        manifest.append((name, name, size_mb))
    if not manifest:
        raise ModelError(f"{repo_id} exposes no downloadable files")
    return manifest


def _stream(url: str, destination: Path, on_bytes: Callable[[int], None]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    timeout = httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0)
    with httpx.stream("GET", url, follow_redirects=True, headers=_headers(), timeout=timeout) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=CHUNK):
                handle.write(chunk)
                on_bytes(len(chunk))
    partial.replace(destination)


def download(
    spec_key: str,
    *,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch one catalogue entry, reporting a 0..1 fraction per stage."""
    settings = settings or get_settings()
    spec = require(spec_key)
    directory = model_dir(spec, settings)
    directory.mkdir(parents=True, exist_ok=True)

    existing = status(spec, settings)
    if existing.downloaded and not force:
        report(stage="model", fraction=1.0, message=f"{spec.label} already installed")
        return {"key": spec.key, "path": str(directory), "downloaded": True, "skipped": True}

    missing_packages = existing.missing_packages
    if missing_packages:
        report(
            stage="model",
            fraction=0.0,
            message=f"warning: python packages missing ({', '.join(missing_packages)})",
        )

    if not spec.files and not spec.snapshot_repo:
        # Nothing to stream: the runtime comes from pip (OmniVoice).
        raise ModelError(
            f"{spec.label} ships with its Python package - {spec.pip_hint or 'pip install the runtime'}"
        )

    report(stage="download", fraction=0.0, message=f"preparing {spec.label}")

    if spec.snapshot_repo:
        try:
            manifest = _snapshot_manifest(spec.snapshot_repo)
        except Exception as exc:  # noqa: BLE001 - offline / auth problems
            raise ModelError(f"Cannot list {spec.snapshot_repo}: {exc}") from exc
        jobs: list[tuple[str, str, float, str | None]] = [
            (_hf_url(spec.snapshot_repo, remote), local, size, None) for remote, local, size in manifest
        ]
    else:
        jobs = []
        for item in spec.files:
            if item.url:
                url = item.url
            elif item.repo_id:
                url = _hf_url(item.repo_id, item.repo_path or item.filename)
            else:  # pragma: no cover - catalogue invariant
                raise ModelError(f"{spec.key}: no source for {item.filename}")
            jobs.append((url, item.filename, item.size_mb, None))

    total_mb = sum(max(size, 0.0) for _, _, size, _ in jobs) or float(spec.size_mb) or 1.0

    def make_on_bytes(name: str, base_mb: float, total: float) -> Callable[[int], None]:
        """Byte counter for one file: reports the aggregate download fraction."""
        state = {"done": 0.0}

        def on_bytes(count: int) -> None:
            state["done"] += count / 1024**2
            fraction = min((base_mb + state["done"]) / total, 0.995)
            report(stage="download", fraction=fraction, message=f"{name} ({fraction * 100:.0f}%)")

        return on_bytes

    done_mb = 0.0
    for url, local_name, _, _ in jobs:
        target = directory / local_name
        target.parent.mkdir(parents=True, exist_ok=True)

        report(stage="download", fraction=min(done_mb / total_mb, 0.99), message=f"downloading {local_name}")
        _stream(url, target, make_on_bytes(local_name, done_mb, total_mb))
        done_mb += target.stat().st_size / 1024**2 if target.is_file() else 0.0

    (directory / MARKER).write_text(f"{spec.key}\n", encoding="utf-8")
    report(stage="model", fraction=1.0, message=f"{spec.label} ready")

    return {
        "key": spec.key,
        "path": str(directory),
        "downloaded": True,
        "installed_mb": size_on_disk(directory),
    }


def remove(spec_key: str, settings: Settings | None = None) -> bool:
    """Delete an installed model (the UI offers this to reclaim disk space)."""
    spec = require(spec_key)
    directory = model_dir(spec, settings)
    if not directory.exists():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# Resolvers used by the AI services
# ---------------------------------------------------------------------------
def whisper_reference(size: str, settings: Settings | None = None) -> tuple[str, Path]:
    """Return ``(model_reference, download_root)`` for faster-whisper.

    A local directory wins so an already-downloaded model never touches the
    network; otherwise the canonical repo id is returned and faster-whisper
    downloads it into the managed folder.
    """
    settings = settings or get_settings()
    root = model_root(settings) / "whisper"
    root.mkdir(parents=True, exist_ok=True)

    for candidate in (root / f"whisper-{size}", root / size):
        if (candidate / WHISPER_CORE).is_file():
            return str(candidate), root

    return f"Systran/faster-whisper-{size}", root


def llm_specs() -> list[ModelSpec]:
    """Catalogue entries that can translate offline (``kind == "llm"``)."""
    return [spec for spec in CATALOG if spec.kind == "llm"]


def llm_filenames() -> dict[str, str]:
    """``{gguf filename: label}`` — every value ``settings.llm_model_file`` may take.

    The filename (not the catalogue key) is what the settings document stores, so
    the picker and the resolver cannot drift apart.
    """
    return {
        item.filename: spec.label
        for spec in llm_specs()
        for item in spec.files
        if item.filename.endswith(".gguf")
    }


def llm_label(filename: str) -> str:
    """Readable name for a GGUF file, or the filename itself when it is unknown."""
    return llm_filenames().get(filename, filename)


def llm_path(settings: Settings | None = None) -> Path | None:
    """Locate the GGUF named by ``settings.llm_model_file``.

    ``download()`` writes into ``llm/<catalogue key>/``; the flat ``llm/<file>``
    layout is still accepted for a hand-copied model.
    """
    settings = settings or get_settings()
    filename = settings.llm_model_file
    candidates = [model_dir(spec, settings) / filename for spec in llm_specs()]
    candidates.append(model_root(settings) / "llm" / filename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def separator_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    folder = model_root(settings) / "separator"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


__all__ = [
    "CATALOG",
    "MODEL_INDEX",
    "ModelError",
    "ModelSpec",
    "ModelStatus",
    "download",
    "list_models",
    "llm_filenames",
    "llm_label",
    "llm_path",
    "llm_specs",
    "model_dir",
    "model_root",
    "remove",
    "require",
    "separator_dir",
    "size_on_disk",
    "status",
    "whisper_reference",
]
