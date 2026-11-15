import {
  Captions,
  Eye,
  EyeOff,
  FileWarning,
  Pause,
  Play,
  ScanText,
  SkipBack,
  SkipForward,
  Volume2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { saveSettings } from "@/lib/api";
import { formatTime } from "@/lib/format";
import { toAssetUrl } from "@/lib/media";
import { player } from "@/lib/player";
import {
  DEFAULT_REGION,
  MIN_REGION_SPAN,
  formatRegion,
  regionFrom,
  regionLabel,
  type Region,
} from "@/lib/region";
import { selectActiveSegment, useEditor } from "@/store/editorStore";

const RATIO: Record<string, string> = {
  "9:16": "9 / 16",
  "16:9": "16 / 9",
  "1:1": "1 / 1",
};

/** Where the video pixels really land inside the padded, aspect-locked box. */
type FrameBox = { left: number; top: number; width: number; height: number };

/** `#RRGGBB` + 0..1 -> a CSS colour the caption box can use. */
function rgba(hex: string, alpha: number): string {
  const value = hex.replace("#", "");
  const red = Number.parseInt(value.slice(0, 2), 16);
  const green = Number.parseInt(value.slice(2, 4), 16);
  const blue = Number.parseInt(value.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

export function PreviewPane() {
  const source = useEditor((state) => state.source);
  const aspect = useEditor((state) => state.aspect);
  const playhead = useEditor((state) => state.playhead);
  const playing = useEditor((state) => state.playing);
  const setPlaying = useEditor((state) => state.setPlaying);
  const setPlayhead = useEditor((state) => state.setPlayhead);
  const previewSubtitles = useEditor((state) => state.previewSubtitles);
  const subtitleStyle = useEditor((state) => state.subtitleStyle);
  const previewDub = useEditor((state) => state.previewDub);
  const keepOriginalAudio = useEditor((state) => state.keepOriginalAudio);
  const duckGain = useEditor((state) => state.duckGain);
  const update = useEditor((state) => state.update);
  const segment = useEditor(selectActiveSegment);
  const dubPath = useEditor((state) => state.dub?.track_path);
  const segments = useEditor((state) => state.segments);
  const settings = useEditor((state) => state.settings);
  const setSettings = useEditor((state) => state.setSettings);
  const pushLog = useEditor((state) => state.pushLog);

  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const [dubUrl, setDubUrl] = useState<string | null>(null);

  // The OCR scan box, drawn on the clip itself: the schematic in Settings
  // cannot show where this clip's logo or banner actually sits.
  const [regionMode, setRegionMode] = useState(false);
  const [draft, setDraft] = useState<Region | null>(null);
  const [frameBox, setFrameBox] = useState<FrameBox | null>(null);
  const [drawing, setDrawing] = useState(false);
  const origin = useRef<{ x: number; y: number } | null>(null);
  const latest = useRef<Region | null>(null);
  const moved = useRef(false);

  const span = settings?.ocr.region_span ?? MIN_REGION_SPAN;
  const storedRegion = settings?.ocr.region ?? DEFAULT_REGION;
  const shownRegion = draft ?? storedRegion;

  useEffect(() => {
    player.attach(videoRef.current);
    return () => player.attach(null);
  }, [source?.url]);

  // Playhead follows the media clock, never the other way round.
  useEffect(() => player.subscribe((seconds) => setPlayhead(seconds)), [setPlayhead]);

  useEffect(() => {
    if (!dubPath) {
      setDubUrl(null);
      return;
    }
    let alive = true;
    void toAssetUrl(dubPath).then((url) => {
      if (alive) setDubUrl(url);
    });
    return () => {
      alive = false;
    };
  }, [dubPath]);

  // The dub plays *over* the clip, never instead of it: while a dub is
  // audible the original is turned down (or muted when the user asked to drop
  // it), so the music and room tone of the source survive the preview.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const underDub = previewDub && Boolean(dubUrl);
    video.volume = underDub ? (keepOriginalAudio ? duckGain : 0) : 1;
  }, [previewDub, dubUrl, keepOriginalAudio, duckGain, source?.url]);

  useEffect(() => {
    player.attachAudio(audioRef.current);
    return () => player.attachAudio(null);
  }, [dubUrl]);

  useEffect(() => {
    if (!playing || !previewDub || !dubUrl) {
      player.syncDub(false);
      return;
    }
    player.syncDub(true);
    const timer = window.setInterval(() => player.syncDub(true), 1000);
    return () => window.clearInterval(timer);
  }, [playing, previewDub, dubUrl]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (regionMode) {
        // Space would start the clip under the box being drawn.
        if (event.code === "Escape") {
          event.preventDefault();
          setRegionMode(false);
          setDraft(null);
          origin.current = null;
        }
        return;
      }
      if (event.code === "Space") {
        event.preventDefault();
        player.toggle();
      } else if (event.code === "ArrowLeft") {
        event.preventDefault();
        setPlayhead(Math.max(0, playhead - 1), { seek: true });
      } else if (event.code === "ArrowRight") {
        event.preventDefault();
        setPlayhead(playhead + 1, { seek: true });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playhead, setPlayhead, regionMode]);

  /**
   * The overlay must sit on the *video pixels*, not on the padded box: a 16:9
   * clip previewed inside a 9:16 project leaves letterbox bars that are not
   * part of the source frame, and percentages measured against the box would
   * be off by exactly those bars.
   */
  const measureFrame = useCallback(() => {
    const box = frameRef.current;
    if (!box) return;
    // client* is the padding box: exactly where absolute children sit.
    const outerWidth = box.clientWidth;
    const outerHeight = box.clientHeight;
    const video = videoRef.current;
    const vw = video?.videoWidth ?? 0;
    const vh = video?.videoHeight ?? 0;
    if (!vw || !vh) {
      setFrameBox({ left: 0, top: 0, width: outerWidth, height: outerHeight });
      return;
    }
    const scale = Math.min(outerWidth / vw, outerHeight / vh);
    const width = vw * scale;
    const height = vh * scale;
    setFrameBox({
      left: (outerWidth - width) / 2,
      top: (outerHeight - height) / 2,
      width,
      height,
    });
  }, []);

  useEffect(() => {
    if (!regionMode) return;
    measureFrame();
    const box = frameRef.current;
    const video = videoRef.current;
    const observer =
      box && typeof ResizeObserver !== "undefined" ? new ResizeObserver(measureFrame) : null;
    if (box && observer) observer.observe(box);
    video?.addEventListener("loadedmetadata", measureFrame);
    window.addEventListener("resize", measureFrame);
    return () => {
      observer?.disconnect();
      video?.removeEventListener("loadedmetadata", measureFrame);
      window.removeEventListener("resize", measureFrame);
    };
  }, [regionMode, measureFrame, source?.url]);

  const enterRegion = () => {
    player.pause(); // a moving frame is hard to draw on
    setDraft(null);
    setRegionMode(true);
  };

  const exitRegion = () => {
    setRegionMode(false);
    setDraft(null);
    setDrawing(false);
    origin.current = null;
    latest.current = null;
  };

  const saveRegion = async (region: Region | null) => {
    try {
      setSettings(await saveSettings({ ocr: { region: formatRegion(region) } }));
      setDraft(null);
      pushLog(
        "success",
        region ? `Vùng quét OCR: ${formatRegion(region)}` : "Vùng quét OCR: dải mặc định",
      );
    } catch (error) {
      setDraft(null); // the box on screen and the saved value must agree
      pushLog("error", `Lưu vùng quét thất bại: ${(error as Error).message}`);
    }
  };

  const point = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(100, Math.max(0, ((event.clientX - rect.left) / rect.width) * 100)),
      y: Math.min(100, Math.max(0, ((event.clientY - rect.top) / rect.height) * 100)),
    };
  };

  const duration = source?.duration ?? 0;
  const text = (segment?.translation || segment?.text || "").trim();
  const caption = previewSubtitles && text ? text : null;

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="relative flex min-h-0 flex-1 items-center justify-center bg-[radial-gradient(circle_at_50%_0%,#15191f,#0b0d10_70%)] p-3">
        {source && !source.url ? (
          // A restored project whose clip moved away: keep the frame honest and
          // tell the user how to bring it back instead of showing a black box.
          <div className="flex max-w-[24rem] flex-col items-center gap-2 text-center">
            <FileWarning size={28} className="text-amber-400" />
            <p className="text-xs font-medium text-slate-300">Không tìm thấy video gốc của dự án</p>
            {source.path && (
              <p className="break-all font-mono text-[10px] text-slate-600">{source.path}</p>
            )}
            <p className="text-[11px] leading-relaxed text-slate-500">
              Mở lại tệp bằng “Nhập video” — phụ đề và track lồng tiếng của dự án vẫn được giữ nguyên.
            </p>
          </div>
        ) : source ? (
          <div
            ref={frameRef}
            className="relative overflow-hidden rounded-lg border border-border bg-black shadow-panel"
            style={{
              aspectRatio: RATIO[aspect],
              ...(aspect === "9:16" ? { height: "100%" } : { width: "100%" }),
              maxHeight: "100%",
              maxWidth: "100%",
              // Size container: the caption and HUD scale with the *frame*, not the window.
              containerType: "size",
            }}
          >
            <video
              ref={videoRef}
              key={source.url}
              src={source.url}
              className="h-full w-full object-contain"
              playsInline
              preload="metadata"
              onClick={() => {
                if (!regionMode) player.toggle();
              }}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              // Restore the timeline position when the element (re)mounts.
              onLoadedMetadata={() => {
                measureFrame();
                player.seek(useEditor.getState().playhead);
              }}
            />

            {caption && (
              <div
                className="pointer-events-none absolute inset-x-0 flex"
                style={{
                  justifyContent:
                    subtitleStyle.horizontal === "left"
                      ? "flex-start"
                      : subtitleStyle.horizontal === "right"
                        ? "flex-end"
                        : "center",
                  paddingLeft: `${subtitleStyle.margin_h}%`,
                  paddingRight: `${subtitleStyle.margin_h}%`,
                  ...(subtitleStyle.position === "middle"
                    ? { top: "50%", transform: "translateY(-50%)" }
                    : subtitleStyle.position === "top"
                      ? { top: `${subtitleStyle.margin_v}%` }
                      : { bottom: `${subtitleStyle.margin_v}%` }),
                }}
              >
                {/* Sized in `cqh` so the overlay is the same fraction of the frame
                    the ASS style burns in (≈4.2 % of the height). */}
                <span
                  className="max-w-full leading-snug"
                  style={{
                    fontFamily: subtitleStyle.font,
                    fontWeight: subtitleStyle.bold ? 700 : 400,
                    fontSize: `max(9px, calc(4.2cqh * ${subtitleStyle.size}))`,
                    color: subtitleStyle.color,
                    textAlign: subtitleStyle.horizontal,
                    overflowWrap: "anywhere",
                    ...(subtitleStyle.outline > 0
                      ? {
                          WebkitTextStrokeWidth: `${(0.23 * subtitleStyle.outline).toFixed(2)}cqh`,
                          WebkitTextStrokeColor: subtitleStyle.outline_color,
                          paintOrder: "stroke fill",
                        }
                      : {}),
                    ...(subtitleStyle.background
                      ? {
                          backgroundColor: rgba(
                            subtitleStyle.background_color,
                            subtitleStyle.background_opacity,
                          ),
                          borderRadius: "0.24em",
                          padding: "0.12em 0.36em",
                        }
                      : { textShadow: "0 2px 6px rgba(0,0,0,0.9)" }),
                  }}
                >
                  {caption}
                </span>
              </div>
            )}

            <span
              className="pointer-events-none absolute left-1 top-1 whitespace-nowrap rounded bg-black/50 px-1 py-0.5 font-mono text-slate-300"
              style={{ fontSize: "clamp(6px, 2.1cqw, 11px)" }}
            >
              {aspect} · {source.width}×{source.height}
            </span>

            {/* The OCR crop, drawn where the text actually is. The stored
                rectangle only covers the source frame, so the overlay is
                parked on the video pixels rather than on the padded box. */}
            {regionMode && frameBox && (
              <div
                className="absolute z-20 cursor-crosshair touch-none"
                style={{
                  left: frameBox.left,
                  top: frameBox.top,
                  width: frameBox.width,
                  height: frameBox.height,
                }}
                onPointerDown={(event) => {
                  event.preventDefault();
                  event.currentTarget.setPointerCapture(event.pointerId);
                  origin.current = point(event);
                  latest.current = regionFrom(origin.current, origin.current, span);
                  moved.current = false;
                  setDraft(latest.current);
                  setDrawing(true);
                }}
                onPointerMove={(event) => {
                  if (!drawing || !origin.current) return;
                  const next = point(event);
                  moved.current =
                    moved.current ||
                    Math.abs(next.x - origin.current.x) >= 1.5 ||
                    Math.abs(next.y - origin.current.y) >= 1.5;
                  latest.current = regionFrom(origin.current, next, span);
                  setDraft(latest.current);
                }}
                onPointerUp={() => {
                  if (!drawing) return;
                  setDrawing(false);
                  origin.current = null;
                  // A stray click must not shrink the cue to a 4% dot.
                  if (!moved.current) {
                    setDraft(null);
                    return;
                  }
                  if (latest.current) void saveRegion(latest.current);
                }}
                onPointerCancel={() => {
                  setDrawing(false);
                  origin.current = null;
                }}
              >
                <div
                  className="pointer-events-none absolute border-2 border-brand bg-brand/10"
                  style={{
                    left: `${shownRegion[0]}%`,
                    top: `${shownRegion[1]}%`,
                    width: `${shownRegion[2] - shownRegion[0]}%`,
                    height: `${shownRegion[3] - shownRegion[1]}%`,
                    boxShadow: "0 0 0 9999px rgba(0,0,0,0.55)",
                  }}
                />
                <span className="pointer-events-none absolute inset-x-0 top-1.5 mx-auto w-fit max-w-[94%] rounded bg-black/70 px-2 py-1 text-center font-mono text-[10px] text-slate-200">
                  Kéo để chọn vùng quét · {regionLabel(shownRegion)} · Esc để xong
                </span>
                <div
                  className="absolute inset-x-0 bottom-2 mx-auto flex w-fit gap-1 rounded bg-black/70 px-1.5 py-1"
                  onPointerDown={(event) => event.stopPropagation()}
                >
                  <button
                    className="rounded px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-white/10 hover:text-white"
                    onClick={() => void saveRegion(null)}
                    title="Quay về dải dưới 45% khung hình"
                  >
                    Mặc định
                  </button>
                  <button
                    className="rounded bg-brand px-1.5 py-0.5 text-[10px] font-medium text-surface-0 hover:bg-brand-hover"
                    onClick={exitRegion}
                  >
                    Xong
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 text-center text-slate-500">
            <Captions size={28} />
            <p className="text-xs">
              Chưa có video — mở một clip ở panel bên trái, hoặc mở lại dự án đã lưu (Ctrl+O).
            </p>
            <p className="text-[11px] text-slate-600">Mẹo: Space để phát/dừng, ←/→ để tua 1 giây.</p>
          </div>
        )}
      </div>

      <div className="flex h-10 shrink-0 items-center gap-2 border-t border-border bg-surface-1 px-3">
        <button
          className="flex h-7 w-7 items-center justify-center rounded-full bg-brand text-surface-0 hover:bg-brand-hover disabled:opacity-40"
          onClick={() => player.toggle()}
          disabled={!source}
          title="Phát / dừng (Space)"
        >
          {playing ? <Pause size={13} /> : <Play size={13} />}
        </button>
        <button
          className="text-slate-400 hover:text-slate-200 disabled:opacity-30"
          onClick={() => setPlayhead(Math.max(0, playhead - 1), { seek: true })}
          disabled={!source}
          title="Lùi 1 giây"
        >
          <SkipBack size={13} />
        </button>
        <button
          className="text-slate-400 hover:text-slate-200 disabled:opacity-30"
          onClick={() => setPlayhead(playhead + 1, { seek: true })}
          disabled={!source}
          title="Tiến 1 giây"
        >
          <SkipForward size={13} />
        </button>

        <span className="font-mono text-[11px] text-slate-300">{formatTime(playhead)}</span>
        <span className="font-mono text-[10px] text-slate-600">/ {formatTime(duration)}</span>

        <div className="mx-2 h-1 flex-1 overflow-hidden rounded bg-surface-4">
          <div
            className="h-full bg-brand/70"
            style={{ width: `${duration ? Math.min((playhead / duration) * 100, 100) : 0}%` }}
          />
        </div>

        <span className="text-[10px] text-slate-500">
          {segments.length} dòng · {segments.filter((item) => item.translation?.trim()).length} đã dịch
        </span>

        <button
          className={`flex items-center gap-1 rounded px-1.5 py-1 text-[10px] disabled:opacity-30 ${
            regionMode ? "bg-brand/15 text-brand" : "text-slate-500 hover:text-slate-300"
          }`}
          onClick={() => (regionMode ? exitRegion() : enterRegion())}
          disabled={!source?.url}
          title="Chọn vùng quét phụ đề ngay trên khung hình — OCR chỉ đọc trong vùng này"
        >
          <ScanText size={12} />
          Vùng OCR
        </button>

        <button
          className={`flex items-center gap-1 rounded px-1.5 py-1 text-[10px] ${
            previewSubtitles ? "text-brand" : "text-slate-500"
          }`}
          onClick={() => update({ previewSubtitles: !previewSubtitles })}
          title="Bật/tắt lớp phủ phụ đề"
        >
          {previewSubtitles ? <Eye size={12} /> : <EyeOff size={12} />}
          Phụ đề
        </button>

        <button
          className={`flex items-center gap-1 rounded px-1.5 py-1 text-[10px] disabled:opacity-30 ${
            previewDub && dubUrl ? "text-emerald-400" : "text-slate-500"
          }`}
          onClick={() => update({ previewDub: !previewDub })}
          disabled={!dubUrl}
          title={`Nghe track lồng tiếng — âm gốc hạ còn ${Math.round((keepOriginalAudio ? duckGain : 0) * 100)}%`}
        >
          <Volume2 size={12} />
          Lồng tiếng
        </button>
      </div>

      <audio ref={audioRef} src={dubUrl ?? undefined} preload="auto" className="hidden" />
    </section>
  );
}
