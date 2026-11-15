import { Captions, ChevronDown, ChevronUp, Film, Maximize2, Music2, Ruler } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { clamp, formatTime } from "@/lib/format";
import { player } from "@/lib/player";
import { useEditor } from "@/store/editorStore";

const TRACKS = [
  { id: "video", label: "Video", icon: Film, accent: "bg-track-video/80 border-track-video" },
  { id: "subtitle", label: "Phụ đề", icon: Captions, accent: "bg-track-subtitle/80 border-track-subtitle" },
  { id: "dub", label: "Lồng tiếng", icon: Music2, accent: "bg-track-dubbed/80 border-track-dubbed" },
] as const;

const STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120];

export function TimelinePane() {
  const source = useEditor((state) => state.source);
  const segments = useEditor((state) => state.segments);
  const dub = useEditor((state) => state.dub);
  const playhead = useEditor((state) => state.playhead);
  const playing = useEditor((state) => state.playing);
  const zoom = useEditor((state) => state.zoom);
  const selected = useEditor((state) => state.selected);
  const follow = useEditor((state) => state.followPlayhead);
  const update = useEditor((state) => state.update);
  const setPlayhead = useEditor((state) => state.setPlayhead);
  const collapsed = useEditor((state) => state.timelineCollapsed);

  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [scrubbing, setScrubbing] = useState(false);

  const duration = Math.max(source?.duration ?? 0, 1);
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
    <section className={`flex shrink-0 flex-col border-t border-border bg-surface-1 ${collapsed ? "h-8" : "h-52"}`}>
      <header className="flex h-8 shrink-0 items-center gap-3 border-b border-border px-3">
        <button
          className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-200"
          onClick={() => update({ timelineCollapsed: !collapsed })}
          title={collapsed ? "Mở timeline" : "Thu gọn timeline để xem video lớn hơn"}
        >
          {collapsed ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          Timeline
        </button>
        <span className="font-mono text-[10px] text-slate-500">
          {formatTime(playhead)} / {formatTime(source?.duration ?? 0)}
        </span>

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

            {/* Video lane */}
            <div className="relative h-11 border-b border-border/60">
              {source ? (
                <div
                  data-clip
                  className="absolute inset-y-1 flex items-center gap-1.5 overflow-hidden rounded border bg-track-video/70 px-2"
                  style={{ left: 0, width: Math.max(duration * pps, 12) }}
                  title={source.path || source.name}
                >
                  <Film size={10} className="shrink-0 text-white/80" />
                  <span className="truncate text-[10px] font-medium text-white/90">{source.name}</span>
                  <span className="ml-auto shrink-0 font-mono text-[9px] text-white/60">
                    {source.duration.toFixed(1)}s
                  </span>
                </div>
              ) : (
                <span className="absolute left-2 top-3 text-[10px] text-slate-600">chưa có video</span>
              )}
            </div>

            {/* Subtitle lane */}
            <div className="relative h-11 border-b border-border/60">
              {segments.map((segment) => {
                const width = Math.max((segment.end - segment.start) * pps, 6);
                const isActive = playhead >= segment.start && playhead < segment.end;
                return (
                  <button
                    key={segment.index}
                    data-clip
                    className={`absolute inset-y-1.5 overflow-hidden rounded border px-1 text-left text-[9px] leading-none transition-colors ${
                      selected === segment.index
                        ? "border-brand bg-track-subtitle text-white"
                        : isActive
                          ? "border-brand/60 bg-track-subtitle/90 text-white"
                          : "border-track-subtitle bg-track-subtitle/70 text-amber-50/90 hover:bg-track-subtitle"
                    }`}
                    style={{ left: segment.start * pps, width }}
                    title={`#${segment.index} · ${segment.text || "(trống)"}`}
                    onClick={() => {
                      update({ selected: segment.index });
                      setPlayhead(segment.start, { seek: true });
                    }}
                  >
                    <span className="flex h-full items-center">{segment.text || `#${segment.index}`}</span>
                  </button>
                );
              })}
              {!segments.length && (
                <span className="absolute left-2 top-3 text-[10px] text-slate-600">
                  chưa có phụ đề — chạy bước 1 ở panel AI
                </span>
              )}
            </div>

            {/* Dubbing lane */}
            <div className="relative h-11">
              {dub?.segments.map((report) => {
                const width = Math.max((report.end - report.start) * pps, 6);
                return (
                  <div
                    key={report.index}
                    data-clip
                    className={`absolute inset-y-1.5 flex items-center overflow-hidden rounded border px-1 text-[9px] text-white/90 ${
                      report.clamped
                        ? "border-rose-400/70 bg-track-dubbed/70 ring-1 ring-rose-400/40"
                        : "border-track-dubbed bg-track-dubbed/70"
                    }`}
                    style={{ left: report.start * pps, width }}
                    title={`#${report.index} · TTS ${report.tts_duration.toFixed(2)}s → ${report.target_duration.toFixed(2)}s · ${report.applied_ratio.toFixed(2)}×${
                      report.overflow_s > 0 ? ` · cắt ${report.overflow_s.toFixed(2)}s` : ""
                    }`}
                  >
                    <span className="truncate">{report.clamped ? "⚠ " : ""}{report.applied_ratio.toFixed(2)}×</span>
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
