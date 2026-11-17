import { FolderOpen, Loader2, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { deleteProject, listProjects } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { isDesktop, pickProjectFile } from "@/lib/media";
import { openProjectFrom } from "@/lib/project";
import { useEditor } from "@/store/editorStore";
import type { ProjectSummary } from "@/types/editor";

function stamp(row: ProjectSummary): string {
  const date = row.saved_at ? new Date(row.saved_at) : new Date(row.modified * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * "Mở" button: file dialog on the desktop, plus the list of saved projects.
 *
 * The list is the only way to reach a project from the browser preview (there
 * is no file dialog there) and it is the fastest way back to yesterday's work
 * in the packaged app.
 */
export function ProjectMenu() {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<ProjectSummary[]>([]);
  const [cachedRows, setCachedRows] = useState<ProjectSummary[]>([]);
  const [folder, setFolder] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const online = useEditor((state) => state.engineOnline);
  const activePath = useEditor((state) => state.projectPath);
  const projectName = useEditor((state) => state.projectName);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const listing = await listProjects();
      setRows(listing.projects);
      setCachedRows(listing.cached_projects ?? []);
      setFolder(listing.folder);
    } catch (problem) {
      setRows([]);
      setCachedRows([]);
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setLoading(false);
    }
  }, []);

  const toggle = () => {
    if (!open) void refresh();
    setOpen(!open);
  };

  // Dismiss on outside click or Escape, like every other popover.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const chooseFile = async () => {
    const path = await pickProjectFile();
    if (!path) return;
    setOpen(false);
    await openProjectFrom(path);
  };

  const openRow = async (row: ProjectSummary) => {
    setOpen(false);
    await openProjectFrom(row.path);
  };

  const removeRow = async (row: ProjectSummary) => {
    if (!window.confirm(`Xoá dự án "${row.name}" khỏi ổ đĩa?`)) return;
    try {
      await deleteProject(row.path);
      const store = useEditor.getState();
      // The open document no longer has a file: keep editing, ask where to save next time.
      if (store.projectPath === row.path) {
        store.update({ projectPath: null, projectSavedAt: null, projectDirty: true });
        store.pushLog("warn", `Đã xoá tệp dự án ${row.name} — bản đang mở vẫn còn trong trình soạn thảo.`);
      } else {
        store.pushLog("info", `Đã xoá dự án ${row.name}.`);
      }
      await refresh();
    } catch (problem) {
      useEditor.getState().pushLog("error", `Không xoá được dự án: ${(problem as Error).message}`);
    }
  };

  return (
    <div className="relative" ref={root}>
      <button className="btn-ghost" onClick={toggle} title="Mở dự án đã lưu (Ctrl+O)">
        <FolderOpen size={12} />
        Mở
      </button>

      {open && (
        <div className="absolute left-0 top-full z-40 mt-1 w-[22rem] overflow-hidden rounded-lg border border-border bg-surface-2 shadow-panel">
          {isDesktop && (
            <button
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-slate-200 hover:bg-surface-3"
              onClick={() => void chooseFile()}
            >
              <FolderOpen size={12} className="text-brand" />
              Mở tệp .svaproj…
            </button>
          )}

          <div className="flex items-center justify-between border-y border-border bg-surface-1 px-3 py-1.5">
            <span className="text-[10px] uppercase tracking-wide text-slate-500">
              Dự án đã lưu{projectName ? ` · đang mở: ${projectName}` : ""}
            </span>
            {loading && <Loader2 size={10} className="animate-spin text-slate-500" />}
          </div>

          <div className="max-h-80 overflow-y-auto">
            {error ? (
              <p className="px-3 py-2 text-[11px] leading-relaxed text-rose-300">{error}</p>
            ) : rows.length === 0 && cachedRows.length === 0 ? (
              <p className="px-3 py-3 text-[11px] leading-relaxed text-slate-500">
                Chưa có dự án nào. Nhấn “Lưu” (Ctrl+S) để tạo tệp đầu tiên — mọi thay đổi cũng được tự động
                giữ lại khi đóng app.
              </p>
            ) : (
              <>
                {rows.map((row) => (
                  <div
                    key={row.path}
                    className={`group flex items-center gap-2 px-2 py-1.5 ${
                      row.path === activePath ? "bg-brand/10" : "hover:bg-surface-3"
                    }`}
                  >
                    <button className="min-w-0 flex-1 text-left" onClick={() => void openRow(row)}>
                      <div className="truncate text-[11px] text-slate-200">
                        {row.name}
                        {row.damaged && <span className="ml-1 text-rose-400">(hỏng)</span>}
                      </div>
                      <div className="truncate font-mono text-[10px] text-slate-500">
                        {stamp(row)} · {formatBytes(row.size_bytes)} · {row.path}
                      </div>
                    </button>
                    <button
                      className="shrink-0 rounded p-1 text-slate-500 opacity-0 transition-opacity hover:text-rose-300 group-hover:opacity-100"
                      title="Xoá dự án"
                      onClick={() => void removeRow(row)}
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                ))}

                {cachedRows.length > 0 && (
                  <>
                    <div className="border-y border-border bg-surface-1 px-3 py-1 text-[10px] uppercase tracking-wide text-amber-300/80">
                      Đã cache trước đó (chưa lưu file)
                    </div>
                    {cachedRows.map((row) => (
                      <div
                        key={row.path}
                        className="group flex items-center gap-2 px-2 py-1.5 hover:bg-surface-3"
                      >
                        <button className="min-w-0 flex-1 text-left" onClick={() => void openRow(row)}>
                          <div className="flex items-center gap-1.5 truncate text-[11px] text-slate-200">
                            <span className="shrink-0 rounded bg-amber-500/20 px-1 py-0.2 text-[9px] font-medium text-amber-300">
                              Nháp
                            </span>
                            <span className="truncate">{row.name}</span>
                          </div>
                          <div className="truncate font-mono text-[10px] text-slate-500">
                            {stamp(row)} · {formatBytes(row.size_bytes)}
                          </div>
                        </button>
                        <button
                          className="shrink-0 rounded p-1 text-slate-500 opacity-0 transition-opacity hover:text-rose-300 group-hover:opacity-100"
                          title="Xoá bản nháp cache"
                          onClick={() => void removeRow(row)}
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    ))}
                  </>
                )}
              </>
            )}
          </div>

          {folder && (
            <div className="truncate border-t border-border bg-surface-1 px-3 py-1 font-mono text-[10px] text-slate-600">
              {online === false ? "engine offline" : folder}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
