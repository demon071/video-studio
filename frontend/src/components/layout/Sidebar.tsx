import { Boxes, Captions, Film, SlidersHorizontal, Sparkles, TerminalSquare } from "lucide-react";
import { useEffect } from "react";

import { AiPanel } from "@/components/ai/AiPanel";
import { ModelsPanel } from "@/components/ai/ModelsPanel";
import { SettingsPanel } from "@/components/ai/SettingsPanel";
import { LogPanel } from "@/components/layout/LogPanel";
import { MediaPanel } from "@/components/media/MediaPanel";
import { useEditor, type SidebarTab } from "@/store/editorStore";

const TABS: Array<{ id: SidebarTab; label: string; icon: typeof Film }> = [
  { id: "media", label: "Media", icon: Film },
  { id: "ai", label: "AI", icon: Sparkles },
  { id: "models", label: "Model", icon: Boxes },
  // The keys/model names live here: the one tab that must never be hidden.
  { id: "settings", label: "Cài đặt", icon: SlidersHorizontal },
  { id: "log", label: "Log", icon: TerminalSquare },
];

export function Sidebar() {
  // Shared (not local) so a panel can send the user here — see `SettingsLink`.
  const tab = useEditor((state) => state.sidebarTab);
  const setTab = useEditor((state) => state.setSidebarTab);
  const job = useEditor((state) => state.job);
  const errorCount = useEditor((state) => state.logs.filter((line) => line.level === "error").length);
  const segments = useEditor((state) => state.segments.length);
  const hardware = useEditor((state) => state.hardware);

  // A failed job is the one moment the user must look at the log.
  useEffect(() => {
    if (job?.state === "error") setTab("log");
  }, [job?.state, job?.id, setTab]);

  return (
    <aside className="flex w-sidebar shrink-0 flex-col border-r border-border bg-surface-1">
      <nav className="flex h-9 shrink-0 items-stretch border-b border-border">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            onClick={() => setTab(entry.id)}
            className={`relative flex flex-1 items-center justify-center gap-1 whitespace-nowrap text-[11px] transition-colors ${
              tab === entry.id
                ? "border-b-2 border-brand text-brand"
                : "border-b-2 border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            <entry.icon size={11} />
            {entry.label}
            {entry.id === "ai" && segments > 0 && (
              <span className="rounded bg-surface-4 px-1 font-mono text-[9px] text-slate-400">{segments}</span>
            )}
            {entry.id === "log" && errorCount > 0 && (
              <span className="rounded bg-rose-500/20 px-1 font-mono text-[9px] text-rose-300">{errorCount}</span>
            )}
          </button>
        ))}
      </nav>

      <div className="min-h-0 flex-1">
        {tab === "media" && <MediaPanel />}
        {tab === "ai" && <AiPanel />}
        {tab === "models" && <ModelsPanel />}
        {tab === "settings" && <SettingsPanel />}
        {tab === "log" && <LogPanel />}
      </div>

      <div className="flex h-7 shrink-0 items-center justify-between border-t border-border px-3 text-[9px] text-slate-600">
        <span className="flex items-center gap-1">
          <Captions size={9} />
          ShortVideoAI v0.1.0
        </span>
        <span className="font-mono">
          {hardware ? `${hardware.profile.gpu_name ?? "CPU"} · ${hardware.profile.vram_free_gb.toFixed(1)}GB free` : "—"}
        </span>
      </div>
    </aside>
  );
}
