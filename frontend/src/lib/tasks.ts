/**
 * Every long-running editor action in one place.
 *
 * Components stay declarative: they only read the store and call one of these
 * functions. Each task brackets itself with `pending` flags and writes a
 * human-readable line to the activity log, so a failure is never silent.
 */
import {
  exportCapCut,
  exportSubtitles,
  probeVideo,
  remixDub,
  renderVideo,
  synthesizeCue,
  synthesizeDub,
  transcribeVideo,
  translateSegments,
  type DubbingBody,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
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
  // OCR means "read the picture only": there is no speech pass to fill holes in.
  const ocrFill = state.ocrFill && state.sttProvider !== "ocr";
  state.pushLog(
    "info",
    `Đang nhận dạng giọng nói (${state.sttProvider}${state.sttProvider === "local" ? ` · ${state.whisperSize}` : ""}${
      clean ? " · tách giọng trước" : ""
    }${ocrFill ? " · đọc lại đoạn thiếu chữ bằng OCR" : ""})…`,
  );
  try {
    const result = await transcribeVideo({
      video_path: source.path,
      language: state.sourceLanguage,
      provider: state.sttProvider,
      model: state.sttProvider === "local" ? state.whisperSize : undefined,
      vad: true,
      audio_mode: state.audioMode,
      ocr_fill: ocrFill,
    });
    state.setSegments(result.segments);
    state.pushLog(
      "success",
      `Đã tạo ${result.segments.length} dòng phụ đề · ngôn ngữ ${result.language} · ${result.provider}/${result.model} trên ${result.device}`,
    );
    if (result.ocr_filled) {
      state.pushLog(
        "info",
        `OCR đọc thêm ${result.ocr_filled} dòng ở những đoạn có tiếng mà nhận dạng giọng nói để trống.`,
      );
    }
  } catch (error) {
    state.pushLog("error", `Tạo phụ đề thất bại: ${message(error)}`);
  } finally {
    state.endTask("transcribe");
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

  state.beginTask("render");
  try {
    let subtitlePath: string | null = null;
    if (state.segments.length) {
      const assPath = joinPath(directoryOf(output), `${stem(output)}.ass`);
      const ass = await exportSubtitles({
        segments: state.segments,
        output_path: assPath,
        fmt: "ass",
        aspect: state.aspect,
        use_translation: state.segments.some((segment) => segment.translation?.trim()),
        style: state.subtitleStyle,
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
      background_gain: state.duckGain,
      // Without a dub the export keeps the untouched soundtrack, exactly as it
      // came in; with one, the toggle decides whether it survives underneath.
      keep_original_audio: !state.dub || state.keepOriginalAudio,
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
