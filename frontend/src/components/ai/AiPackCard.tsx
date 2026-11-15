import {
  Boxes,
  CheckCircle2,
  Download,
  FolderOpen,
  Loader2,
  RefreshCw,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useState } from "react";

import { refreshAiPack, refreshModels } from "@/hooks/useProgressSocket";
import { discardAiPackStaging, installAiPack, waitForJob } from "@/lib/api";
import { applyAiPack, isDesktop, reloadShell, removeAiPack, restartEngine } from "@/lib/desktop";
import { formatBytes } from "@/lib/format";
import { pickAiPackFile } from "@/lib/media";
import { useEditor } from "@/store/editorStore";

type Busy = "url" | "file" | "apply" | "remove" | "restart" | null;

/**
 * AI pack card: the installer ships the core engine, the heavy AI stack
 * (torch/CUDA, CTranslate2, llama.cpp, TTS) is a separate download. Installing
 * extracts the zip through the engine, then the Tauri shell swaps the folder and
 * restarts the sidecar - the WebView reloads onto the new port.
 */
export function AiPackCard() {
  const status = useEditor((state) => state.aiPack);
  const job = useEditor((state) => state.job);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<Busy>(null);

  useEffect(() => {
    void refreshAiPack();
  }, []);

  useEffect(() => {
    const configured = status?.download_url ?? "";
    if (configured) setUrl((current) => current || configured);
  }, [status?.download_url]);

  const installing = job?.kind === "ai-pack-install" && job.state === "running";
  const locked = installing || busy !== null;

  const log = (level: "info" | "warn" | "error", text: string) => useEditor.getState().pushLog(level, text);

  const adoptStagedPack = async (jobId: string) => {
    const done = await waitForJob(jobId);
    if (done.state !== "done") throw new Error(done.error || "Cài AI pack thất bại");
    await refreshAiPack();
    await refreshModels();
    if (!isDesktop) {
      log("warn", "Đã giải nén AI pack. Chạy app desktop (hoặc khởi động lại engine) để dùng.");
      return;
    }
    log("info", "Đang chuyển engine sang AI pack và khởi động lại…");
    await applyAiPack();
    reloadShell();
  };

  const installFrom = async (source: "url" | "file") => {
    setBusy(source);
    try {
      let body: { url: string } | { path: string };
      if (source === "url") {
        const trimmed = url.trim();
        if (!trimmed) throw new Error("Nhập URL của AI pack trước");
        body = { url: trimmed };
      } else {
        const picked = await pickAiPackFile();
        if (!picked) return;
        body = { path: picked };
      }
      log("info", source === "url" ? "Đang tải AI pack…" : "Đang giải nén AI pack từ tệp…");
      const started = await installAiPack(body);
      await adoptStagedPack(started.job.id);
    } catch (error) {
      log("error", `Cài AI pack thất bại: ${(error as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const handleRestart = async () => {
    setBusy("restart");
    try {
      await restartEngine();
      reloadShell();
    } catch (error) {
      log("error", `Khởi động lại thất bại: ${(error as Error).message}`);
      setBusy(null);
    }
  };

  const handleRemove = async () => {
    if (!window.confirm("Xoá AI pack khỏi ổ đĩa? Engine sẽ quay lại bản lõi và tải lại ứng dụng.")) return;
    setBusy("remove");
    try {
      await removeAiPack();
      reloadShell();
    } catch (error) {
      log("error", `Xoá AI pack thất bại: ${(error as Error).message}`);
      setBusy(null);
    }
  };

  const handleDiscard = async () => {
    setBusy("apply");
    try {
      await discardAiPackStaging();
      await refreshAiPack();
      log("info", "Đã dọn bản giải nén tạm");
    } catch (error) {
      log("error", `Dọn staging thất bại: ${(error as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const badge = (() => {
    if (!status) return { text: "Đang kiểm tra…", className: "text-slate-400", icon: Loader2 };
    if (status.ai_available && status.running_from_pack)
      return { text: "Đang chạy từ AI pack", className: "text-emerald-400", icon: CheckCircle2 };
    if (status.ai_available) return { text: "AI có sẵn trong bản cài", className: "text-emerald-400", icon: CheckCircle2 };
    if (status.installed) return { text: "Đã cài · cần khởi động lại", className: "text-amber-300", icon: TriangleAlert };
    return { text: "Chưa cài AI stack", className: "text-amber-300", icon: TriangleAlert };
  })();
  const BadgeIcon = badge.icon;
  const limitedDisk = status ? status.free_disk_gb > 0 && status.free_disk_gb < status.min_free_gb : false;
  const progress = installing ? Math.round(job.progress * 100) : 0;

  return (
    <section className="rounded-md border border-border bg-surface-2 px-2.5 py-2">
      <header className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-200">
          <Boxes size={11} className="text-slate-400" />
          Engine AI (AI pack)
        </div>
        <span className={`flex items-center gap-1 text-[9px] ${badge.className}`}>
          <BadgeIcon size={9} className={!status ? "animate-spin" : undefined} />
          {badge.text}
        </span>
      </header>

      <p className="mt-1 text-[10px] leading-relaxed text-slate-500">
        {status?.ai_available
          ? "Engine này đã có torch/CUDA, faster-whisper, llama.cpp và TTS — chạy hoàn toàn offline."
          : "Bản cài chỉ có engine lõi (API + FFmpeg). AI pack bổ sung torch/CUDA, faster-whisper, llama.cpp và TTS (~2.1 GB tải, ~3.5 GB trên đĩa)."}
      </p>

      {status?.installed && (
        <div className="mt-1.5 flex items-center justify-between font-mono text-[9px] text-slate-500">
          <span>đã cài {formatBytes(status.size_mb * 1024 * 1024)}</span>
          <span>{status.version ? `v${status.version}` : status.profile ?? "full"}</span>
        </div>
      )}

      {status && (
        <div className="mt-1 flex items-center justify-between text-[9px] text-slate-600">
          <span>ổ đĩa còn {status.free_disk_gb.toFixed(1)} GB</span>
          {status.engine.frozen && !status.ai_available && <span>engine lõi</span>}
        </div>
      )}

      {installing && (
        <div className="mt-2">
          <div className="h-1 overflow-hidden rounded bg-surface-4">
            <div className="h-full bg-brand transition-[width]" style={{ width: `${progress}%` }} />
          </div>
          <div className="mt-0.5 flex items-center justify-between text-[9px] text-slate-500">
            <span className="truncate">{job?.stage ? `${job.stage} · ${job.message ?? ""}` : job?.message}</span>
            <span className="font-mono">{progress}%</span>
          </div>
        </div>
      )}

      {limitedDisk && !installing && (
        <p className="mt-1.5 flex items-start gap-1 rounded bg-amber-500/10 px-1.5 py-1 text-[9px] leading-relaxed text-amber-300">
          <TriangleAlert size={9} className="mt-px shrink-0" />
          Không đủ chỗ trống: cần ~{status?.min_free_gb.toFixed(0)} GB, còn {status?.free_disk_gb.toFixed(1)} GB.
        </p>
      )}

      {!status?.ai_available && !status?.installed && (
        <div className="mt-2 space-y-1.5">
          <input
            className="field !py-1 !text-[10px]"
            placeholder="https://…/ShortVideoAI_0.1.0_ai-pack-full.zip"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            disabled={locked}
          />
          <div className="flex items-center gap-1.5">
            <button
              className="btn-primary flex-1 !px-2 !py-1 !text-[10px]"
              onClick={() => void installFrom("url")}
              disabled={locked || !url.trim()}
            >
              {busy === "url" || installing ? <Loader2 size={10} className="animate-spin" /> : <Download size={10} />}
              Tải &amp; cài
            </button>
            <button
              className="btn-ghost flex-1 !px-2 !py-1 !text-[10px]"
              onClick={() => void installFrom("file")}
              disabled={locked || !isDesktop}
              title={isDesktop ? "Chọn tệp .zip đã tải sẵn" : "Chỉ dùng được trong app desktop"}
            >
              {busy === "file" ? <Loader2 size={10} className="animate-spin" /> : <FolderOpen size={10} />}
              Từ tệp .zip
            </button>
          </div>
        </div>
      )}

      {(status?.ai_available || status?.installed) && (
        <div className="mt-2 flex items-center gap-1.5">
          <button className="btn-ghost flex-1 !px-2 !py-1 !text-[10px]" onClick={() => void handleRestart()} disabled={locked || !isDesktop}>
            {busy === "restart" ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
            Khởi động lại engine
          </button>
          {status?.installed && (
            <button className="btn-ghost !px-2 !py-1 !text-[10px]" onClick={() => void handleRemove()} disabled={locked}>
              {busy === "remove" ? <Loader2 size={10} className="animate-spin" /> : <Trash2 size={10} />}
              Xoá
            </button>
          )}
        </div>
      )}

      {status?.staging && !installing && (
        <button
          className="mt-1.5 text-[9px] text-slate-500 underline-offset-2 hover:text-slate-300 hover:underline"
          onClick={() => void handleDiscard()}
          disabled={locked}
        >
          dọn bản giải nén tạm
        </button>
      )}

      {!isDesktop && (
        <p className="mt-1.5 text-[9px] leading-relaxed text-slate-600">
          Trong trình duyệt (dev) chỉ giải nén được; việc chuyển engine do app desktop thực hiện.
        </p>
      )}
    </section>
  );
}
