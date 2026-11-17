import {
  Captions,
  Crosshair,
  Eye,
  EyeOff,
  FileWarning,
  FlipHorizontal,
  Image as ImageIcon,
  Loader2,
  Pause,
  Play,
  ScanText,
  SkipBack,
  SkipForward,
  Square,
  Sparkles,
  Trash2,
  Type,
  Volume2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ocrRegionTranslate, saveSettings } from "@/lib/api";
import { timelineDuration } from "@/lib/clips";
import { clamp, formatTime } from "@/lib/format";
import { pickImage, toAssetUrl } from "@/lib/media";
import { player } from "@/lib/player";
import {
  DEFAULT_REGION,
  MIN_REGION_SPAN,
  formatRegion,
  regionFrom,
  regionLabel,
  type Region,
} from "@/lib/region";
import { runFastPreview } from "@/lib/tasks";
import { selectActiveSegment, useEditor } from "@/store/editorStore";
import type { LayerKind } from "@/types/editor";

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
  const videoClips = useEditor((state) => state.videoClips);
  const aspect = useEditor((state) => state.aspect);
  const playhead = useEditor((state) => state.playhead);
  const playing = useEditor((state) => state.playing);
  const setPlaying = useEditor((state) => state.setPlaying);
  const setPlayhead = useEditor((state) => state.setPlayhead);
  const previewSubtitles = useEditor((state) => state.previewSubtitles);
  const subtitleStyle = useEditor((state) => state.subtitleStyle);
  const secondaryStyle = useEditor((state) => state.secondaryStyle);
  const presentationMode = useEditor((state) => state.presentationMode);
  const secondarySegments = useEditor((state) => state.secondarySegments);
  const layers = useEditor((state) => state.layers);
  const selectedLayerId = useEditor((state) => state.selectedLayerId);
  const addLayer = useEditor((state) => state.addLayer);
  const patchLayer = useEditor((state) => state.patchLayer);
  const removeLayer = useEditor((state) => state.removeLayer);
  const flipHorizontal = useEditor((state) => state.flipHorizontal);
  const pending = useEditor((state) => state.pending);
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
  const [drawLayerId, setDrawLayerId] = useState<string | null>(null);
  const [logoUrls, setLogoUrls] = useState<Record<string, string>>({});
  const [layerDrag, setLayerDrag] = useState<{
    id: string;
    mode: "move" | "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "draw";
    startX: number;
    startY: number;
    orig: { x: number; y: number; w: number; h: number };
  } | null>(null);
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
    player.setClipsProvider(() => useEditor.getState().videoClips);
    player.attach(videoRef.current);
    return () => {
      player.attach(null);
      player.setClipsProvider(null);
    };
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

  const duration = videoClips.length ? timelineDuration(videoClips) : (source?.duration ?? 0);
  const text = (segment?.translation || segment?.text || "").trim();
  const caption =
    previewSubtitles && presentationMode !== "none" && text ? text : null;
  const secondaryText =
    previewSubtitles && presentationMode === "bilingual"
      ? (
          secondarySegments.find((s) => playhead >= s.start && playhead < s.end)?.text ||
          segment?.text ||
          ""
        ).trim()
      : "";
  const activeLayers = layers.filter(
    (l) => l.end <= l.start || (playhead >= l.start && playhead <= l.end),
  );
  const selectedLayer = layers.find((l) => l.id === selectedLayerId) ?? null;

  useEffect(() => {
    let alive = true;
    const logoLayers = layers.filter((l) => l.kind === "logo" && l.image_path);
    for (const l of logoLayers) {
      if (!l.image_path || logoUrls[l.image_path]) continue;
      void toAssetUrl(l.image_path).then((url) => {
        if (alive && l.image_path) {
          setLogoUrls((prev) => ({ ...prev, [l.image_path!]: url }));
        }
      });
    }
    return () => {
      alive = false;
    };
  }, [layers, logoUrls]);

  const handleAddVisualLayer = async (kind: LayerKind) => {
    let imagePath: string | null = null;
    if (kind === "logo") {
      imagePath = await pickImage();
    }
    const d = Math.max(duration, 5);
    const id = `layer_${Date.now()}`;
    addLayer({
      id,
      kind,
      label:
        kind === "mask"
          ? "Khung che"
          : kind === "blur"
            ? "Làm mờ"
            : kind === "logo"
              ? "Logo"
              : "Chữ nổi",
      start: 0,
      end: d,
      x: kind === "logo" ? 0.72 : 0.1,
      y: kind === "text" ? 0.12 : kind === "logo" ? 0.08 : 0.72,
      w: kind === "logo" ? 0.2 : 0.8,
      h: kind === "logo" ? 0.14 : 0.14,
      color: kind === "text" ? "#FFE082" : "#000000",
      opacity: kind === "mask" ? 0.85 : 1.0,
      text: kind === "text" ? "Tiêu đề / Ghi chú" : "",
      font_size: 32,
      image_path: imagePath,
    });
  };

  const layerPoint = (event: React.PointerEvent<HTMLElement>) => {
    const rect = frameRef.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return { x: 0, y: 0 };
    return {
      x: clamp((event.clientX - rect.left) / rect.width, 0, 1),
      y: clamp((event.clientY - rect.top) / rect.height, 0, 1),
    };
  };

  const applyLayerDrag = (event: React.PointerEvent<HTMLElement>) => {
    if (!layerDrag) return;
    const pt = layerPoint(event);
    const dx = pt.x - layerDrag.startX;
    const dy = pt.y - layerDrag.startY;
    const { x, y, w, h } = layerDrag.orig;
    const minSize = 0.04;

    if (layerDrag.mode === "draw") {
      const nx = Math.min(layerDrag.startX, pt.x);
      const ny = Math.min(layerDrag.startY, pt.y);
      const nw = Math.max(minSize, Math.abs(pt.x - layerDrag.startX));
      const nh = Math.max(minSize, Math.abs(pt.y - layerDrag.startY));
      patchLayer(layerDrag.id, {
        x: Number(nx.toFixed(4)),
        y: Number(ny.toFixed(4)),
        w: Number(Math.min(1 - nx, nw).toFixed(4)),
        h: Number(Math.min(1 - ny, nh).toFixed(4)),
      });
      return;
    }

    if (layerDrag.mode === "move") {
      const nx = clamp(x + dx, 0, 1 - w);
      const ny = clamp(y + dy, 0, 1 - h);
      patchLayer(layerDrag.id, { x: Number(nx.toFixed(4)), y: Number(ny.toFixed(4)) });
      return;
    }

    let nx = x;
    let ny = y;
    let nw = w;
    let nh = h;
    if (layerDrag.mode.includes("w")) {
      const maxX = x + w - minSize;
      nx = clamp(x + dx, 0, maxX);
      nw = x + w - nx;
    }
    if (layerDrag.mode.includes("e")) {
      nw = clamp(w + dx, minSize, 1 - x);
    }
    if (layerDrag.mode.includes("n")) {
      const maxY = y + h - minSize;
      ny = clamp(y + dy, 0, maxY);
      nh = y + h - ny;
    }
    if (layerDrag.mode.includes("s")) {
      nh = clamp(h + dy, minSize, 1 - y);
    }
    patchLayer(layerDrag.id, {
      x: Number(nx.toFixed(4)),
      y: Number(ny.toFixed(4)),
      w: Number(nw.toFixed(4)),
      h: Number(nh.toFixed(4)),
    });
  };

  const handleOcrTranslateNow = async () => {
    if (!source?.path) return;
    try {
      pushLog("info", "Đang quét & dịch nhanh vùng OCR tại khung hình hiện tại…");
      const res = await ocrRegionTranslate({
        video_path: source.path,
        timestamp: playhead,
        region: shownRegion,
        source_language: useEditor.getState().sourceLanguage,
        target_language: useEditor.getState().targetLanguage,
        translate_provider: useEditor.getState().translateProvider,
      });
      pushLog(
        "success",
        `OCR Dịch vùng [${formatTime(res.timestamp)}]: "${res.text}" → "${res.translation}"`,
      );
    } catch (err) {
      pushLog("error", `OCR Dịch vùng thất bại: ${(err as Error).message}`);
    }
  };

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
              className={`h-full w-full object-contain ${flipHorizontal ? "-scale-x-100" : ""}`}
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

            {/* Visual Layers (Mask, Blur, Logo, Text) — kéo thả & chỉnh kích thước trực tiếp */}
            {activeLayers.map((layer) => {
              const isSelected = selectedLayerId === layer.id;
              const handles: Array<{
                mode: "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
                cls: string;
              }> = [
                { mode: "nw", cls: "-left-1 -top-1 cursor-nwse-resize" },
                { mode: "n", cls: "left-1/2 -top-1 -translate-x-1/2 cursor-ns-resize" },
                { mode: "ne", cls: "-right-1 -top-1 cursor-nesw-resize" },
                { mode: "e", cls: "-right-1 top-1/2 -translate-y-1/2 cursor-ew-resize" },
                { mode: "se", cls: "-bottom-1 -right-1 cursor-nwse-resize" },
                { mode: "s", cls: "-bottom-1 left-1/2 -translate-x-1/2 cursor-ns-resize" },
                { mode: "sw", cls: "-bottom-1 -left-1 cursor-nesw-resize" },
                { mode: "w", cls: "-left-1 top-1/2 -translate-y-1/2 cursor-ew-resize" },
              ];
              return (
                <div
                  key={layer.id}
                  onPointerDown={(e) => {
                    e.stopPropagation();
                    update({ selectedLayerId: layer.id });
                    const pt = layerPoint(e);
                    e.currentTarget.setPointerCapture(e.pointerId);
                    setLayerDrag({
                      id: layer.id,
                      mode: "move",
                      startX: pt.x,
                      startY: pt.y,
                      orig: { x: layer.x, y: layer.y, w: layer.w, h: layer.h },
                    });
                  }}
                  onPointerMove={(e) => {
                    if (layerDrag?.id === layer.id) {
                      e.stopPropagation();
                      applyLayerDrag(e);
                    }
                  }}
                  onPointerUp={(e) => {
                    if (layerDrag?.id === layer.id) {
                      e.stopPropagation();
                      setLayerDrag(null);
                    }
                  }}
                  onPointerCancel={() => setLayerDrag(null)}
                  className={`absolute select-none rounded touch-none ${
                    layerDrag?.id === layer.id ? "cursor-grabbing" : "cursor-grab"
                  } ${isSelected ? "z-10 ring-2 ring-brand" : ""}`}
                  style={{
                    left: `${layer.x * 100}%`,
                    top: `${layer.y * 100}%`,
                    width: `${layer.w * 100}%`,
                    height: `${layer.h * 100}%`,
                    ...(layer.kind === "mask"
                      ? { backgroundColor: rgba(layer.color || "#000000", layer.opacity) }
                      : layer.kind === "blur"
                        ? {
                            backdropFilter: "blur(10px)",
                            backgroundColor: "rgba(255,255,255,0.08)",
                            border: "1px dashed rgba(255,255,255,0.45)",
                          }
                        : layer.kind === "logo"
                          ? {
                              border: isSelected ? undefined : "1px dashed rgba(52,211,153,0.5)",
                            }
                          : {}),
                  }}
                >
                  {layer.kind === "text" && (
                    <span
                      className="pointer-events-none block w-full text-center font-bold"
                      style={{
                        color: layer.color || "#FFE082",
                        fontSize: "clamp(11px, 3.8cqh, 28px)",
                        textShadow: "0 2px 6px rgba(0,0,0,0.9)",
                      }}
                    >
                      {layer.text}
                    </span>
                  )}
                  {layer.kind === "logo" && (
                    layer.image_path && logoUrls[layer.image_path] ? (
                      <img
                        src={logoUrls[layer.image_path]}
                        alt={layer.label}
                        className="pointer-events-none h-full w-full object-contain"
                        draggable={false}
                      />
                    ) : (
                      <div className="pointer-events-none flex h-full w-full items-center justify-center bg-emerald-500/15 text-[10px] font-medium text-emerald-200">
                        Logo (chưa chọn ảnh)
                      </div>
                    )
                  )}
                  {isSelected &&
                    handles.map((handle) => (
                      <div
                        key={handle.mode}
                        onPointerDown={(e) => {
                          e.stopPropagation();
                          const pt = layerPoint(e);
                          e.currentTarget.setPointerCapture(e.pointerId);
                          setLayerDrag({
                            id: layer.id,
                            mode: handle.mode,
                            startX: pt.x,
                            startY: pt.y,
                            orig: { x: layer.x, y: layer.y, w: layer.w, h: layer.h },
                          });
                        }}
                        onPointerMove={(e) => {
                          if (layerDrag?.id === layer.id) {
                            e.stopPropagation();
                            applyLayerDrag(e);
                          }
                        }}
                        onPointerUp={(e) => {
                          if (layerDrag?.id === layer.id) {
                            e.stopPropagation();
                            setLayerDrag(null);
                          }
                        }}
                        onPointerCancel={() => setLayerDrag(null)}
                        className={`absolute h-2.5 w-2.5 rounded-sm border border-surface-0 bg-brand shadow ${handle.cls}`}
                      />
                    ))}
                </div>
              );
            })}

            {/* Draw-to-place overlay for a visual layer (like OCR region selection) */}
            {drawLayerId && !regionMode && (
              <div
                className="absolute inset-0 z-20 cursor-crosshair bg-black/25 touch-none"
                onPointerDown={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  const pt = layerPoint(e);
                  e.currentTarget.setPointerCapture(e.pointerId);
                  setLayerDrag({
                    id: drawLayerId,
                    mode: "draw",
                    startX: pt.x,
                    startY: pt.y,
                    orig: { x: pt.x, y: pt.y, w: 0.04, h: 0.04 },
                  });
                }}
                onPointerMove={(e) => {
                  if (layerDrag?.id === drawLayerId && layerDrag.mode === "draw") {
                    e.stopPropagation();
                    applyLayerDrag(e);
                  }
                }}
                onPointerUp={() => {
                  if (layerDrag?.id === drawLayerId) {
                    setLayerDrag(null);
                    setDrawLayerId(null);
                  }
                }}
                onPointerCancel={() => {
                  setLayerDrag(null);
                  setDrawLayerId(null);
                }}
              >
                <span className="pointer-events-none absolute inset-x-0 top-2 mx-auto w-fit rounded bg-black/75 px-2.5 py-1 text-[10px] text-slate-200">
                  Kéo chuột trên video để vẽ vùng cho lớp phủ
                </span>
              </div>
            )}

            {secondaryText && (
              <div
                className="pointer-events-none absolute inset-x-0 flex justify-center"
                style={{
                  paddingLeft: `${secondaryStyle.margin_h}%`,
                  paddingRight: `${secondaryStyle.margin_h}%`,
                  bottom: `${secondaryStyle.margin_v}%`,
                }}
              >
                <span
                  className="max-w-full rounded px-1.5 py-0.5 leading-snug"
                  style={{
                    fontFamily: secondaryStyle.font,
                    fontSize: `max(8px, calc(4.2cqh * ${secondaryStyle.size}))`,
                    color: secondaryStyle.color,
                    backgroundColor: "rgba(0,0,0,0.5)",
                  }}
                >
                  {secondaryText}
                </span>
              </div>
            )}

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
                    className="rounded px-1.5 py-0.5 text-[10px] text-amber-300 hover:bg-white/10"
                    onClick={() => void handleOcrTranslateNow()}
                    title="Quét chữ trong vùng này và dịch ngay lập tức (OCR Translator)"
                  >
                    Quét & Dịch ngay
                  </button>
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

      {/* CapCap Visual Layer & Quick Preview Toolbar */}
      {source && (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-border bg-surface-2 px-3 py-1 text-[10px]">
          <span className="font-semibold uppercase tracking-wider text-slate-500">Lớp phủ:</span>
          <button
            className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
            onClick={() => void handleAddVisualLayer("mask")}
            title="Thêm khung che phụ đề gốc (Mask Box)"
          >
            <Square size={10} />+ Khung che
          </button>
          <button
            className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
            onClick={() => void handleAddVisualLayer("blur")}
            title="Thêm vùng làm mờ phụ đề gốc / logo (Blur)"
          >
            <Sparkles size={10} />+ Làm mờ
          </button>
          <button
            className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
            onClick={() => void handleAddVisualLayer("text")}
            title="Thêm lớp chữ cố định (Text Layer)"
          >
            <Type size={10} />+ Chữ
          </button>
          <button
            className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
            onClick={() => void handleAddVisualLayer("logo")}
            title="Thêm ảnh Logo / Watermark"
          >
            <ImageIcon size={10} />+ Logo
          </button>
          <button
            className={`btn-ghost !px-1.5 !py-0.5 !text-[10px] ${regionMode ? "border-brand bg-brand/15 text-brand" : ""}`}
            onClick={() => (regionMode ? exitRegion() : enterRegion())}
            disabled={!source?.url}
            title="Chọn vùng quét phụ đề OCR ngay trên khung hình"
          >
            <ScanText size={10} />
            Vùng OCR
          </button>
          <button
            className={`btn-ghost !px-1.5 !py-0.5 !text-[10px] ${flipHorizontal ? "border-brand text-brand" : ""}`}
            onClick={() => update({ flipHorizontal: !flipHorizontal })}
            title="Lật ngang video (Horizontal Flip)"
          >
            <FlipHorizontal size={10} />
            Lật ngang
          </button>

          {selectedLayer && (
            <div className="ml-1 flex flex-wrap items-center gap-1.5 rounded border border-border bg-surface-1 px-2 py-0.5">
              <span className="font-mono text-brand">{selectedLayer.label}</span>
              <button
                className={`btn-ghost !px-1.5 !py-0 !text-[10px] ${
                  drawLayerId === selectedLayer.id ? "border-brand bg-brand/15 text-brand" : ""
                }`}
                onClick={() =>
                  setDrawLayerId((prev) => (prev === selectedLayer.id ? null : selectedLayer.id))
                }
                title="Kéo vẽ lại vùng trên video giống chọn vùng OCR"
              >
                <Crosshair size={10} />
                Kéo vẽ
              </button>
              {selectedLayer.kind === "text" && (
                <input
                  className="field !w-28 !px-1 !py-0 !text-[10px]"
                  value={selectedLayer.text}
                  onChange={(e) => patchLayer(selectedLayer.id, { text: e.target.value })}
                />
              )}
              {selectedLayer.kind === "logo" && (
                <button
                  className="btn-ghost !px-1.5 !py-0 !text-[10px] text-emerald-300"
                  onClick={async () => {
                    const p = await pickImage();
                    if (p) patchLayer(selectedLayer.id, { image_path: p });
                  }}
                >
                  Chọn ảnh
                </button>
              )}
              <label className="flex items-center gap-0.5 text-slate-400">
                X%
                <input
                  type="number"
                  min={0}
                  max={96}
                  className="field !w-11 !px-1 !py-0 font-mono !text-[10px]"
                  value={Math.round(selectedLayer.x * 100)}
                  onChange={(e) =>
                    patchLayer(selectedLayer.id, { x: clamp(Number(e.target.value) / 100, 0, 1 - selectedLayer.w) })
                  }
                />
              </label>
              <label className="flex items-center gap-0.5 text-slate-400">
                Y%
                <input
                  type="number"
                  min={0}
                  max={96}
                  className="field !w-11 !px-1 !py-0 font-mono !text-[10px]"
                  value={Math.round(selectedLayer.y * 100)}
                  onChange={(e) =>
                    patchLayer(selectedLayer.id, { y: clamp(Number(e.target.value) / 100, 0, 1 - selectedLayer.h) })
                  }
                />
              </label>
              <label className="flex items-center gap-0.5 text-slate-400">
                Rộng%
                <input
                  type="number"
                  min={4}
                  max={100}
                  className="field !w-11 !px-1 !py-0 font-mono !text-[10px]"
                  value={Math.round(selectedLayer.w * 100)}
                  onChange={(e) =>
                    patchLayer(selectedLayer.id, { w: clamp(Number(e.target.value) / 100, 0.04, 1 - selectedLayer.x) })
                  }
                />
              </label>
              <label className="flex items-center gap-0.5 text-slate-400">
                Cao%
                <input
                  type="number"
                  min={4}
                  max={100}
                  className="field !w-11 !px-1 !py-0 font-mono !text-[10px]"
                  value={Math.round(selectedLayer.h * 100)}
                  onChange={(e) =>
                    patchLayer(selectedLayer.id, { h: clamp(Number(e.target.value) / 100, 0.04, 1 - selectedLayer.y) })
                  }
                />
              </label>
              <button
                className="text-slate-400 hover:text-rose-400"
                onClick={() => removeLayer(selectedLayer.id)}
                title="Xoá lớp phủ này"
              >
                <Trash2 size={10} />
              </button>
            </div>
          )}

          <button
            className="btn-ghost ml-auto !px-2 !py-0.5 !text-[10px] text-emerald-300"
            disabled={Boolean(pending.fastPreview)}
            onClick={() => void runFastPreview()}
            title="Render nhanh 5 giây quanh vị trí playhead bằng FFmpeg (Fast Preview)"
          >
            {pending.fastPreview ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
            Xem thử 5s
          </button>
        </div>
      )}

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

        <div className="mx-1 h-1 min-w-[2rem] flex-1 overflow-hidden rounded bg-surface-4">
          <div
            className="h-full bg-brand/70"
            style={{ width: `${duration ? Math.min((playhead / duration) * 100, 100) : 0}%` }}
          />
        </div>

        <span className="hidden truncate text-[10px] text-slate-500 xl:inline">
          {segments.length} dòng · {segments.filter((item) => item.translation?.trim()).length} đã dịch
        </span>

        <button
          className={`flex shrink-0 items-center gap-1 whitespace-nowrap rounded px-1.5 py-1 text-[10px] disabled:opacity-30 ${
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
          className={`flex shrink-0 items-center gap-1 whitespace-nowrap rounded px-1.5 py-1 text-[10px] ${
            previewSubtitles ? "text-brand" : "text-slate-500"
          }`}
          onClick={() => update({ previewSubtitles: !previewSubtitles })}
          title="Bật/tắt lớp phủ phụ đề"
        >
          {previewSubtitles ? <Eye size={12} /> : <EyeOff size={12} />}
          Phụ đề
        </button>

        <button
          className={`flex shrink-0 items-center gap-1 whitespace-nowrap rounded px-1.5 py-1 text-[10px] disabled:opacity-30 ${
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
