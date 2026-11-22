/**
 * Every long-running editor action in one place.
 *
 * Components stay declarative: they only read the store and call one of these
 * functions. Each task brackets itself with `pending` flags and writes a
 * human-readable line to the activity log, so a failure is never silent.
 */
import {
  analyzeDialogueContext,
  exportAudioOnly,
  exportCapCut,
  exportResourcePackage,
  exportSubtitles,
  importSrtFile,
  probeVideo,
  remixDub,
  renderFastPreview,
  renderVideo,
  stripVideoBgm,
  synthesizeCue,
  synthesizeDub,
  transcribeVideo,
  translateSegments,
  type DubbingBody,
} from "@/lib/api";
import {
  clipTimelineBounds,
  deleteClipsRange,
  isClipsEdited,
  splitClipsAt,
  timelineDuration,
  trimLeftClipsAt,
  trimRightClipsAt,
} from "@/lib/clips";
import { formatBytes } from "@/lib/format";
import { player } from "@/lib/player";
import {
  baseName,
  directoryOf,
  joinPath,
  pickSavePath,
  pickSubtitleSavePath,
  stem,
  toAssetUrl,
} from "@/lib/media";
import { staleCues, useEditor, type SourceClip } from "@/store/editorStore";
import type { Aspect, CapCutExportResult, DubbingResult } from "@/types/editor";

const store = () => useEditor.getState();

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function openVideo(path: string): Promise<void> {
  const state = store();
  // A restored project whose clip moved away is waiting for exactly this pick:
  // relink it and keep the subtitles and the dub instead of starting over.
  const relink = Boolean(state.source && !state.source.url && (state.segments.length || state.dub));
  if (!relink && state.projectDirty && (state.source || state.segments.length)) {
    const proceed = window.confirm(
      "Bản đang dựng có thay đổi chưa lưu thành tệp. Nhập video mới và bắt đầu lại?",
    );
    if (!proceed) return;
  }
  state.beginTask("import");
  stopCuePlayback();
  try {
    const info = await probeVideo(path);
    const url = await toAssetUrl(path);
    const clip: SourceClip = {
      path,
      url,
      name: baseName(path),
      duration: info.duration,
      width: info.width,
      height: info.height,
      fps: info.fps,
      hasAudio: info.has_audio,
      aspect: (info.aspect as Aspect) ?? "9:16",
    };
    if (relink) {
      state.relinkSource(clip);
      state.pushLog("success", `Đã liên kết lại video ${clip.name} — phụ đề và lồng tiếng được giữ nguyên.`);
    } else {
      state.setSource(clip);
      state.pushLog(
        "success",
        `Đã nhập ${clip.name} · ${info.width}×${info.height} · ${info.fps.toFixed(1)} fps · ${info.duration.toFixed(2)}s`,
      );
    }
    if (!info.has_audio) state.pushLog("warn", "Video không có âm thanh — bỏ qua bước nhận dạng giọng nói.");
  } catch (error) {
    state.pushLog("error", `Không đọc được video: ${message(error)}`);
  } finally {
    state.endTask("import");
  }
}

export async function runTranscribe(): Promise<void> {
  const state = store();
  const source = state.source;
  if (!source?.path) {
    state.pushLog("warn", "Hãy mở video từ ổ đĩa trước khi tạo phụ đề (engine cần đường dẫn tuyệt đối).");
    return;
  }
  state.beginTask("transcribe");
  const clean = state.audioMode === "clean";
  state.pushLog(
    "info",
    `Đang nhận dạng giọng nói (${state.sttProvider}${state.sttProvider === "local" ? ` · ${state.whisperSize}` : ""}${
      clean ? " · tách giọng trước" : ""
    })…`,
  );
  try {
    const result = await transcribeVideo({
      video_path: source.path,
      language: state.sourceLanguage,
      provider: state.sttProvider,
      model: state.sttProvider === "local" ? state.whisperSize : undefined,
      vad: true,
      audio_mode: state.audioMode,
      dedup_enabled: state.dedupEnabled,
      dedup_similarity: state.dedupSimilarity,
      hallucination_filter_enabled: state.hallucinationFilter,
      hallucination_silence_threshold: state.hallucinationSilence,
      diarize: state.diarize,
      num_speakers: state.numSpeakers,
    });
    state.setSegments(result.segments);
    state.pushLog(
      "success",
      `Đã tạo ${result.segments.length} dòng phụ đề · ngôn ngữ ${result.language} · ${result.provider}/${result.model} trên ${result.device}`,
    );
  } catch (error) {
    state.pushLog("error", `Tạo phụ đề thất bại: ${message(error)}`);
  } finally {
    state.endTask("transcribe");
  }
}

