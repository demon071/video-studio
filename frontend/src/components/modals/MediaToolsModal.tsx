import { Combine, Film, FolderOpen, Loader2, Music, Plus, Scissors, Split, Trash2, Wrench, X } from "lucide-react";
import { useState } from "react";

import { runMediaBatchTrim, runMediaConcat, runMediaDemux, runMediaMux } from "@/lib/api";
import { directoryOf, joinPath, pickDirectory, pickSavePath, pickVideo, pickVideos, stem } from "@/lib/media";
import { useEditor } from "@/store/editorStore";

type ToolTab = "trim" | "concat" | "demux" | "mux";
type TrimMode = "head_tail" | "range" | "split_duration" | "split_ranges";

export function MediaToolsModal({ onClose }: { onClose: () => void }) {
  const source = useEditor((state) => state.source);
  const pushLog = useEditor((state) => state.pushLog);

  const [tab, setTab] = useState<ToolTab>("trim");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string>("");

  // Tab 1: Trim (1 file, nhiều file, thư mục, bỏ đầu/cuối, chia nhiều đoạn)
  const [trimMode, setTrimMode] = useState<TrimMode>("head_tail");
  const [trimInputDir, setTrimInputDir] = useState("");
  const [trimFiles, setTrimFiles] = useState<string[]>(source?.path ? [source.path] : []);
  const [trimOutputDir, setTrimOutputDir] = useState(
    source?.path ? joinPath(directoryOf(source.path), "trimmed") : "",
  );
  const [trimHead, setTrimHead] = useState(3);
  const [trimTail, setTrimTail] = useState(3);
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(Math.min(source?.duration ?? 10, 30));
  const [chunkDuration, setChunkDuration] = useState(15);
  const [splitRanges, setSplitRanges] = useState<Array<[number, number]>>([
    [0, 5],
    [10, 15],
  ]);
  const [trimReencode, setTrimReencode] = useState(false);

  // Tab 2: Concat
  const [concatList, setConcatList] = useState<string[]>(source?.path ? [source.path] : []);
  const [concatReencode, setConcatReencode] = useState(false);

  // Tab 3: Demux
  const [demuxVideo, setDemuxVideo] = useState(source?.path ?? "");

  // Tab 4: Mux
  const [muxVideo, setMuxVideo] = useState(source?.path ?? "");
  const [muxAudio, setMuxAudio] = useState("");
  const [muxReplace, setMuxReplace] = useState(true);

  const handleTrim = async () => {
    if (!trimInputDir && !trimFiles.length) return;
    let outDir = trimOutputDir.trim();
    if (!outDir) {
      const picked = await pickDirectory("Chọn thư mục lưu video đã cắt");
      if (!picked) return;
      outDir = picked;
      setTrimOutputDir(picked);
    }
    setBusy(true);
    setStatus("Đang cắt video bằng FFmpeg…");
    try {
      const res = await runMediaBatchTrim({
        video_paths: trimFiles,
        input_dir: trimInputDir.trim() || null,
        output_dir: outDir,
        mode: trimMode,
        trim_head: trimHead,
        trim_tail: trimTail,
        start: trimStart,
        end: trimEnd,
        chunk_duration: chunkDuration,
        ranges: splitRanges,
        reencode: trimReencode,
      });
      const skipNote = res.skipped.length ? ` · Bỏ qua ${res.skipped.length}: ${res.skipped.slice(0, 2).join(", ")}` : "";
      setStatus(`Đã cắt xong ${res.count} file → ${outDir}${skipNote}`);
      pushLog("success", `Cắt video hoàn tất: ${res.count} file lưu tại ${outDir}${skipNote}`);
    } catch (err) {
      setStatus(`Lỗi: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleConcat = async () => {
    if (concatList.length < 2) return;
    const first = concatList[0];
    const out = await pickSavePath(
      joinPath(directoryOf(first), `${stem(first)}_merged.mp4`),
    );
    if (!out) return;
    setBusy(true);
    setStatus("Đang ghép video…");
    try {
      const res = await runMediaConcat({
        video_paths: concatList,
        output_path: out,
        reencode: concatReencode,
      });
      setStatus(`Đã ghép: ${res.output_path}`);
      pushLog("success", `Đã ghép ${concatList.length} video → ${res.output_path}`);
    } catch (err) {
      setStatus(`Lỗi: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleDemux = async (kind: "audio" | "video" | "both") => {
    if (!demuxVideo) return;
    const dir = directoryOf(demuxVideo);
    const base = stem(demuxVideo);
    const audioOut = kind !== "video" ? joinPath(dir, `${base}_audio.wav`) : null;
    const videoOut = kind !== "audio" ? joinPath(dir, `${base}_muted.mp4`) : null;
    setBusy(true);
    setStatus("Đang tách luồng media…");
    try {
      const res = await runMediaDemux({
        video_path: demuxVideo,
        audio_output: audioOut,
        video_only_output: videoOut,
      });
      setStatus(`Đã tách: ${res.audio_path || ""} ${res.video_path || ""}`);
      pushLog("success", `Đã tách media → ${res.audio_path || res.video_path}`);
    } catch (err) {
      setStatus(`Lỗi: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleMux = async () => {
    if (!muxVideo || !muxAudio) return;
    const out = await pickSavePath(
      joinPath(directoryOf(muxVideo), `${stem(muxVideo)}_muxed.mp4`),
    );
    if (!out) return;
    setBusy(true);
    setStatus("Đang ghép âm thanh vào video…");
    try {
      const res = await runMediaMux({
        video_path: muxVideo,
        audio_path: muxAudio,
        output_path: out,
        replace_audio: muxReplace,
      });
      setStatus(`Đã lưu: ${res.output_path}`);
      pushLog("success", `Đã ghép âm thanh vào video → ${res.output_path}`);
    } catch (err) {
      setStatus(`Lỗi: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="flex w-full max-w-xl flex-col rounded-xl border border-border bg-surface-1 shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <Wrench size={15} className="text-brand" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
              Công cụ Media FFmpeg (4 trong 1)
            </h3>
          </div>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose}>
            <X size={15} />
          </button>
        </header>

        <div className="grid grid-cols-4 border-b border-border bg-surface-2 text-[11px]">
          <button
            className={`flex items-center justify-center gap-1.5 py-2.5 font-medium ${
              tab === "trim" ? "border-b-2 border-brand text-brand" : "text-slate-400 hover:text-slate-200"
            }`}
            onClick={() => setTab("trim")}
          >
            <Scissors size={12} />
            1. Cắt Video
          </button>
          <button
            className={`flex items-center justify-center gap-1.5 py-2.5 font-medium ${
              tab === "concat" ? "border-b-2 border-brand text-brand" : "text-slate-400 hover:text-slate-200"
            }`}
            onClick={() => setTab("concat")}
          >
            <Combine size={12} />
            2. Ghép Video
          </button>
          <button
            className={`flex items-center justify-center gap-1.5 py-2.5 font-medium ${
              tab === "demux" ? "border-b-2 border-brand text-brand" : "text-slate-400 hover:text-slate-200"
            }`}
            onClick={() => setTab("demux")}
          >
            <Split size={12} />
            3. Tách Âm/Hình
          </button>
          <button
            className={`flex items-center justify-center gap-1.5 py-2.5 font-medium ${
              tab === "mux" ? "border-b-2 border-brand text-brand" : "text-slate-400 hover:text-slate-200"
            }`}
            onClick={() => setTab("mux")}
          >
            <Music size={12} />
            4. Ghép Nhạc
          </button>
        </div>

        <div className="space-y-3 p-4 text-xs">
          {tab === "trim" && (
            <div className="space-y-3">
              {/* Nguồn vào: 1 file / nhiều file / thư mục */}
              <div className="space-y-1.5 rounded border border-border bg-surface-2 p-2.5">
                <div className="flex flex-wrap items-center justify-between gap-1.5">
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                    Nguồn video ({trimInputDir ? `Thư mục: ${trimInputDir}` : `${trimFiles.length} file`})
                  </span>
                  <div className="flex gap-1">
                    <button
                      className="btn-ghost !px-2 !py-0.5 !text-[10px]"
                      onClick={async () => {
                        const paths = await pickVideos();
                        if (paths.length) {
                          setTrimInputDir("");
                          setTrimFiles(paths);
                          if (!trimOutputDir) setTrimOutputDir(joinPath(directoryOf(paths[0]), "trimmed"));
                        }
                      }}
                    >
                      Chọn 1 / nhiều file…
                    </button>
                    <button
                      className="btn-ghost !px-2 !py-0.5 !text-[10px]"
                      onClick={async () => {
                        const dir = await pickDirectory("Chọn thư mục chứa video cần cắt");
                        if (dir) {
                          setTrimInputDir(dir);
                          setTrimFiles([]);
                          setTrimOutputDir(joinPath(dir, "trimmed"));
                        }
                      }}
                    >
                      <FolderOpen size={10} />
                      Chọn thư mục…
                    </button>
                  </div>
                </div>
                {trimInputDir ? (
                  <div className="flex items-center justify-between gap-2 rounded bg-surface-1 px-2 py-1 font-mono text-[10px] text-emerald-300">
                    <span className="truncate">{trimInputDir}</span>
                    <button className="text-slate-400 hover:text-rose-300" onClick={() => setTrimInputDir("")}>
                      ×
                    </button>
                  </div>
                ) : trimFiles.length > 0 ? (
                  <div className="max-h-20 space-y-0.5 overflow-y-auto rounded bg-surface-1 p-1.5 font-mono text-[10px] text-slate-300">
                    {trimFiles.map((f, idx) => (
                      <div key={f} className="flex items-center justify-between gap-2">
                        <span className="truncate">{idx + 1}. {f}</span>
                        <button
                          className="text-slate-500 hover:text-rose-400"
                          onClick={() => setTrimFiles((prev) => prev.filter((_, i) => i !== idx))}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[10px] text-slate-500">Chưa chọn video hoặc thư mục nào.</p>
                )}
              </div>

              {/* 4 chế độ cắt */}
              <div className="grid grid-cols-4 gap-1 text-[10px]">
                {(
                  [
                    { id: "head_tail", label: "Bỏ đầu / cuối" },
                    { id: "range", label: "Khoảng [Đầu–Cuối]" },
                    { id: "split_duration", label: "Chia đều đoạn" },
                    { id: "split_ranges", label: "Nhiều đoạn" },
                  ] as const
                ).map((m) => (
                  <button
                    key={m.id}
                    onClick={() => setTrimMode(m.id)}
                    className={`rounded border px-1.5 py-1 font-medium transition-colors ${
                      trimMode === m.id
                        ? "border-brand bg-brand/15 text-brand"
                        : "border-border bg-surface-2 text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>

              {trimMode === "head_tail" && (
                <div className="grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <span className="text-[10px] text-slate-400">Bỏ đoạn đầu (giây)</span>
                    <input
                      type="number"
                      step={0.5}
                      min={0}
                      className="field w-full font-mono"
                      value={trimHead}
                      onChange={(e) => setTrimHead(Math.max(0, Number(e.target.value)))}
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-[10px] text-slate-400">Bỏ đoạn cuối (giây)</span>
                    <input
                      type="number"
                      step={0.5}
                      min={0}
                      className="field w-full font-mono"
                      value={trimTail}
                      onChange={(e) => setTrimTail(Math.max(0, Number(e.target.value)))}
                    />
                  </label>
                </div>
              )}

              {trimMode === "range" && (
                <div className="grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <span className="text-[10px] text-slate-400">Bắt đầu (giây)</span>
                    <input
                      type="number"
                      step={0.1}
                      min={0}
                      className="field w-full font-mono"
                      value={trimStart}
                      onChange={(e) => setTrimStart(Math.max(0, Number(e.target.value)))}
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-[10px] text-slate-400">Kết thúc (giây)</span>
                    <input
                      type="number"
                      step={0.1}
                      min={0.1}
                      className="field w-full font-mono"
                      value={trimEnd}
                      onChange={(e) => setTrimEnd(Math.max(0.1, Number(e.target.value)))}
                    />
                  </label>
                </div>
              )}

              {trimMode === "split_duration" && (
                <label className="block space-y-1">
                  <span className="text-[10px] text-slate-400">Thời lượng mỗi đoạn (giây)</span>
                  <input
                    type="number"
                    step={1}
                    min={1}
                    className="field w-full font-mono"
                    value={chunkDuration}
                    onChange={(e) => setChunkDuration(Math.max(1, Number(e.target.value)))}
                  />
                </label>
              )}

              {trimMode === "split_ranges" && (
                <div className="space-y-1.5 rounded border border-border bg-surface-2 p-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-slate-400">Danh sách các đoạn cần cắt</span>
                    <button
                      className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
                      onClick={() => {
                        const lastEnd = splitRanges[splitRanges.length - 1]?.[1] ?? 0;
                        setSplitRanges((prev) => [...prev, [lastEnd, lastEnd + 5]]);
                      }}
                    >
                      <Plus size={10} /> Thêm đoạn
                    </button>
                  </div>
                  <div className="max-h-28 space-y-1 overflow-y-auto">
                    {splitRanges.map(([rs, re], i) => (
                      <div key={i} className="flex items-center gap-1.5">
                        <span className="w-10 font-mono text-[10px] text-slate-500">#{i + 1}</span>
                        <input
                          type="number"
                          step={0.1}
                          min={0}
                          className="field !w-20 !py-0.5 font-mono !text-[11px]"
                          value={rs}
                          onChange={(e) =>
                            setSplitRanges((prev) =>
                              prev.map((r, idx) => (idx === i ? [Number(e.target.value), r[1]] : r)),
                            )
                          }
                        />
                        <span className="text-slate-500">→</span>
                        <input
                          type="number"
                          step={0.1}
                          min={0.1}
                          className="field !w-20 !py-0.5 font-mono !text-[11px]"
                          value={re}
                          onChange={(e) =>
                            setSplitRanges((prev) =>
                              prev.map((r, idx) => (idx === i ? [r[0], Number(e.target.value)] : r)),
                            )
                          }
                        />
                        <span className="text-[10px] text-slate-500">s</span>
                        {splitRanges.length > 1 && (
                          <button
                            className="ml-auto text-slate-500 hover:text-rose-400"
                            onClick={() => setSplitRanges((prev) => prev.filter((_, idx) => idx !== i))}
                          >
                            <Trash2 size={11} />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Thư mục xuất */}
              <div className="flex gap-2">
                <input
                  className="field flex-1 font-mono !text-[11px]"
                  value={trimOutputDir}
                  placeholder="Thư mục lưu file sau khi cắt…"
                  onChange={(e) => setTrimOutputDir(e.target.value)}
                />
                <button
                  className="btn-ghost"
                  onClick={async () => {
                    const dir = await pickDirectory("Chọn thư mục lưu kết quả");
                    if (dir) setTrimOutputDir(dir);
                  }}
                >
                  Lưu vào…
                </button>
              </div>

              <label className="flex items-center gap-2 text-[11px] text-slate-400">
                <input
                  type="checkbox"
                  checked={trimReencode}
                  onChange={(e) => setTrimReencode(e.target.checked)}
                />
                Mã hoá lại chính xác từng khung hình (tắt = copy siêu tốc)
              </label>

              <button
                className="btn-primary w-full"
                onClick={() => void handleTrim()}
                disabled={busy || (!trimInputDir && trimFiles.length === 0)}
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Scissors size={12} />}
                Thực hiện cắt Video
              </button>
            </div>
          )}

          {tab === "concat" && (
            <>
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-slate-400">Danh sách video ghép nối tiếp ({concatList.length})</span>
                <button
                  className="btn-ghost !py-1 !text-[11px]"
                  onClick={async () => {
                    const paths = await pickVideos();
                    if (paths.length) setConcatList((prev) => [...prev, ...paths]);
                  }}
                >
                  + Thêm nhiều video
                </button>
              </div>
              <div className="max-h-40 space-y-1 overflow-y-auto rounded border border-border bg-surface-2 p-2">
                {concatList.map((p, i) => (
                  <div key={`${p}-${i}`} className="flex items-center justify-between gap-2 text-[11px]">
                    <span className="truncate font-mono text-slate-300">
                      {i + 1}. {p}
                    </span>
                    <button
                      className="text-slate-500 hover:text-rose-400"
                      onClick={() => setConcatList((prev) => prev.filter((_, idx) => idx !== i))}
                    >
                      Xoá
                    </button>
                  </div>
                ))}
              </div>
              <label className="flex items-center gap-2 text-[11px] text-slate-400">
                <input
                  type="checkbox"
                  checked={concatReencode}
                  onChange={(e) => setConcatReencode(e.target.checked)}
                />
                Đồng bộ lại codec H.264/AAC (bật khi các video khác độ phân giải)
              </label>
              <button
                className="btn-primary w-full"
                onClick={() => void handleConcat()}
                disabled={busy || concatList.length < 2}
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Combine size={12} />}
                Ghép {concatList.length} Video
              </button>
            </>
          )}

          {tab === "demux" && (
            <>
              <div className="flex gap-2">
                <input
                  className="field flex-1 font-mono !text-[11px]"
                  value={demuxVideo}
                  placeholder="Đường dẫn video…"
                  onChange={(e) => setDemuxVideo(e.target.value)}
                />
                <button
                  className="btn-ghost"
                  onClick={async () => {
                    const p = await pickVideo();
                    if (p) setDemuxVideo(p);
                  }}
                >
                  Chọn…
                </button>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <button
                  className="btn-ghost"
                  onClick={() => void handleDemux("audio")}
                  disabled={busy || !demuxVideo}
                >
                  <Music size={12} />
                  Tách lấy Audio (.wav)
                </button>
                <button
                  className="btn-ghost"
                  onClick={() => void handleDemux("video")}
                  disabled={busy || !demuxVideo}
                >
                  <Film size={12} />
                  Tách Video tắt tiếng
                </button>
                <button
                  className="btn-primary"
                  onClick={() => void handleDemux("both")}
                  disabled={busy || !demuxVideo}
                >
                  <Split size={12} />
                  Tách cả hai
                </button>
              </div>
            </>
          )}

          {tab === "mux" && (
            <>
              <div className="space-y-2">
                <div className="flex gap-2">
                  <input
                    className="field flex-1 font-mono !text-[11px]"
                    value={muxVideo}
                    placeholder="Đường dẫn video…"
                    onChange={(e) => setMuxVideo(e.target.value)}
                  />
                  <button
                    className="btn-ghost"
                    onClick={async () => {
                      const p = await pickVideo();
                      if (p) setMuxVideo(p);
                    }}
                  >
                    Video…
                  </button>
                </div>
                <input
                  className="field w-full font-mono !text-[11px]"
                  value={muxAudio}
                  placeholder="Đường dẫn tệp âm thanh (.wav / .mp3)…"
                  onChange={(e) => setMuxAudio(e.target.value)}
                />
              </div>
              <label className="flex items-center gap-2 text-[11px] text-slate-400">
                <input
                  type="checkbox"
                  checked={muxReplace}
                  onChange={(e) => setMuxReplace(e.target.checked)}
                />
                Thay thế hoàn toàn âm thanh gốc (tắt = trộn chung với âm gốc)
              </label>
              <button
                className="btn-primary w-full"
                onClick={() => void handleMux()}
                disabled={busy || !muxVideo || !muxAudio}
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Music size={12} />}
                Ghép Âm Thanh Vào Video
              </button>
            </>
          )}

          {status && (
            <div className="rounded border border-border bg-surface-2 px-3 py-2 font-mono text-[11px] text-slate-300">
              {status}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
