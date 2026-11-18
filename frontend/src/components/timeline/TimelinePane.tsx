import {
  ArrowLeftToLine,
  ArrowRightToLine,
  Captions,
  ChevronDown,
  ChevronUp,
  Film,
  Layers,
  Maximize2,
  Music,
  Music2,
  Ruler,
  ScanText,
  Scissors,
  Sparkles,
  Split,
  Trash2,
  Undo2,
  VolumeX,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { clipTimelineBounds, timelineDuration } from "@/lib/clips";
import { remixDub } from "@/lib/api";
import { clamp, formatTime } from "@/lib/format";
import { baseName } from "@/lib/media";
import { player } from "@/lib/player";
import {
  deleteTimelineClip,
  runRangeTranscribe,
  runTimelineCut,
  splitTimelineClip,
  trimClipEdge,
  undoTimelineCut,
} from "@/lib/tasks";
import { useEditor } from "@/store/editorStore";

const TRACKS = [
  { id: "video", label: "Video", icon: Film, accent: "bg-track-video/80 border-track-video" },
  { id: "layers", label: "Lớp phủ", icon: Layers, accent: "bg-violet-500/70 border-violet-400" },
  { id: "subtitle", label: "Phụ đề", icon: Captions, accent: "bg-track-subtitle/80 border-track-subtitle" },
  { id: "dub", label: "Lồng tiếng", icon: Music2, accent: "bg-track-dubbed/80 border-track-dubbed" },
  { id: "bgm", label: "Âm nền", icon: Music, accent: "bg-emerald-600/70 border-emerald-400" },
] as const;

const STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120];