function remapItems<T extends { start: number; end: number }>(
  items: T[],
  mode: "trim_left" | "trim_right" | "delete_range",
  a: number,
  b: number,
): T[] {
  const out: T[] = [];
  for (const item of items) {
    if (mode === "trim_left") {
      if (item.end <= a + 0.05) continue;
      const start = Math.max(0, item.start - a);
      const end = Math.max(start + 0.05, item.end - a);
      out.push({ ...item, start: Number(start.toFixed(3)), end: Number(end.toFixed(3)) });
    } else if (mode === "trim_right") {
      if (item.start >= b - 0.05) continue;
      const start = Math.max(0, item.start);
      const end = Math.min(b, item.end);
      if (end > start + 0.05) {
        out.push({ ...item, start: Number(start.toFixed(3)), end: Number(end.toFixed(3)) });
      }
    } else {
      const removed = b - a;
      if (item.end <= a + 0.05) {
        out.push(item);
      } else if (item.start >= b - 0.05) {
        out.push({
          ...item,
          start: Number(Math.max(0, item.start - removed).toFixed(3)),
          end: Number(Math.max(0.05, item.end - removed).toFixed(3)),
        });
      } else if (item.start < a && item.end > b) {
        out.push({ ...item, end: Number(Math.max(item.start + 0.05, item.end - removed).toFixed(3)) });
      } else if (item.start < a) {
        out.push({ ...item, end: Number(Math.max(item.start + 0.05, a).toFixed(3)) });
      } else if (item.end > b) {
        const ns = a;
        const ne = a + (item.end - b);
        if (ne > ns + 0.05) {
          out.push({ ...item, start: Number(ns.toFixed(3)), end: Number(ne.toFixed(3)) });
        }
      }
    }
  }
  return out;
}

function remapAndRemixDub(
  dub: ReturnType<typeof store>["dub"],
  mode: "trim_left" | "trim_right" | "delete_range",
  start: number,
  end: number,
  newDur: number,
) {
  if (!dub) return null;
  const nextReports = remapItems(dub.segments, mode, start, end).map((r, idx) => ({
    ...r,
    index: idx + 1,
  }));
  const updatedDub = {
    ...dub,
    duration: Math.max(newDur, 0.5),
    segments: nextReports,
  };
  if (nextReports.length > 0 && nextReports.every((r) => Boolean(r.clip_path))) {
    void remixDub(nextReports, Math.max(newDur, 0.5))
      .then((res) => {
        useEditor.setState((cur) =>
          cur.dub
            ? {
                dub: { ...cur.dub, track_path: res.track_path, duration: res.duration },
              }
            : {},
        );
      })
      .catch(() => null);
  }
  return updatedDub;
}

export async function runTimelineCut(
  mode: "trim_left" | "trim_right" | "delete_range",
  customTime?: number,
): Promise<void> {
  const state = store();
  const source = state.source;
  if (!source?.path || !state.videoClips.length) {
    state.pushLog("warn", "Hãy mở video trước khi cắt.");
    return;
  }
  const total = timelineDuration(state.videoClips);
  let start = customTime ?? state.playhead;
  let end = customTime ?? state.playhead;
  if (mode === "delete_range") {
    if (!state.selectionRange) {
      state.pushLog("warn", "Hãy chọn đoạn [ Đầu – Cuối ] trên Timeline trước khi xóa.");
      return;
    }
    [start, end] = state.selectionRange;
  }

  const nextClips =
    mode === "trim_left"
      ? trimLeftClipsAt(state.videoClips, start)
      : mode === "trim_right"
        ? trimRightClipsAt(state.videoClips, end)
        : deleteClipsRange(state.videoClips, start, end);
  const newDur = timelineDuration(nextClips);
  if (Math.abs(newDur - total) < 0.02) {
    state.pushLog("warn", "Đoạn cắt quá ngắn hoặc không hợp lệ.");
    return;
  }

  const snapshot = {
    videoClips: state.videoClips,
    segments: state.segments,
    secondarySegments: state.secondarySegments,
    layers: state.layers,
    dub: state.dub,
  };
  const nextSegs = remapItems(state.segments, mode, start, end).map((s, idx) => ({
    ...s,
    index: idx + 1,
  }));
  const nextSecondary = remapItems(state.secondarySegments, mode, start, end).map((s, idx) => ({
    ...s,
    index: idx + 1,
  }));
  const nextLayers = remapItems(state.layers, mode, start, end);
  const nextDub = remapAndRemixDub(state.dub, mode, start, end, newDur);
  const nextPlayhead = mode === "delete_range" ? Math.min(start, newDur) : 0;

  useEditor.setState((prev) => ({
    videoClips: nextClips,
    segments: nextSegs,
    secondarySegments: nextSecondary,
    layers: nextLayers,
    dub: nextDub,
    selectionRange: null,
    dubDirty: false,
    playhead: nextPlayhead,
    selected: nextSegs[0]?.index ?? null,
    projectDirty: true,
    cutUndoStack: [...prev.cutUndoStack.slice(-9), snapshot],
  }));
  player.seek(nextPlayhead);
  const label =
    mode === "trim_left"
      ? `Cắt trái (${start.toFixed(1)}s)`
      : mode === "trim_right"
        ? `Cắt phải (${end.toFixed(1)}s)`
        : `Xóa đoạn ${start.toFixed(1)}s–${end.toFixed(1)}s`;
  state.pushLog("success", `${label} → thời lượng timeline ${newDur.toFixed(2)}s (đã đồng bộ sub & lồng tiếng)`);
}

