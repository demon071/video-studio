import { Loader2, ScanText, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ocrFrame } from "@/lib/api";
import { formatTime } from "@/lib/format";
import { toAssetUrl } from "@/lib/media";
import { useEditor } from "@/store/editorStore";
import type { OcrFrameResult, SubtitleSegment } from "@/types/editor";

/**
 * "Đọc lại khung này" — the fix for a cue the engine got wrong.
 *
 * The extraction pass reads a whole clip and only trusts a line it saw twice;
 * when one cue still comes out wrong, the useful move is to look at *that*
 * frame again, with the same crop and the same engine. The new text never lands
 * on the timeline by itself: the dialog shows the old and the new line next to
 * each other and the user decides. Replacing keeps the cue's timings — only the
 * words change — and the dub is flagged for a re-run, because it was spoken
 * from the text that just changed.
 *
 * The still under the header is the exact crop the reader saw, so a scan
 * rectangle that misses the line is visible instead of guessed.
 */
const OCR_LANGUAGES = [
  { value: "auto", label: "Tự động" },
  { value: "zh", label: "Tiếng Trung" },
  { value: "vi", label: "Tiếng Việt" },
  { value: "en", label: "English" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
];

export function CueReadingDialog({
  segment,
  onClose,
}: {
  segment: SubtitleSegment;
  onClose: () => void;
}) {
  const videoPath = useEditor((state) => state.source?.path ?? "");
  const storeLanguage = useEditor((state) => state.sourceLanguage);
  const patchSegment = useEditor((state) => state.patchSegment);
  const pushLog = useEditor((state) => state.pushLog);
  const beginTask = useEditor((state) => state.beginTask);
  const endTask = useEditor((state) => state.endTask);

  const [ocrLanguage, setOcrLanguage] = useState(storeLanguage || "auto");
  const [reading, setReading] = useState(true);
  const [result, setResult] = useState<OcrFrameResult | null>(null);
  const [chosen, setChosen] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [still, setStill] = useState<string | null>(null);
  // A remount (React 19 StrictMode) reads again; the older answer is dropped by
  // the run id, so the spinner can never get stuck and the last read wins.
  const runId = useRef(0);

  const read = async (langOverride?: string) => {
    const current = (runId.current += 1);
    setReading(true);
    setError(null);
    beginTask("ocr-cue");
    try {
      const outcome = await ocrFrame({
        video_path: videoPath,
        start: segment.start,
        end: segment.end,
        language: langOverride ?? ocrLanguage,
      });
      if (current !== runId.current) return;
      setResult(outcome);
      setChosen(outcome.text);
      if (!outcome.text) {
        pushLog(
          "warn",
          `OCR không đọc ra chữ ở khung ${formatTime(segment.start)} — kiểm tra vùng quét trong Cài đặt.`,
        );
      }
    } catch (problem) {
      if (current !== runId.current) return;
      const reason = (problem as Error).message;
      setError(reason);
      pushLog("error", `Đọc lại khung thất bại: ${reason}`);
    } finally {
      if (current === runId.current) setReading(false);
      endTask("ocr-cue");
    }
  };

  // Open -> read at once: the dialog only exists because this frame was asked
  // for, and a second click would be a wasted one.
  useEffect(() => {
    void read();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    setStill(null);
    if (!result?.frame_path) return;
    void toAssetUrl(result.frame_path).then((url) => {
      if (!cancelled) setStill(url);
    });
    return () => {
      cancelled = true;
    };
  }, [result?.frame_path]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const replace = () => {
    const text = chosen.trim();
    if (!text) return;
    patchSegment(segment.index, { text });
    pushLog(
      "success",
      `Đã thay nội dung dòng ${segment.index} bằng kết quả OCR — nhớ dịch lại và lồng tiếng lại dòng này.`,
    );
    onClose();
  };

  const flat = (value: string) => value.replace(/\s+/g, " ").trim();
  const changed = flat(chosen) !== "" && flat(chosen) !== flat(segment.text);
  const [left, top, right, bottom] = result?.region ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={onClose}
    >
      <div
        className="flex max-h-[85vh] w-[min(30rem,100%)] flex-col overflow-hidden rounded-lg border border-border bg-surface-2 shadow-panel"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
          <ScanText size={12} className="text-brand" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-300">
            Đọc lại khung · dòng {segment.index}
          </span>
          <span className="ml-auto font-mono text-[10px] text-slate-500">
            {formatTime(segment.start)} → {formatTime(segment.end)}
          </span>
          <button className="text-slate-500 hover:text-slate-200" title="Đóng (Esc)" onClick={onClose}>
            <X size={12} />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {reading && (
            <p className="flex items-center gap-2 text-[11px] text-slate-400">
              <Loader2 size={12} className="animate-spin" />
              Đang đọc lại khung hình…
            </p>
          )}

          {error && (
            <p className="flex items-start gap-1.5 rounded bg-rose-500/10 px-2 py-1.5 text-[10px] leading-relaxed text-rose-300">
              <TriangleAlert size={11} className="mt-px shrink-0" />
              {error}
            </p>
          )}

          {result && !reading && (
            <>
              {still && (
                <img
                  src={still}
                  alt="Vùng quét vừa đọc"
                  className="max-h-32 w-full rounded border border-border bg-black object-contain"
                />
              )}

              <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[9px] text-slate-500">
                <span>
                  {result.engine === "winocr" ? "Windows Media OCR" : "RapidOCR"} ·{" "}
                  {result.device === "gpu" ? "GPU" : "CPU"}
                </span>
                <span>{result.frames.length} khung</span>
                {left !== undefined && (
                  <span>
                    quét x{left}–{right}% · y{top}–{bottom}%
                  </span>
                )}
              </div>

              <div>
                <span className="text-[10px] uppercase tracking-wide text-slate-500">Dòng hiện tại</span>
                <p className="mt-0.5 whitespace-pre-wrap rounded border border-border bg-surface-1 px-2 py-1.5 text-[11px] leading-snug text-slate-400">
                  {segment.text || "(trống)"}
                </p>
              </div>

              <div>
                <span className="text-[10px] uppercase tracking-wide text-slate-500">Đọc lại được</span>
                <textarea
                  className="field mt-0.5 min-h-[2rem] w-full resize-none !px-1.5 !py-1 !text-[11px] leading-snug"
                  rows={2}
                  value={chosen}
                  placeholder="Không đọc được chữ nào — sửa tay ở đây nếu cần"
                  onChange={(event) => setChosen(event.target.value)}
                />
              </div>

              {result.candidates.length > 1 && (
                <div className="flex flex-wrap gap-1">
                  {result.candidates.map((candidate) => (
                    <button
                      key={candidate}
                      className={`rounded border px-1.5 py-0.5 text-left text-[10px] ${
                        candidate === chosen
                          ? "border-brand bg-brand/15 text-brand"
                          : "border-border bg-surface-1 text-slate-400 hover:text-slate-200"
                      }`}
                      title="Dùng cách đọc này"
                      onClick={() => setChosen(candidate)}
                    >
                      {candidate}
                    </button>
                  ))}
                </div>
              )}

              {!result.text && (
                <p className="rounded bg-amber-500/10 px-2 py-1.5 text-[10px] leading-relaxed text-amber-300">
                  Không có chữ nào trong vùng quét. Clip có thể không in phụ đề cứng, hoặc vùng quét đang
                  lệch — chỉnh ở Cài đặt → OCR → Vùng quét rồi bấm “Đọc lại”.
                </p>
              )}

              {result.frames.length > 1 && (
                <div className="space-y-0.5 rounded bg-surface-1 px-2 py-1 font-mono text-[9px] text-slate-500">
                  {result.frames.map((frame) => (
                    <div key={frame.time} className="flex gap-2">
                      <span className="shrink-0 text-slate-600">{formatTime(frame.time)}</span>
                      <span className="truncate">{frame.text || "—"}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        <footer className="flex shrink-0 items-center gap-2 border-t border-border px-3 py-2">
          <select
            className="field !w-28 !px-1.5 !py-0.5 !text-[10px]"
            value={ocrLanguage}
            disabled={reading}
            title="Ngôn ngữ OCR dùng khi đọc lại khung này"
            onChange={(event) => {
              const nextLang = event.target.value;
              setOcrLanguage(nextLang);
              void read(nextLang);
            }}
          >
            {OCR_LANGUAGES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <button className="btn-ghost !px-2 !py-1 !text-[10px]" onClick={() => void read()} disabled={reading}>
            {reading ? <Loader2 size={10} className="animate-spin" /> : <ScanText size={10} />}
            Đọc lại
          </button>
          {result?.text && !changed && !reading && (
            <span className="text-[10px] text-slate-500">Trùng với dòng hiện tại</span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button className="btn-ghost !px-2 !py-1 !text-[10px]" onClick={onClose} disabled={reading}>
              Giữ nguyên
            </button>
            <button
              className="btn-primary !px-2 !py-1 !text-[10px]"
              onClick={replace}
              disabled={reading || !changed}
              title="Thay nội dung gốc của dòng này, giữ nguyên thời gian"
            >
              Thay thế dòng này
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
