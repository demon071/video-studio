"""Settings panel: online provider keys/models, offline GGUF, OCR, audio mode.

The document lives in ``<data>/settings/providers.json`` (see
:mod:`app.services.provider_config`); every other service reads it through
``provider_config.overlay()``, so saving here is enough to change what runs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas.settings import ProviderTestRequest, SettingsPatch
from app.services import provider_config

router = APIRouter(tags=["settings"])


@router.get("/api/settings")
def read_settings() -> dict[str, Any]:
    return provider_config.describe()


@router.put("/api/settings")
def write_settings(patch: SettingsPatch) -> dict[str, Any]:
    try:
        provider_config.save(patch.document())
    except provider_config.ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return provider_config.describe()


@router.post("/api/settings/providers/{name}/test")
def test_provider(name: str, body: ProviderTestRequest | None = None) -> dict[str, Any]:
    overrides = body.overrides() if body else {}
    try:
        outcome = provider_config.test_connection(name, overrides=overrides)
    except provider_config.ProviderConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return outcome.to_dict()


__all__ = ["router"]