export function splitTimelineClip(atTime?: number): void {
  const state = store();
  if (!state.videoClips.length) return;
  const t = atTime ?? state.playhead;
  const nextClips = splitClipsAt(state.videoClips, t);
  if (nextClips.length === state.videoClips.length) {
    state.pushLog("warn", "Đặt playhead vào giữa clip (cách 2 mép >= 0.1s) để tách.");
    return;
  }
  const snapshot = {
    videoClips: state.videoClips,
    segments: state.segments,
    secondarySegments: state.secondarySegments,
    layers: state.layers,
    dub: state.dub,
  };
  useEditor.setState((prev) => ({
    videoClips: nextClips,
    projectDirty: true,
    cutUndoStack: [...prev.cutUndoStack.slice(-9), snapshot],
  }));
  state.pushLog("info", `Đã tách clip tại ${t.toFixed(2)}s (${nextClips.length} đoạn)`);
}

export function deleteTimelineClip(clipId: string): void {
  const state = store();
  if (state.videoClips.length <= 1) {
    state.pushLog("warn", "Không thể xóa đoạn video cuối cùng.");
    return;
  }
  const bound = clipTimelineBounds(state.videoClips).find((b) => b.clip.id === clipId);
  if (!bound) return;
  const snapshot = {
    videoClips: state.videoClips,
    segments: state.segments,
    secondarySegments: state.secondarySegments,
    layers: state.layers,
    dub: state.dub,
  };
  const nextClips = state.videoClips.filter((c) => c.id !== clipId);
  const nextSegs = remapItems(state.segments, "delete_range", bound.start, bound.end).map((s, idx) => ({
    ...s,
    index: idx + 1,
  }));
  const nextSecondary = remapItems(state.secondarySegments, "delete_range", bound.start, bound.end).map(
    (s, idx) => ({ ...s, index: idx + 1 }),
  );
  const nextLayers = remapItems(state.layers, "delete_range", bound.start, bound.end);
  const newDur = timelineDuration(nextClips);
  const nextDub = remapAndRemixDub(state.dub, "delete_range", bound.start, bound.end, newDur);
  const nextPlayhead = Math.min(bound.start, newDur);

  useEditor.setState((prev) => ({
    videoClips: nextClips,
    selectedClipId: null,
    segments: nextSegs,
    secondarySegments: nextSecondary,
    layers: nextLayers,
    dub: nextDub,
    dubDirty: false,
    playhead: nextPlayhead,
    projectDirty: true,
    cutUndoStack: [...prev.cutUndoStack.slice(-9), snapshot],
  }));
  player.seek(nextPlayhead);
  state.pushLog("success", `Đã xóa đoạn clip → thời lượng còn ${newDur.toFixed(2)}s`);
}

export function trimClipEdge(clipId: string, side: "left" | "right", newSourceTime: number): void {
  const state = store();
  const bound = clipTimelineBounds(state.videoClips).find((b) => b.clip.id === clipId);
  if (!bound) return;
  const oldStart = bound.clip.source_start;
  const oldEnd = bound.clip.source_end;
  if (side === "left" && Math.abs(newSourceTime - oldStart) < 0.05) return;
  if (side === "right" && Math.abs(newSourceTime - oldEnd) < 0.05) return;

  const snapshot = {
    videoClips: state.videoClips,
    segments: state.segments,
    secondarySegments: state.secondarySegments,
    layers: state.layers,
    dub: state.dub,
  };
  const nextClips = state.videoClips.map((c) => {
    if (c.id !== clipId) return c;
    return side === "left"
      ? { ...c, source_start: Number(newSourceTime.toFixed(3)) }
      : { ...c, source_end: Number(newSourceTime.toFixed(3)) };
  });
  const newDur = timelineDuration(nextClips);

  let nextSegs = state.segments;
  let nextSecondary = state.secondarySegments;
  let nextLayers = state.layers;
  let nextDub = state.dub;
  if (side === "left" && newSourceTime > oldStart) {
    const cutTo = bound.start + (newSourceTime - oldStart);
    nextSegs = remapItems(state.segments, "delete_range", bound.start, cutTo).map((s, i) => ({
      ...s,
      index: i + 1,
    }));
    nextSecondary = remapItems(state.secondarySegments, "delete_range", bound.start, cutTo).map((s, i) => ({
      ...s,
      index: i + 1,
    }));
    nextLayers = remapItems(state.layers, "delete_range", bound.start, cutTo);
    nextDub = remapAndRemixDub(state.dub, "delete_range", bound.start, cutTo, newDur);
  } else if (side === "right" && newSourceTime < oldEnd) {
    const cutFrom = bound.start + (newSourceTime - oldStart);
    nextSegs = remapItems(state.segments, "delete_range", cutFrom, bound.end).map((s, i) => ({
      ...s,
      index: i + 1,
    }));
    nextSecondary = remapItems(state.secondarySegments, "delete_range", cutFrom, bound.end).map((s, i) => ({
      ...s,
      index: i + 1,
    }));
    nextLayers = remapItems(state.layers, "delete_range", cutFrom, bound.end);
    nextDub = remapAndRemixDub(state.dub, "delete_range", cutFrom, bound.end, newDur);
  }

  useEditor.setState((prev) => ({
    videoClips: nextClips,
    segments: nextSegs,
    secondarySegments: nextSecondary,
    layers: nextLayers,
    dub: nextDub,
    dubDirty: false,
    projectDirty: true,
    cutUndoStack: [...prev.cutUndoStack.slice(-9), snapshot],
  }));
}

