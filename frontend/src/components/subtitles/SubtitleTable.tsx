import {
  ChevronLeft,
  ChevronsRight,
  Clock,
  History,
  Loader2,
  Merge,
  Plus,
  RotateCw,
  ScanText,
  Search,
  SlidersHorizontal,
  Split,
  Trash2,
  Volume2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { CueReadingDialog } from "@/components/subtitles/CueReadingDialog";
import { SubtitleStylePanel } from "@/components/subtitles/SubtitleStylePanel";
import { formatSrtTime, parseTimecode } from "@/lib/format";
import { listenCue, regenerateCue } from "@/lib/tasks";
import { selectActiveSegment, staleCues, useEditor } from "@/store/editorStore";
import type { DubbingSegmentReport, SubtitleSegment } from "@/types/editor";

const SPEAKER_COLORS = ["#38BDF8", "#F472B6", "#34D399", "#FBBF24", "#A78BFA", "#FB923C"];

function speakerBadgeColor(speaker: string | null | undefined): string | null {
  if (!speaker) return null;
  let hash = 0;
  for (let i = 0; i < speaker.length; i += 1) {
    hash = (hash * 31 + speaker.charCodeAt(i)) | 0;
  }
  return SPEAKER_COLORS[Math.abs(hash) % SPEAKER_COLORS.length];
}

/**
 * Subtitle editor — the column to the right of the preview.
 *
 * The seven-column table does not survive a side panel: next to the player
 * there is only room for the two texts, so every cue became a card. The header
 * row keeps the index, in/out points and the fit ratio; the original line and
 * its translation are stacked underneath, each on its own full-width field.
 *
 * The scan button on that row is the repair path: when the speech engine or the
 * OCR misread one line, it reads that cue's frame again and asks before the new
 * text replaces the old one (see `CueReadingDialog`). The header repeats the same
 * button for the cue selected on the timeline, whose row may be scrolled away.
 *
 * Each row also carries the two voice buttons: play that line's own take back,
 * and speak it again after an edit without dubbing the whole video. The header
 * holds the caption style popover, which is also where the `.srt` / `.ass`
 * files are written.
 */
export function SubtitleTable() {
  const segments = useEditor((state) => state.segments);
  const selected = useEditor((state) => state.selected);
  const dub = useEditor((state) => state.dub);
  const pending = useEditor((state) => state.pending);
  const active = useEditor(selectActiveSegment);
  const update = useEditor((state) => state.update);
  const patchSegment = useEditor((state) => state.patchSegment);
  const removeSegment = useEditor((state) => state.removeSegment);
  const restoreSegmentTiming = useEditor((state) => state.restoreSegmentTiming);
  const presentationMode = useEditor((state) => state.presentationMode);
  const activeTrack = useEditor((state) => state.activeSubtitleTrack);
  const secondarySegments = useEditor((state) => state.secondarySegments);
  const setPlayhead = useEditor((state) => state.setPlayhead);
  const dubDirty = useEditor((state) => state.dubDirty);
  const hasSource = useEditor((state) => Boolean(state.source));
  const hasPath = useEditor((state) => Boolean(state.source?.path));
  const collapsed = useEditor((state) => state.tableCollapsed);

  /** Cue whose frame is being read again; the dialog does the rest. */
  const [readingCue, setReadingCue] = useState<SubtitleSegment | null>(null);
  const [showTools, setShowTools] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [replaceQuery, setReplaceQuery] = useState("");
  const [shiftSeconds, setShiftSeconds] = useState("0.20");
  const [expandedCueIndex, setExpandedCueIndex] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const handleReplaceAll = () => {
    const q = searchQuery.trim();
    if (!q) return;
    let count = 0;
    const next = segments.map((seg) => {
      let changed = false;
      let nextText = seg.text;
      let nextTrans = seg.translation ?? "";
      if (nextText.includes(q)) {
        nextText = nextText.split(q).join(replaceQuery);
        changed = true;
      }
      if (nextTrans.includes(q)) {
        nextTrans = nextTrans.split(q).join(replaceQuery);
        changed = true;
      }
      if (changed) count += 1;
      return changed ? { ...seg, text: nextText, translation: nextTrans } : seg;
    });
    if (count > 0) {
      update({ segments: next, dubDirty: dub !== null, projectDirty: true });
      useEditor.getState().pushLog("success", `Đã thay thế "${q}" → "${replaceQuery}" trong ${count} dòng.`);
    } else {
      useEditor.getState().pushLog("warn", `Không tìm thấy "${q}" trong bảng phụ đề.`);
    }
  };

  const handleShiftTiming = (deltaSec: number) => {
    if (!Number.isFinite(deltaSec) || Math.abs(deltaSec) < 0.001 || !segments.length) return;
    const next = segments.map((seg) => {
      const len = Math.max(0.2, seg.end - seg.start);
      const ns = Math.max(0, Number((seg.start + deltaSec).toFixed(3)));
      return {
        ...seg,
        start: ns,
        end: Number((ns + len).toFixed(3)),
      };
    });
    update({ segments: next, dubDirty: dub !== null, projectDirty: true });
    useEditor
      .getState()
      .pushLog("info", `Đã dịch thời gian toàn bộ phụ đề ${deltaSec > 0 ? `+${deltaSec.toFixed(2)}s` : `${deltaSec.toFixed(2)}s`}`);
  };

  useEffect(() => {
    if (selected === null || collapsed) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-cue-index="${selected}"]`);
    row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected, collapsed]);

  const reports = useMemo(() => {
    const map = new Map<number, DubbingSegmentReport>();
    dub?.segments.forEach((report) => map.set(report.index, report));
    return map;
  }, [dub]);

  // The takes say exactly which lines are behind; recomputed only when the
  // subtitles, the dub or the spoken-language switch changes - not on playback.
  const speaksTranslation = useEditor((state) => state.useTranslationForDub);
  const stale = useMemo(
    () => staleCues(segments, dub, speaksTranslation),
    [segments, dub, speaksTranslation],
  );

  const activeIndex = active?.index ?? null;

  /** Cue picked on the timeline — the header shortcut re-reads it without scrolling to its row. */
  const selectedCue = useMemo(
    () => segments.find((item) => item.index === selected) ?? null,
    [segments, selected],
  );

  const addLine = () => {
    const state = useEditor.getState();
    const start = Number(state.playhead.toFixed(2));
    const next: SubtitleSegment = {
      index: (segments[segments.length - 1]?.index ?? 0) + 1,
      start,
      end: Math.min(start + 2, Math.max(state.source?.duration ?? start + 2, start + 0.5)),
      text: "",
      translation: "",
    };
    update({ segments: [...segments, next].sort((a, b) => a.start - b.start), selected: next.index });
  };

  const splitWordsHalf = (str: string): [string, string] => {
    const words = str.trim().split(/\s+/).filter(Boolean);
    if (words.length <= 1) return [str.trim(), ""];
    const half = Math.ceil(words.length / 2);
    return [words.slice(0, half).join(" "), words.slice(half).join(" ")];
  };

  const mergeWithNext = (index: number) => {
    const pos = segments.findIndex((s) => s.index === index);
    if (pos === -1 || pos + 1 >= segments.length) return;
    const cur = segments[pos];
    const nxt = segments[pos + 1];
    const mergedSeg: SubtitleSegment = {
      ...cur,
      end: Math.max(cur.end, nxt.end),
      text: [cur.text.trim(), nxt.text.trim()].filter(Boolean).join(" "),
      translation: [cur.translation?.trim(), nxt.translation?.trim()].filter(Boolean).join(" "),
    };
    const nextList = [
      ...segments.slice(0, pos),
      mergedSeg,
      ...segments.slice(pos + 2),
    ].map((s, idx) => ({ ...s, index: idx + 1 }));
    update({
      segments: nextList,
      selected: pos + 1,
      dubDirty: dub !== null,
      projectDirty: true,
    });
  };

  const splitSegment = (index: number) => {
    const pos = segments.findIndex((s) => s.index === index);
    if (pos === -1) return;
    const cur = segments[pos];
    if (cur.end - cur.start < 0.3) return;
    const ph = useEditor.getState().playhead;
    const cutAt =
      ph > cur.start + 0.15 && ph < cur.end - 0.15
        ? Number(ph.toFixed(2))
        : Number(((cur.start + cur.end) / 2).toFixed(2));
    const [textA, textB] = splitWordsHalf(cur.text || "");
    const [trA, trB] = splitWordsHalf(cur.translation || "");
    const first: SubtitleSegment = { ...cur, end: cutAt, text: textA, translation: trA };
    const second: SubtitleSegment = { ...cur, start: cutAt, text: textB, translation: trB };
    const nextList = [
      ...segments.slice(0, pos),
      first,
      second,
      ...segments.slice(pos + 1),
    ].map((s, idx) => ({ ...s, index: idx + 1 }));
    update({
      segments: nextList,
      selected: pos + 1,
      dubDirty: dub !== null,
      projectDirty: true,
    });
  };

  if (collapsed) {
    return (
      <aside className="flex w-8 shrink-0 flex-col border-l border-border bg-surface-1">
        <button
          className="flex h-full w-full flex-col items-center gap-2 pt-2 text-slate-500 hover:text-slate-200"
          onClick={() => update({ tableCollapsed: false })}
          title="Mở bảng phụ đề"
        >
          <ChevronLeft size={12} />
          <span className="text-[10px] font-semibold uppercase tracking-wider [writing-mode:vertical-rl]">
            Bảng phụ đề
          </span>
          <span className="font-mono text-[9px]">{segments.length}</span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="flex h-full w-full flex-col bg-surface-1">
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-border px-2.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Bảng phụ đề</span>
        <span className="font-mono text-[10px] text-slate-600">{segments.length}</span>
        {presentationMode === "bilingual" && (
          <div className="flex items-center overflow-hidden rounded border border-border text-[9px]">
            <button
              className={`px-1.5 py-0.5 ${activeTrack === 1 ? "bg-brand text-surface-0 font-bold" : "text-slate-400"}`}
              onClick={() => update({ activeSubtitleTrack: 1 })}
              title="Phụ đề chính (TS1)"
            >
              TS1 ({segments.length})
            </button>
            <button
              className={`px-1.5 py-0.5 ${activeTrack === 2 ? "bg-brand text-surface-0 font-bold" : "text-slate-400"}`}
              onClick={() => update({ activeSubtitleTrack: 2 })}
              title="Phụ đề phụ song ngữ (TS2)"
            >
              TS2 ({secondarySegments.length || segments.length})
            </button>
          </div>
        )}
        {dubDirty && (
          <span className="h-1.5 w-1.5 rounded-full bg-amber-400" title="Phụ đề đã sửa — cần lồng tiếng lại" />
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            className={`text-slate-500 hover:text-brand ${showTools ? "text-brand" : ""}`}
            title="Công cụ hàng loạt: Tìm & Thay thế · Dịch thời gian ±giây"
            onClick={() => setShowTools((v) => !v)}
          >
            <Search size={11} />
          </button>
          <button
            className="text-slate-500 hover:text-amber-300 disabled:opacity-40"
            title="Khôi phục mốc thời gian gốc ban đầu (Restore Original Timing)"
            disabled={!segments.some((s) => s.original_start != null)}
            onClick={() => restoreSegmentTiming()}
          >
            <History size={11} />
          </button>
          <SubtitleStylePanel />
          <button
            className="text-slate-500 hover:text-brand disabled:cursor-not-allowed disabled:opacity-40"
            title="Đọc lại khung hình của dòng đang chọn (OCR)"
            disabled={!hasPath || !selectedCue}
            onClick={() => {
              if (!selectedCue) return;
              setPlayhead(selectedCue.start, { seek: true });
              setReadingCue(selectedCue);
            }}
          >
            <ScanText size={11} />
          </button>
          <button className="btn-ghost !px-2 !py-0.5 !text-[10px]" onClick={addLine} disabled={!hasSource}>
            <Plus size={10} />
            Thêm dòng
          </button>
          <button
            className="text-slate-500 hover:text-slate-200"
            onClick={() => update({ tableCollapsed: true })}
            title="Thu gọn để xem video lớn hơn"
          >
            <ChevronsRight size={12} />
          </button>
        </div>
      </header>

      {showTools && (
        <div className="space-y-1.5 border-b border-border bg-surface-2 px-2.5 py-2 text-[10px]">
          <div className="flex items-center gap-1">
            <input
              className="field flex-1 !px-1.5 !py-0.5 !text-[10px]"
              value={searchQuery}
              placeholder="Tìm chữ…"
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <input
              className="field flex-1 !px-1.5 !py-0.5 !text-[10px]"
              value={replaceQuery}
              placeholder="Thay bằng…"
              onChange={(e) => setReplaceQuery(e.target.value)}
            />
            <button
              className="btn-ghost !px-2 !py-0.5 !text-[10px] text-brand"
              disabled={!searchQuery.trim() || !segments.length}
              onClick={handleReplaceAll}
            >
              Thay tất cả
            </button>
          </div>
          <div className="flex items-center gap-1">
            <Clock size={10} className="text-slate-500" />
            <span className="text-slate-400">Dịch thời gian:</span>
            <input
              type="number"
              step={0.05}
              className="field !w-16 !px-1 !py-0.5 font-mono !text-[10px]"
              value={shiftSeconds}
              onChange={(e) => setShiftSeconds(e.target.value)}
            />
            <span className="text-slate-500">giây</span>
            <button
              className="btn-ghost ml-auto !px-1.5 !py-0.5 !text-[10px]"
              disabled={!segments.length}
              onClick={() => handleShiftTiming(-Math.abs(Number(shiftSeconds) || 0.2))}
            >
              − Sớm hơn
            </button>
            <button
              className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
              disabled={!segments.length}
              onClick={() => handleShiftTiming(Math.abs(Number(shiftSeconds) || 0.2))}
            >
              + Trễ hơn
            </button>
          </div>
        </div>
      )}

      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
        {segments.length === 0 && (
          <p className="p-4 text-center text-[11px] leading-relaxed text-slate-600">
            Chưa có phụ đề. Chạy <span className="text-slate-400">Tạo phụ đề tự động</span> ở panel AI, hoặc bấm
            “Thêm dòng” để gõ tay.
          </p>
        )}

        {segments.map((segment) => {
          const report = reports.get(segment.index);
          const isActive = activeIndex === segment.index;
          const isSelected = selected === segment.index;
          const cueBusy = Boolean(pending[`cue:${segment.index}`]);
          return (
            <div
              key={segment.index}
              data-cue-index={segment.index}
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("input, textarea, button")) return;
                update({ selected: segment.index });
              }}
              onDoubleClick={() => setPlayhead(segment.start, { seek: true })}
              className={`border-b border-border/60 px-2.5 py-1.5 transition-colors ${isActive ? "bg-brand/10" : isSelected ? "bg-surface-3/60" : "hover:bg-surface-2/60"
                }`}
            >
              <div className="flex items-center gap-1">
                <button
                  className={`w-5 shrink-0 text-left font-mono text-[10px] ${isActive ? "text-brand" : "text-slate-500"
                    } hover:text-brand`}
                  title="Tua tới dòng này"
                  onClick={() => {
                    update({ selected: segment.index });
                    setPlayhead(segment.start, { seek: true });
                  }}
                >
                  {segment.index}
                </button>

                <TimeInput value={segment.start} onCommit={(value) => patchSegment(segment.index, { start: value })} />
                <span className="text-[9px] text-slate-600">→</span>
                <TimeInput value={segment.end} onCommit={(value) => patchSegment(segment.index, { end: value })} />

                <input
                  className="field !w-14 !px-1 !py-0.5 font-mono !text-[9px]"
                  style={{
                    color: speakerBadgeColor(segment.speaker) || "#38BDF8",
                    borderColor: segment.speaker ? `${speakerBadgeColor(segment.speaker)}66` : undefined,
                  }}
                  value={segment.speaker ?? ""}
                  placeholder="SPK"
                  title="Người nói (Speaker ID — tự tô màu theo nhân vật)"
                  onChange={(e) => patchSegment(segment.index, { speaker: e.target.value || null })}
                />

                <span className="ml-auto shrink-0 text-right font-mono text-[9px] leading-tight">
                  {report ? (
                    report.clamped ? (
                      <span
                        className="text-amber-300"
                        title={`TTS ${report.tts_duration.toFixed(2)}s → ${report.target_duration.toFixed(2)}s, cắt ${report.overflow_s.toFixed(2)}s`}
                      >
                        {report.applied_ratio.toFixed(2)}×
                        {report.overflow_s > 0.01 && <span className="text-rose-400"> -{report.overflow_s.toFixed(2)}s</span>}
                      </span>
                    ) : (
                      <span className="text-emerald-400" title={`TTS ${report.tts_duration.toFixed(2)}s`}>
                        {report.applied_ratio.toFixed(2)}×
                      </span>
                    )
                  ) : (
                    <span className="text-slate-600" title="Thời lượng dòng">
                      {(segment.end - segment.start).toFixed(2)}s
                    </span>
                  )}
                </span>

                <button
                  className="shrink-0 text-slate-600 hover:text-sky-300 disabled:cursor-not-allowed disabled:opacity-40"
                  title={
                    dub
                      ? "Nghe lại audio của dòng này — phát đúng bản thu, không tạo mới"
                      : "Dòng chưa có audio — sẽ đọc thử một lần (không thay đổi track lồng tiếng)"
                  }
                  disabled={cueBusy}
                  onClick={() => {
                    update({ selected: segment.index });
                    void listenCue(segment.index);
                  }}
                >
                  {cueBusy ? <Loader2 size={11} className="animate-spin" /> : <Volume2 size={11} />}
                </button>

                <button
                  className={`shrink-0 disabled:cursor-not-allowed disabled:opacity-40 ${stale.has(segment.index)
                      ? "text-amber-300 hover:text-amber-200"
                      : "text-slate-600 hover:text-emerald-300"
                    }`}
                  title={
                    !dub
                      ? "Cần lồng tiếng cả video trước khi tạo lại từng dòng"
                      : stale.has(segment.index)
                        ? "Dòng đã sửa — đọc lại để track lồng tiếng khớp với phụ đề"
                        : "Đọc lại dòng này và ghép vào track lồng tiếng (các dòng khác giữ nguyên)"
                  }
                  disabled={!dub || cueBusy}
                  onClick={() => {
                    update({ selected: segment.index });
                    void regenerateCue(segment.index);
                  }}
                >
                  <RotateCw size={11} />
                </button>

                <button
                  className="shrink-0 text-slate-600 hover:text-brand disabled:cursor-not-allowed disabled:opacity-40"
                  title="Đọc lại khung hình của dòng này bằng OCR — dùng khi dòng bị nhận dạng sai"
                  disabled={!hasPath}
                  onClick={() => {
                    update({ selected: segment.index });
                    setPlayhead(segment.start, { seek: true });
                    setReadingCue(segment);
                  }}
                >
                  <ScanText size={11} />
                </button>

                <button
                  className="shrink-0 text-slate-600 hover:text-sky-300 disabled:cursor-not-allowed disabled:opacity-40"
                  title="Tách đôi dòng này (tại vị trí playhead hoặc chính giữa)"
                  disabled={segment.end - segment.start < 0.3}
                  onClick={() => splitSegment(segment.index)}
                >
                  <Split size={11} />
                </button>

                <button
                  className="shrink-0 text-slate-600 hover:text-amber-300 disabled:cursor-not-allowed disabled:opacity-40"
                  title="Gộp dòng này với dòng kế tiếp"
                  disabled={segment.index === segments[segments.length - 1]?.index}
                  onClick={() => mergeWithNext(segment.index)}
                >
                  <Merge size={11} />
                </button>

                <button
                  className={`shrink-0 ${
                    expandedCueIndex === segment.index || segment.voice || segment.speed || segment.highlights?.length
                      ? "text-brand"
                      : "text-slate-600 hover:text-slate-300"
                  }`}
                  title="Tuỳ chỉnh nâng cao cho dòng này: Giọng đọc riêng · Tốc độ đọc riêng · Từ khóa Highlight"
                  onClick={() =>
                    setExpandedCueIndex((prev) => (prev === segment.index ? null : segment.index))
                  }
                >
                  <SlidersHorizontal size={11} />
                </button>

                <button
                  className="shrink-0 text-slate-600 hover:text-rose-400"
                  title="Xoá dòng"
                  onClick={() => removeSegment(segment.index)}
                >
                  <Trash2 size={11} />
                </button>
              </div>

              {expandedCueIndex === segment.index && (
                <div className="mt-1 grid grid-cols-3 gap-1 rounded border border-border/70 bg-surface-2 p-1.5 text-[9px]">
                  <label className="space-y-0.5">
                    <span className="text-slate-400">Giọng riêng (Voice)</span>
                    <input
                      className="field !px-1 !py-0.5 font-mono !text-[9px]"
                      value={segment.voice ?? ""}
                      placeholder="Mặc định"
                      onChange={(e) =>
                        patchSegment(segment.index, { voice: e.target.value.trim() || null })
                      }
                    />
                  </label>
                  <label className="space-y-0.5">
                    <span className="text-slate-400">Tốc độ (×)</span>
                    <input
                      type="number"
                      step={0.05}
                      min={0.5}
                      max={2.0}
                      className="field !px-1 !py-0.5 font-mono !text-[9px]"
                      value={segment.speed ?? ""}
                      placeholder="1.0"
                      onChange={(e) =>
                        patchSegment(segment.index, {
                          speed: e.target.value ? Number(e.target.value) : null,
                        })
                      }
                    />
                  </label>
                  <label className="space-y-0.5">
                    <span className="text-amber-300">Highlight (phẩy)</span>
                    <input
                      className="field !px-1 !py-0.5 !text-[9px]"
                      value={(segment.highlights ?? [])
                        .map((h) => (typeof h === "string" ? h : String(h.text ?? "")))
                        .filter(Boolean)
                        .join(", ")}
                      placeholder="từ 1, từ 2"
                      onChange={(e) => {
                        const list = e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean);
                        patchSegment(segment.index, { highlights: list.length ? list : null });
                      }}
                    />
                  </label>
                </div>
              )}

              <textarea
                className="field mt-1 min-h-[2rem] w-full resize-none !px-1.5 !py-1 !text-[11px] leading-snug"
                rows={2}
                value={segment.text}
                placeholder="Nội dung gốc…"
                onChange={(event) => patchSegment(segment.index, { text: event.target.value })}
                onFocus={() => update({ selected: segment.index })}
              />

              <textarea
                className="field mt-1 min-h-[2rem] w-full resize-none !px-1.5 !py-1 !text-[11px] leading-snug"
                rows={2}
                value={segment.translation ?? ""}
                placeholder="Bản dịch…"
                onChange={(event) => patchSegment(segment.index, { translation: event.target.value })}
                onFocus={() => update({ selected: segment.index })}
              />
            </div>
          );
        })}
      </div>

      <footer className="flex h-6 shrink-0 items-center justify-between gap-2 border-t border-border px-2.5 text-[10px] text-slate-600">
        <span className="shrink-0">
          {segments.length} dòng · {segments.reduce((total, item) => total + item.text.length, 0)} ký tự
        </span>
        <span className="truncate" title="Loa: nghe lại dòng · Mũi tên tròn: đọc lại audio (vàng = dòng đã sửa) · Quét: đọc lại khung hình">
          Loa = nghe · mũi tên vàng = dòng đã sửa, đọc lại audio · quét = đọc lại khung
        </span>
      </footer>

      {readingCue && <CueReadingDialog segment={readingCue} onClose={() => setReadingCue(null)} />}
    </aside>
  );
}

function TimeInput({ value, onCommit }: { value: number; onCommit: (seconds: number) => void }) {
  const [draft, setDraft] = useState(() => formatSrtTime(value));

  useEffect(() => {
    setDraft(formatSrtTime(value));
  }, [value]);

  return (
    <input
      className="field !w-[4.6rem] !px-1 !py-0.5 font-mono !text-[10px]"
      value={draft}
      spellCheck={false}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        const parsed = parseTimecode(draft);
        if (parsed === null) setDraft(formatSrtTime(value));
        else if (Math.abs(parsed - value) > 0.001) onCommit(parsed);
        else setDraft(formatSrtTime(value));
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
      }}
    />
  );
}
