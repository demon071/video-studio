"""Caption style presets ("My Presets") shared across projects."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.schemas.project import StylePreset
from app.services import style_presets

router = APIRouter(tags=["styles"])


class PresetList(BaseModel):
    presets: list[StylePreset] = Field(default_factory=list)


class PresetPutRequest(BaseModel):
    presets: list[StylePreset] = Field(default_factory=list)


@router.get("/api/styles/presets", response_model=PresetList)
def list_presets() -> PresetList:
    return PresetList(presets=style_presets.load_presets())


@router.put("/api/styles/presets", response_model=PresetList)
def replace_presets(request: PresetPutRequest) -> PresetList:
    """Replace the whole store: the editor owns the order and the ids."""
    try:
        saved = style_presets.save_presets(request.presets)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return PresetList(presets=saved)


@router.post("/api/styles/presets", response_model=PresetList)
def upsert_preset(preset: StylePreset) -> PresetList:
    try:
        saved = style_presets.upsert_preset(preset)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return PresetList(presets=saved)


@router.delete("/api/styles/presets/{preset_id}", response_model=PresetList)
def delete_preset(preset_id: str) -> PresetList:
    try:
        saved = style_presets.delete_preset(preset_id)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return PresetList(presets=saved)


__all__ = ["router"]