export function undoTimelineCut(): void {
  const state = store();
  const prev = state.cutUndoStack[state.cutUndoStack.length - 1];
  if (!prev) return;
  const restoredDub = prev.dub ?? state.dub;
  useEditor.setState((curr) => ({
    videoClips: prev.videoClips,
    segments: prev.segments,
    secondarySegments: prev.secondarySegments,
    layers: prev.layers,
    dub: restoredDub,
    selectionRange: null,
    dubDirty: false,
    playhead: 0,
    selected: prev.segments[0]?.index ?? null,
    cutUndoStack: curr.cutUndoStack.slice(0, -1),
  }));
  if (restoredDub && restoredDub.segments.length > 0 && restoredDub.segments.every((r) => Boolean(r.clip_path))) {
    const restoredDur = Math.max(timelineDuration(prev.videoClips), 0.5);
    void remixDub(restoredDub.segments, restoredDur)
      .then((res) => {
        useEditor.setState((cur) =>
          cur.dub ? { dub: { ...cur.dub, track_path: res.track_path, duration: res.duration } } : {},
        );
      })
      .catch(() => null);
  }
  player.seek(0);
  state.pushLog("info", "Đã hoàn tác thao tác cắt trên Timeline.");
}

export async function runRangeTranscribe(mode: "ocr" | "whisper"): Promise<void> {
  const state = store();
  const source = state.source;
  const range = state.selectionRange;
  if (!source?.path || !range) {
    state.pushLog("warn", "Hãy kéo chọn khoảng thời gian trên Timeline trước.");
    return;
  }
  const [start, end] = range;
  state.beginTask("transcribe");
  state.pushLog("info", `Đang tách lại đoạn ${start.toFixed(1)}s–${end.toFixed(1)}s bằng ${mode.toUpperCase()}…`);
  try {
    const result = await transcribeVideo({
      video_path: source.path,
      language: state.sourceLanguage,
      provider: mode === "ocr" ? "ocr" : "local",
      model: mode === "whisper" ? state.whisperSize : undefined,
      start_time: start,
      end_time: end,
      dedup_enabled: state.dedupEnabled,
      dedup_similarity: state.dedupSimilarity,
    });
    const outside = state.segments.filter((s) => s.end <= start + 0.05 || s.start >= end - 0.05);
    const merged = [...outside, ...result.segments]
      .sort((a, b) => a.start - b.start)
      .map((item, idx) => ({ ...item, index: idx + 1 }));
    state.setSegments(merged);
    state.pushLog("success", `Đã thay thế ${result.segments.length} dòng trong khoảng ${start.toFixed(1)}s–${end.toFixed(1)}s.`);
  } catch (error) {
    state.pushLog("error", `Tách lại khoảng chọn thất bại: ${message(error)}`);
  } finally {
    state.endTask("transcribe");
  }
}

export async function runImportSrtText(srtContent: string, targetTrack: 1 | 2 = 1): Promise<void> {
  const state = store();
  state.beginTask("import");
  try {
    const result = await importSrtFile({ srt_content: srtContent });
    if (targetTrack === 2) {
      state.update({ secondarySegments: result.segments, presentationMode: "bilingual" });
      state.pushLog("success", `Đã nhập ${result.segments.length} dòng vào Phụ đề 2 (TS2).`);
    } else {
      state.setSegments(result.segments);
      state.pushLog("success", `Đã nhập ${result.segments.length} dòng phụ đề từ tệp SRT.`);
    }
  } catch (error) {
    state.pushLog("error", `Nhập SRT thất bại: ${message(error)}`);
  } finally {
    state.endTask("import");
  }
}

export async function runAnalyzeContext(): Promise<void> {
  const state = store();
  if (!state.segments.length) {
    state.pushLog("warn", "Chưa có phụ đề để phân tích ngữ cảnh.");
    return;
  }
  state.beginTask("analyzeContext");
  state.pushLog("info", "Đang phân tích nhân vật & đại từ xưng hô toàn kịch bản…");
  try {
    const result = await analyzeDialogueContext({
      segments: state.segments,
      target_language: state.targetLanguage,
      provider: state.translateProvider,
      preset: state.translatePreset,
    });
    const normalized = {
      ...result,
      summary: result.summary ?? "",
      characters: Array.isArray(result.characters) ? result.characters : [],
      pronoun_rules: Array.isArray(result.pronoun_rules) ? result.pronoun_rules : [],
      term_glossary: Array.isArray(result.term_glossary) ? result.term_glossary : [],
      context_guidance: result.context_guidance ?? result.context ?? "",
    };
    state.update({
      contextResult: normalized,
      contextGuidance: normalized.context_guidance || state.contextGuidance,
    });
    state.pushLog(
      "success",
      `Đã phân tích ${normalized.characters.length} nhân vật & quy tắc xưng hô.`,
    );
  } catch (error) {
    state.pushLog("error", `Phân tích ngữ cảnh thất bại: ${message(error)}`);
  } finally {
    state.endTask("analyzeContext");
  }
}

