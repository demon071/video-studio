"""Aggregated REST surface mounted by :mod:`app.main`."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    ai_pack,
    capcut_export,
    dubbing,
    jobs,
    media,
    models,
    project,
    render,
    settings,
    subtitles,
    system,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(settings.router)
api_router.include_router(ai_pack.router)
api_router.include_router(models.router)
api_router.include_router(media.router)
api_router.include_router(project.router)
api_router.include_router(subtitles.router)
api_router.include_router(dubbing.router)
api_router.include_router(render.router)
api_router.include_router(capcut_export.router)
api_router.include_router(jobs.router)

__all__ = ["api_router"]
