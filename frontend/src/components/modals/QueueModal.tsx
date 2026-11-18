import {
  CheckCircle2,
  Eraser,
  Eye,
  FilePlus2,
  FolderOpen,
  ListChecks,
  Loader2,
  Play,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import {
  addQueueItems,
  cleanQueueItem,
  clearCompletedQueue,
  fetchQueue,
  fetchQueuePreview,
  openSystemPath,
  removeQueueItem,
  startQueueBatch,
  stopQueueBatch,
} from "@/lib/api";
import { pickAudio, pickVideos } from "@/lib/media";
import { syncActiveQueueItem } from "@/lib/project";
import { openVideo } from "@/lib/tasks";
import { DEFAULT_VOICES, useEditor } from "@/store/editorStore";
import type {
  Aspect,
  QueueSnapshot,
  SttProvider,
  SubtitlePresentationMode,
  SubtitleSegment,
  SubtitleStyle,
  TranslateProvider,
  TtsProvider,
  VideoClip,
  VisualLayer,
} from "@/types/editor";

const TTS_VOICE_OPTIONS: Record<TtsProvider, Array<{ value: string; label: string }>> = {
  capcut: [
    { value: "BV421_vivn_streaming", label: "Nhỏ Ngọt Ngào (VI)" },
    { value: "vi_female_huong", label: "Giọng Nữ Phổ Thông (VI)" },
    { value: "BV074_streaming", label: "Cô Gái Hoạt Ngôn (VI)" },
    { value: "BV562_streaming", label: "Mai (VI)" },
    { value: "BV075_streaming", label: "Thanh Niên Tự Tin (VI)" },
    { value: "BV560_streaming", label: "Alex Đại Đế (VI)" },
  ],
  vieneu: [
    { value: "Ngọc Huyền", label: "Ngọc Huyền (Nữ · Bắc)" },
    { value: "Minh Đức", label: "Minh Đức (Nam · Bắc)" },
    { value: "Phạm Tuyên", label: "Phạm Tuyên (Nam · Bắc)" },
    { value: "Thái Sơn", label: "Thái Sơn (Nam · Nam)" },
    { value: "Trúc Ly", label: "Trúc Ly (Nữ · Bắc)" },
    { value: "Thùy Dung", label: "Thùy Dung (Nữ · Nam)" },
  ],
  piper: [
    { value: "vi_VN-vais1000-medium", label: "vi_VN-vais1000-medium" },
    { value: "banmai", label: "Ban Mai" },
  ],
  elevenlabs: [
    { value: "21m00Tcm4TlvDq8ikWAM", label: "Rachel" },
    { value: "pNInz6obpgDQGcFmaJgB", label: "Adam" },
  ],
  omnivoice: [{ value: "auto", label: "Tự động (auto)" }],
};

export function QueueModal({ onClose }: { onClose: () => void }) {
  const state = useEditor();
  const [snap, setSnap] = useState<QueueSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [sttProvider, setSttProvider] = useState<SttProvider>(state.sttProvider);
  const [translateProvider, setTranslateProvider] = useState<TranslateProvider>(state.translateProvider);
  const [targetLanguage, setTargetLanguage] = useState<string>(state.targetLanguage || "vi");
  const [ttsProvider, setTtsProvider] = useState<TtsProvider>(state.ttsProvider);
  const [ttsVoice, setTtsVoice] = useState<string>(state.ttsVoice || DEFAULT_VOICES[state.ttsProvider]);
  const [aspect, setAspect] = useState<Aspect>(state.aspect);
  const [bgmMode, setBgmMode] = useState<"keep" | "strip" | "mute" | "custom">(
    state.backgroundAudioPath ? "custom" : state.keepOriginalAudio ? "keep" : "mute",
  );
  const [bgmPath, setBgmPath] = useState<string>(state.backgroundAudioPath || "");

  const refresh = async () => {
    try {
      setSnap(await fetchQueue());
    } catch {
      // ignore poll error
    }
  };

  useEffect(() => {
    void (async () => {
      try {
        const synced = await syncActiveQueueItem();
        if (synced) {
          setSnap(synced);
          return;
        }
      } catch {
        // fallback to refresh
      }
      await refresh();
    })();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, []);

  const enqueuePaths = async (paths: string[]) => {
    if (!paths.length) return;
    setLoading(true);
    setErrorMsg(null);
    try {
      setSnap(
        await addQueueItems({
          video_paths: paths,
          source_mode: sttProvider === "ocr" ? "ocr" : "audio",
          stt_provider: sttProvider,
          target_language: targetLanguage,
          translate_provider: translateProvider,
          translate_preset: state.translatePreset || "general",
          tts_provider: ttsProvider,
          tts_voice: ttsVoice,
          aspect,
          bgm_mode: bgmMode,
          bgm_path: bgmMode === "custom" ? bgmPath.trim() || null : null,
          subtitle_style: state.subtitleStyle,
        }),
      );
    } catch (err) {
      setErrorMsg((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handlePickAdd = async () => {
    const paths = await pickVideos();
    await enqueuePaths(paths);
  };

  const handleReviewItem = async (itemId: string) => {
    setLoading(true);
    setErrorMsg(null);
    try {
      await syncActiveQueueItem().catch(() => null);
      const res = await fetchQueuePreview(itemId);
      await openVideo(res.video_path);
      const ed = (res.editor_state ?? {}) as Record<string, unknown>;
      const defaultBg =
        res.item.bgm_mode === "strip" || res.item.bgm_mode === "custom"
          ? (res.item.bgm_path ?? null)
          : null;
      const defaultKeepOrig = res.item.bgm_mode === "keep" || !res.item.bgm_mode;
      const current = useEditor.getState();

      useEditor.setState({
        activeQueueItemId: itemId,
        projectDirty: false,
        segments: res.segments,
        dub: res.dub,
        dubDirty: typeof ed.dub_dirty === "boolean" ? ed.dub_dirty : false,
        aspect: ((ed.aspect as Aspect) || (res.item.aspect as Aspect) || state.aspect),
        selected: res.segments[0]?.index ?? null,
        subtitleStyle:
          ed.subtitle_style && typeof ed.subtitle_style === "object"
            ? (ed.subtitle_style as SubtitleStyle)
            : current.subtitleStyle,
        presentationMode:
          typeof ed.presentation_mode === "string"
            ? (ed.presentation_mode as SubtitlePresentationMode)
            : current.presentationMode,
        secondarySegments: Array.isArray(ed.secondary_segments)
          ? (ed.secondary_segments as SubtitleSegment[])
          : [],
        secondaryStyle:
          ed.secondary_style && typeof ed.secondary_style === "object"
            ? (ed.secondary_style as SubtitleStyle)
            : current.secondaryStyle,
        layers: Array.isArray(ed.layers) ? (ed.layers as VisualLayer[]) : [],
        videoClips:
          Array.isArray(ed.video_clips) && ed.video_clips.length > 0
            ? (ed.video_clips as VideoClip[])
            : current.videoClips,
        backgroundAudioPath:
          "background_path" in ed ? ((ed.background_path as string | null) ?? null) : defaultBg,
        keepOriginalAudio:
          typeof ed.keep_original_audio === "boolean" ? ed.keep_original_audio : defaultKeepOrig,
        voiceGain: typeof ed.voice_gain === "number" ? ed.voice_gain : current.voiceGain,
        duckGain: typeof ed.background_gain === "number" ? ed.background_gain : current.duckGain,
        flipHorizontal:
          typeof ed.flip_horizontal === "boolean" ? ed.flip_horizontal : current.flipHorizontal,
      });
      state.pushLog(
        "success",
        `Đã nạp "${res.item.title}" (${res.segments.length} dòng sub) — mọi chỉnh sửa sẽ tự lưu lại cho xuất hàng loạt.`,
      );
      onClose();
    } catch (err) {
      setErrorMsg(`Không thể mở xem lại: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const readyCount = snap?.items.filter((i) => i.status === "ready_to_export").length ?? 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="flex max-h-[88vh] w-full max-w-4xl flex-col rounded-xl border border-border bg-surface-1 shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <ListChecks size={15} className="text-brand" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
              Hàng đợi xử lý hàng loạt
            </h3>
            {snap?.running && (
              <span className="flex items-center gap-1 rounded bg-brand/15 px-2 py-0.5 text-[10px] font-medium text-brand">
                <Loader2 size={10} className="animate-spin" />
                Đang chạy
              </span>
            )}
          </div>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose}>
            <X size={15} />
          </button>
        </header>

        {/* Batch pipeline configuration */}
        <div className="grid grid-cols-2 gap-2 border-b border-border bg-surface-2/60 px-4 py-2.5 text-[11px] sm:grid-cols-6">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-slate-400">1. Tách sub</span>
            <select
              className="field !py-1 !text-[11px]"
              value={sttProvider}
              onChange={(e) => setSttProvider(e.target.value as SttProvider)}
            >
              <option value="local">Whisper (local)</option>
              <option value="sensevoice">SenseVoice (offline)</option>
              <option value="ocr">OCR (chữ trên video)</option>
              <option value="capcut">CapCut (cloud)</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-slate-400">2. Dịch sub</span>
            <select
              className="field !py-1 !text-[11px]"
              value={translateProvider}
              onChange={(e) => setTranslateProvider(e.target.value as TranslateProvider)}
            >
              <option value="google">Google Dịch</option>
              <option value="bing">Bing Dịch</option>
              <option value="gemini">Gemini</option>
              <option value="openai">OpenAI</option>
              <option value="deepseek">DeepSeek</option>
              <option value="ollama">Ollama (local)</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-slate-400">3. Lồng tiếng</span>
            <select
              className="field !py-1 !text-[11px]"
              value={ttsProvider}
              onChange={(e) => {
                const next = e.target.value as TtsProvider;
                setTtsProvider(next);
                setTtsVoice(DEFAULT_VOICES[next]);
              }}
            >
              <option value="capcut">CapCut (cloud)</option>
              <option value="vieneu">VieNeu-TTS (offline)</option>
              <option value="piper">Piper TTS (offline)</option>
              <option value="elevenlabs">ElevenLabs</option>
              <option value="omnivoice">OmniVoice (offline)</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-slate-400">Giọng đọc</span>
            <select
              className="field !py-1 !text-[11px]"
              value={ttsVoice}
              onChange={(e) => setTtsVoice(e.target.value)}
            >
              {TTS_VOICE_OPTIONS[ttsProvider].map((v) => (
                <option key={v.value} value={v.value}>
                  {v.label}
                </option>
              ))}
            </select>
          </label>
          <div className="grid grid-cols-2 gap-1.5">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] text-slate-400">Ngôn ngữ</span>
              <select
                className="field !py-1 !text-[11px]"
                value={targetLanguage}
                onChange={(e) => setTargetLanguage(e.target.value)}
              >
                <option value="vi">VI</option>
                <option value="en">EN</option>
                <option value="zh">ZH</option>
                <option value="ja">JA</option>
                <option value="ko">KO</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] text-slate-400">Khung hình</span>
              <select
                className="field !py-1 !text-[11px]"
                value={aspect}
                onChange={(e) => setAspect(e.target.value as Aspect)}
              >
                <option value="9:16">9:16</option>
                <option value="16:9">16:9</option>
                <option value="1:1">1:1</option>
              </select>
            </label>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[10px] text-slate-400">4. Nhạc nền (Bản quyền)</span>
            <select
              className="field !py-1 !text-[11px]"
              value={bgmMode}
              onChange={(e) => setBgmMode(e.target.value as "keep" | "strip" | "mute" | "custom")}
            >
              <option value="keep">Giữ âm gốc</option>
              <option value="strip">Xóa nhạc nền gốc (AI)</option>
              <option value="custom">Thay nhạc nền riêng…</option>
              <option value="mute">Tắt hẳn âm gốc</option>
            </select>
            {bgmMode === "custom" && (
              <button
                className="btn-ghost !px-1.5 !py-0.5 !text-[10px] text-emerald-300"
                onClick={async () => {
                  const p = await pickAudio();
                  if (p) setBgmPath(p);
                }}
                title={bgmPath || "Chọn file nhạc nền"}
              >
                {bgmPath ? "Đã chọn nhạc" : "Chọn file nhạc…"}
              </button>
            )}
          </div>
        </div>

        {/* Action bar */}
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-2 px-4 py-2.5">
          <button className="btn-ghost" onClick={() => void handlePickAdd()} disabled={loading}>
            <FilePlus2 size={12} />
            Thêm nhiều video
          </button>
          {state.source?.path && (
            <button
              className="btn-ghost"
              onClick={() => void enqueuePaths([state.source!.path!])}
              disabled={loading}
            >
              + Thêm video đang mở
            </button>
          )}
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            {!snap?.running ? (
              <>
                <button
                  className="btn-ghost"
                  disabled={!snap?.items.length}
                  onClick={async () => {
                    await syncActiveQueueItem().catch(() => null);
                    setSnap(
                      await startQueueBatch({
                        mode: "process_only",
                        subtitle_style: useEditor.getState().subtitleStyle,
                      }),
                    );
                  }}
                  title="Tách sub -> Dịch -> Lồng tiếng (dừng lại để xem & sửa trước khi xuất MP4)"
                >
                  <Play size={11} />
                  Chỉ xử lý (Xem trước khi xuất)
                </button>
                {readyCount > 0 && (
                  <button
                    className="btn-ghost text-emerald-300"
                    onClick={async () => {
                      await syncActiveQueueItem().catch(() => null);
                      setSnap(
                        await startQueueBatch({
                          mode: "export_selected",
                          subtitle_style: useEditor.getState().subtitleStyle,
                        }),
                      );
                    }}
                    title="Xuất MP4 hàng loạt cho các video đã xử lý xong"
                  >
                    <Play size={11} />
                    Xuất MP4 đã xử lý ({readyCount})
                  </button>
                )}
                <button
                  className="btn-primary"
                  disabled={!snap?.items.length}
                  onClick={async () => {
                    await syncActiveQueueItem().catch(() => null);
                    setSnap(
                      await startQueueBatch({
                        mode: "process_and_export",
                        subtitle_style: useEditor.getState().subtitleStyle,
                      }),
                    );
                  }}
                >
                  <Play size={11} />
                  Xử lý & Xuất MP4
                </button>
              </>
            ) : (
              <>
                <button
                  className="btn-ghost text-amber-300"
                  onClick={async () => setSnap(await stopQueueBatch(false))}
                >
                  <Square size={11} />
                  Dừng sau video hiện tại
                </button>
                <button
                  className="btn-ghost text-rose-400"
                  onClick={async () => setSnap(await stopQueueBatch(true))}
                >
                  <Square size={11} />
                  Dừng ngay
                </button>
              </>
            )}
          </div>
        </div>

        {errorMsg && (
          <div className="border-b border-rose-500/30 bg-rose-500/10 px-4 py-1.5 text-[11px] text-rose-300">
            {errorMsg}
          </div>
        )}

        <div className="flex-1 space-y-2 overflow-y-auto p-4">
          {!snap?.items.length && (
            <p className="py-10 text-center text-xs text-slate-500">
              Hàng đợi đang trống. Chọn cấu hình ở trên rồi bấm &quot;Thêm nhiều video&quot; để lên lịch xử lý hàng loạt.
            </p>
          )}
          {snap?.items.map((item) => (
            <div
              key={item.id}
              className="flex items-center gap-3 rounded-lg border border-border bg-surface-2 p-2.5 text-xs"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate font-medium text-slate-200">{item.title}</span>
                  <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                    {item.aspect} · {(item.stt_provider || item.source_mode).toUpperCase()} · {item.translate_provider} → {item.target_language} · {item.tts_provider}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
                  <span>{item.stage}</span>
                  {item.error && <span className="text-rose-400">· {item.error}</span>}
                  {item.output_path && (
                    <span className="truncate font-mono text-[10px] text-emerald-400">
                      → {item.output_path}
                    </span>
                  )}
                </div>
                <div className="mt-1.5 h-1 w-full overflow-hidden rounded bg-surface-4">
                  <div
                    className={`h-full transition-all ${
                      item.status === "error"
                        ? "bg-rose-500"
                        : item.status === "completed" || item.status === "ready_to_export"
                          ? "bg-emerald-400"
                          : "bg-brand"
                    }`}
                    style={{ width: `${Math.round(item.progress * 100)}%` }}
                  />
                </div>
              </div>

              <div className="flex shrink-0 items-center gap-1">
                {item.output_path && (
                  <>
                    <button
                      className="btn-ghost !px-2 !py-1 !text-[10px] text-emerald-300"
                      title="Mở video đã xuất bằng trình phát mặc định"
                      onClick={() =>
                        void openSystemPath(item.output_path!, "open").catch((err) =>
                          setErrorMsg((err as Error).message),
                        )
                      }
                    >
                      <Play size={11} />
                      Xem video
                    </button>
                    <button
                      className="btn-ghost !px-2 !py-1 !text-[10px] text-emerald-300"
                      title="Mở thư mục chứa video đã xuất"
                      onClick={() =>
                        void openSystemPath(item.output_path!, "reveal").catch((err) =>
                          setErrorMsg((err as Error).message),
                        )
                      }
                    >
                      <FolderOpen size={11} />
                      Mở thư mục
                    </button>
                  </>
                )}
                {(item.status === "ready_to_export" || item.status === "completed") && (
                  <button
                    className="btn-ghost !px-2 !py-1 !text-[10px] text-sky-300"
                    title="Nạp video, phụ đề và track lồng tiếng vào trình chỉnh sửa để xem lại trước khi xuất"
                    onClick={() => void handleReviewItem(item.id)}
                  >
                    <Eye size={11} />
                    Xem lại
                  </button>
                )}
                <button
                  className="rounded p-1 text-slate-500 hover:bg-surface-3 hover:text-amber-300"
                  title="Dọn dữ liệu tạm & chạy lại từ đầu"
                  onClick={async () => setSnap(await cleanQueueItem(item.id))}
                >
                  <Eraser size={13} />
                </button>
                <button
                  className="rounded p-1 text-slate-500 hover:bg-surface-3 hover:text-rose-400"
                  title="Xoá khỏi hàng đợi"
                  onClick={async () => setSnap(await removeQueueItem(item.id))}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>

        <footer className="flex items-center justify-between border-t border-border px-4 py-2.5 text-[11px] text-slate-500">
          <span>Tổng cộng: {snap?.items.length ?? 0} video</span>
          <div className="flex items-center gap-2">
            {(() => {
              const firstOut = snap?.items.find((i) => i.output_path)?.output_path;
              if (!firstOut) return null;
              return (
                <button
                  className="btn-ghost !py-1 !text-[11px] text-emerald-300"
                  title="Mở thư mục lưu các video đã xuất"
                  onClick={() =>
                    void openSystemPath(firstOut, "reveal").catch((err) =>
                      setErrorMsg((err as Error).message),
                    )
                  }
                >
                  <FolderOpen size={11} />
                  Mở thư mục lưu
                </button>
              );
            })()}
            <button
              className="btn-ghost !py-1 !text-[11px]"
              onClick={async () => setSnap(await clearCompletedQueue())}
            >
              <CheckCircle2 size={11} />
              Xoá mục đã xong
            </button>
            <button className="btn-ghost !py-1 !text-[11px]" onClick={onClose}>
              Đóng
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
