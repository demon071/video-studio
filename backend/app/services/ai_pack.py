"""On-demand "AI pack": the heavy engine installed after the fact.

The installer ships the *core* engine (~110 MB: REST/WS API, FFmpeg pipeline,
model manager). The offline AI stack (torch/CUDA, CTranslate2, llama.cpp, ONNX
Runtime, TTS) is distributed separately as a zip produced by
``scripts/make_ai_pack.ps1`` (~2.1 GB compressed, ~3.5 GB on disk) because it
cannot fit in an NSIS installer.

This module owns the Python half of the feature:

* :func:`status` - what is installed, what the *running* engine can do;
* :func:`install_from_file` / :func:`install_from_url` - verify and extract the
  archive into ``%APPDATA%\\ShortVideoAI\\ai-pack.staging``.

The swap itself belongs to the Tauri shell (``apply_ai_pack`` in
``src-tauri/src/engine.rs``): Windows locks a running ``.exe`` and its DLLs, so
the shell must kill the engine, move ``ai-pack.staging/engine`` into
``ai-pack/engine`` and respawn - the supervisor then prefers that binary.

Layout::

    %APPDATA%\\ShortVideoAI\\
      ai-pack\\
        ai-pack.json                    manifest written by the pack builder
        engine\\shortvideoai-engine.exe  full-profile PyInstaller bundle
        engine\\_internal\\...
      ai-pack.staging\\                 extraction target (removed after the swap)
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from app import APP_VERSION
from app.config import Settings, get_settings
from app.core.progress import NULL_REPORTER, Reporter

log = logging.getLogger(__name__)

PACK_FOLDER = "ai-pack"
STAGING_FOLDER = "ai-pack.staging"
MANIFEST_NAME = "ai-pack.json"
ENGINE_EXE = "shortvideoai-engine.exe"
INTERNAL_FOLDER = "_internal"
SCHEMA = 1

# A 3.5 GB bundle needs room for the archive plus the extracted tree, and the
# swap happens while the old copy may still exist.
MIN_FREE_GB = 7.0
DOWNLOAD_CHUNK = 1 << 20
EXTRACT_CHUNK = 4 << 20


class AiPackError(RuntimeError):
    """A pack could not be read, verified or installed."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def pack_root(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.data_path / PACK_FOLDER


