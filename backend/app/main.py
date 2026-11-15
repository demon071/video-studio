"""FastAPI entry point: REST routers, ``/ws/progress`` and process hygiene.

Run in development with ``npm run dev:backend`` (uvicorn --reload). The same
module is the PyInstaller entry point (``python -m app.main``), in which case
uvicorn runs the app object directly because a frozen binary cannot re-import
itself for the reloader.

The frozen binary is built with ``--noconsole`` so the Tauri sidecar never
flashes a console window. That means ``sys.stderr`` is *None* in production:
logging is routed to a rotating file under ``%APPDATA%\\ShortVideoAI\\logs``
instead of a stream handler.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import APP_NAME, APP_VERSION
from app.api.events import bus
from app.api.jobs import jobs
from app.api.routes import api_router
from app.api.ws import router as ws_router
from app.config import ensure_data_dirs, get_settings, resolve_ffmpeg
from app.core import hardware
from app.services import omnivoice_tts, stt_service

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
LOG_DATE = "%H:%M:%S"

if sys.stderr is not None:
    # Guarded: a windowed PyInstaller build has no stderr and StreamHandler(None)
    # would raise on the first record.
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE)

log = logging.getLogger(APP_NAME.lower())


def setup_file_logging(log_dir: Path) -> Path | None:
    """Mirror everything to ``logs/engine.log``; the only log in production."""
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_sva_file", False):
            return getattr(handler, "baseFilename", None)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "engine.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:  # read-only or locked directory: keep the console path
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE))
    handler.setLevel(logging.INFO)
    handler._sva_file = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    return Path(handler.baseFilename)


def _start_parent_watchdog(pid: int) -> None:
    """Exit when the desktop shell that launched the sidecar disappears."""

    def watch() -> None:
        try:
            import psutil  # noqa: PLC0415
        except ImportError:  # pragma: no cover - core dependency
            return
        while True:
            time.sleep(2.0)
            if not psutil.pid_exists(pid):
                log.warning("parent process %s is gone - shutting the engine down", pid)
                os._exit(0)

    threading.Thread(target=watch, daemon=True, name="parent-watchdog").start()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    folders = ensure_data_dirs(settings)
    log_file = setup_file_logging(folders["logs"])

    bus.bind_loop(asyncio.get_running_loop())
    jobs.start()

    # Importing torch takes a second or two: do it off the event loop so the
    # first HTTP request is already fast.
    profile = await asyncio.to_thread(hardware.detect)

    log.info("%s %s listening on %s:%s", APP_NAME, APP_VERSION, settings.host, settings.port)
    log.info("data dir: %s", folders["root"])
    log.info("log file: %s", log_file or "console only")
    log.info("ffmpeg: %s", resolve_ffmpeg(settings) or "NOT FOUND")
    log.info(
        "compute: %s%s | whisper %s/%s | llm layers %s | preset %s",
        profile.device,
        f" ({profile.gpu_name}, {profile.vram_free_gb:.1f} GB free)" if profile.gpu_name else "",
        profile.whisper_model,
        profile.whisper_compute_type,
        profile.llm_gpu_layers,
        profile.preset,
    )
    for note in profile.notes:
        log.warning("hardware: %s", note)

    if settings.parent_pid:
        _start_parent_watchdog(settings.parent_pid)

    try:
        yield
    finally:
        jobs.shutdown()
        # Both hold torch weights: hand the VRAM back before the process exits.
        stt_service.unload()
        omnivoice_tts.unload()
        log.info("%s stopped", APP_NAME)


app = FastAPI(
    title=f"{APP_NAME} Engine",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# The WebView origin differs between dev (http://localhost:1420) and production
# (http://tauri.localhost on Windows), so both are whitelisted explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(ws_router)


@app.exception_handler(FileNotFoundError)
async def _missing_file(_: Request, exc: FileNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def root() -> dict[str, Any]:
    return {
        "name": f"{APP_NAME} Engine",
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/api/health",
        "socket": "/ws/progress",
    }


def main() -> None:
    """Entry point used by PyInstaller and by ``python -m app.main``."""
    settings = get_settings()
    if settings.parent_pid:
        _start_parent_watchdog(settings.parent_pid)

    if settings.packaged:
        # ``log_config=None`` keeps uvicorn from installing stream handlers that
        # point at a nonexistent console in the windowed build; our own rotating
        # file handler (attached in the lifespan) is the only sink.
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level.lower(),
            log_config=None,
            access_log=False,
        )
        return

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=True,
        reload_dirs=["app"],
    )


if __name__ == "__main__":
    main()
