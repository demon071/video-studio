import { AlertTriangle, RefreshCw } from "lucide-react";

import { RightPanel } from "@/components/layout/RightPanel";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { PreviewPane } from "@/components/preview/PreviewPane";
import { TimelinePane } from "@/components/timeline/TimelinePane";
import { useEngine } from "@/hooks/useEngine";
import { useProgressSocket } from "@/hooks/useProgressSocket";
import { useProjectPersistence } from "@/hooks/useProjectPersistence";
import { desktopInfo } from "@/lib/desktop";
import { backendHttpUrl } from "@/lib/endpoint";
import { useEditor } from "@/store/editorStore";

/**
 * Editor shell — Step 3.
 *
 * Left: source + AI tools (STT → translate → dub → model manager).
 * Centre: preview player with a live subtitle overlay.
 * Right: the subtitle editor, full height so a line and its translation sit
 * side by side with the frame they belong to.
 * Bottom: three-track timeline (video / subtitles / dubbing) with the playhead.
 * The log lives in the sidebar so the preview keeps the height of a 9:16 frame.
 */
export default function App() {
  useProgressSocket();
  const { probing, retry } = useEngine();
  // Restores the previous session, autosaves every edit and owns Ctrl+S / Ctrl+O.
  useProjectPersistence();
  const online = useEditor((state) => state.engineOnline);
  const info = desktopInfo();
  const endpoint = backendHttpUrl();

  return (
    <div className="flex h-full flex-col overflow-hidden bg-surface-0 text-slate-200">
      <TopBar />

      {online === false && (
        <div className="flex shrink-0 items-center gap-2 border-b border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-[11px] text-rose-200">
          <AlertTriangle size={12} />
          <span>
            Không kết nối được engine tại <span className="font-mono">{endpoint}</span>.{" "}
            {info?.managed ? (
              <>
                Sidecar không phản hồi — xem log{" "}
                <code className="rounded bg-black/30 px-1 font-mono">{info.log_path ?? "engine.log"}</code>.
              </>
            ) : (
              <>
                Chạy <code className="rounded bg-black/30 px-1 font-mono">npm run dev</code> từ thư mục gốc của repo.
              </>
            )}
          </span>
          <button className="btn-ghost ml-auto !px-2 !py-0.5 !text-[10px]" onClick={retry} disabled={probing}>
            <RefreshCw size={10} className={probing ? "animate-spin" : undefined} />
            Thử lại
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="flex min-w-0 flex-1">
          <PreviewPane />
          <RightPanel />
        </main>
      </div>

      <TimelinePane />
    </div>
  );
}
