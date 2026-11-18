"""Performance Queue & Batch Processing Service (ported from CapCap v9.0.1).

Manages a persistent ``processing_queue.json`` in ``settings.data_path`` and
executes multi-video pipelines sequentially in a background thread:
- ``process_only``: Transcribe -> Translate -> TTS Dubbing
- ``process_and_export``: Transcribe -> Translate -> TTS Dubbing -> Export MP4
- ``export_selected``: Export MP4 for already-processed items
Supports ``stop_after_current``, ``stop_now``, and ``clean_video_data``.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.config import get_settings
from app.schemas.subtitle import (
    DubbingResult,
    SubtitleSegment,
    SubtitleStyle,
    TranscribeRequest,
    VideoClipRange,
    VisualLayer,
)
from app.services import media_processor, stt_service, translate_service, tts_service
from app.utils.subtitles import write_subtitles

log = logging.getLogger(__name__)

QueueStatus = Literal["pending", "processing", "ready_to_export", "completed", "error", "stopped"]
QueueMode = Literal["process_only", "process_and_export", "export_selected"]


@dataclass
class QueueItem:
    id: str
    video_path: str
    title: str
    status: QueueStatus = "pending"
    progress: float = 0.0
    stage: str = "Chờ xử lý"
    error: str | None = None
    output_path: str | None = None
    work_dir: str | None = None
    source_mode: str = "audio"
    stt_provider: str = "local"
    target_language: str = "vi"
    translate_provider: str = "google"
    translate_preset: str = "general"
    tts_provider: str = "capcut"
    tts_voice: str | None = None
    aspect: str = "9:16"
    bgm_mode: str = "keep"
    bgm_path: str | None = None
    subtitle_style: dict[str, Any] | None = None
    segments_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class ProcessingQueueManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: list[QueueItem] = []
        self._running: bool = False
        self._stop_after_current: bool = False
        self._stop_now: bool = False
        self._current_id: str | None = None
        self._loaded: bool = False

    def _queue_file(self) -> Path:
        path = get_settings().data_path / "processing_queue.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        qf = self._queue_file()
        if not qf.is_file():
            return
        try:
            raw = json.loads(qf.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for entry in raw:
                    if isinstance(entry, dict) and entry.get("id") and entry.get("video_path"):
                        if entry.get("status") == "processing":
                            entry["status"] = "stopped"
                            entry["stage"] = "Bị gián đoạn"
                        self._items.append(QueueItem(**{k: v for k, v in entry.items() if k in QueueItem.__dataclass_fields__}))
        except Exception as exc:
            log.warning("Could not load processing_queue.json: %s", exc)

    def _save(self) -> None:
        try:
            data = [asdict(item) for item in self._items]
            self._queue_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Could not save processing_queue.json: %s", exc)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            return {
                "running": self._running,
                "stop_after_current": self._stop_after_current,
                "current_id": self._current_id,
                "items": [asdict(item) for item in self._items],
            }

    def add_items(
        self,
        video_paths: list[str],
        *,
        source_mode: str = "audio",
        stt_provider: str | None = None,
        target_language: str = "vi",
        translate_provider: str = "google",
        translate_preset: str = "general",
        tts_provider: str = "capcut",
        tts_voice: str | None = None,
        aspect: str = "9:16",
        bgm_mode: str = "keep",
        bgm_path: str | None = None,
        subtitle_style: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_stt = stt_provider or ("ocr" if source_mode == "ocr" else "local")
        with self._lock:
            self._ensure_loaded()
            for vp in video_paths:
                p = Path(vp)
                item = QueueItem(
                    id=uuid4().hex[:10],
                    video_path=str(p),
                    title=p.name,
                    source_mode=source_mode,
                    stt_provider=resolved_stt,
                    target_language=target_language,
                    translate_provider=translate_provider,
                    translate_preset=translate_preset,
                    tts_provider=tts_provider,
                    tts_voice=tts_voice,
                    aspect=aspect,
                    bgm_mode=bgm_mode,
                    bgm_path=bgm_path,
                    subtitle_style=subtitle_style,
                )
                self._items.append(item)
            self._save()
            return self.snapshot()

    def get_preview(self, item_id: str) -> dict[str, Any]:
        """Return saved segments & dub result for a processed queue item so the UI can review it."""
        with self._lock:
            self._ensure_loaded()
            item = next((it for it in self._items if it.id == item_id), None)
        if item is None:
            raise KeyError(f"Queue item {item_id} not found")
        if not item.work_dir:
            raise ValueError("Video này chưa được xử lý.")
        work = Path(item.work_dir)
        seg_file = work / "segments.json"
        dub_file = work / "dub.json"
        state_file = work / "editor_state.json"
        segments = json.loads(seg_file.read_text(encoding="utf-8")) if seg_file.is_file() else []
        dub = json.loads(dub_file.read_text(encoding="utf-8")) if dub_file.is_file() else None
        editor_state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
        return {
            "item": asdict(item),
            "video_path": item.video_path,
            "segments": segments,
            "dub": dub,
            "editor_state": editor_state,
        }

    def save_item_state(self, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist user edits (segments, dub, style, layers, cuts, audio) back into the queue item."""
        with self._lock:
            self._ensure_loaded()
            item = next((it for it in self._items if it.id == item_id), None)
        if item is None:
            raise KeyError(f"Queue item {item_id} not found")

        settings = get_settings()
        work = Path(item.work_dir) if item.work_dir else (settings.temp_path / f"queue-{item.id}")
        work.mkdir(parents=True, exist_ok=True)

        raw_segments = payload.get("segments")
        if isinstance(raw_segments, list):
            parsed_segments = [SubtitleSegment.model_validate(s) for s in raw_segments]
            (work / "segments.json").write_text(
                json.dumps([s.model_dump(mode="json") for s in parsed_segments], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            seg_file = work / "segments.json"
            parsed_segments = (
                [SubtitleSegment.model_validate(s) for s in json.loads(seg_file.read_text(encoding="utf-8"))]
                if seg_file.is_file()
                else []
            )

        raw_dub = payload.get("dub")
        if isinstance(raw_dub, dict):
            track_src = raw_dub.get("track_path")
            target_dub_wav = work / "dub_track.wav"
            if track_src and Path(track_src).is_file():
                src_p = Path(track_src).resolve()
                if src_p != target_dub_wav.resolve():
                    shutil.copyfile(src_p, target_dub_wav)
                raw_dub = {**raw_dub, "track_path": str(target_dub_wav)}
            (work / "dub.json").write_text(json.dumps(raw_dub, ensure_ascii=False, indent=2), encoding="utf-8")

        aspect = str(payload.get("aspect") or item.aspect or "9:16")
        editor_state = {
            "aspect": aspect,
            "subtitle_style": payload.get("subtitle_style"),
            "presentation_mode": payload.get("presentation_mode", "single"),
            "secondary_segments": payload.get("secondary_segments") or [],
            "secondary_style": payload.get("secondary_style"),
            "layers": payload.get("layers") or [],
            "video_clips": payload.get("video_clips") or [],
            "background_path": payload.get("background_path"),
            "keep_original_audio": payload.get("keep_original_audio"),
            "voice_gain": payload.get("voice_gain"),
            "background_gain": payload.get("background_gain"),
            "flip_horizontal": payload.get("flip_horizontal", False),
            "dub_dirty": bool(payload.get("dub_dirty", False)),
        }
        (work / "editor_state.json").write_text(
            json.dumps(editor_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Keep subtitles.ass in sync with the edited segments and style.
        ass_path = work / "subtitles.ass"
        pres_mode = str(editor_state.get("presentation_mode") or "single")
        if parsed_segments and pres_mode != "none":
            ass_w, ass_h = media_processor.TARGET_ASPECTS.get(aspect, (1080, 1920))
            style_obj = (
                SubtitleStyle.model_validate(editor_state["subtitle_style"])
                if isinstance(editor_state.get("subtitle_style"), dict)
                else SubtitleStyle()
            )
            sec_segs = [
                SubtitleSegment.model_validate(s)
                for s in (editor_state.get("secondary_segments") or [])
                if isinstance(s, dict)
            ]
            sec_style = (
                SubtitleStyle.model_validate(editor_state["secondary_style"])
                if isinstance(editor_state.get("secondary_style"), dict)
                else None
            )
            write_subtitles(
                parsed_segments,
                ass_path,
                fmt="ass",
                width=ass_w,
                height=ass_h,
                style=style_obj,
                use_translation=any(bool((s.translation or "").strip()) for s in parsed_segments),
                presentation_mode=pres_mode,  # type: ignore[arg-type]
                secondary_segments=sec_segs,
                secondary_style=sec_style,
            )
        elif ass_path.is_file():
            ass_path.unlink(missing_ok=True)

        next_status: QueueStatus = (
            "ready_to_export" if item.status in ("ready_to_export", "completed") else item.status
        )
        self._update_item(
            item,
            work_dir=str(work),
            aspect=aspect,
            segments_count=len(parsed_segments),
            status=next_status,
            stage=f"Đã lưu chỉnh sửa ({len(parsed_segments)} dòng) · Chờ xuất MP4",
        )
        return self.snapshot()

    def remove_item(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            self._items = [it for it in self._items if it.id != item_id]
            self._save()
            return self.snapshot()

    def clear_completed(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            self._items = [it for it in self._items if it.status not in ("completed", "error", "stopped")]
            self._save()
            return self.snapshot()

    def clean_video_data(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            for item in self._items:
                if item.id == item_id:
                    if item.work_dir and Path(item.work_dir).is_dir():
                        shutil.rmtree(item.work_dir, ignore_errors=True)
                    item.work_dir = None
                    item.status = "pending"
                    item.progress = 0.0
                    item.stage = "Đã dọn dữ liệu tạm"
                    item.error = None
                    item.updated_at = time.time()
            self._save()
            return self.snapshot()

    def request_stop(self, *, immediate: bool = False) -> dict[str, Any]:
        with self._lock:
            self._stop_after_current = True
            if immediate:
                self._stop_now = True
            return self.snapshot()

    def start_batch(
        self,
        mode: QueueMode = "process_and_export",
        item_ids: list[str] | None = None,
        subtitle_style: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            if isinstance(subtitle_style, dict):
                for it in self._items:
                    if item_ids and it.id not in item_ids:
                        continue
                    it.subtitle_style = subtitle_style
                    if it.work_dir:
                        st_file = Path(it.work_dir) / "editor_state.json"
                        if st_file.is_file():
                            try:
                                ed = json.loads(st_file.read_text(encoding="utf-8"))
                                if isinstance(ed, dict):
                                    ed["subtitle_style"] = subtitle_style
                                    st_file.write_text(json.dumps(ed, ensure_ascii=False, indent=2), encoding="utf-8")
                            except Exception:
                                pass
                self._save()
            if self._running:
                return self.snapshot()
            self._running = True
            self._stop_after_current = False
            self._stop_now = False
            thread = threading.Thread(target=self._worker, args=(mode, item_ids), daemon=True)
            thread.start()
            return self.snapshot()

    def _update_item(self, item: QueueItem, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(item, k, v)
            item.updated_at = time.time()
            self._save()

    def _worker(self, mode: QueueMode, item_ids: list[str] | None) -> None:
        settings = get_settings()
        try:
            while True:
                with self._lock:
                    if self._stop_after_current or self._stop_now:
                        break
                    target: QueueItem | None = None
                    for it in self._items:
                        if item_ids and it.id not in item_ids:
                            continue
                        if mode == "export_selected" and it.status == "ready_to_export":
                            target = it
                            break
                        if mode == "process_and_export" and it.status in ("pending", "error", "stopped", "ready_to_export"):
                            target = it
                            break
                        if mode == "process_only" and it.status in ("pending", "error", "stopped"):
                            target = it
                            break
                    if target is None:
                        break
                    self._current_id = target.id

                self._process_one(target, mode, settings)
        finally:
            with self._lock:
                self._running = False
                self._current_id = None
                self._stop_after_current = False
                self._stop_now = False
                self._save()

    def _process_one(self, item: QueueItem, mode: QueueMode, settings: Any) -> None:
        source = Path(item.video_path)
        if not source.is_file():
            self._update_item(item, status="error", error=f"Không tìm thấy video: {source}", stage="Lỗi")
            return

        work_folder = Path(item.work_dir) if item.work_dir else (settings.temp_path / f"queue-{item.id}")
        work_folder.mkdir(parents=True, exist_ok=True)
        was_ready = item.status == "ready_to_export" and (work_folder / "segments.json").is_file()
        self._update_item(item, status="processing", progress=0.05, stage="Bắt đầu xử lý", work_dir=str(work_folder), error=None)

        try:
            ass_path = work_folder / "subtitles.ass"
            dub_track_path = work_folder / "dub_track.wav"
            segments_file = work_folder / "segments.json"
            dub_file = work_folder / "dub.json"
            state_file = work_folder / "editor_state.json"

            if mode in ("process_only", "process_and_export") and not was_ready:
                resolved_stt = item.stt_provider or ("ocr" if item.source_mode == "ocr" else "local")
                self._update_item(item, progress=0.1, stage=f"Tách phụ đề ({resolved_stt})...")
                tx = stt_service.transcribe(
                    TranscribeRequest(video_path=str(source), provider=resolved_stt),  # type: ignore[arg-type]
                    settings=settings,
                )
                if self._stop_now:
                    self._update_item(item, status="stopped", stage="Đã dừng")
                    return

                self._update_item(item, progress=0.4, stage=f"Dịch phụ đề ({item.translate_provider})...")
                tr = translate_service.translate(
                    tx.segments,
                    target_language=item.target_language or "vi",
                    provider=item.translate_provider,
                    preset=item.translate_preset or None,
                    auto_context=(item.translate_provider or "").lower() in ("gemini", "openai", "deepseek", "ollama"),
                    settings=settings,
                )
                if self._stop_now:
                    self._update_item(item, status="stopped", stage="Đã dừng")
                    return

                segments_file.write_text(
                    json.dumps([s.model_dump(mode="json") for s in tr.segments], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                ass_w, ass_h = media_processor.TARGET_ASPECTS.get(item.aspect, (1080, 1920))
                init_style = (
                    SubtitleStyle.model_validate(item.subtitle_style)
                    if isinstance(item.subtitle_style, dict)
                    else SubtitleStyle()
                )
                write_subtitles(
                    tr.segments,
                    ass_path,
                    fmt="ass",
                    width=ass_w,
                    height=ass_h,
                    style=init_style,
                    use_translation=True,
                )

                self._update_item(item, progress=0.65, stage=f"Lồng tiếng ({item.tts_provider})...", segments_count=len(tr.segments))
                dub_res = tts_service.dub(
                    tr.segments,
                    work_dir=work_folder,
                    provider=item.tts_provider,
                    voice=item.tts_voice,
                    use_translation=True,
                    video_path=str(source),
                    settings=settings,
                )
                dub_track_path = dub_res.track_path
                dub_payload = DubbingResult(
                    track_path=str(dub_res.track_path),
                    duration=dub_res.duration,
                    provider=dub_res.provider,
                    voice=dub_res.voice,
                    segments=dub_res.reports,
                    skipped=dub_res.skipped,
                    rewritten=dub_res.rewritten,
                ).model_dump(mode="json")
                dub_file.write_text(json.dumps(dub_payload, ensure_ascii=False, indent=2), encoding="utf-8")

                if item.bgm_mode == "strip":
                    self._update_item(item, progress=0.78, stage="Tách bỏ nhạc nền (AI)...")
                    vocals, _inst = media_processor.separate_vocals(source, work_folder)
                    self._update_item(item, bgm_path=str(vocals))

                if mode == "process_only":
                    self._update_item(
                        item,
                        status="ready_to_export",
                        progress=1.0,
                        stage=f"Đã xử lý ({len(tr.segments)} dòng) · Chờ xem & xuất",
                    )
                    return

            if mode in ("process_and_export", "export_selected"):
                editor_state: dict[str, Any] = (
                    json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
                )
                saved_segments: list[SubtitleSegment] = (
                    [SubtitleSegment.model_validate(s) for s in json.loads(segments_file.read_text(encoding="utf-8"))]
                    if segments_file.is_file()
                    else []
                )
                saved_dub: dict[str, Any] | None = (
                    json.loads(dub_file.read_text(encoding="utf-8")) if dub_file.is_file() else None
                )

                # If the user edited subtitle lines during review without re-dubbing yet,
                # re-synthesize the dub track from the edited segments before exporting.
                dub_dirty = bool(editor_state.get("dub_dirty", False))
                if saved_dub and isinstance(saved_dub.get("track_path"), str):
                    cand = Path(saved_dub["track_path"])
                    if cand.is_file():
                        dub_track_path = cand
                if saved_segments and (dub_dirty or not dub_track_path.is_file()):
                    self._update_item(
                        item,
                        progress=0.72,
                        stage=f"Cập nhật lồng tiếng theo bản sửa ({item.tts_provider})...",
                    )
                    dub_res = tts_service.dub(
                        saved_segments,
                        work_dir=work_folder,
                        provider=item.tts_provider,
                        voice=item.tts_voice,
                        use_translation=True,
                        video_path=str(source),
                        settings=settings,
                    )
                    dub_track_path = dub_res.track_path
                    dub_payload = DubbingResult(
                        track_path=str(dub_res.track_path),
                        duration=dub_res.duration,
                        provider=dub_res.provider,
                        voice=dub_res.voice,
                        segments=dub_res.reports,
                        skipped=dub_res.skipped,
                        rewritten=dub_res.rewritten,
                    ).model_dump(mode="json")
                    dub_file.write_text(json.dumps(dub_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    if editor_state:
                        editor_state["dub_dirty"] = False
                        state_file.write_text(json.dumps(editor_state, ensure_ascii=False, indent=2), encoding="utf-8")

                # Always rebuild subtitles.ass from the latest saved segments & style.
                pres_mode = str(editor_state.get("presentation_mode") or "single")
                if saved_segments and pres_mode != "none":
                    ass_w, ass_h = media_processor.TARGET_ASPECTS.get(item.aspect, (1080, 1920))
                    raw_style = editor_state.get("subtitle_style") or item.subtitle_style
                    style_obj = (
                        SubtitleStyle.model_validate(raw_style)
                        if isinstance(raw_style, dict)
                        else SubtitleStyle()
                    )
                    sec_segs = [
                        SubtitleSegment.model_validate(s)
                        for s in (editor_state.get("secondary_segments") or [])
                        if isinstance(s, dict)
                    ]
                    sec_style = (
                        SubtitleStyle.model_validate(editor_state["secondary_style"])
                        if isinstance(editor_state.get("secondary_style"), dict)
                        else None
                    )
                    write_subtitles(
                        saved_segments,
                        ass_path,
                        fmt="ass",
                        width=ass_w,
                        height=ass_h,
                        style=style_obj,
                        use_translation=any(bool((s.translation or "").strip()) for s in saved_segments),
                        presentation_mode=pres_mode,  # type: ignore[arg-type]
                        secondary_segments=sec_segs,
                        secondary_style=sec_style,
                    )

                bg_path: str | Path | None = None
                keep_orig = True
                if item.bgm_mode == "strip":
                    if item.bgm_path and Path(item.bgm_path).is_file():
                        bg_path = item.bgm_path
                    else:
                        self._update_item(item, progress=0.78, stage="Tách bỏ nhạc nền (AI)...")
                        vocals, _inst = media_processor.separate_vocals(source, work_folder)
                        bg_path = vocals
                        self._update_item(item, bgm_path=str(vocals))
                    keep_orig = False
                elif item.bgm_mode == "custom" and item.bgm_path and Path(item.bgm_path).is_file():
                    bg_path = item.bgm_path
                    keep_orig = False
                elif item.bgm_mode == "mute":
                    keep_orig = False

                if "background_path" in editor_state:
                    custom_bg = editor_state.get("background_path")
                    bg_path = custom_bg if (custom_bg and Path(custom_bg).is_file()) else None
                if isinstance(editor_state.get("keep_original_audio"), bool):
                    keep_orig = bool(editor_state["keep_original_audio"])

                bg_gain = float(editor_state.get("background_gain") if editor_state.get("background_gain") is not None else 0.35)
                v_gain = float(editor_state.get("voice_gain") if editor_state.get("voice_gain") is not None else 1.0)
                flip_h = bool(editor_state.get("flip_horizontal", False))
                layers = [
                    VisualLayer.model_validate(ly)
                    for ly in (editor_state.get("layers") or [])
                    if isinstance(ly, dict)
                ]
                v_clips = [
                    VideoClipRange.model_validate(cl)
                    for cl in (editor_state.get("video_clips") or [])
                    if isinstance(cl, dict)
                ]

                self._update_item(item, progress=0.85, stage="Xuất video MP4...")
                out_dir = settings.data_path / "exports"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_mp4 = out_dir / f"{source.stem}_dubbed.mp4"
                produced = media_processor.render_final(
                    source,
                    out_mp4,
                    aspect=item.aspect,
                    subtitle_path=ass_path if (ass_path.is_file() and pres_mode != "none") else None,
                    dub_track_path=dub_track_path if dub_track_path.is_file() else None,
                    background_path=bg_path,
                    background_gain=bg_gain,
                    voice_gain=v_gain,
                    keep_original_audio=keep_orig,
                    flip_horizontal=flip_h,
                    layers=layers,
                    video_clips=v_clips,
                )
                self._update_item(
                    item,
                    status="completed",
                    progress=1.0,
                    stage="Hoàn tất",
                    output_path=str(produced),
                )
        except Exception as exc:
            log.exception("Queue item %s failed", item.id)
            self._update_item(item, status="error", error=str(exc), stage="Lỗi xử lý")


queue_manager = ProcessingQueueManager()

__all__ = ["ProcessingQueueManager", "QueueItem", "queue_manager"]
