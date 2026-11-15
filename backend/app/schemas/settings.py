"""Requests for the Settings panel (provider keys, models, offline GGUF, OCR, audio mode).

Everything is optional so the panel can send only the fields it touched -- a
``None`` means "leave it alone", while an empty string means "clear it".
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.services import provider_config


class ProviderPatch(BaseModel):
    """One provider block. Slot keys (``model``, ``stt_model``) stay generic."""

    api_key: str | None = Field(default=None, description="Empty string clears the stored key")
    base_url: str | None = Field(default=None, description="Empty string falls back to the default host")
    slots: dict[str, str] = Field(
        default_factory=dict,
        description="Model names by slot, e.g. {'model': 'gemini-2.5-pro'}",
    )


class LlmPatch(BaseModel):
    """Offline translator weights: a GGUF filename from the model catalogue."""

    model: str | None = Field(
        default=None,
        description="Tên tệp GGUF (vd. gemma-3-4b-it-q4_k_m.gguf); chuỗi rỗng về bản mặc định",
    )


class OcrPatch(BaseModel):
    engine: str | None = Field(default=None, description="rapidocr | paddleocr | easyocr")
    profile: str | None = Field(default=None, description="small (chính xác) | tiny (nhanh)")
    region: str | None = Field(
        default=None,
        description=(
            'Vùng quét phụ đề "trái,trên,phải,dưới" theo % khung hình '
            '("0,55,100,100"); chuỗi rỗng quay về dải mặc định'
        ),
    )
    device: str | None = Field(
        default=None,
        description=(
            "auto | gpu | cpu — GPU khi onnxruntime có provider (CUDA hoặc DirectML), "
            "tự lùi về CPU nếu gói chưa cài; auto là mặc định"
        ),
    )


class AudioPatch(BaseModel):
    mode: str | None = Field(default=None, description="fast | clean (tách giọng trước khi nhận dạng)")


class SettingsPatch(BaseModel):
    """Partial update of ``providers.json``."""

    providers: dict[str, ProviderPatch] = Field(default_factory=dict)
    llm: LlmPatch | None = None
    ocr: OcrPatch | None = None
    audio: AudioPatch | None = None

    @model_validator(mode="after")
    def _known_providers(self) -> SettingsPatch:
        unknown = sorted(set(self.providers) - set(provider_config.PROVIDERS))
        if unknown:
            raise ValueError(f"Provider không hỗ trợ: {', '.join(unknown)}")
        return self

    def document(self) -> dict:
        """Flatten into the shape :func:`provider_config.save` expects."""
        providers: dict[str, dict[str, str]] = {}
        for name, patch in self.providers.items():
            values = dict(patch.slots)
            if patch.api_key is not None:
                values["api_key"] = patch.api_key.strip()
            if patch.base_url is not None:
                values["base_url"] = patch.base_url.strip()
            providers[name] = values
        document: dict = {"providers": providers}
        if self.llm is not None:
            document["llm"] = {k: v for k, v in self.llm.model_dump().items() if v is not None}
        if self.ocr is not None:
            document["ocr"] = {k: v for k, v in self.ocr.model_dump().items() if v is not None}
        if self.audio is not None:
            document["audio"] = {k: v for k, v in self.audio.model_dump().items() if v is not None}
        return document


class ProviderTestRequest(BaseModel):
    """Optional unsaved values to probe before the user commits them."""

    api_key: str | None = None
    base_url: str | None = None
    slots: dict[str, str] = Field(default_factory=dict)

    def overrides(self) -> dict[str, str]:
        values = {k: v for k, v in self.slots.items() if isinstance(v, str) and v.strip()}
        if self.api_key and self.api_key.strip():
            values["api_key"] = self.api_key.strip()
        if self.base_url and self.base_url.strip():
            values["base_url"] = self.base_url.strip()
        return values


__all__ = [
    "AudioPatch",
    "LlmPatch",
    "OcrPatch",
    "ProviderPatch",
    "ProviderTestRequest",
    "SettingsPatch",
]
