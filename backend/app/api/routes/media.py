"""Media inspection and stills for the editor's source bin."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.routes._common import run_job, with_suffix, work_dir
from app.schemas.media import (
    MediaBatchTrimRequest,
    MediaConcatRequest,
    MediaDemuxRequest,
    MediaMuxRequest,
    MediaTrimRequest,
    ProbeRequest,
    ProbeResult,
    ThumbnailRequest,
    ThumbnailResult,
    TimelineCutRequest,
)
from app.services import media_processor

router = APIRouter(tags=["media"])


@router.post("/api/media/probe", response_model=ProbeResult)
def probe(request: ProbeRequest) -> ProbeResult:
    source = Path(request.video_path)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {source}")
    try:
        info = media_processor.probe_video(source)
    except media_processor.MediaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    ratio = info.width / info.height if info.height else 0.0
    if ratio > 1.5:
        aspect = "16:9"
    elif ratio < 0.8:
        aspect = "9:16"
    else:
        aspect = "1:1"

    return ProbeResult(
        video_path=str(source),
        duration=round(info.duration, 3),
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        aspect=aspect,
    )


@router.post("/api/media/thumbnail", response_model=ThumbnailResult)
def thumbnail(request: ThumbnailRequest) -> ThumbnailResult:
    source = Path(request.video_path)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {source}")

    output = Path(request.output_path) if request.output_path else source.with_name(f"{source.stem}.thumb.jpg")

    def work(report) -> dict:
        report(stage="thumbnail", fraction=0.0, message="grabbing still")
        try:
            produced = media_processor.generate_thumbnail(source, with_suffix(output, ".jpg"), at=request.at)
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="thumbnail ready")
        return ThumbnailResult(video_path=str(source), output_path=str(produced)).model_dump(mode="json")

    return ThumbnailResult(**run_job("thumbnail", work))


@router.post("/api/media/strip-bgm")
def strip_bgm(request: ProbeRequest) -> dict:
    """Separate vocals/speech from background music using UVR-MDX so copyright BGM is removed."""
    source = Path(request.video_path)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {source}")
    folder = work_dir("strip-bgm")

    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Đang tách bỏ nhạc nền bằng AI (UVR-MDX)...")
        try:
            vocals, _inst = media_processor.separate_vocals(source, folder)
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã tách bỏ nhạc nền gốc")
        return {"output_path": str(vocals)}

    return run_job("media_tools", work)


@router.post("/api/media/cut")
def cut_timeline_video(request: TimelineCutRequest) -> dict:
    """Trim left, trim right, or delete a selected range from the timeline video."""
    source = Path(request.video_path)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {source}")

    folder = work_dir("cuts")
    out_path = folder / f"{source.stem}_{request.mode}.mp4"

    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Đang cắt video...")
        try:
            duration = media_processor.probe_duration(source)
            if duration <= 0.2:
                raise media_processor.MediaError("Video quá ngắn để cắt.")

            if request.mode == "trim_left":
                cut_at = min(max(request.start, 0.05), duration - 0.1)
                produced = media_processor.trim_media(
                    source, out_path, start=cut_at, end=duration, reencode=True
                )
            elif request.mode == "trim_right":
                cut_at = min(max(request.end or request.start, 0.1), duration - 0.05)
                produced = media_processor.trim_media(
                    source, out_path, start=0.0, end=cut_at, reencode=True
                )
            else:
                a = max(0.0, request.start)
                b = min(duration, request.end)
                if b <= a + 0.05:
                    raise media_processor.MediaError("Khoảng chọn cần xóa quá ngắn.")
                if a <= 0.05 and b >= duration - 0.05:
                    raise media_processor.MediaError("Không thể xóa toàn bộ video.")
                if a <= 0.05:
                    produced = media_processor.trim_media(
                        source, out_path, start=b, end=duration, reencode=True
                    )
                elif b >= duration - 0.05:
                    produced = media_processor.trim_media(
                        source, out_path, start=0.0, end=a, reencode=True
                    )
                else:
                    part1 = folder / f"{source.stem}_part1.mp4"
                    part2 = folder / f"{source.stem}_part2.mp4"
                    media_processor.trim_media(source, part1, start=0.0, end=a, reencode=True)
                    report(fraction=0.5, message="Đang ghép lại phần còn lại...")
                    media_processor.trim_media(source, part2, start=b, end=duration, reencode=True)
                    produced = media_processor.concat_media([part1, part2], out_path, reencode=False)

            new_duration = media_processor.probe_duration(produced)
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã cắt xong video")
        return {"output_path": str(produced), "duration": round(new_duration, 3)}

    return run_job("media_tools", work)


@router.post("/api/media/tools/trim")
def trim_tool(request: MediaTrimRequest) -> dict:
    """Cut a segment [start, end] from a video file."""
    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Cắt video...")
        try:
            produced = media_processor.trim_media(
                request.video_path,
                request.output_path,
                start=request.start,
                end=request.end,
                reencode=request.reencode,
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã cắt xong video")
        return {"output_path": str(produced)}

    return run_job("media_tools", work)


_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


@router.post("/api/media/tools/batch-trim")
def batch_trim_tool(request: MediaBatchTrimRequest) -> dict:
    """Trim multiple files from a folder/list, split a file into segments, or trim head/tail."""
    files: list[Path] = []
    if request.input_dir:
        folder = Path(request.input_dir)
        if not folder.is_dir():
            raise HTTPException(status_code=404, detail=f"Không tìm thấy thư mục: {folder}")
        files.extend(
            sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _VIDEO_EXTS)
        )
    for vp in request.video_paths:
        p = Path(vp)
        if p.is_file() and p not in files:
            files.append(p)

    if not files:
        raise HTTPException(status_code=400, detail="Không tìm thấy file video nào để cắt.")

    out_dir = Path(request.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def work(report) -> dict:
        outputs: list[str] = []
        skipped: list[str] = []
        total = len(files)

        for idx, src in enumerate(files, start=1):
            report(
                stage="media_tools",
                fraction=(idx - 1) / total,
                message=f"Đang cắt ({idx}/{total}): {src.name}",
            )
            try:
                dur = media_processor.probe_duration(src)
                if dur <= 0.1:
                    skipped.append(f"{src.name} (quá ngắn)")
                    continue

                if request.mode == "head_tail":
                    s = max(0.0, request.trim_head)
                    e = max(0.0, dur - request.trim_tail)
                    if e <= s + 0.1:
                        skipped.append(f"{src.name} (thời lượng còn lại <= 0.1s)")
                        continue
                    out = out_dir / f"{src.stem}_trimmed.mp4"
                    produced = media_processor.trim_media(
                        src, out, start=s, end=e, reencode=request.reencode
                    )
                    outputs.append(str(produced))

                elif request.mode == "range":
                    s = max(0.0, request.start)
                    e = min(dur, request.end) if request.end > 0 else dur
                    if e <= s + 0.1:
                        skipped.append(f"{src.name} (khoảng cắt không hợp lệ)")
                        continue
                    out = out_dir / f"{src.stem}_cut.mp4"
                    produced = media_processor.trim_media(
                        src, out, start=s, end=e, reencode=request.reencode
                    )
                    outputs.append(str(produced))

                elif request.mode == "split_duration":
                    step = max(0.5, request.chunk_duration)
                    part = 1
                    cur = 0.0
                    while cur < dur - 0.1:
                        nxt = min(dur, cur + step)
                        if nxt - cur < 0.1:
                            break
                        out = out_dir / f"{src.stem}_part{part:03d}.mp4"
                        produced = media_processor.trim_media(
                            src, out, start=cur, end=nxt, reencode=request.reencode
                        )
                        outputs.append(str(produced))
                        part += 1
                        cur = nxt

                elif request.mode == "split_ranges":
                    if not request.ranges:
                        skipped.append(f"{src.name} (chưa có danh sách đoạn)")
                        continue
                    for seg_idx, (rs, re_) in enumerate(request.ranges, start=1):
                        s = max(0.0, float(rs))
                        e = min(dur, float(re_))
                        if e <= s + 0.1:
                            continue
                        out = out_dir / f"{src.stem}_seg{seg_idx:03d}.mp4"
                        produced = media_processor.trim_media(
                            src, out, start=s, end=e, reencode=request.reencode
                        )
                        outputs.append(str(produced))

            except media_processor.MediaError as exc:
                skipped.append(f"{src.name} ({exc})")

        report(fraction=1.0, message=f"Đã cắt xong {len(outputs)} file")
        return {"outputs": outputs, "count": len(outputs), "skipped": skipped}

    return run_job("media_tools", work)


@router.post("/api/media/tools/concat")
def concat_tool(request: MediaConcatRequest) -> dict:
    """Concatenate multiple video files in order."""
    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Ghép video...")
        try:
            produced = media_processor.concat_media(
                request.video_paths,
                request.output_path,
                reencode=request.reencode,
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã ghép xong video")
        return {"output_path": str(produced)}

    return run_job("media_tools", work)


@router.post("/api/media/tools/demux")
def demux_tool(request: MediaDemuxRequest) -> dict:
    """Extract audio stream and/or mute video stream."""
    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Tách âm thanh / video...")
        try:
            outputs = media_processor.extract_streams(
                request.video_path,
                audio_output=request.audio_output,
                video_only_output=request.video_only_output,
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã tách xong")
        return outputs

    return run_job("media_tools", work)


@router.post("/api/media/tools/mux")
def mux_tool(request: MediaMuxRequest) -> dict:
    """Mux audio into video (replace or mix)."""
    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Ghép âm thanh vào video...")
        try:
            produced = media_processor.mux_audio_video(
                request.video_path,
                request.audio_path,
                request.output_path,
                replace_audio=request.replace_audio,
                audio_gain=request.audio_gain,
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã ghép xong")
        return {"output_path": str(produced)}

    return run_job("media_tools", work)


__all__ = ["router"]