export async function runTranslate(): Promise<void> {
  const state = store();
  if (!state.segments.length) {
    state.pushLog("warn", "Chưa có dòng phụ đề nào để dịch.");
    return;
  }
  state.beginTask("translate");
  state.pushLog("info", `Đang dịch ${state.segments.length} dòng sang ${state.targetLanguage} (${state.translateProvider})…`);
  try {
    const result = await translateSegments({
      segments: state.segments,
      target_language: state.targetLanguage,
      source_language: state.sourceLanguage,
      provider: state.translateProvider,
      style: state.translateStyle,
      preset: state.translatePreset,
      auto_context: state.autoContext,
      context_guidance: state.contextGuidance || null,
    });
    state.setSegments(result.segments);
    state.pushLog("success", `Đã dịch ${result.segments.length} dòng · ${result.provider}/${result.model}`);
  } catch (error) {
    state.pushLog("error", `Dịch thất bại: ${message(error)}`);
  } finally {
    state.endTask("translate");
  }
}

export async function runDub(): Promise<void> {
  const state = store();
  if (!state.segments.length) {
    state.pushLog("warn", "Chưa có phụ đề để lồng tiếng.");
    return;
  }
  if (state.useTranslationForDub && !state.segments.some((segment) => segment.translation?.trim())) {
    state.pushLog("warn", "Chưa có bản dịch — hãy dịch trước hoặc tắt 'Dùng bản dịch'.");
    return;
  }

  const body: DubbingBody = {
    segments: state.segments,
    use_translation: state.useTranslationForDub,
    provider: state.ttsProvider,
    voice: state.ttsVoice || null,
    rate: state.ttsProvider === "capcut" ? state.ttsRate : null,
    timing_rewrite: true,
    language: state.useTranslationForDub ? state.targetLanguage : state.sourceLanguage,
    video_path: state.source?.path ?? null,
    speaker_voices: state.speakerVoices,
    trim_silence: state.trimSilence,
    fit_voice_mode: state.fitVoiceMode,
  };

  state.beginTask("dub");
  state.pushLog("info", `Đang lồng tiếng ${state.segments.length} dòng · ${state.ttsProvider} · ${state.ttsVoice}…`);
  try {
    const result = await synthesizeDub(body);
    state.setDub(result);
    const clamped = result.segments.filter((segment) => segment.clamped);
    const trimmed = result.segments.reduce((total, segment) => total + segment.overflow_s, 0);
    const rewrittenNote = result.rewritten ? ` · ${result.rewritten} dòng được viết gọn` : "";
    state.pushLog(
      "success",
      `Xong track lồng tiếng · ${result.duration.toFixed(2)}s · ${result.segments.length - result.skipped}/${result.segments.length} dòng${rewrittenNote}`,
    );
    if (clamped.length) {
      state.pushLog(
        "warn",
        `${clamped.length} dòng bị giới hạn tốc độ ${clamped[0].applied_ratio.toFixed(2)}× — cắt ${trimmed.toFixed(2)}s. Hãy rút ngắn câu hoặc tăng thời lượng dòng.`,
      );
    }
    if (state.keepOriginalAudio) {
      state.pushLog(
        "info",
        `Âm thanh gốc giữ lại và hạ còn ${Math.round(state.duckGain * 100)}% dưới giọng lồng tiếng.`,
      );
    }
  } catch (error) {
    state.pushLog("error", `Lồng tiếng thất bại: ${message(error)}`);
  } finally {
    state.endTask("dub");
  }
}

export async function runStripBgm(): Promise<void> {
  const state = store();
  const source = state.source;
  if (!source?.path) {
    state.pushLog("warn", "Hãy mở video trước khi tách nhạc nền.");
    return;
  }
  state.beginTask("stripBgm");
  state.pushLog("info", "Đang tách bỏ nhạc nền bản quyền bằng AI (UVR-MDX)…");
  try {
    const res = await stripVideoBgm(source.path);
    useEditor.setState({
      backgroundAudioPath: res.output_path,
      keepOriginalAudio: false,
      projectDirty: true,
    });
    state.pushLog("success", `Đã tách bỏ nhạc nền gốc → giữ lại âm thoại sạch: ${res.output_path}`);
  } catch (error) {
    state.pushLog("error", `Tách nhạc nền thất bại: ${message(error)}`);
  } finally {
    state.endTask("stripBgm");
  }
}

