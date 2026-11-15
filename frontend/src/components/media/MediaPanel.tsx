import { AudioLines, Clock3, FileVideo2, Frame, Loader2, RefreshCw, Wand2 } from "lucide-react";
import { useRef } from "react";

import { CapCutPanel } from "@/components/media/CapCutPanel";
import { baseName, isDesktop, pickVideo } from "@/lib/media";
import { openVideo } from "@/lib/tasks";
import { useEditor } from "@/store/editorStore";
import type { Aspect } from "@/types/editor";

const ASPECTS: Array<{ value: Aspect; hint: string }> = [
  { value: "9:16", hint: "TikTok / Reels / Shorts" },
  { value: "16:9", hint: "YouTube ngang" },
  { value: "1:1", hint: "Feed vuông" },
];

export function MediaPanel() {
  const source = useEditor((state) => state.source);
  const aspect = useEditor((state) => state.aspect);
  const update = useEditor((state) => state.update);
  const importing = useEditor((state) => Boolean(state.pending.import));
  const segments = useEditor((state) => state.segments);
  const projectPath = useEditor((state) => state.projectPath);
  const projectName = useEditor((state) => state.projectName);
  const fileInput = useRef<HTMLInputElement>(null);

  const handlePick = async () => {
    if (isDesktop) {
      const path = await pickVideo();
      if (path) await openVideo(path);
      return;
    }
    fileInput.current?.click();
  };

  const handleBrowserFile = (file: File | undefined) => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.preload = "metadata";
    video.src = url;
    video.onloadedmetadata = () => {
      update({
        source: {
          path: "",
          url,
          name: file.name,
          duration: video.duration,
          width: video.videoWidth,
          height: video.videoHeight,
          fps: 30,
          hasAudio: true,
          aspect: video.videoWidth < video.videoHeight ? "9:16" : "16:9",
        },
      });
      useEditor.getState().pushLog(
        "warn",
        "Video chỉ được nạp vào trình duyệt — các bước AI cần bản desktop (npm run dev).",
      );
    };
    video.onerror = () => useEditor.getState().pushLog("error", "Trình duyệt không phát được định dạng này.");
  };

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <input
        ref={fileInput}
        type="file"
        accept="video/*"
        className="hidden"
        onChange={(event) => handleBrowserFile(event.target.files?.[0])}
      />

      <button
        onClick={() => void handlePick()}
        disabled={importing}
        className="group flex flex-col items-center gap-2 rounded-lg border border-dashed border-border-strong bg-surface-2/60 px-4 py-6 text-center transition-colors hover:border-brand/60 hover:bg-surface-2"
      >
        {importing ? (
          <Loader2 size={20} className="animate-spin text-brand" />
        ) : (
          <FileVideo2 size={20} className="text-slate-400 group-hover:text-brand" />
        )}
        <span className="text-xs font-medium text-slate-200">{source ? "Đổi video" : "Chọn video để dựng"}</span>
        <span className="text-[11px] leading-relaxed text-slate-500">
          MP4 · MOV · MKV · WebM. Video được phát trực tiếp từ ổ đĩa, không copy.
        </span>
      </button>

      {source && (
        <section className="rounded-lg border border-border bg-surface-2">
          <div className="panel-title border-b border-border">Nguồn</div>
          <div className="space-y-2 p-3">
            <div className="truncate font-mono text-[11px] text-slate-300" title={source.path || source.name}>
              {source.name}
            </div>
            <dl className="grid grid-cols-2 gap-y-1.5 text-[11px]">
              <Row icon={<Frame size={10} />} label="Khung hình" value={`${source.width}×${source.height}`} />
              <Row icon={<Clock3 size={10} />} label="Thời lượng" value={`${source.duration.toFixed(2)}s`} />
              <Row icon={<Wand2 size={10} />} label="FPS" value={source.fps.toFixed(1)} />
              <Row
                icon={<AudioLines size={10} />}
                label="Âm thanh"
                value={source.hasAudio ? "có" : "không"}
                tone={source.hasAudio ? "ok" : "warn"}
              />
            </dl>
            {!source.path && (
              <p className="rounded bg-amber-500/10 px-2 py-1.5 text-[10px] leading-relaxed text-amber-300">
                Chế độ trình duyệt: chưa có đường dẫn tuyệt đối nên AI và render bị tắt.
              </p>
            )}
          </div>
        </section>
      )}

      <section className="rounded-lg border border-border bg-surface-2">
        <div className="panel-title border-b border-border">Khung xuất</div>
        <div className="grid grid-cols-3 gap-1.5 p-2">
          {ASPECTS.map((option) => (
            <button
              key={option.value}
              onClick={() => update({ aspect: option.value })}
              className={`rounded-md border px-2 py-2 text-left transition-colors ${
                aspect === option.value
                  ? "border-brand bg-brand/10"
                  : "border-border bg-surface-3 hover:border-border-strong"
              }`}
            >
              <div className={`text-[11px] font-semibold ${aspect === option.value ? "text-brand" : "text-slate-200"}`}>
                {option.value}
              </div>
              <div className="mt-0.5 text-[10px] leading-tight text-slate-500">{option.hint}</div>
            </button>
          ))}
        </div>
      </section>

      <CapCutPanel />

      <section className="rounded-lg border border-border bg-surface-2">
        <div className="panel-title border-b border-border">Dự án</div>
        <dl className="divide-y divide-border text-[11px]">
          <div className="flex items-center justify-between gap-2 px-3 py-2">
            <dt className="shrink-0 text-slate-500">Tệp</dt>
            <dd className="truncate font-mono text-slate-200" title={projectPath ?? "Chưa lưu thành tệp"}>
              {projectName || (projectPath ? baseName(projectPath) : "chưa lưu")}
            </dd>
          </div>
          <div className="flex items-center justify-between px-3 py-2">
            <dt className="text-slate-500">Dòng phụ đề</dt>
            <dd className="font-mono text-slate-200">{segments.length}</dd>
          </div>
          <div className="flex items-center justify-between px-3 py-2">
            <dt className="text-slate-500">Đã dịch</dt>
            <dd className="font-mono text-slate-200">
              {segments.filter((segment) => segment.translation?.trim()).length}
            </dd>
          </div>
          <div className="flex items-center justify-between px-3 py-2">
            <dt className="text-slate-500">Tự động lưu</dt>
            <dd className="text-emerald-400">bật</dd>
          </div>
        </dl>
      </section>

      <button
        className="btn-ghost mt-auto w-full"
        onClick={() =>
          useEditor.getState().update({
            segments: [],
            selected: null,
            dub: null,
            dubDirty: false,
            subtitlePath: null,
            playhead: 0,
          })
        }
        disabled={!segments.length}
      >
        <RefreshCw size={12} />
        Xoá toàn bộ phụ đề
      </button>
    </div>
  );
}

function Row({
  icon,
  label,
  value,
  tone = "default",
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone?: "default" | "ok" | "warn";
}) {
  return (
    <>
      <dt className="flex items-center gap-1 text-slate-500">
        {icon}
        {label}
      </dt>
      <dd
        className={`font-mono ${
          tone === "ok" ? "text-emerald-400" : tone === "warn" ? "text-amber-400" : "text-slate-200"
        }`}
      >
        {value}
      </dd>
    </>
  );
}
