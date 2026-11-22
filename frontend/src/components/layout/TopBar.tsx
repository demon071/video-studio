import { ChevronDown, Cpu, FilePlus2, FolderArchive, FolderOpen, ListChecks, Loader2, Music, Play, Save, Upload, Wrench, X } from "lucide-react";
import { useState } from "react";

import { MediaToolsModal } from "@/components/modals/MediaToolsModal";
import { QueueModal } from "@/components/modals/QueueModal";
import { ProjectMenu } from "@/components/project/ProjectMenu";
import { formatPercent } from "@/lib/format";
import { pickVideo } from "@/lib/media";
import { cancelJob, openSystemPath } from "@/lib/api";
import { saveCurrentProject } from "@/lib/project";
import { openVideo, runExport, runExportAudio, runExportResourcePackage } from "@/lib/tasks";
import { useEditor } from "@/store/editorStore";
import type { Aspect, QualityProfile } from "@/types/editor";

const ASPECTS: Array<{ value: Aspect; label: string }> = [
  { value: "9:16", label: "9:16" },
  { value: "16:9", label: "16:9" },
  { value: "1:1", label: "1:1" },
];

export function TopBar() {
  const source = useEditor((state) => state.source);
  const aspect = useEditor((state) => state.aspect);
  const update = useEditor((state) => state.update);
  const job = useEditor((state) => state.job);
  const pending = useEditor((state) => state.pending);
  const hardware = useEditor((state) => state.hardware);
  const socket = useEditor((state) => state.socket);
  const hasDub = useEditor((state) => state.dub !== null);
  const projectName = useEditor((state) => state.projectName);
  const projectPath = useEditor((state) => state.projectPath);
  const projectDirty = useEditor((state) => state.projectDirty);
  const activeQueueItemId = useEditor((state) => state.activeQueueItemId);
  const segmentCount = useEditor((state) => state.segments.length);
  const qualityProfile = useEditor((state) => state.qualityProfile);
  const useNvenc = useEditor((state) => state.useNvenc);
  const lastExport = useEditor((state) => state.lastExport);
  const pushLog = useEditor((state) => state.pushLog);

  const [showQueue, setShowQueue] = useState(false);
  const [showMediaTools, setShowMediaTools] = useState(false);
  const [showExportMenu, setShowExportMenu] = useState(false);

  const busy = Object.keys(pending).length > 0 || job?.state === "running";
  const importing = Boolean(pending.import);
  const saving = Boolean(pending.project);

  const handleImport = async () => {
    const path = await pickVideo();
    if (path) await openVideo(path);
  };

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-surface-1 px-3">
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded bg-brand/15 font-mono text-[11px] font-bold text-brand">
          S
        </span>
        <span className="text-sm font-semibold tracking-tight text-slate-100">ShortVideoAI</span>
      </div>

      <span className="h-5 w-px bg-border" />

      <button className="btn-ghost" onClick={() => void handleImport()} disabled={importing}>
        {importing ? <Loader2 size={12} className="animate-spin" /> : <FilePlus2 size={12} />}
        Nhập video
      </button>

      <ProjectMenu />

      <button
        className="btn-ghost"
        onClick={() => void saveCurrentProject()}
        disabled={saving || (!source && !segmentCount)}
        title="Lưu dự án (Ctrl+S) · Ctrl+Shift+S để lưu thành tệp mới"
      >
        {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
        Lưu
        {projectDirty && (projectPath || activeQueueItemId) && (
          <span className="ml-0.5 h-1.5 w-1.5 rounded-full bg-amber-400" title="Có thay đổi chưa lưu" />
        )}
      </button>

      <button
        className="btn-ghost"
        onClick={() => setShowQueue(true)}
        title="Hàng đợi xử lý hàng loạt (Performance Queue)"
      >
        <ListChecks size={12} />
        Hàng đợi
      </button>

      <button
        className="btn-ghost"
        onClick={() => setShowMediaTools(true)}
        title="Công cụ Media FFmpeg: Cắt, Ghép, Tách âm thanh, Lồng nhạc"
      >
        <Wrench size={12} />
        Công cụ Media
      </button>

      <div className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-[11px] text-slate-500">
        <span className="truncate font-mono text-slate-400" title={projectPath ?? undefined}>
          {projectName || source?.name || "Chưa có video nào"}
        </span>
        {projectName && source && <span className="truncate text-[10px] text-slate-600">· {source.name}</span>}
      </div>

      <div className="flex items-center overflow-hidden rounded-md border border-border">
        {ASPECTS.map((option) => (
          <button
            key={option.value}
            onClick={() => update({ aspect: option.value })}
            className={`px-2.5 py-1 text-[11px] transition-colors ${
              aspect === option.value
                ? "bg-brand text-surface-0 font-semibold"
                : "bg-surface-3 text-slate-300 hover:bg-surface-4"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-2 py-1 text-[11px]">
        <Cpu size={11} className={hardware?.device === "cuda" ? "text-emerald-400" : "text-slate-400"} />
        <span className="font-mono text-slate-300">
          {hardware ? `${hardware.device.toUpperCase()} · ${hardware.profile.whisper_model}` : socket === "open" ? "…" : "offline"}
        </span>
      </div>

      {job && (job.state === "running" || job.state === "queued") ? (
        <div className="flex min-w-[180px] items-center gap-2 rounded-md border border-brand/40 bg-brand/10 px-2 py-1">
          <Loader2 size={11} className="shrink-0 animate-spin text-brand" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[10px] text-slate-300">
              {job.kind || "job"} · {job.stage || "…"}
            </div>
            <div className="mt-0.5 h-0.5 overflow-hidden rounded bg-surface-4">
              <div className="h-full bg-brand transition-[width]" style={{ width: formatPercent(job.progress) }} />
            </div>
          </div>
          <span className="font-mono text-[10px] text-brand">{formatPercent(job.progress)}</span>
          <button
            className="rounded p-0.5 text-slate-400 hover:text-rose-300"
            title="Huỷ tác vụ"
            onClick={() => void cancelJob(job.id).catch(() => undefined)}
          >
            <X size={11} />
          </button>
        </div>
      ) : null}

      <div className="relative flex items-center">
        <button className="btn-primary rounded-r-none" onClick={() => void runExport()} disabled={!source || busy}>
          <Upload size={12} />
          Xuất video
        </button>
        <button
          className="btn-primary rounded-l-none border-l border-black/20 !px-1.5"
          onClick={() => setShowExportMenu((v) => !v)}
          title="Tuỳ chọn chất lượng xuất & Xuất âm thanh rời (Voice / BGM / Mix)"
        >
          <ChevronDown size={11} />
        </button>

        {showExportMenu && (
          <div className="absolute right-0 top-full z-40 mt-1 w-56 space-y-2 rounded-lg border border-border bg-surface-2 p-2.5 text-xs shadow-panel">
            <div className="space-y-1">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                Chất lượng xuất MP4
              </span>
              <select
                className="field w-full !py-1 !text-[11px]"
                value={qualityProfile}
                onChange={(e) => update({ qualityProfile: e.target.value as QualityProfile })}
              >
                <option value="high">Cao (CRF 20 · Chuẩn)</option>
                <option value="balanced">Cân bằng (CRF 22 · Nhanh)</option>
                <option value="low">Nhẹ / Xem nhanh (CRF 26)</option>
              </select>
              <label className="flex items-center gap-1.5 pt-0.5 text-[11px] text-slate-400">
                <input
                  type="checkbox"
                  checked={useNvenc}
                  onChange={(e) => update({ useNvenc: e.target.checked })}
                />
                Tăng tốc phần cứng NVENC (GPU)
              </label>
            </div>

            <div className="space-y-1 border-t border-border pt-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                Xuất âm thanh độc lập (.WAV)
              </span>
              <button
                className="btn-ghost w-full justify-start !py-1 !text-[11px]"
                disabled={!hasDub || busy}
                onClick={() => {
                  setShowExportMenu(false);
                  void runExportAudio("voice");
                }}
              >
                <Music size={11} />
                Xuất riêng Giọng đọc (Voice)
              </button>
              <button
                className="btn-ghost w-full justify-start !py-1 !text-[11px]"
                disabled={!source || busy}
                onClick={() => {
                  setShowExportMenu(false);
                  void runExportAudio("bgm");
                }}
              >
                <Music size={11} />
                Xuất riêng Nhạc nền (BGM)
              </button>
              <button
                className="btn-ghost w-full justify-start !py-1 !text-[11px]"
                disabled={(!source && !hasDub) || busy}
                onClick={() => {
                  setShowExportMenu(false);
                  void runExportAudio("mix");
                }}
              >
                <Music size={11} />
                Xuất bản trộn (Voice + BGM Mix)
              </button>
            </div>

            <div className="space-y-1 border-t border-border pt-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-amber-300">
                Gói tài nguyên CapCap (.ZIP)
              </span>
              <button
                className="btn-ghost w-full justify-start !py-1 !text-[11px] text-amber-200"
                disabled={(!segmentCount && !hasDub) || busy}
                onClick={() => {
                  setShowExportMenu(false);
                  void runExportResourcePackage();
                }}
                title="Đóng gói đầy đủ file .SRT gốc/dịch, .ASS, từng câu TTS .WAV rời + bảng index.csv và track giọng"
              >
                <FolderArchive size={11} />
                Xuất gói tài nguyên (SRT + ASS + TTS rời)
              </button>
            </div>
          </div>
        )}
      </div>

      {hasDub && (
        <span className="rounded bg-track-dubbed/20 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
          có lồng tiếng
        </span>
      )}

      {lastExport && !(job && (job.state === "running" || job.state === "queued")) && (
        <div
          className="flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[11px]"
          title={lastExport.output_path}
        >
          <span className="max-w-[140px] truncate font-mono text-[10px] text-emerald-300">
            Đã xuất: {lastExport.output_path.split(/[\\/]/).pop()}
          </span>
          <button
            className="btn-ghost !px-1.5 !py-0.5 !text-[10px] text-emerald-200 hover:text-white"
            onClick={() => void openSystemPath(lastExport.output_path, "open").catch((e) => pushLog("error", (e as Error).message))}
            title="Mở video vừa xuất bằng trình phát mặc định"
          >
            <Play size={10} />
            Xem video
          </button>
          <button
            className="btn-ghost !px-1.5 !py-0.5 !text-[10px] text-emerald-200 hover:text-white"
            onClick={() => void openSystemPath(lastExport.output_path, "reveal").catch((e) => pushLog("error", (e as Error).message))}
            title="Mở thư mục chứa video vừa xuất"
          >
            <FolderOpen size={10} />
            Mở thư mục
          </button>
          <button
            className="rounded p-0.5 text-emerald-300/70 hover:text-white"
            onClick={() => update({ lastExport: null })}
            title="Đóng"
          >
            <X size={10} />
          </button>
        </div>
      )}

      {showQueue && <QueueModal onClose={() => setShowQueue(false)} />}
      {showMediaTools && <MediaToolsModal onClose={() => setShowMediaTools(false)} />}
    </header>
  );
}