def staging_root(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.data_path / STAGING_FOLDER


def engine_dir(settings: Settings | None = None) -> Path:
    return pack_root(settings) / "engine"


def engine_exe(settings: Settings | None = None) -> Path:
    return engine_dir(settings) / ENGINE_EXE


def is_installed(settings: Settings | None = None) -> bool:
    """Cheap check suitable for ``/api/health`` (no directory walk)."""
    return engine_exe(settings).is_file()


def _manifest_path(settings: Settings | None = None) -> Path:
    return pack_root(settings) / MANIFEST_NAME


def _read_manifest(settings: Settings | None = None) -> dict[str, Any]:
    try:
        return json.loads(_manifest_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _package_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover - broken install
        return False


def _folder_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return round(total / 1024**2, 1)


def free_disk_gb(path: Path | None = None) -> float:
    probe = path or get_settings().data_path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return round(shutil.disk_usage(probe).free / 1024**3, 1)
    except OSError:  # pragma: no cover - exotic filesystem
        return 0.0


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def current_engine() -> dict[str, Any]:
    """Describe the process that is answering right now."""
    executable = Path(sys.executable).resolve()
    return {
        "path": str(executable),
        "frozen": bool(getattr(sys, "frozen", False)),
        "ai": _package_available("torch"),
    }


def _running_from_pack(settings: Settings | None = None) -> bool:
    try:
        executable = Path(sys.executable).resolve()
    except OSError:  # pragma: no cover - defensive
        return False
    try:
        pretty = executable.relative_to(pack_root(settings).resolve())
    except (ValueError, OSError):
        return False
    return str(pretty).replace("\\", "/").startswith("engine/")


def status(settings: Settings | None = None) -> dict[str, Any]:
    """Everything the AI runtime card in the UI needs."""
    settings = settings or get_settings()
    root = pack_root(settings)
    manifest = _read_manifest(settings)
    installed = is_installed(settings)
    engine = current_engine()
    running_pack = _running_from_pack(settings)
    ai_available = bool(engine["ai"])

    return {
        "installed": installed,
        "path": str(root),
        "engine_path": str(engine_exe(settings)) if installed else None,
        "manifest": manifest or None,
        "version": manifest.get("version"),
        "profile": manifest.get("profile"),
        "size_mb": _folder_mb(root) if installed else 0.0,
        "ai_available": ai_available,
        "running_from_pack": running_pack,
        "engine": engine,
        "staging": staging_root(settings).exists(),
        "download_url": (settings.ai_pack_url or "").strip(),
        "free_disk_gb": free_disk_gb(root),
        "min_free_gb": MIN_FREE_GB,
        # Installing while the core engine runs leaves a ready folder on disk;
        # the shell has to restart into it.
        "needs_restart": installed and not ai_available,
    }


def discard_staging(settings: Settings | None = None) -> dict[str, Any]:
    staging = staging_root(settings)
    removed = staging.exists()
    if removed:
        shutil.rmtree(staging, ignore_errors=True)
        log.info("removed AI pack staging folder %s", staging)
    return {"removed": removed, "path": str(staging)}


# ---------------------------------------------------------------------------
# Archive handling
# ---------------------------------------------------------------------------
def _entry_prefix(names: list[str]) -> str:
    """Return the path prefix that precedes ``engine/<exe>`` in the archive."""
    wanted = f"engine/{ENGINE_EXE}"
    for name in names:
        if name == wanted:
            return ""
        if name.endswith("/" + wanted):
            return name[: -len(wanted)]
    raise AiPackError(
        f"this archive is not an AI pack: it contains no engine/{ENGINE_EXE}"
    )


def _safe_target(base: Path, relative: str) -> Path:
    """Reject absolute paths and ``..`` traversal inside a zip entry."""
    clean = PurePosixPath(relative.replace("\\", "/"))
    if clean.is_absolute() or ".." in clean.parts:
        raise AiPackError(f"unsafe path in the archive: {relative!r}")
    return base.joinpath(*clean.parts)


def _extract(
    archive: zipfile.ZipFile,
    prefix: str,
    staging: Path,
    *,
    report: Reporter,
    total_bytes: int,
) -> int:
    written = 0
    for info in archive.infolist():
        if info.is_dir() or not info.filename.startswith(prefix):
            continue
        relative = info.filename[len(prefix) :]
        if not relative or relative == MANIFEST_NAME:
            continue
        target = _safe_target(staging, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as destination:
            while True:
                block = source.read(EXTRACT_CHUNK)
                if not block:
                    break
                destination.write(block)
                written += len(block)
                if total_bytes:
                    report(
                        fraction=written / total_bytes,
                        message=f"đang giải nén {written / 1024**2:.0f} MB",
                    )
    return written


def install_from_file(
    archive_path: str | Path,
    *,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Verify ``archive_path`` and unpack it into the staging folder."""
    settings = settings or get_settings()
    source = Path(archive_path)
    if not source.is_file():
        raise AiPackError(f"AI pack not found: {source}")
    if source.suffix.lower() != ".zip":
        raise AiPackError("the AI pack must be a .zip archive")

    free = free_disk_gb(pack_root(settings))
    if free and free < MIN_FREE_GB:
        raise AiPackError(
            f"not enough free disk space: {free:.1f} GB available, "
            f"~{MIN_FREE_GB:.0f} GB required ({settings.data_path.drive} drive)"
        )

    staging = staging_root(settings)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    report(stage="verify", fraction=0.0, message=f"đọc {source.name}")
    try:
        with zipfile.ZipFile(source) as archive:
            prefix = _entry_prefix(archive.namelist())
            members = [
                info
                for info in archive.infolist()
                if not info.is_dir() and info.filename.startswith(prefix)
            ]
            total = sum(info.file_size for info in members) or 1
            report(
                stage="extract",
                fraction=0.0,
                message=f"giải nén {len(members)} tệp / {total / 1024**2:.0f} MB",
            )
            started = time.monotonic()
            written = _extract(archive, prefix, staging, report=report, total_bytes=total)
            manifest = _manifest_from(archive, prefix)
        elapsed = round(time.monotonic() - started, 1)
        log.info("AI pack extracted in %ss (%s MB)", elapsed, round(written / 1024**2, 1))

        if not is_staged(staging):
            raise AiPackError(
                "the archive is incomplete: engine/"
                f"{ENGINE_EXE} or engine/{INTERNAL_FOLDER} is missing"
            )

        manifest.setdefault("schema", SCHEMA)
        manifest.setdefault("version", APP_VERSION)
        manifest.setdefault("profile", "full")
        manifest["installed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        manifest["bytes"] = written
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        report(stage="ready", fraction=1.0, message="sẵn sàng áp dụng")
        return {
            "staging": str(staging),
            "manifest": manifest,
            "bytes": written,
            "elapsed_s": elapsed,
            "status": status(settings),
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _manifest_from(archive: zipfile.ZipFile, prefix: str) -> dict[str, Any]:
    name = f"{prefix}{MANIFEST_NAME}"
    try:
        return json.loads(archive.read(name).decode("utf-8"))
    except KeyError:
        return {}
    except (ValueError, OSError):  # pragma: no cover - malformed pack
        log.warning("ignoring an unreadable %s inside the AI pack", name)
        return {}


def is_staged(staging: Path | None = None, settings: Settings | None = None) -> bool:
    folder = staging or staging_root(settings)
    engine = folder / "engine"
    return (engine / ENGINE_EXE).is_file() and (engine / INTERNAL_FOLDER).is_dir()


def install_from_url(
    url: str,
    *,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Stream ``url`` to the temp folder, then install it like a local file."""
    settings = settings or get_settings()
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise AiPackError("the AI pack URL must start with http:// or https://")

    target = settings.temp_path / f"ai-pack-{int(time.time())}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")

    report(stage="download", fraction=0.0, message=f"tải {url}")
    timeout = httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0)
    downloaded = 0
    started = time.monotonic()
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=DOWNLOAD_CHUNK):
                    handle.write(chunk)
                    downloaded += len(chunk)
                    report(
                        fraction=(downloaded / total) if total else None,
                        message=(
                            f"tải {downloaded / 1024**2:.0f} MB"
                            + (f" / {total / 1024**2:.0f} MB" if total else "")
                        ),
                    )
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    else:
        report(stage="download", fraction=1.0, message=f"đã tải {downloaded / 1024**2:.0f} MB")
        log.info(
            "AI pack downloaded in %ss (%s MB)",
            round(time.monotonic() - started, 1),
            round(downloaded / 1024**2, 1),
        )

    try:
        result = install_from_file(target, report=report, settings=settings)
    finally:
        target.unlink(missing_ok=True)
    result["downloaded_bytes"] = downloaded
    return result


def summary_for_health(settings: Settings | None = None) -> dict[str, Any]:
    """Cheap subset for ``/api/health`` (called by the UI every few seconds)."""
    engine = current_engine()
    return {
        "engine_path": engine["path"],
        "frozen": engine["frozen"],
        "ai": engine["ai"],
        "ai_pack": is_installed(settings),
    }


__all__ = [
    "AiPackError",
    "ENGINE_EXE",
    "MANIFEST_NAME",
    "MIN_FREE_GB",
    "PACK_FOLDER",
    "STAGING_FOLDER",
    "current_engine",
    "discard_staging",
    "engine_dir",
    "engine_exe",
    "free_disk_gb",
    "install_from_file",
    "install_from_url",
    "is_installed",
    "is_staged",
    "pack_root",
    "staging_root",
    "status",
    "summary_for_health",
]
