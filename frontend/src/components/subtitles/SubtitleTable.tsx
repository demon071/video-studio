import { ChevronLeft, ChevronsRight, History, Loader2, Plus, RotateCw, ScanText, Trash2, Volume2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { CueReadingDialog } from "@/components/subtitles/CueReadingDialog";
import { SubtitleStylePanel } from "@/components/subtitles/SubtitleStylePanel";
import { formatSrtTime, parseTimecode } from "@/lib/format";
import { listenCue, regenerateCue } from "@/lib/tasks";
import { selectActiveSegment, staleCues, useEditor } from "@/store/editorStore";
import type { DubbingSegmentReport, SubtitleSegment } from "@/types/editor";

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

      <div className="min-h-0 flex-1 overflow-y-auto">
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
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("input, textarea, button")) return;
                update({ selected: segment.index });
              }}
              onDoubleClick={() => setPlayhead(segment.start, { seek: true })}
              className={`border-b border-border/60 px-2.5 py-1.5 transition-colors ${
                isActive ? "bg-brand/10" : isSelected ? "bg-surface-3/60" : "hover:bg-surface-2/60"
              }`}
            >
              <div className="flex items-center gap-1">
                <button
                  className={`w-5 shrink-0 text-left font-mono text-[10px] ${
                    isActive ? "text-brand" : "text-slate-500"
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
                  className="field !w-14 !px-1 !py-0.5 font-mono !text-[9px] text-brand"
                  value={segment.speaker ?? ""}
                  placeholder="SPK"
                  title="Người nói (Speaker ID)"
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
                  className={`shrink-0 disabled:cursor-not-allowed disabled:opacity-40 ${
                    stale.has(segment.index)
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
                  className="shrink-0 text-slate-600 hover:text-rose-400"
                  title="Xoá dòng"
                  onClick={() => removeSegment(segment.index)}
                >
                  <Trash2 size={11} />
                </button>
              </div>

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
