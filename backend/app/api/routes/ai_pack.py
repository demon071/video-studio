"""On-demand AI pack: status, install job, staging cleanup.

The folder swap after a successful extraction is performed by the Tauri shell
(``apply_ai_pack``) because a running Windows executable cannot be replaced
from inside the process that owns it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.jobs import jobs
from app.schemas.ai_pack import AiPackInstallRequest
from app.services import ai_pack

router = APIRouter(tags=["ai-pack"])


@router.get("/api/ai-pack")
def pack_status() -> dict[str, Any]:
    return ai_pack.status()


@router.post("/api/ai-pack/install")
def install(request: AiPackInstallRequest) -> dict[str, Any]:
    url = (request.url or "").strip()
    path = (request.path or "").strip()
    if bool(url) == bool(path):
        raise HTTPException(
            status_code=422,
            detail="send exactly one of `url` or `path`",
        )

    if url:
        work = lambda report: ai_pack.install_from_url(url, report=report)  # noqa: E731
    else:
        work = lambda report: ai_pack.install_from_file(path, report=report)  # noqa: E731

    job = jobs.submit("ai-pack-install", work)
    return {"job": job.model_dump(mode="json"), "status": ai_pack.status()}


@router.delete("/api/ai-pack/staging")
def discard() -> dict[str, Any]:
    return ai_pack.discard_staging()


__all__ = ["router"]
