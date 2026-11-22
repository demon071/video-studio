"""Persistent store for user caption style presets ("My Presets", ported from CapCap)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.config import Settings, get_settings
from app.schemas.project import StylePreset
from app.schemas.subtitle import SubtitleStyle

DEFAULT_PRESETS: tuple[StylePreset, ...] = (
    StylePreset(
        id="default",
        name="Mặc định (Hộp đen chuẩn)",
        style=SubtitleStyle(),
    ),
    StylePreset(
        id="drama_gold",
        name="Phim ngắn Vàng rực",
        style=SubtitleStyle(
            color="#FFD54F",
            outline_color="#101010",
            outline=1.5,
            bold=True,
            background=False,
            highlight_mode="auto",
            highlight_color="#FFFFFF",
            animation="pop",
        ),
    ),
    StylePreset(
        id="clean_box",
        name="Hộp đen tối giản",
        style=SubtitleStyle(
            color="#FFFFFF",
            outline=0.0,
            bold=True,
            background=True,
            background_color="#000000",
            background_opacity=0.65,
            highlight_mode="auto",
            highlight_color="#FACC15",
        ),
    ),
    StylePreset(
        id="tiktok_pop",
        name="TikTok Pop Highlight",
        style=SubtitleStyle(
            color="#FFFFFF",
            outline_color="#000000",
            outline=2.0,
            bold=True,
            background=False,
            highlight_mode="auto",
            highlight_color="#38BDF8",
            animation="pop",
        ),
    ),
)


def _presets_file(settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    folder = cfg.data_path / "settings"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "style_presets.json"


def load_presets(settings: Settings | None = None) -> list[StylePreset]:
    path = _presets_file(settings)
    if not path.is_file():
        return [p.model_copy(deep=True) for p in DEFAULT_PRESETS]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("presets", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list) or not items:
            return [p.model_copy(deep=True) for p in DEFAULT_PRESETS]
        parsed = [StylePreset.model_validate(item) for item in items]
        return parsed or [p.model_copy(deep=True) for p in DEFAULT_PRESETS]
    except Exception:
        return [p.model_copy(deep=True) for p in DEFAULT_PRESETS]


def save_presets(presets: list[StylePreset], settings: Settings | None = None) -> list[StylePreset]:
    path = _presets_file(settings)
    cleaned = list(presets) if presets else [p.model_copy(deep=True) for p in DEFAULT_PRESETS]
    payload = {"presets": [item.model_dump(mode="json") for item in cleaned]}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return cleaned


def upsert_preset(preset: StylePreset, settings: Settings | None = None) -> list[StylePreset]:
    current = load_presets(settings)
    replaced = False
    next_list: list[StylePreset] = []
    for item in current:
        if item.id == preset.id:
            next_list.append(preset)
            replaced = True
        else:
            next_list.append(item)
    if not replaced:
        next_list.append(preset)
    return save_presets(next_list, settings)


def delete_preset(preset_id: str, settings: Settings | None = None) -> list[StylePreset]:
    current = load_presets(settings)
    next_list = [item for item in current if item.id != preset_id]
    return save_presets(next_list, settings)


__all__ = [
    "DEFAULT_PRESETS",
    "delete_preset",
    "load_presets",
    "save_presets",
    "upsert_preset",
]
