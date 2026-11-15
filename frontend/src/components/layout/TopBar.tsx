import { Cpu, FilePlus2, Loader2, Save, Upload, X } from "lucide-react";

import { ProjectMenu } from "@/components/project/ProjectMenu";
import { formatPercent } from "@/lib/format";
import { pickVideo } from "@/lib/media";
import { cancelJob } from "@/lib/api";
import { saveCurrentProject } from "@/lib/project";
import { openVideo, runExport } from "@/lib/tasks";
import { useEditor } from "@/store/editorStore";
import type { Aspect } from "@/types/editor";

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
  const segmentCount = useEditor((state) => state.segments.length);

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
        {projectDirty && projectPath && (
          <span className="ml-0.5 h-1.5 w-1.5 rounded-full bg-amber-400" title="Có thay đổi chưa lưu" />
        )}
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

      <button className="btn-primary" onClick={() => void runExport()} disabled={!source || busy}>
        <Upload size={12} />
        Xuất video
      </button>

      {hasDub && (
        <span className="rounded bg-track-dubbed/20 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
          có lồng tiếng
        </span>
      )}
    </header>
  );
}