export async function runExport(): Promise<void> {
  const state = store();
  const source = state.source;
  if (!source?.path) {
    state.pushLog("warn", "Hãy mở video từ ổ đĩa trước khi xuất.");
    return;
  }

  const suggested = joinPath(directoryOf(source.path), `${stem(source.path)}_short.mp4`);
  const output = (await pickSavePath(suggested)) ?? null;
  if (!output) return;

  state.update({ lastExport: null });
  state.beginTask("render");
  try {
    let subtitlePath: string | null = null;
    if (state.segments.length && state.presentationMode !== "none") {
      const assPath = joinPath(directoryOf(output), `.${stem(output)}_burn.ass`);
      const ass = await exportSubtitles({
        segments: state.segments,
        output_path: assPath,
        fmt: "ass",
        aspect: state.aspect,
        use_translation: state.segments.some((segment) => segment.translation?.trim()),
        style: state.subtitleStyle,
        presentation_mode: state.presentationMode,
        secondary_segments: state.secondarySegments,
        secondary_style: state.secondaryStyle,
      });
      subtitlePath = ass.output_path;
      state.pushLog("info", `Đã ghi phụ đề ${ass.lines} dòng → ${ass.output_path}`);
    }

    state.pushLog("info", `Đang render ${state.aspect}…`);
    const result = await renderVideo({
      video_path: source.path,
      output_path: output,
      aspect: state.aspect,
      subtitle_path: subtitlePath,
      dub_track_path: state.dub?.track_path ?? null,
      background_path: state.backgroundAudioPath,
      background_gain: state.duckGain,
      voice_gain: state.voiceGain,
      keep_original_audio: !state.dub || state.keepOriginalAudio,
      quality_profile: state.qualityProfile,
      use_nvenc: state.useNvenc,
      flip_horizontal: state.flipHorizontal,
      scale_mode: state.scaleMode,
      brightness: state.brightness,
      contrast: state.contrast,
      saturation: state.saturation,
      warps: state.dub?.warps,
      layers: state.layers,
      video_clips: isClipsEdited(state.videoClips, source.duration)
        ? state.videoClips.map((c) => ({ source_start: c.source_start, source_end: c.source_end }))
        : undefined,
    });
    state.update({
      lastExport: {
        output_path: result.output_path,
        width: result.width,
        height: result.height,
        size_bytes: result.size_bytes,
        elapsed_s: result.elapsed_s,
      },
    });
    state.pushLog(
      "success",
      `Đã xuất ${result.output_path} · ${result.width}×${result.height} · ${formatBytes(result.size_bytes)} · ${result.elapsed_s}s`,
    );
  } catch (error) {
    state.pushLog("error", `Render thất bại: ${message(error)}`);
  } finally {
    state.endTask("render");
  }
}

export async function runFastPreview(): Promise<string | null> {
  const state = store();
  const source = state.source;
  if (!source?.path) {
    state.pushLog("warn", "Hãy mở video trước khi xem thử 5s.");
    return null;
  }
  state.beginTask("fastPreview");
  state.pushLog("info", `Đang render bản xem thử 5s tại ${state.playhead.toFixed(1)}s…`);
  try {
    let subtitlePath: string | null = null;
    if (state.segments.length && state.presentationMode !== "none") {
      const assPath = joinPath(directoryOf(source.path), `.${stem(source.path)}_preview.ass`);
      const ass = await exportSubtitles({
        segments: state.segments,
        output_path: assPath,
        fmt: "ass",
        aspect: state.aspect,
        use_translation: state.segments.some((segment) => segment.translation?.trim()),
        style: state.subtitleStyle,
        presentation_mode: state.presentationMode,
        secondary_segments: state.secondarySegments,
        secondary_style: state.secondaryStyle,
      });
      subtitlePath = ass.output_path;
    }
    const result = await renderFastPreview({
      video_path: source.path,
      output_path: joinPath(directoryOf(source.path), `.${stem(source.path)}_preview5s.mp4`),
      aspect: state.aspect,
      subtitle_path: subtitlePath,
      dub_track_path: state.dub?.track_path ?? null,
      background_path: state.backgroundAudioPath,
      background_gain: state.duckGain,
      voice_gain: state.voiceGain,
      keep_original_audio: !state.dub || state.keepOriginalAudio,
      flip_horizontal: state.flipHorizontal,
      scale_mode: state.scaleMode,
      brightness: state.brightness,
      contrast: state.contrast,
      saturation: state.saturation,
      layers: state.layers,
      start_time: Math.max(0, state.playhead),
      sample_duration: 5.0,
    });
    state.pushLog("success", `Xem thử 5s sẵn sàng (${result.elapsed_s}s)`);
    return result.output_path;
  } catch (error) {
    state.pushLog("error", `Xem thử 5s thất bại: ${message(error)}`);
    return null;
  } finally {
    state.endTask("fastPreview");
  }
}

export async function runExportAudio(mode: "voice" | "bgm" | "mix"): Promise<void> {
  const state = store();
  const source = state.source;
  if (!source?.path && !state.dub?.track_path) {
    state.pushLog("warn", "Chưa có nguồn âm thanh để xuất.");
    return;
  }
  const baseDir = source?.path ? directoryOf(source.path) : "";
  const baseNameStr = source?.path ? stem(source.path) : "audio";
  const suggested = baseDir ? joinPath(baseDir, `${baseNameStr}_${mode}.wav`) : `${baseNameStr}_${mode}.wav`;
  const output = await pickSavePath(suggested);
  if (!output) return;

  state.beginTask("exportAudio");
  try {
    const res = await exportAudioOnly({
      output_path: output,
      mode,
      dub_track_path: state.dub?.track_path ?? null,
      video_path: source?.path ?? null,
      background_gain: state.duckGain,
      voice_gain: state.voiceGain,
    });
    state.pushLog("success", `Đã xuất âm thanh (${mode}) → ${res.output_path} (${formatBytes(res.size_bytes)})`);
  } catch (error) {
    state.pushLog("error", `Xuất âm thanh thất bại: ${message(error)}`);
  } finally {
    state.endTask("exportAudio");
  }
}

