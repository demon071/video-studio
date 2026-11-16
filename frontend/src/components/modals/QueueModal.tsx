import {
  CheckCircle2,
  Eraser,
  FilePlus2,
  ListChecks,
  Loader2,
  Play,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import {
  addQueueItems,
  cleanQueueItem,
  clearCompletedQueue,
  fetchQueue,
  removeQueueItem,
  startQueueBatch,
  stopQueueBatch,
} from "@/lib/api";
import { pickVideo } from "@/lib/media";
import { useEditor } from "@/store/editorStore";
import type { QueueSnapshot } from "@/types/editor";

export function QueueModal({ onClose }: { onClose: () => void }) {
  const state = useEditor();
  const [snap, setSnap] = useState<QueueSnapshot | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    try {
      setSnap(await fetchQueue());
    } catch {
      // ignore poll error
    }
  };

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1800);
    return () => window.clearInterval(timer);
  }, []);

  const handleAddCurrent = async () => {
    if (!state.source?.path) return;
    setLoading(true);
    try {
      setSnap(
        await addQueueItems({
          video_paths: [state.source.path],
          source_mode: state.sttProvider === "ocr" ? "ocr" : "audio",
          target_language: state.targetLanguage,
          translate_provider: state.translateProvider,
          translate_preset: state.translatePreset,
          tts_provider: state.ttsProvider,
          tts_voice: state.ttsVoice,
          aspect: state.aspect,
        }),
      );
    } finally {
      setLoading(false);
    }
  };

  const handlePickAdd = async () => {
    const path = await pickVideo();
    if (!path) return;
    setLoading(true);
    try {
      setSnap(
        await addQueueItems({
          video_paths: [path],
          source_mode: state.sttProvider === "ocr" ? "ocr" : "audio",
          target_language: state.targetLanguage,
          translate_provider: state.translateProvider,
          translate_preset: state.translatePreset,
          tts_provider: state.ttsProvider,
          tts_voice: state.ttsVoice,
          aspect: state.aspect,
        }),
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-border bg-surface-1 shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <ListChecks size={15} className="text-brand" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
              Hàng đợi xử lý hàng loạt (Performance Queue)
            </h3>
            {snap?.running && (
              <span className="flex items-center gap-1 rounded bg-brand/15 px-2 py-0.5 text-[10px] font-medium text-brand">
                <Loader2 size={10} className="animate-spin" />
                Đang chạy
              </span>
            )}
          </div>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose}>
            <X size={15} />
          </button>
        </header>

        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-2 px-4 py-2.5">
          <button className="btn-ghost" onClick={() => void handlePickAdd()} disabled={loading}>
            <FilePlus2 size={12} />
            Thêm video từ máy
          </button>
          {state.source?.path && (
            <button className="btn-ghost" onClick={() => void handleAddCurrent()} disabled={loading}>
              + Thêm video đang mở
            </button>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            {!snap?.running ? (
              <>
                <button
                  className="btn-ghost"
                  disabled={!snap?.items.length}
                  onClick={async () => setSnap(await startQueueBatch({ mode: "process_only" }))}
                  title="Tách sub -> Dịch -> Lồng tiếng (chưa xuất MP4)"
                >
                  <Play size={11} />
                  Chỉ xử lý (Chờ xuất)
                </button>
                <button
                  className="btn-primary"
                  disabled={!snap?.items.length}
                  onClick={async () => setSnap(await startQueueBatch({ mode: "process_and_export" }))}
                >
                  <Play size={11} />
                  Xử lý & Xuất MP4
                </button>
              </>
            ) : (
              <>
                <button
                  className="btn-ghost text-amber-300"
                  onClick={async () => setSnap(await stopQueueBatch(false))}
                >
                  <Square size={11} />
                  Dừng sau video hiện tại
                </button>
                <button
                  className="btn-ghost text-rose-400"
                  onClick={async () => setSnap(await stopQueueBatch(true))}
                >
                  <Square size={11} />
                  Dừng ngay
                </button>
              </>
            )}
          </div>
        </div>

        <div className="flex-1 space-y-2 overflow-y-auto p-4">
          {!snap?.items.length && (
            <p className="py-10 text-center text-xs text-slate-500">
              Hàng đợi đang trống. Bấm &quot;Thêm video từ máy&quot; để lên lịch xử lý hàng loạt.
            </p>
          )}
          {snap?.items.map((item) => (
            <div
              key={item.id}
              className="flex items-center gap-3 rounded-lg border border-border bg-surface-2 p-2.5 text-xs"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium text-slate-200">{item.title}</span>
                  <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                    {item.aspect} · {item.source_mode.toUpperCase()} · {item.translate_provider}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
                  <span>{item.stage}</span>
                  {item.error && <span className="text-rose-400">· {item.error}</span>}
                  {item.output_path && (
                    <span className="truncate font-mono text-[10px] text-emerald-400">
                      → {item.output_path}
                    </span>
                  )}
                </div>
                <div className="mt-1.5 h-1 w-full overflow-hidden rounded bg-surface-4">
                  <div
                    className={`h-full transition-all ${
                      item.status === "error"
                        ? "bg-rose-500"
                        : item.status === "completed"
                          ? "bg-emerald-400"
                          : "bg-brand"
                    }`}
                    style={{ width: `${Math.round(item.progress * 100)}%` }}
                  />
                </div>
              </div>

              <div className="flex shrink-0 items-center gap-1">
                <button
                  className="rounded p-1 text-slate-500 hover:bg-surface-3 hover:text-amber-300"
                  title="Dọn dữ liệu tạm của video này"
                  onClick={async () => setSnap(await cleanQueueItem(item.id))}
                >
                  <Eraser size={13} />
                </button>
                <button
                  className="rounded p-1 text-slate-500 hover:bg-surface-3 hover:text-rose-400"
                  title="Xoá khỏi hàng đợi"
                  onClick={async () => setSnap(await removeQueueItem(item.id))}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>

        <footer className="flex items-center justify-between border-t border-border px-4 py-2.5 text-[11px] text-slate-500">
          <span>Tổng cộng: {snap?.items.length ?? 0} video</span>
          <div className="flex items-center gap-2">
            <button
              className="btn-ghost !py-1 !text-[11px]"
              onClick={async () => setSnap(await clearCompletedQueue())}
            >
              <CheckCircle2 size={11} />
              Xoá mục đã xong
            </button>
            <button className="btn-ghost !py-1 !text-[11px]" onClick={onClose}>
              Đóng
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
