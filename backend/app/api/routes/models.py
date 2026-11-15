"""On-demand model catalogue and downloads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.jobs import jobs
from app.services import model_manager

router = APIRouter(tags=["models"])


@router.get("/api/models")
def list_models() -> dict[str, Any]:
    models = model_manager.list_models()
    return {
        "models": [model.to_dict() for model in models],
        "models_path": str(model_manager.model_root()),
        "downloaded": [model.key for model in models if model.downloaded],
    }


@router.post("/api/models/{spec_key}/download")
def download_model(spec_key: str, force: bool = False) -> dict[str, Any]:
    try:
        spec = model_manager.require(spec_key)
    except model_manager.ModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    job = jobs.submit(
        f"model-download:{spec.key}",
        lambda report: model_manager.download(spec.key, report=report, force=force),
    )
    return {"job": job.model_dump(mode="json"), "model": model_manager.status(spec).to_dict()}


@router.delete("/api/models/{spec_key}")
def delete_model(spec_key: str) -> dict[str, Any]:
    try:
        removed = model_manager.remove(spec_key)
    except model_manager.ModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"key": spec_key, "removed": removed}


__all__ = ["router"]
