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
from app.schemas.subtitle import DubbingResult, SubtitleSegment, SubtitleStyle, TranscribeRequest
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
        segments = json.loads(seg_file.read_text(encoding="utf-8")) if seg_file.is_file() else []
        dub = json.loads(dub_file.read_text(encoding="utf-8")) if dub_file.is_file() else None
        return {
            "item": asdict(item),
            "video_path": item.video_path,
            "segments": segments,
            "dub": dub,
        }

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

    def start_batch(self, mode: QueueMode = "process_and_export", item_ids: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
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
                        if mode in ("process_only", "process_and_export") and it.status in ("pending", "error", "stopped"):
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
        self._update_item(item, status="processing", progress=0.05, stage="Bắt đầu xử lý", work_dir=str(work_folder), error=None)

        try:
            ass_path = work_folder / "subtitles.ass"
            dub_track_path = work_folder / "dub_track.wav"
            segments_file = work_folder / "segments.json"
            dub_file = work_folder / "dub.json"

            if mode in ("process_only", "process_and_export"):
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
                write_subtitles(
                    tr.segments,
                    ass_path,
                    fmt="ass",
                    width=ass_w,
                    height=ass_h,
                    style=SubtitleStyle(),
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

                if mode == "process_only":
                    self._update_item(
                        item,
                        status="ready_to_export",
                        progress=1.0,
                        stage=f"Đã xử lý ({len(tr.segments)} dòng) · Chờ xem & xuất",
                    )
                    return

            if mode in ("process_and_export", "export_selected"):
                self._update_item(item, progress=0.85, stage="Xuất video MP4...")
                out_dir = settings.data_path / "exports"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_mp4 = out_dir / f"{source.stem}_dubbed.mp4"
                produced = media_processor.render_final(
                    source,
                    out_mp4,
                    aspect=item.aspect,
                    subtitle_path=ass_path if ass_path.is_file() else None,
                    dub_track_path=dub_track_path if dub_track_path.is_file() else None,
                    keep_original_audio=True,
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
