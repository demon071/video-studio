import { CheckCircle2, Download, HardDrive, Loader2, Trash2, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { AiPackCard } from "@/components/ai/AiPackCard";
import { downloadModel, removeModel } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { refreshModels } from "@/hooks/useProgressSocket";
import { useEditor } from "@/store/editorStore";
import type { ModelStatus } from "@/types/editor";

const GROUPS: Array<{ kind: ModelStatus["kind"]; title: string; hint: string }> = [
  { kind: "stt", title: "Nhận dạng giọng nói", hint: "faster-whisper / SenseVoice, chạy offline" },
  { kind: "llm", title: "Dịch offline", hint: "Qwen2.5-3B / Gemma 3 4B, GGUF Q4" },
  { kind: "tts", title: "Giọng đọc offline", hint: "OmniVoice / VoiceStudio" },
  { kind: "separator", title: "Tách nhạc nền", hint: "UVR MDX-Net — dùng cho chế độ Giọng sạch" },
  { kind: "ocr", title: "Đọc phụ đề cứng (OCR)", hint: "RapidOCR — bộ dò nhanh / chính xác" },
];

export function ModelsPanel() {
  const models = useEditor((state) => state.models);
  const modelsPath = useEditor((state) => state.modelsPath);
  const job = useEditor((state) => state.job);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const handleDownload = async (key: string) => {
    setBusyKey(key);
    try {
      await downloadModel(key);
      useEditor.getState().pushLog("info", `Đang tải model ${key}…`);
    } catch (error) {
      useEditor.getState().pushLog("error", `Tải model thất bại: ${(error as Error).message}`);
    } finally {
      setBusyKey(null);
    }
  };

  const handleRemove = async (model: ModelStatus) => {
    if (!window.confirm(`Xoá ${model.label} (${formatBytes(model.installed_mb * 1024 * 1024)}) khỏi ổ đĩa?`)) return;
    try {
      await removeModel(model.key);
      await refreshModels();
      useEditor.getState().pushLog("info", `Đã xoá ${model.key}`);
    } catch (error) {
      useEditor.getState().pushLog("error", `Xoá thất bại: ${(error as Error).message}`);
    }
  };

  const totalInstalled = models.reduce((total, model) => total + model.installed_mb, 0);

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <AiPackCard />

      <div className="flex items-center justify-between rounded-md border border-border bg-surface-2 px-2.5 py-2 text-[11px]">
        <span className="flex items-center gap-1.5 text-slate-400">
          <HardDrive size={11} />
          Đã cài
        </span>
        <span className="font-mono text-slate-200">{totalInstalled.toFixed(0)} MB</span>
      </div>

      {GROUPS.map((group) => {
        const items = models.filter((model) => model.kind === group.kind);
        if (!items.length) return null;
        return (
          <section key={group.kind}>
            <div className="mb-1.5 px-1">
              <div className="text-[11px] font-semibold text-slate-200">{group.title}</div>
              <div className="text-[10px] text-slate-500">{group.hint}</div>
            </div>
            <div className="space-y-1.5">
              {items.map((model) => {
                const downloading = job?.state === "running" && job.kind === `model-download:${model.key}`;
                const progress = downloading ? Math.round(job.progress * 100) : 0;
                return (
                  <article
                    key={model.key}
                    className="rounded-md border border-border bg-surface-2 px-2.5 py-2"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-1 text-[11px] font-medium text-slate-200">
                          {model.downloaded ? (
                            <CheckCircle2 size={10} className="shrink-0 text-emerald-400" />
                          ) : (
                            <Download size={10} className="shrink-0 text-slate-500" />
                          )}
                          <span className="truncate">{model.label}</span>
                        </div>
                        <div className="mt-0.5 line-clamp-2 text-[10px] leading-relaxed text-slate-500">
                          {model.description}
                        </div>
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="font-mono text-[10px] text-slate-400">
                          {model.downloaded ? `${model.installed_mb.toFixed(0)} MB` : `${model.size_mb} MB`}
                        </div>
                        <div className="mt-0.5 text-[9px] uppercase tracking-wide text-slate-600">
                          {model.licence}
                        </div>
                      </div>
                    </div>

                    {downloading && (
                      <div className="mt-2">
                        <div className="h-1 overflow-hidden rounded bg-surface-4">
                          <div className="h-full bg-brand transition-[width]" style={{ width: `${progress}%` }} />
                        </div>
                        <div className="mt-0.5 truncate text-[9px] text-slate-500">{job?.message}</div>
                      </div>
                    )}

                    {model.missing_packages.length > 0 && (
                      <p className="mt-1.5 flex items-start gap-1 rounded bg-amber-500/10 px-1.5 py-1 text-[9px] leading-relaxed text-amber-300">
                        <TriangleAlert size={9} className="mt-px shrink-0" />
                        thiếu gói: {model.missing_packages.join(", ")} — cài trong requirements-ai.txt
                      </p>
                    )}

                    {/* Nothing to stream (OmniVoice): the weights come from the package. */}
                    {model.pip_hint && (
                      <p className="mt-1.5 break-all rounded bg-surface-3 px-1.5 py-1 font-mono text-[9px] leading-relaxed text-slate-400">
                        {model.pip_hint}
                      </p>
                    )}

                    <div className="mt-2 flex items-center justify-end gap-1.5">
                      {model.pip_hint ? (
                        <span className="text-[9px] uppercase tracking-wide text-slate-600">đi kèm gói pip</span>
                      ) : model.downloaded ? (
                        <button
                          className="btn-ghost !px-2 !py-1 !text-[10px]"
                          onClick={() => void handleRemove(model)}
                        >
                          <Trash2 size={10} />
                          Xoá
                        </button>
                      ) : (
                        <button
                          className="btn-primary !px-2 !py-1 !text-[10px]"
                          onClick={() => void handleDownload(model.key)}
                          disabled={downloading || busyKey === model.key}
                        >
                          {downloading ? <Loader2 size={10} className="animate-spin" /> : <Download size={10} />}
                          {downloading ? "Đang tải" : "Tải về"}
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
        );
      })}

      <p className="mt-auto break-all text-[9px] leading-relaxed text-slate-600">
        Lưu tại: <span className="font-mono">{modelsPath || "%APPDATA%\\ShortVideoAI\\models"}</span>
      </p>
    </div>
  );
}
