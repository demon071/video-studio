import { CircleCheck, FolderOpen, FolderSearch, Loader2, PackagePlus, RotateCcw, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { fetchCapCutTargets } from "@/lib/api";
import { isDesktop, openFolder, pickDirectory } from "@/lib/media";
import { runExportCapCut } from "@/lib/tasks";
import { useEditor } from "@/store/editorStore";
import type { CapCutExportResult, CapCutTargets } from "@/types/editor";

/**
 * Hand the open project to CapCut Desktop.
 *
 * The engine writes a real draft folder rather than a burned-in MP4: the clip,
 * one text clip per cue and one audio clip per dubbed line, so everything stays
 * fully editable on the other side.
 */
export function CapCutPanel() {
  const source = useEditor((state) => state.source);
  const captions = useEditor((state) => state.segments.length);
  const dubLines = useEditor((state) => state.dub?.segments.length ?? 0);
  const exporting = useEditor((state) => Boolean(state.pending.capcut));
  const pushLog = useEditor((state) => state.pushLog);

  const [targets, setTargets] = useState<CapCutTargets | null>(null);
  const [probeError, setProbeError] = useState<string | null>(null);
  /** Folder the user picked; `null` keeps the detected CapCut folder. */
  const [custom, setCustom] = useState<string | null>(null);
  const [result, setResult] = useState<CapCutExportResult | null>(null);

  const detect = useCallback(async () => {
    setProbeError(null);
    try {
      setTargets(await fetchCapCutTargets());
    } catch (error) {
      setTargets(null);
      setProbeError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    void detect();
  }, [detect]);

  const chooseFolder = async () => {
    const folder = await pickDirectory("Chọn thư mục draft của CapCut");
    if (!folder) return;
    setCustom(folder);
    setResult(null);
    pushLog("info", `Draft CapCut sẽ được lưu vào ${folder}`);
  };

  const handleExport = async () => {
    const created = await runExportCapCut(custom);
    if (created) setResult(created);
  };

  const destination = custom ?? targets?.drafts_root ?? targets?.fallback_root ?? null;
  const detected = custom === null && Boolean(targets?.drafts_root);
  const blocked = !isDesktop || !source?.path;

  return (
    <section className="rounded-lg border border-border bg-surface-2">
      <div className="panel-title flex items-center justify-between gap-2 border-b border-border">
        <span>CapCut</span>
        {targets?.capcut_version && (
          <span className="font-mono text-[10px] font-normal text-slate-500">{targets.capcut_version}</span>
        )}
      </div>

      <div className="space-y-2.5 p-3">
        <p className="text-[10px] leading-relaxed text-slate-500">
          Mở dự án này trong CapCut Desktop để tinh chỉnh: video, từng dòng phụ đề và từng câu lồng tiếng nằm trên
          track riêng, không bị gộp vào hình.
        </p>

        <div className="rounded-md border border-border bg-surface-3 px-2 py-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] uppercase tracking-wide text-slate-500">
              {detected ? "Thư mục CapCut" : "Thư mục draft"}
            </span>
            {targets && (
              <span className="shrink-0 font-mono text-[10px] text-slate-500">{targets.drafts} dự án</span>
            )}
          </div>
          <div className="mt-0.5 truncate font-mono text-[10px] text-slate-300" title={destination ?? undefined}>
            {destination ?? "chưa xác định"}
          </div>
          <div className="mt-1.5 flex items-center gap-1.5">
            <button
              className="inline-flex items-center gap-1 rounded border border-border bg-surface-2 px-1.5 py-0.5 text-[10px] text-slate-300 transition-colors hover:border-border-strong hover:text-slate-100 disabled:opacity-40"
              onClick={() => void chooseFolder()}
              disabled={!isDesktop || exporting}
              title="Chọn thư mục khác để lưu draft"
            >
              <FolderSearch size={10} />
              Chọn…
            </button>
            {!detected && (
              <button
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-slate-500 transition-colors hover:text-slate-200 disabled:opacity-40"
                onClick={() => setCustom(null)}
                disabled={exporting}
                title="Dùng lại thư mục CapCut tự phát hiện"
              >
                <RotateCcw size={10} />
                Mặc định
              </button>
            )}
            {probeError && (
              <button
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-slate-500 transition-colors hover:text-slate-200"
                onClick={() => void detect()}
                title={probeError}
              >
                <RotateCcw size={10} />
                Dò lại
              </button>
            )}
          </div>
        </div>

        {targets && !targets.exists && (
          <p className="flex items-start gap-1.5 rounded bg-amber-500/10 px-2 py-1.5 text-[10px] leading-relaxed text-amber-300">
            <TriangleAlert size={11} className="mt-0.5 shrink-0" />
            Chưa thấy thư mục CapCut trên máy — draft vẫn được tạo và có thể chép tay vào CapCut.
          </p>
        )}

        {targets?.capcut_running && (
          <p className="flex items-start gap-1.5 rounded bg-brand/10 px-2 py-1.5 text-[10px] leading-relaxed text-brand">
            <TriangleAlert size={11} className="mt-0.5 shrink-0" />
            CapCut đang chạy: xuất xong hãy khởi động lại CapCut để thấy dự án mới trong danh sách.
          </p>
        )}

        <button className="btn-primary w-full" onClick={() => void handleExport()} disabled={blocked || exporting}>
          {exporting ? <Loader2 size={12} className="animate-spin" /> : <PackagePlus size={12} />}
          {exporting ? "Đang tạo draft…" : "Xuất sang CapCut"}
        </button>

        <div className="flex items-center justify-between gap-2 text-[10px] text-slate-500">
          <span>
            {captions} dòng phụ đề
            {dubLines ? ` · ${dubLines} câu lồng tiếng` : ""}
          </span>
          {blocked && <span>{isDesktop ? "mở video từ ổ đĩa trước" : "chỉ có ở bản desktop"}</span>}
        </div>

        {result && (
          <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 p-2">
            <div className="flex items-start gap-1.5">
              <CircleCheck size={12} className="mt-0.5 shrink-0 text-emerald-400" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[11px] font-medium text-slate-200" title={result.draft_path}>
                  {result.draft_name}
                </div>
                <div className="mt-0.5 text-[10px] leading-relaxed text-slate-500">
                  {result.tracks} track · {result.captions} phụ đề · {result.dub_lines} câu lồng tiếng ·{" "}
                  {result.width}×{result.height}
                </div>
                {!result.registered && (
                  <div className="mt-1 text-[10px] leading-relaxed text-amber-300">
                    Chưa thêm vào danh sách dự án của CapCut.
                  </div>
                )}
                <button
                  className="mt-1.5 inline-flex items-center gap-1 text-[10px] text-brand transition-colors hover:text-brand/80"
                  onClick={() => void openFolder(result.draft_path)}
                >
                  <FolderOpen size={10} />
                  Mở thư mục draft
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