export async function runExportResourcePackage(): Promise<void> {
  const state = store();
  const source = state.source;
  if (!state.segments.length && !state.dub?.track_path) {
    state.pushLog("warn", "Chưa có phụ đề hoặc track lồng tiếng để xuất gói tài nguyên.");
    return;
  }
  const baseDir = source?.path ? directoryOf(source.path) : "";
  const baseNameStr = source?.path ? stem(source.path) : "resource_pack";
  const suggested = baseDir
    ? joinPath(baseDir, `${baseNameStr}_resources.zip`)
    : `${baseNameStr}_resources.zip`;
  const output = await pickSavePath(suggested);
  if (!output) return;

  state.beginTask("exportPackage");
  state.pushLog("info", "Đang đóng gói tài nguyên (.SRT + .ASS + TTS WAV rời + index.csv)…");
  try {
    const res = await exportResourcePackage({
      output_path: output,
      aspect: state.aspect,
      segments: state.segments,
      secondary_segments: state.secondarySegments,
      presentation_mode: state.presentationMode,
      style: state.subtitleStyle,
      secondary_style: state.secondaryStyle,
      dub_track_path: state.dub?.track_path ?? null,
      dub_segments: state.dub?.segments ?? [],
      background_path: state.backgroundAudioPath,
    });
    state.pushLog(
      "success",
      `Đã xuất gói tài nguyên (${res.files.length} mục · ${res.tts_clips} file TTS rời · ${formatBytes(res.size_bytes)}) → ${res.output_path}`,
    );
  } catch (error) {
    state.pushLog("error", `Xuất gói tài nguyên thất bại: ${message(error)}`);
  } finally {
    state.endTask("exportPackage");
  }
}

/**
 * Hand the project to CapCut Desktop as an editable draft: the clip, one text
 * segment per cue and one audio clip per dubbed line, on separate tracks.
 */
export async function runExportCapCut(outputDir: string | null = null): Promise<CapCutExportResult | null> {
  const state = store();
  const source = state.source;
  if (!source?.path) {
    state.pushLog("warn", "Hãy mở video từ ổ đĩa trước khi xuất sang CapCut.");
    return null;
  }

  state.beginTask("capcut");
  try {
    const result = await exportCapCut({
      video_path: source.path,
      aspect: state.aspect,
      segments: state.segments,
      use_translation: state.segments.some((segment) => segment.translation?.trim()),
      style: state.subtitleStyle,
      // Per-line takes make every dub line movable in CapCut; the assembled
      // track is the fallback for a dub rendered by an older build.
      dub_segments: state.dub?.segments ?? [],
      dub_track_path: state.dub?.track_path ?? null,
      keep_original_audio: !state.dub || state.keepOriginalAudio,
      background_gain: state.duckGain,
      draft_name: state.projectName || stem(source.path),
      output_dir: outputDir,
    });
    const dub = result.dub_lines ? ` · ${result.dub_lines} câu lồng tiếng` : "";
    state.pushLog(
      "success",
      `Đã tạo dự án CapCut "${result.draft_name}" · ${result.tracks} track · ${result.captions} dòng phụ đề${dub}`,
    );
    state.pushLog("info", `Mở CapCut để sửa tiếp: ${result.draft_path}`);
    if (!result.registered) {
      state.pushLog(
        "warn",
        "Draft chưa được thêm vào danh sách của CapCut — mở CapCut rồi dùng Import, hoặc chọn đúng thư mục CapCut.",
      );
    }
    for (const warning of result.warnings) state.pushLog("warn", warning);
    return result;
  } catch (error) {
    state.pushLog("error", `Xuất CapCut thất bại: ${message(error)}`);
    return null;
  } finally {
    state.endTask("capcut");
  }
}

/* ---------------------------------------------------------------------------
 * Subtitle files
 * ------------------------------------------------------------------------ */
export async function runExportSubtitles(fmt: "srt" | "ass"): Promise<void> {
  const state = store();
  if (!state.segments.length) {
    state.pushLog("warn", "Chưa có dòng phụ đề nào để xuất.");
    return;
  }

  const base = state.source?.path ? stem(state.source.path) : "phu-de";
  const folder = state.source?.path ? directoryOf(state.source.path) : "";
  const output = await pickSubtitleSavePath(folder ? joinPath(folder, `${base}.${fmt}`) : `${base}.${fmt}`, fmt);
  if (!output) return;

  state.beginTask("subtitles");
  try {
    const result = await exportSubtitles({
      segments: state.segments,
      output_path: output,
      fmt,
      aspect: state.aspect,
      use_translation: state.segments.some((segment) => segment.translation?.trim()),
      style: state.subtitleStyle,
    });
    state.pushLog("success", `Đã xuất phụ đề ${result.lines} dòng → ${result.output_path}`);
  } catch (error) {
    state.pushLog("error", `Xuất phụ đề thất bại: ${message(error)}`);
  } finally {
    state.endTask("subtitles");
  }
}

/* ---------------------------------------------------------------------------
 * One cue at a time: listen to it, or speak it again after an edit
 * ------------------------------------------------------------------------ */
/** Only one line is audible at a time; a second click stops the first. */
let speaking: HTMLAudioElement | null = null;
let speakingPath: string | null = null;

