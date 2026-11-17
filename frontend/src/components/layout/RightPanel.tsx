import { Captions, ChevronLeft, Sliders } from "lucide-react";
import { useEffect, useState } from "react";

import { PropertiesPanel } from "@/components/inspector/PropertiesPanel";
import { SubtitleTable } from "@/components/subtitles/SubtitleTable";
import { useEditor } from "@/store/editorStore";

export function RightPanel() {
  const [tab, setTab] = useState<"subtitles" | "properties">("subtitles");
  const collapsed = useEditor((state) => state.tableCollapsed);
  const selectedLayerId = useEditor((state) => state.selectedLayerId);
  const selectedClipId = useEditor((state) => state.selectedClipId);
  const update = useEditor((state) => state.update);

  useEffect(() => {
    if (selectedLayerId || selectedClipId) {
      setTab("properties");
    }
  }, [selectedLayerId, selectedClipId]);

  if (collapsed) {
    return (
      <aside className="flex w-8 shrink-0 flex-col border-l border-border bg-surface-1">
        <button
          className="flex h-full w-full flex-col items-center gap-2 pt-2 text-slate-500 hover:text-slate-200"
          onClick={() => update({ tableCollapsed: false })}
          title="Mở bảng Phụ đề & Thuộc tính"
        >
          <ChevronLeft size={13} />
          <span className="text-[10px] uppercase tracking-wider [writing-mode:vertical-rl]">
            Phụ đề · Thuộc tính
          </span>
        </button>
      </aside>
    );
  }

  return (
    <div className="flex h-full w-[clamp(21rem,27vw,30rem)] shrink-0 flex-col border-l border-border bg-surface-1">
      <div className="flex h-8 shrink-0 items-center border-b border-border bg-surface-2 px-2">
        <button
          onClick={() => setTab("subtitles")}
          className={`flex flex-1 items-center justify-center gap-1 border-b-2 py-1.5 text-[11px] font-medium transition-colors ${tab === "subtitles"
              ? "border-brand text-brand"
              : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
        >
          <Captions size={11} />
          Phụ đề
        </button>
        <button
          onClick={() => setTab("properties")}
          className={`flex flex-1 items-center justify-center gap-1 border-b-2 py-1.5 text-[11px] font-medium transition-colors ${tab === "properties"
              ? "border-brand text-brand"
              : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
        >
          <Sliders size={11} />
          Thuộc tính
        </button>
      </div>

      <div className="min-h-0 flex-1">
        {tab === "subtitles" ? <SubtitleTable /> : <PropertiesPanel />}
      </div>
    </div>
  );
}
