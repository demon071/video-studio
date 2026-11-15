import { Eraser } from "lucide-react";

import { useEditor } from "@/store/editorStore";

const TONE: Record<string, string> = {
  info: "text-slate-400",
  success: "text-emerald-400",
  warn: "text-amber-300",
  error: "text-rose-400",
};

export function LogPanel() {
  const logs = useEditor((state) => state.logs);
  const clearLogs = useEditor((state) => state.clearLogs);
  const socket = useEditor((state) => state.socket);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              socket === "open" ? "bg-emerald-400" : socket === "connecting" ? "bg-amber-400" : "bg-rose-500"
            }`}
          />
          {socket === "open" ? "WebSocket connected" : socket === "connecting" ? "connecting…" : "disconnected"}
        </span>
        <button className="text-slate-500 hover:text-slate-300" onClick={clearLogs} title="Xoá log">
          <Eraser size={11} />
        </button>
      </div>

      <div className="flex-1 space-y-1 overflow-y-auto p-2 font-mono text-[10px] leading-relaxed">
        {logs.length === 0 && <p className="p-2 text-slate-600">Chưa có hoạt động nào.</p>}
        {logs.map((line) => (
          <div key={line.id} className="flex gap-1.5">
            <span className="shrink-0 text-slate-600">
              {new Date(line.ts).toLocaleTimeString("vi-VN", { hour12: false })}
            </span>
            <span className={`min-w-0 break-words ${TONE[line.level] ?? "text-slate-400"}`}>{line.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