/** Stop whatever cue is playing (also called when another clip is imported). */
export function stopCuePlayback(): void {
  speaking?.pause();
  speaking = null;
  speakingPath = null;
}

async function playClip(path: string): Promise<void> {
  stopCuePlayback();
  const audio = new Audio(await toAssetUrl(path));
  speaking = audio;
  speakingPath = path;
  audio.addEventListener("ended", () => {
    if (speaking === audio) stopCuePlayback();
  });
  try {
    await audio.play();
  } catch (error) {
    if (speaking === audio) stopCuePlayback();
    throw error;
  }
}

/**
 * Where a cue's take sits on disk: recorded in the report, or — for dubs made
 * before takes were recorded — next to the track, where the engine always wrote
 * `line_NNN.aligned.wav`.
 */
function takePath(index: number, dub: DubbingResult | null): string | null {
  const report = dub?.segments.find((item) => item.index === index);
  if (report?.clip_path) return report.clip_path;
  if (!dub?.track_path) return null;
  return joinPath(directoryOf(dub.track_path), `line_${String(index).padStart(3, "0")}.aligned.wav`);
}

/**
 * Listen to one cue.
 *
 * A take on disk is played as it is — nothing is generated while the audio is
 * already there. Only a line with no take is spoken once, `audition` (for the
 * ears only, never written into the track) and always without the timing
 * rewrite: that step loads the translation model, which has no business on a
 * play button.
 */
export async function listenCue(index: number): Promise<void> {
  const state = store();
  const take = takePath(index, state.dub);

  if (take) {
    if (speakingPath === take) {
      stopCuePlayback();
      return;
    }
    try {
      await playClip(take);
      return;
    } catch {
      // No file there after all (temp folder cleared): speak it once below.
    }
  }

  const segment = state.segments.find((item) => item.index === index);
  const text = (state.useTranslationForDub ? segment?.translation : segment?.text) || segment?.text || "";
  if (!text.trim()) {
    state.pushLog("warn", `Dòng ${index} không có chữ để đọc.`);
    return;
  }

  const flag = `cue:${index}`;
  state.beginTask(flag);
  try {
    const fresh = await synthesizeCue({
      segments: state.segments,
      index,
      audition: true,
      use_translation: state.useTranslationForDub,
      provider: state.ttsProvider,
      voice: state.ttsVoice || null,
      rate: state.ttsRate,
      timing_rewrite: false,
      language: state.useTranslationForDub ? state.targetLanguage : state.sourceLanguage,
    });
    if (fresh.report.clip_path) await playClip(fresh.report.clip_path);
    state.pushLog("info", `Dòng ${index} chưa có audio — đọc thử (${fresh.provider}/${fresh.voice}).`);
  } catch (error) {
    state.pushLog("error", `Không tạo được audio dòng ${index}: ${message(error)}`);
  } finally {
    state.endTask(flag);
  }
}

/**
 * Speak one cue again and rebuild the track around it, so an edit no longer
 * means dubbing the whole video. Requires an existing track: the other lines'
 * takes are reused as they are.
 */
export async function regenerateCue(index: number): Promise<void> {
  const state = store();
  const segment = state.segments.find((item) => item.index === index);
  if (!segment) return;
  if (!state.dub) {
    state.pushLog("warn", "Chưa có track lồng tiếng — hãy lồng tiếng cả video trước.");
    return;
  }
  const text = (state.useTranslationForDub ? segment.translation?.trim() || segment.text : segment.text) ?? "";
  if (!text.trim()) {
    state.pushLog("warn", `Dòng ${index} không có chữ để đọc.`);
    return;
  }

  const flag = `cue:${index}`;
  state.beginTask(flag);
  state.pushLog("info", `Đang tạo lại audio cho dòng ${index}…`);
  try {
    const take = await synthesizeCue({
      segments: state.segments,
      index,
      audition: false,
      use_translation: state.useTranslationForDub,
      provider: state.ttsProvider,
      voice: state.ttsVoice || null,
      rate: state.ttsRate,
      timing_rewrite: true,
      language: state.useTranslationForDub ? state.targetLanguage : state.sourceLanguage,
    });

    // A line added after the dub has no old take: keep the new one anyway.
    const reports = state.dub.segments.some((item) => item.index === index)
      ? state.dub.segments.map((item) => (item.index === index ? take.report : item))
      : [...state.dub.segments, take.report];
    const track = await remixDub(reports, state.dub.duration);
    state.setDub({ ...state.dub, track_path: track.track_path, segments: reports });
    // `setDub` clears the "needs a re-dub" flag; this line is current again, but
    // the others may not be, so ask the takes themselves before trusting it.
    const now = useEditor.getState();
    if (staleCues(now.segments, now.dub, now.useTranslationForDub).size) {
      state.update({ dubDirty: true });
    }
    state.pushLog(
      "success",
      `Đã tạo lại dòng ${index} (${take.report.applied_ratio.toFixed(2)}×) và ghép lại track lồng tiếng.`,
    );
  } catch (error) {
    state.pushLog("error", `Tạo lại audio dòng ${index} thất bại: ${message(error)}`);
  } finally {
    state.endTask(flag);
  }
}
