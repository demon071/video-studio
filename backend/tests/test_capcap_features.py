"""Unit tests for CapCap v9.0.1 features integrated into video-studio."""

from __future__ import annotations

from app.schemas.subtitle import SubtitleSegment, SubtitleStyle
from app.services import (
    capcut_client,
    ocr_service,
    subtitle_timeline,
    time_warp_service,
    translate_service,
)
from app.services.subtitle_timeline import Cue
from app.utils.subtitles import parse_srt, to_ass


def test_asr_dedup_cues_with_informative_containment() -> None:
    cues = [
        Cue(start=0.0, end=1.8, text="Xin chào các bạn"),
        Cue(start=0.2, end=2.0, text="Xin chào các bạn!"),
        Cue(start=2.1, end=4.8, text="Xin chào các bạn hôm nay chúng ta học"),
    ]
    deduped = subtitle_timeline.dedup_cues(cues, dedup_enabled=True, dedup_similarity=0.82)
    # Overlapping identical cues merge, informative containment with <0.35s gap merges into the fuller cue
    assert len(deduped) == 1
    assert "hôm nay chúng ta học" in deduped[0].text
    assert deduped[0].start == 0.0
    assert deduped[0].end == 4.8


def test_sanitize_ocr_line_radicals_and_watermarks() -> None:
    assert ocr_service.sanitize_ocr_line("亻尔好 讠兑话") == "你好 说话"
    assert ocr_service.sanitize_ocr_line("@my_channel_id") == ""
    assert ocr_service.sanitize_ocr_line("好看漫剧 专店") == ""


def test_translation_presets_and_rolling_ledger() -> None:
    presets = translate_service.list_presets()
    assert len(presets) >= 5
    ids = {p["id"] for p in presets}
    assert "general_default" in ids

    first_id = presets[0]["id"]
    rules = translate_service.load_preset_rules(first_id)
    assert isinstance(rules, str)

    ledger = translate_service.RollingContextLedger()
    original = ["師尊，弟子明白了"]
    translated = ["Sư tôn, đệ tử đã rõ"]
    translate_service.update_ledger_from_batch(ledger, original, translated)
    guidance = translate_service.build_rolling_context_guidance(ledger)
    assert isinstance(guidance, str)


def test_parse_srt_and_bilingual_ass() -> None:
    srt_text = """1
00:00:01,000 --> 00:00:03,200
Hello world

2
00:00:03,500 --> 00:00:05,000
Second line
"""
    segments = parse_srt(srt_text)
    assert len(segments) == 2
    assert segments[0].start == 1.0
    assert segments[0].end == 3.2
    assert segments[0].text == "Hello world"
    assert segments[0].original_start == 1.0

    segments[0].translation = "Xin chào thế giới"
    segments[1].translation = "Dòng thứ hai"

    ass_bilingual = to_ass(
        segments,
        style=SubtitleStyle(),
        presentation_mode="bilingual",
    )
    assert "Style: Secondary," in ass_bilingual
    assert "Dialogue: 0," in ass_bilingual
    assert "Dialogue: 1," in ass_bilingual

    ass_none = to_ass(segments, style=SubtitleStyle(), presentation_mode="none")
    assert "Dialogue:" not in ass_none


def test_time_warp_plan() -> None:
    warps = [
        {"type": "slow", "media_start": 1.0, "media_end": 2.0, "duration": 0.6},
        {"type": "freeze", "time": 4.0, "duration": 0.5},
    ]
    spans = time_warp_service.compile_plan(warps)
    assert len(spans) == 2
    parts = time_warp_service.video_parts(6.0, warps)
    assert len(parts) >= 3
    mapped = time_warp_service.media_to_timeline(5.0, warps)
    assert mapped == 6.1


def test_capcut_voice_catalog_loaded() -> None:
    voices = capcut_client.list_capcut_voices()
    assert len(voices) > 10