export function TimelinePane() {
  const source = useEditor((state) => state.source);
  const videoClips = useEditor((state) => state.videoClips);
  const selectedClipId = useEditor((state) => state.selectedClipId);
  const segments = useEditor((state) => state.segments);
  const patchSegment = useEditor((state) => state.patchSegment);
  const layers = useEditor((state) => state.layers);
  const selectedLayerId = useEditor((state) => state.selectedLayerId);
  const patchLayer = useEditor((state) => state.patchLayer);
  const dub = useEditor((state) => state.dub);
  const backgroundAudioPath = useEditor((state) => state.backgroundAudioPath);
  const keepOriginalAudio = useEditor((state) => state.keepOriginalAudio);
  const playhead = useEditor((state) => state.playhead);
  const playing = useEditor((state) => state.playing);
  const zoom = useEditor((state) => state.zoom);
  const selected = useEditor((state) => state.selected);
  const follow = useEditor((state) => state.followPlayhead);
  const selectionRange = useEditor((state) => state.selectionRange);
  const cutUndoCount = useEditor((state) => state.cutUndoStack.length);
  const pending = useEditor((state) => state.pending);
  const update = useEditor((state) => state.update);
  const setPlayhead = useEditor((state) => state.setPlayhead);
  const collapsed = useEditor((state) => state.timelineCollapsed);

  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const [videoTrimDrag, setVideoTrimDrag] = useState<{
    clipId: string;
    side: "left" | "right";
    sourceTime: number;
  } | null>(null);
  const [layerDrag, setLayerDrag] = useState<{
    id: string;
    mode: "move" | "start" | "end";
    startClientX: number;
    origStart: number;
    origEnd: number;
  } | null>(null);
  const [subDrag, setSubDrag] = useState<{
    index: number;
    mode: "move" | "start" | "end";
    startClientX: number;
    origStart: number;
    origEnd: number;
    moved: boolean;
  } | null>(null);
  const [dubDrag, setDubDrag] = useState<{
    index: number;
    mode: "move" | "start" | "end";
    startClientX: number;
    origStart: number;
    origEnd: number;
    moved: boolean;
  } | null>(null);

  const duration = Math.max(
    videoClips.length ? timelineDuration(videoClips) : (source?.duration ?? 0),
    1,
  );
  const clipBounds = clipTimelineBounds(videoClips);
  const pps = zoom;
  const contentWidth = duration * pps + 32;
  const step = STEPS.find((candidate) => candidate * pps >= 58) ?? 120;
  const tickCount = Math.floor(duration / step) + 1;

  const seekFromPointer = (event: React.PointerEvent) => {
    const rect = contentRef.current?.getBoundingClientRect();
    if (!rect) return;
    const seconds = clamp((event.clientX - rect.left) / pps, 0, duration);
    setPlayhead(Number(seconds.toFixed(3)), { seek: true });
  };

  useEffect(() => {
    if (!follow || !playing) return;
    const container = scrollRef.current;
    if (!container) return;
    const x = playhead * pps;
    const left = container.scrollLeft;
    const right = left + container.clientWidth;
    if (x < left + 24 || x > right - 48) {
      container.scrollLeft = Math.max(0, x - container.clientWidth * 0.4);
    }
  }, [playhead, follow, playing, pps]);

  const fit = () => {
    const container = scrollRef.current;
    if (!container || !source) return;
    update({ zoom: clamp((container.clientWidth - 40) / Math.max(source.duration, 1), 10, 400) });
  };

  return (
    <section className={`flex shrink-0 flex-col border-t border-border bg-surface-1 ${collapsed ? "h-8" : "h-72"}`}>
      <header className="flex min-h-8 shrink-0 flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-border px-3 py-1">
        <button
          className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-200"
          onClick={() => update({ timelineCollapsed: !collapsed })}
          title={collapsed ? "Mở timeline" : "Thu gọn timeline để xem video lớn hơn"}
        >
          {collapsed ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          Timeline
        </button>
        <span className="font-mono text-[10px] text-slate-500">
          {formatTime(playhead)} / {formatTime(videoClips.length ? timelineDuration(videoClips) : (source?.duration ?? 0))}
        </span>

        {/* CapCut-style Non-Destructive Timeline Cut, Split & Range */}
        {source && (
          <div className="flex flex-wrap items-center gap-1 text-[10px]">
            <button
              className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
              disabled={playhead <= 0.1 || playhead >= duration - 0.1}
              onClick={() => splitTimelineClip()}
              title="Tách đôi clip tại vị trí playhead (không xóa)"
            >
              <Split size={10} />
              Tách
            </button>
            <button
              className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
              disabled={playhead <= 0.1 || playhead >= duration - 0.1}
              onClick={() => void runTimelineCut("trim_left")}
              title="Cắt bỏ phần bên trái playhead (tức thì, không render lại)"
            >
              <ArrowLeftToLine size={10} />
              Cắt trái
            </button>
            <button
              className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
              disabled={playhead <= 0.1 || playhead >= duration - 0.1}
              onClick={() => void runTimelineCut("trim_right")}
              title="Cắt bỏ phần bên phải playhead (tức thì, không render lại)"
            >
              <ArrowRightToLine size={10} />
              Cắt phải
            </button>
            {selectedClipId && videoClips.length > 1 && (
              <button
                className="btn-ghost !px-1.5 !py-0.5 !text-[10px] text-rose-300"
                onClick={() => deleteTimelineClip(selectedClipId)}
                title="Xóa đoạn clip đang chọn trên Timeline"
              >
                <Trash2 size={10} />
                Xóa clip
              </button>
            )}
            {cutUndoCount > 0 && (
              <button
                className="btn-ghost !px-1.5 !py-0.5 !text-[10px] text-amber-300"
                onClick={undoTimelineCut}
                title="Hoàn tác lần cắt vừa rồi"
              >
                <Undo2 size={10} />
                Hoàn tác
              </button>
            )}
            <span className="mx-0.5 h-3 w-px bg-border" />
            <button
              className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
              title="Đặt điểm bắt đầu khoảng chọn tại vị trí playhead"
              onClick={() => {
                const end = selectionRange ? Math.max(selectionRange[1], playhead + 1) : Math.min(duration, playhead + 5);
                update({ selectionRange: [playhead, end] });
              }}
            >
              [ Đầu
            </button>
            <button
              className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
              title="Đặt điểm kết thúc khoảng chọn tại vị trí playhead"
              onClick={() => {
                const start = selectionRange ? Math.min(selectionRange[0], Math.max(0, playhead - 0.5)) : Math.max(0, playhead - 5);
                update({ selectionRange: [start, Math.max(start + 0.5, playhead)] });
              }}
            >
              Cuối ]
            </button>
            {selectionRange && (
              <>
                <span className="font-mono text-[10px] text-brand">
                  {selectionRange[0].toFixed(1)}s–{selectionRange[1].toFixed(1)}s
                </span>
                <button
                  className="btn-ghost !px-1.5 !py-0.5 !text-[10px] text-rose-300"
                  disabled={Boolean(pending.cut)}
                  onClick={() => void runTimelineCut("delete_range")}
                  title="Xóa đoạn video đang chọn và nối phần còn lại"
                >
                  <Scissors size={10} />
                  Xóa đoạn
                </button>
                <button
                  className="btn-ghost !px-1.5 !py-0.5 !text-[10px] text-amber-300"
                  disabled={Boolean(pending.transcribe)}
                  onClick={() => void runRangeTranscribe("ocr")}
                  title="Tách lại phụ đề trong khoảng chọn bằng OCR"
                >
                  <ScanText size={10} />
                  Alt: OCR
                </button>
                <button
                  className="btn-ghost !px-1.5 !py-0.5 !text-[10px] text-sky-300"
                  disabled={Boolean(pending.transcribe)}
                  onClick={() => void runRangeTranscribe("whisper")}
                  title="Tách lại phụ đề trong khoảng chọn bằng Whisper"
                >
                  <Sparkles size={10} />
                  Alt: Whisper
                </button>
                <button
                  className="text-slate-500 hover:text-slate-300"
                  onClick={() => update({ selectionRange: null })}
                  title="Bỏ khoảng chọn"
                >
                  ×
                </button>
              </>
            )}
          </div>
        )}

        <label className="ml-auto flex items-center gap-1.5 text-[10px] text-slate-500">
          <Ruler size={10} />
          <input
            type="range"
            min={15}
            max={240}
            step={5}
            value={zoom}
            onChange={(event) => update({ zoom: Number(event.target.value) })}
            className="h-1 w-28 accent-brand"
          />
          <span className="w-12 font-mono text-slate-400">{Math.round(zoom)} px/s</span>
        </label>

        <label className="flex cursor-pointer items-center gap-1 text-[10px] text-slate-500">
          <input
            type="checkbox"
            className="h-3 w-3 accent-brand"
            checked={follow}
            onChange={(event) => update({ followPlayhead: event.target.checked })}
          />
          bám playhead
        </label>

        <button className="btn-ghost !px-2 !py-0.5 !text-[10px]" onClick={fit} disabled={!source}>
          <Maximize2 size={10} />
          Vừa khung
        </button>
      </header>

      <div className={`${collapsed ? "hidden" : "flex"} min-h-0 flex-1`}>
        <div className="w-[5.5rem] shrink-0 border-r border-border bg-surface-2">
          <div className="h-5 border-b border-border" />
          {TRACKS.map((track) => (
            <div key={track.id} className="flex h-11 items-center gap-1.5 border-b border-border/60 px-2">
              <track.icon size={11} className="text-slate-500" />
              <span className="text-[10px] text-slate-400">{track.label}</span>
            </div>
          ))}
        </div>

        <div ref={scrollRef} className="min-w-0 flex-1 overflow-x-auto overflow-y-hidden">
          <div
            ref={contentRef}
            className="relative h-full select-none"
            style={{ width: contentWidth }}
            onPointerDown={(event) => {
              if ((event.target as HTMLElement).closest("[data-clip]")) return;
              event.currentTarget.setPointerCapture(event.pointerId);
              setScrubbing(true);
              seekFromPointer(event);
            }}
            onPointerMove={(event) => {
              if (scrubbing) seekFromPointer(event);
            }}
            onPointerUp={() => setScrubbing(false)}
            onPointerCancel={() => setScrubbing(false)}
          >
            {/* Ruler */}
            <div className="relative h-5 border-b border-border bg-surface-2/60">
              {Array.from({ length: tickCount }, (_, index) => index * step).map((seconds) => (
                <div key={seconds} className="absolute top-0 h-full" style={{ left: seconds * pps }}>
                  <div className="h-full w-px bg-border" />
                  <span className="absolute left-1 top-0.5 font-mono text-[9px] text-slate-500">
                    {formatTime(seconds)}
                  </span>
                </div>
              ))}
            </div>

            {/* Video lane — hiển thị các đoạn clip kiểu CapCut, kéo 2 mép để cắt tức thì */}
            <div className="relative h-11 border-b border-border/60">
              {source && clipBounds.length ? (
                clipBounds.map(({ clip, index, start, end }) => {
                  const previewStart =
                    videoTrimDrag?.clipId === clip.id && videoTrimDrag.side === "left"
                      ? videoTrimDrag.sourceTime
                      : clip.source_start;
                  const previewEnd =
                    videoTrimDrag?.clipId === clip.id && videoTrimDrag.side === "right"
                      ? videoTrimDrag.sourceTime
                      : clip.source_end;
                  const deltaLeft = previewStart - clip.source_start;
                  const leftPx = (start + deltaLeft) * pps;
                  const widthPx = Math.max((previewEnd - previewStart) * pps, 14);
                  const isSel = selectedClipId === clip.id;

                  return (
                    <div
                      key={clip.id}
                      data-clip
                      onClick={(e) => {
                        e.stopPropagation();
                        update({ selectedClipId: clip.id });
                      }}
                      className={`group absolute inset-y-1 flex cursor-pointer items-center gap-1 overflow-hidden rounded border px-2.5 select-none touch-none ${
                        isSel
                          ? "border-brand bg-track-video text-white ring-1 ring-brand"
                          : "border-track-video bg-track-video/75 text-white/90 hover:bg-track-video/90"
                      }`}
                      style={{ left: leftPx, width: widthPx }}
                      title={`Đoạn ${index + 1}: ${previewStart.toFixed(1)}s–${previewEnd.toFixed(1)}s (nguồn) · kéo 2 mép để cắt`}
                    >
                      <div
                        onPointerDown={(e) => {
                          e.stopPropagation();
                          update({ selectedClipId: clip.id });
                          e.currentTarget.setPointerCapture(e.pointerId);
                          setVideoTrimDrag({ clipId: clip.id, side: "left", sourceTime: clip.source_start });
                        }}
                        onPointerMove={(e) => {
                          if (videoTrimDrag?.clipId !== clip.id || videoTrimDrag.side !== "left") return;
                          e.stopPropagation();
                          const rect = contentRef.current?.getBoundingClientRect();
                          if (!rect) return;
                          const tlPos = (e.clientX - rect.left) / pps;
                          const candidate = clamp(
                            clip.source_start + (tlPos - start),
                            0,
                            clip.source_end - 0.1,
                          );
                          setVideoTrimDrag({ clipId: clip.id, side: "left", sourceTime: Number(candidate.toFixed(2)) });
                        }}
                        onPointerUp={(e) => {
                          if (videoTrimDrag?.clipId !== clip.id || videoTrimDrag.side !== "left") return;
                          e.stopPropagation();
                          const nextVal = videoTrimDrag.sourceTime;
                          setVideoTrimDrag(null);
                          trimClipEdge(clip.id, "left", nextVal);
                        }}
                        onPointerCancel={() => setVideoTrimDrag(null)}
                        className="absolute inset-y-0 left-0 w-2 cursor-ew-resize bg-white/15 hover:bg-brand/80"
                        title="Kéo sang phải để cắt đầu đoạn"
                      />
                      <Film size={10} className="shrink-0 opacity-80" />
                      {!keepOriginalAudio && (
                        <span title="Đã tắt tiếng gốc" className="shrink-0 text-amber-300">
                          <VolumeX size={10} />
                        </span>
                      )}
                      <span className="truncate text-[10px] font-medium">
                        {videoTrimDrag?.clipId === clip.id
                          ? `${previewStart.toFixed(1)}s–${previewEnd.toFixed(1)}s`
                          : clipBounds.length > 1
                            ? `#${index + 1} · ${clip.name}`
                            : clip.name}
                      </span>
                      <span className="ml-auto shrink-0 font-mono text-[9px] opacity-70">
                        {(previewEnd - previewStart).toFixed(1)}s
                      </span>
                      <div
                        onPointerDown={(e) => {
                          e.stopPropagation();
                          update({ selectedClipId: clip.id });
                          e.currentTarget.setPointerCapture(e.pointerId);
                          setVideoTrimDrag({ clipId: clip.id, side: "right", sourceTime: clip.source_end });
                        }}
                        onPointerMove={(e) => {
                          if (videoTrimDrag?.clipId !== clip.id || videoTrimDrag.side !== "right") return;
                          e.stopPropagation();
                          const rect = contentRef.current?.getBoundingClientRect();
                          if (!rect) return;
                          const tlPos = (e.clientX - rect.left) / pps;
                          const maxSrc = index === clipBounds.length - 1 ? source.duration : end;
                          const candidate = clamp(
                            clip.source_start + (tlPos - start),
                            clip.source_start + 0.1,
                            Math.max(clip.source_end, maxSrc),
                          );
                          setVideoTrimDrag({ clipId: clip.id, side: "right", sourceTime: Number(candidate.toFixed(2)) });
                        }}
                        onPointerUp={(e) => {
                          if (videoTrimDrag?.clipId !== clip.id || videoTrimDrag.side !== "right") return;
                          e.stopPropagation();
                          const nextVal = videoTrimDrag.sourceTime;
                          setVideoTrimDrag(null);
                          trimClipEdge(clip.id, "right", nextVal);
                        }}
                        onPointerCancel={() => setVideoTrimDrag(null)}
                        className="absolute inset-y-0 right-0 w-2 cursor-ew-resize bg-white/15 hover:bg-brand/80"
                        title="Kéo sang trái để cắt cuối đoạn"
                      />
                    </div>
                  );
                })
              ) : (
                <span className="absolute left-2 top-3 text-[10px] text-slate-600">chưa có video</span>
              )}
            </div>

            {/* Visual Layers lane (Làm mờ, Khung che, Chữ, Logo) */}
            <div className="relative h-11 border-b border-border/60">
              {layers.map((layer) => {
                const start = Math.max(0, layer.start);
                const end = layer.end > start ? layer.end : duration;
                const width = Math.max((end - start) * pps, 14);
                const isSelected = selectedLayerId === layer.id;
                const kindStyle =
                  layer.kind === "blur"
                    ? "border-violet-400 bg-violet-600/75 text-violet-50"
                    : layer.kind === "mask"
                      ? "border-amber-400 bg-amber-600/75 text-amber-50"
                      : layer.kind === "logo"
                        ? "border-emerald-400 bg-emerald-600/75 text-emerald-50"
                        : "border-sky-400 bg-sky-600/75 text-sky-50";

                const handleLayerMove = (e: React.PointerEvent<HTMLDivElement>) => {
                  if (!layerDrag || layerDrag.id !== layer.id) return;
                  e.stopPropagation();
                  const deltaSec = (e.clientX - layerDrag.startClientX) / pps;
                  const minLen = 0.1;
                  if (layerDrag.mode === "move") {
                    const span = Math.max(minLen, layerDrag.origEnd - layerDrag.origStart);
                    const ns = clamp(layerDrag.origStart + deltaSec, 0, Math.max(0, duration - span));
                    patchLayer(layer.id, {
                      start: Number(ns.toFixed(3)),
                      end: Number((ns + span).toFixed(3)),
                    });
                  } else if (layerDrag.mode === "start") {
                    const ns = clamp(layerDrag.origStart + deltaSec, 0, layerDrag.origEnd - minLen);
                    patchLayer(layer.id, { start: Number(ns.toFixed(3)) });
                  } else {
                    const ne = clamp(layerDrag.origEnd + deltaSec, layerDrag.origStart + minLen, duration);
                    patchLayer(layer.id, { end: Number(ne.toFixed(3)) });
                  }
                };

                return (
                  <div
                    key={layer.id}
                    data-clip
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      update({ selectedLayerId: layer.id });
                      e.currentTarget.setPointerCapture(e.pointerId);
                      setLayerDrag({
                        id: layer.id,
                        mode: "move",
                        startClientX: e.clientX,
                        origStart: start,
                        origEnd: end,
                      });
                    }}
                    onPointerMove={handleLayerMove}
                    onPointerUp={(e) => {
                      if (layerDrag?.id === layer.id) {
                        e.stopPropagation();
                        setLayerDrag(null);
                      }
                    }}
                    onPointerCancel={() => setLayerDrag(null)}
                    className={`group absolute inset-y-1.5 flex cursor-grab items-center overflow-hidden rounded border px-1.5 text-[9px] select-none touch-none ${kindStyle} ${
                      isSelected ? "ring-2 ring-brand" : ""
                    }`}
                    style={{ left: start * pps, width }}
                    title={`${layer.label} (${start.toFixed(1)}s–${end.toFixed(1)}s) · kéo để dịch, kéo 2 mép để cắt`}
                  >
                    <div
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        update({ selectedLayerId: layer.id });
                        e.currentTarget.setPointerCapture(e.pointerId);
                        setLayerDrag({
                          id: layer.id,
                          mode: "start",
                          startClientX: e.clientX,
                          origStart: start,
                          origEnd: end,
                        });
                      }}
                      onPointerMove={handleLayerMove}
                      onPointerUp={(e) => {
                        if (layerDrag?.id === layer.id) {
                          e.stopPropagation();
                          setLayerDrag(null);
                        }
                      }}
                      onPointerCancel={() => setLayerDrag(null)}
                      className="absolute inset-y-0 left-0 w-2 cursor-ew-resize bg-white/0 hover:bg-white/35"
                    />
                    <span className="truncate font-medium">
                      {layer.kind === "text" && layer.text ? `${layer.label}: ${layer.text}` : layer.label}
                    </span>
                    <span className="ml-auto shrink-0 pl-1 font-mono text-[8px] opacity-75">
                      {(end - start).toFixed(1)}s
                    </span>
                    <div
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        update({ selectedLayerId: layer.id });
                        e.currentTarget.setPointerCapture(e.pointerId);
                        setLayerDrag({
                          id: layer.id,
                          mode: "end",
                          startClientX: e.clientX,
                          origStart: start,
                          origEnd: end,
                        });
                      }}
                      onPointerMove={handleLayerMove}
                      onPointerUp={(e) => {
                        if (layerDrag?.id === layer.id) {
                          e.stopPropagation();
                          setLayerDrag(null);
                        }
                      }}
                      onPointerCancel={() => setLayerDrag(null)}
                      className="absolute inset-y-0 right-0 w-2 cursor-ew-resize bg-white/0 hover:bg-white/35"
                    />
                  </div>
                );
              })}
              {!layers.length && (
                <span className="absolute left-2 top-3 text-[10px] text-slate-600">
                  chưa có lớp phủ — thêm Làm mờ / Khung che / Chữ / Logo ở trên video
                </span>
              )}
            </div>

            {/* Subtitle lane */}
            <div className="relative h-11 border-b border-border/60">
              {segments.map((segment) => {
                const width = Math.max((segment.end - segment.start) * pps, 6);
                const isActive = playhead >= segment.start && playhead < segment.end;
                const displayText = segment.translation?.trim() || segment.text || `#${segment.index}`;

                const handleSubMove = (e: React.PointerEvent<HTMLDivElement>) => {
                  if (!subDrag || subDrag.index !== segment.index) return;
                  e.stopPropagation();
                  const deltaPx = e.clientX - subDrag.startClientX;
                  if (Math.abs(deltaPx) > 2 && !subDrag.moved) {
                    setSubDrag({ ...subDrag, moved: true });
                  }
                  const deltaSec = deltaPx / pps;
                  if (subDrag.mode === "move") {
                    const len = subDrag.origEnd - subDrag.origStart;
                    const ns = clamp(subDrag.origStart + deltaSec, 0, Math.max(0, duration - len));
                    patchSegment(segment.index, {
                      start: Number(ns.toFixed(2)),
                      end: Number((ns + len).toFixed(2)),
                    });
                  } else if (subDrag.mode === "start") {
                    const ns = clamp(subDrag.origStart + deltaSec, 0, subDrag.origEnd - 0.2);
                    patchSegment(segment.index, { start: Number(ns.toFixed(2)) });
                  } else if (subDrag.mode === "end") {
                    const ne = clamp(subDrag.origEnd + deltaSec, subDrag.origStart + 0.2, duration);
                    patchSegment(segment.index, { end: Number(ne.toFixed(2)) });
                  }
                };

                return (
                  <div
                    key={segment.index}
                    data-clip
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      update({ selected: segment.index });
                      e.currentTarget.setPointerCapture(e.pointerId);
                      setSubDrag({
                        index: segment.index,
                        mode: "move",
                        startClientX: e.clientX,
                        origStart: segment.start,
                        origEnd: segment.end,
                        moved: false,
                      });
                    }}
                    onPointerMove={handleSubMove}
                    onPointerUp={(e) => {
                      if (subDrag?.index === segment.index) {
                        e.stopPropagation();
                        const wasMoved = subDrag.moved;
                        setSubDrag(null);
                        if (!wasMoved) {
                          setPlayhead(segment.start, { seek: true });
                        }
                      }
                    }}
                    onPointerCancel={() => setSubDrag(null)}
                    className={`group absolute inset-y-1.5 flex cursor-grab items-center overflow-hidden rounded border px-1.5 text-left text-[9px] leading-none transition-colors select-none touch-none ${
                      selected === segment.index
                        ? "border-brand bg-track-subtitle text-white ring-1 ring-brand"
                        : isActive
                          ? "border-brand/60 bg-track-subtitle/90 text-white"
                          : "border-track-subtitle bg-track-subtitle/70 text-amber-50/90 hover:bg-track-subtitle"
                    }`}
                    style={{ left: segment.start * pps, width }}
                    title={`#${segment.index} · ${segment.text || "(trống)"}${segment.translation?.trim() ? `\n→ ${segment.translation}` : ""}\n(Kéo để dịch chuyển, kéo 2 mép để co giãn thời lượng)`}
                  >
                    <div
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        update({ selected: segment.index });
                        e.currentTarget.setPointerCapture(e.pointerId);
                        setSubDrag({
                          index: segment.index,
                          mode: "start",
                          startClientX: e.clientX,
                          origStart: segment.start,
                          origEnd: segment.end,
                          moved: true,
                        });
                      }}
                      onPointerMove={handleSubMove}
                      onPointerUp={(e) => {
                        if (subDrag?.index === segment.index) {
                          e.stopPropagation();
                          setSubDrag(null);
                        }
                      }}
                      onPointerCancel={() => setSubDrag(null)}
                      className="absolute inset-y-0 left-0 w-1.5 cursor-ew-resize bg-white/0 hover:bg-white/40"
                    />
                    <span className="flex h-full items-center truncate">{displayText}</span>
                    <div
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        update({ selected: segment.index });
                        e.currentTarget.setPointerCapture(e.pointerId);
                        setSubDrag({
                          index: segment.index,
                          mode: "end",
                          startClientX: e.clientX,
                          origStart: segment.start,
                          origEnd: segment.end,
                          moved: true,
                        });
                      }}
                      onPointerMove={handleSubMove}
                      onPointerUp={(e) => {
                        if (subDrag?.index === segment.index) {
                          e.stopPropagation();
                          setSubDrag(null);
                        }
                      }}
                      onPointerCancel={() => setSubDrag(null)}
                      className="absolute inset-y-0 right-0 w-1.5 cursor-ew-resize bg-white/0 hover:bg-white/40"
                    />
                  </div>
                );
              })}
              {!segments.length && (
                <span className="absolute left-2 top-3 text-[10px] text-slate-600">
                  chưa có phụ đề — chạy bước 1 ở panel AI
                </span>
              )}
            </div>

            {/* Dubbing lane */}
            <div className="relative h-11 border-b border-border/60">
              {dub?.segments.map((report) => {
                const width = Math.max((report.end - report.start) * pps, 6);
                const isSelected = selected === report.index;

                const patchDubSegment = (patch: Partial<typeof report>) => {
                  const curDub = useEditor.getState().dub;
                  if (!curDub) return;
                  const nextSegs = curDub.segments.map((item) =>
                    item.index === report.index ? { ...item, ...patch } : item,
                  );
                  update({
                    dub: { ...curDub, segments: nextSegs },
                    projectDirty: true,
                  });
                };

                const handleDubMove = (e: React.PointerEvent<HTMLDivElement>) => {
                  if (!dubDrag || dubDrag.index !== report.index) return;
                  e.stopPropagation();
                  const deltaPx = e.clientX - dubDrag.startClientX;
                  if (Math.abs(deltaPx) > 2 && !dubDrag.moved) {
                    setDubDrag({ ...dubDrag, moved: true });
                  }
                  const deltaSec = deltaPx / pps;
                  if (dubDrag.mode === "move") {
                    const len = dubDrag.origEnd - dubDrag.origStart;
                    const ns = clamp(dubDrag.origStart + deltaSec, 0, Math.max(0, duration - len));
                    patchDubSegment({
                      start: Number(ns.toFixed(2)),
                      end: Number((ns + len).toFixed(2)),
                    });
                  } else if (dubDrag.mode === "start") {
                    const ns = clamp(dubDrag.origStart + deltaSec, 0, dubDrag.origEnd - 0.2);
                    const newDur = Math.max(dubDrag.origEnd - ns, 0.2);
                    const newRatio = clamp(report.tts_duration / newDur, 0.5, 2.5);
                    patchDubSegment({
                      start: Number(ns.toFixed(2)),
                      target_duration: Number(newDur.toFixed(3)),
                      applied_ratio: Number(newRatio.toFixed(2)),
                      clamped: newRatio > 1.35,
                      overflow_s: 0,
                    });
                  } else if (dubDrag.mode === "end") {
                    const ne = clamp(dubDrag.origEnd + deltaSec, dubDrag.origStart + 0.2, duration + 2);
                    const newDur = Math.max(ne - dubDrag.origStart, 0.2);
                    const newRatio = clamp(report.tts_duration / newDur, 0.5, 2.5);
                    patchDubSegment({
                      end: Number(ne.toFixed(2)),
                      target_duration: Number(newDur.toFixed(3)),
                      applied_ratio: Number(newRatio.toFixed(2)),
                      clamped: newRatio > 1.35,
                      overflow_s: 0,
                    });
                  }
                };

                const finishDubDrag = (e: React.PointerEvent<HTMLDivElement>) => {
                  if (!dubDrag || dubDrag.index !== report.index) return;
                  e.stopPropagation();
                  const wasMoved = dubDrag.moved;
                  setDubDrag(null);
                  if (!wasMoved) {
                    update({ selected: report.index });
                    setPlayhead(report.start, { seek: true });
                    return;
                  }
                  const curDub = useEditor.getState().dub;
                  if (curDub && curDub.segments.length > 0 && curDub.segments.every((r) => Boolean(r.clip_path))) {
                    const totalDur = Math.max(
                      duration,
                      ...curDub.segments.map((r) => r.end),
                      0.5,
                    );
                    void remixDub(curDub.segments, totalDur)
                      .then((res) => {
                        useEditor.setState((cur) =>
                          cur.dub
                            ? { dub: { ...cur.dub, track_path: res.track_path, duration: res.duration } }
                            : {},
                        );
                      })
                      .catch(() => null);
                  }
                };

                return (
                  <div
                    key={report.index}
                    data-clip
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      update({ selected: report.index });
                      e.currentTarget.setPointerCapture(e.pointerId);
                      setDubDrag({
                        index: report.index,
                        mode: "move",
                        startClientX: e.clientX,
                        origStart: report.start,
                        origEnd: report.end,
                        moved: false,
                      });
                    }}
                    onPointerMove={handleDubMove}
                    onPointerUp={finishDubDrag}
                    onPointerCancel={() => setDubDrag(null)}
                    className={`group absolute inset-y-1.5 flex cursor-grab items-center overflow-hidden rounded border px-1.5 text-[9px] text-white/90 select-none touch-none ${
                      isSelected
                        ? "border-brand bg-track-dubbed ring-1 ring-brand"
                        : report.clamped
                          ? "border-rose-400/70 bg-track-dubbed/70 ring-1 ring-rose-400/40"
                          : "border-track-dubbed bg-track-dubbed/70 hover:bg-track-dubbed"
                    }`}
                    style={{ left: report.start * pps, width }}
                    title={`#${report.index} · TTS ${report.tts_duration.toFixed(2)}s → ${report.target_duration.toFixed(2)}s · ${report.applied_ratio.toFixed(2)}×${
                      report.overflow_s > 0 ? ` · cắt ${report.overflow_s.toFixed(2)}s` : ""
                    }\n(Kéo để dịch vị trí câu đọc, kéo 2 mép để co giãn tốc độ đọc)`}
                  >
                    <div
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        update({ selected: report.index });
                        e.currentTarget.setPointerCapture(e.pointerId);
                        setDubDrag({
                          index: report.index,
                          mode: "start",
                          startClientX: e.clientX,
                          origStart: report.start,
                          origEnd: report.end,
                          moved: true,
                        });
                      }}
                      onPointerMove={handleDubMove}
                      onPointerUp={finishDubDrag}
                      onPointerCancel={() => setDubDrag(null)}
                      className="absolute inset-y-0 left-0 w-1.5 cursor-ew-resize bg-white/0 hover:bg-white/40"
                    />
                    <span className="truncate">{report.clamped ? "⚠ " : ""}{report.applied_ratio.toFixed(2)}×</span>
                    <div
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        update({ selected: report.index });
                        e.currentTarget.setPointerCapture(e.pointerId);
                        setDubDrag({
                          index: report.index,
                          mode: "end",
                          startClientX: e.clientX,
                          origStart: report.start,
                          origEnd: report.end,
                          moved: true,
                        });
                      }}
                      onPointerMove={handleDubMove}
                      onPointerUp={finishDubDrag}
                      onPointerCancel={() => setDubDrag(null)}
                      className="absolute inset-y-0 right-0 w-1.5 cursor-ew-resize bg-white/0 hover:bg-white/40"
                    />
                  </div>
                );
              })}
              {dub && !dub.segments.length && (
                <span className="absolute left-2 top-3 text-[10px] text-slate-600">track rỗng</span>
              )}
              {!dub && (
                <span className="absolute left-2 top-3 text-[10px] text-slate-600">
                  chưa lồng tiếng
                </span>
              )}
            </div>

            {/* Clean/Replacement BGM lane */}
            <div className="relative h-11">
              {backgroundAudioPath ? (
                <div
                  data-clip
                  className="absolute inset-y-1.5 flex items-center gap-1.5 overflow-hidden rounded border border-emerald-400 bg-emerald-600/75 px-2 text-[10px] text-emerald-50"
                  style={{ left: 0, width: Math.max(duration * pps, 24) }}
                  title={backgroundAudioPath}
                >
                  <Music size={10} className="shrink-0" />
                  <span className="truncate font-medium">{baseName(backgroundAudioPath)}</span>
                  <button
                    className="ml-auto shrink-0 rounded px-1 text-emerald-200 hover:bg-black/25 hover:text-rose-300"
                    onClick={(e) => {
                      e.stopPropagation();
                      update({ backgroundAudioPath: null, keepOriginalAudio: true });
                    }}
                    title="Bỏ track âm nền này & bật lại tiếng gốc"
                  >
                    ×
                  </button>
                </div>
              ) : (
                <span className="absolute left-2 top-3 text-[10px] text-slate-600">
                  chưa có âm nền thay thế / đã tách nhạc
                </span>
              )}
            </div>

            {/* Selection Range Highlight */}
            {selectionRange && (
              <div
                className="pointer-events-none absolute bottom-0 top-0 z-[5] border-x border-brand/80 bg-brand/10"
                style={{
                  left: selectionRange[0] * pps,
                  width: Math.max((selectionRange[1] - selectionRange[0]) * pps, 4),
                }}
              />
            )}

            {/* Playhead */}
            <div
              className="pointer-events-none absolute bottom-0 top-0 z-10 w-px bg-playhead"
              style={{ left: playhead * pps }}
            >
              <div className="absolute -left-[4px] top-0 h-2 w-[9px] rounded-b-sm bg-playhead" />
            </div>
          </div>
        </div>
      </div>

      <footer className={`${collapsed ? "hidden" : "flex"} h-6 shrink-0 items-center gap-3 border-t border-border px-3 text-[10px] text-slate-600`}>
        <span>Kéo trên ruler để tua · click clip để chọn</span>
        <button className="ml-auto text-slate-500 hover:text-slate-300" onClick={() => player.toggle()}>
          {playing ? "tạm dừng" : "phát"}
        </button>
      </footer>
    </section>
  );
}
