/**
 * Project documents (`.svaproj`) and the session autosave.
 *
 * Two persistence paths, with different jobs:
 *
 * - `saveCurrentProject` / `openProjectFrom` write and read a file the user
 *   names, through the Tauri dialog. That is the durable copy.
 * - `persistSession` keeps one rolling snapshot in the engine's `projects`
 *   folder so closing the window (or a crash) no longer throws away the
 *   timeline. It is invisible: no dialog, no file name.
 *
 * Both go through `buildProject`, so the document is identical either way.
 */
import { getSession, openProject, putSession, putSessionNow, saveProject, saveQueueItemState } from "@/lib/api";
import {
  baseName,
  isDesktop,
  pickProjectSavePath,
  stem,
  toAssetUrl,
} from "@/lib/media";
import { DEFAULT_VOICES, useEditor, type ProjectSnapshot, type SourceClip } from "@/store/editorStore";
import type {
  Aspect,
  ProjectFile,
  SttProvider,
  TranslateProvider,
  TtsProvider,
} from "@/types/editor";

type Snapshot = ReturnType<typeof useEditor.getState>;

const store = () => useEditor.getState();

const DEFAULT_SETTINGS: ProjectSnapshot["settings"] = {
  sttProvider: "local",
  whisperSize: "medium",
  sourceLanguage: "auto",
  targetLanguage: "vi",
  translateProvider: "google",
  translateStyle: "",
  useTranslationForDub: true,
  ttsProvider: "capcut",
  ttsVoice: DEFAULT_VOICES.capcut,
  ttsRate: "+0%",
  keepOriginalAudio: true,
  duckGain: 0.35,
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function asAspect(value: string | undefined, fallback: Aspect): Aspect {
  return value === "9:16" || value === "16:9" || value === "1:1" ? value : fallback;
}

/** A project saved with a retired engine (edge, voicestudio, …) dubs with CapCut again. */
function asTtsProvider(value: string): TtsProvider {
  return value === "omnivoice" || value === "elevenlabs" || value === "piper" || value === "vieneu"
    ? value
    : "capcut";
}

/* ---------------------------------------------------------------------------
 * Editor state -> document
 * ------------------------------------------------------------------------ */
export function buildProject(state: Snapshot = store()): ProjectFile {
  const { source } = state;
  return {
    format: "shortvideoai-project",
    version: 1,
    name: state.projectName,
    origin: state.projectPath,
    saved_at: state.projectSavedAt,
    source: source
      ? {
          path: source.path,
          name: source.name,
          duration: source.duration,
          width: source.width,
          height: source.height,
          fps: source.fps,
          has_audio: source.hasAudio,
          aspect: source.aspect,
        }
      : null,
    aspect: state.aspect,
    segments: state.segments,
    dub: state.dub,
    subtitle_style: state.subtitleStyle,
    presentation_mode: state.presentationMode,
    secondary_segments: state.secondarySegments,
    secondary_style: state.secondaryStyle,
    layers: state.layers,
    video_clips: state.videoClips,
    background_audio_path: state.backgroundAudioPath,
    voice_gain: state.voiceGain,
    flip_horizontal: state.flipHorizontal,
    speaker_voices: state.speakerVoices,
    quality_profile: state.qualityProfile,
    use_nvenc: state.useNvenc,
    settings: {
      stt_provider: state.sttProvider,
      whisper_size: state.whisperSize,
      source_language: state.sourceLanguage,
      target_language: state.targetLanguage,
      translate_provider: state.translateProvider,
      translate_style: state.translateStyle,
      use_translation_for_dub: state.useTranslationForDub,
      tts_provider: state.ttsProvider,
      tts_voice: state.ttsVoice,
      tts_rate: state.ttsRate,
      keep_original_audio: state.keepOriginalAudio,
      duck_gain: state.duckGain,
    },
    view: {
      playhead: state.playhead,
      preview_subtitles: state.previewSubtitles,
      preview_dub: state.previewDub,
      table_collapsed: state.tableCollapsed,
      timeline_collapsed: state.timelineCollapsed,
    },
  };
}

/** Is there anything worth writing to disk yet? */
export function hasWork(state: Snapshot = store()): boolean {
  return Boolean(state.source || state.segments.length);
}

/** Document title to hand the save dialog, and the engine's default file name. */
export function suggestedName(state: Snapshot = store()): string {
  return state.projectName || (state.source ? stem(state.source.name) : "") || "du-an-moi";
}

/* ---------------------------------------------------------------------------
 * Document -> editor state
 * ------------------------------------------------------------------------ */
async function clipFrom(source: ProjectFile["source"], missing: string[]): Promise<SourceClip | null> {
  if (!source) return null;
  const path = source.path ?? "";
  // A clip that moved away keeps its metadata (timeline length, aspect) but
  // gets no URL, so the preview can explain what is wrong instead of going black.
  const url = path && !missing.includes("video") ? await toAssetUrl(path) : "";
  return {
    path,
    url,
    name: source.name || (path ? baseName(path) : "clip"),
    duration: source.duration,
    width: source.width,
    height: source.height,
    fps: source.fps,
    hasAudio: source.has_audio,
    aspect: asAspect(source.aspect, "9:16"),
  };
}

export async function applyProject(
  document: ProjectFile,
  options: { path?: string | null; missing?: string[]; mode?: "open" | "session" } = {},
): Promise<void> {
  const state = store();
  // Never overwrite work the user already started while the request was in
  // flight (a resumed session must lose to a fresh import).
  if (options.mode === "session" && hasWork(state)) return;
  const missing = options.missing ?? [];
  const settings = { ...DEFAULT_SETTINGS, ...(document.settings ?? {}) };
  const ttsProvider = asTtsProvider(settings.tts_provider);
  const configuredVoice = settings.ttsVoice;

  const hydratedSource = await clipFrom(document.source, missing);
  const hydratedClips =
    Array.isArray(document.video_clips) && document.video_clips.length > 0
      ? document.video_clips.map((c, idx) => ({
          id: c.id || `clip_${idx}`,
          name: c.name || hydratedSource?.name || `Clip ${idx + 1}`,
          source_start: Number(c.source_start) || 0,
          source_end: Number(c.source_end) || (hydratedSource?.duration ?? 1),
        }))
      : undefined;

  state.hydrate({
    source: hydratedSource,
    aspect: asAspect(document.aspect, "9:16"),
    segments: document.segments ?? [],
    // A dub track that vanished (temp was cleaned before the project store
    // adopted it) is dropped instead of pointing the render at a dead path.
    dub: document.dub && !missing.includes("dub") ? document.dub : null,
    subtitleStyle: document.subtitle_style,
    presentationMode: document.presentation_mode,
    secondarySegments: document.secondary_segments,
    secondaryStyle: document.secondary_style,
    layers: document.layers,
    videoClips: hydratedClips,
    backgroundAudioPath: document.background_audio_path ?? null,
    voiceGain: document.voice_gain,
    flipHorizontal: document.flip_horizontal,
    speakerVoices: document.speaker_voices,
    qualityProfile: document.quality_profile,
    useNvenc: document.use_nvenc,
    playhead: document.view?.playhead ?? 0,
    previewSubtitles: document.view?.preview_subtitles ?? true,
    previewDub: document.view?.preview_dub ?? true,
    tableCollapsed: document.view?.table_collapsed ?? false,
    timelineCollapsed: document.view?.timeline_collapsed ?? false,
    settings: {
      sttProvider: settings.stt_provider as SttProvider,
      whisperSize: settings.whisper_size,
      sourceLanguage: settings.source_language,
      targetLanguage: settings.target_language,
      translateProvider: settings.translate_provider as TranslateProvider,
      translateStyle: settings.translate_style,
      useTranslationForDub: settings.use_translation_for_dub,
      ttsProvider,
      ttsVoice: configuredVoice || DEFAULT_VOICES[ttsProvider],
      ttsRate: settings.tts_rate,
      keepOriginalAudio: settings.keep_original_audio,
      duckGain: Math.min(Math.max(settings.duck_gain, 0), 1),
    },
    projectPath: options.path ?? document.origin ?? null,
    projectName: document.name || "",
    projectSavedAt: document.saved_at ?? null,
  });

  const name = document.name || "Dự án";
  const lines = document.segments?.length ?? 0;
  state.pushLog(
    "success",
    options.mode === "session"
      ? `Đã khôi phục phiên làm việc trước · ${name} · ${lines} dòng phụ đề`
      : `Đã mở dự án ${name} · ${lines} dòng phụ đề`,
  );
  if (missing.includes("video")) {
    state.pushLog(
      "warn",
      "Không tìm thấy video gốc của dự án — chọn lại tệp bằng “Nhập video”, phụ đề và lồng tiếng vẫn được giữ.",
    );
  }
  if (missing.includes("dub")) {
    state.pushLog("warn", "Track lồng tiếng của dự án không còn trên đĩa — hãy lồng tiếng lại.");
  }
}

/* ---------------------------------------------------------------------------
 * Save
 * ------------------------------------------------------------------------ */
export async function syncActiveQueueItem(options: { notify?: boolean } = {}) {
  const state = store();
  const itemId = state.activeQueueItemId;
  if (!itemId) return null;
  const snap = await saveQueueItemState(itemId, {
    segments: state.segments,
    dub: state.dub,
    dub_dirty: state.dubDirty,
    aspect: state.aspect,
    subtitle_style: state.subtitleStyle,
    presentation_mode: state.presentationMode,
    secondary_segments: state.secondarySegments,
    secondary_style: state.secondaryStyle,
    layers: state.layers,
    video_clips: state.videoClips,
    background_path: state.backgroundAudioPath,
    keep_original_audio: state.keepOriginalAudio,
    voice_gain: state.voiceGain,
    background_gain: state.duckGain,
    flip_horizontal: state.flipHorizontal,
  });
  state.update({ projectDirty: false });
  if (options.notify) {
    state.pushLog("success", `Đã lưu chỉnh sửa (${state.segments.length} dòng) vào mục Hàng đợi.`);
  }
  return snap;
}

export async function saveCurrentProject(options: { as?: boolean } = {}): Promise<void> {
  const state = store();
  if (!hasWork(state)) {
    state.pushLog("warn", "Chưa có nội dung để lưu.");
    return;
  }

  if (state.activeQueueItemId && !options.as && !state.projectPath) {
    state.beginTask("project");
    try {
      await syncActiveQueueItem({ notify: true });
      void persistSession();
    } catch (error) {
      state.pushLog("error", `Lưu vào Hàng đợi thất bại: ${message(error)}`);
    } finally {
      state.endTask("project");
    }
    return;
  }

  let path = options.as ? null : state.projectPath;
  if (!path && isDesktop) {
    path = await pickProjectSavePath(`${suggestedName(state)}.svaproj`);
    // The dialog was dismissed: keep working, do not shout about it.
    if (!path) return;
  }

  state.beginTask("project");
  try {
    // Re-read the store: the dialog above was modal but the task flag changed.
    const document = buildProject();
    if (!document.name) {
      document.name = path ? stem(path) : state.source ? stem(state.source.name) : "Dự án mới";
    }
    const envelope = await saveProject({ project: document, path });
    state.update({
      projectPath: envelope.path,
      projectName: envelope.project?.name || document.name,
      projectSavedAt: envelope.project?.saved_at ?? null,
      projectDirty: false,
    });
    state.pushLog("success", `Đã lưu dự án → ${envelope.path}`);
    if (envelope.missing.length) {
      state.pushLog("warn", `Dự án trỏ tới tệp không còn tồn tại: ${envelope.missing.join(", ")}.`);
    }
    // Record the new file in the autosave too, so a restart reopens *this*
    // document instead of an unnamed copy.
    void persistSession();
  } catch (error) {
    state.pushLog("error", `Lưu dự án thất bại: ${message(error)}`);
  } finally {
    state.endTask("project");
  }
}

/* ---------------------------------------------------------------------------
 * Open
 * ------------------------------------------------------------------------ */
export async function openProjectFrom(path: string | null | undefined): Promise<void> {
  if (!path) return;
  const state = store();
  if (state.projectDirty && hasWork(state)) {
    const proceed = window.confirm(
      "Bản đang mở có thay đổi chưa lưu. Mở dự án khác và bỏ các thay đổi đó?",
    );
    if (!proceed) return;
  }
  state.beginTask("project");
  try {
    const envelope = await openProject(path);
    if (!envelope.project) throw new Error("Tệp dự án trống.");
    await applyProject(envelope.project, { path: envelope.path, missing: envelope.missing });
  } catch (error) {
    state.pushLog("error", `Mở dự án thất bại: ${message(error)}`);
  } finally {
    state.endTask("project");
  }
}

/** Resume the editor left behind by the previous run. False when there is none. */
export async function restoreLastSession(): Promise<boolean> {
  const envelope = await getSession();
  if (!envelope.project || hasWork(useEditor.getState())) return false;
  await applyProject(envelope.project, { missing: envelope.missing, mode: "session" });
  return true;
}

/* ---------------------------------------------------------------------------
 * Autosave
 * ------------------------------------------------------------------------ */
let sessionInFlight = false;

export async function persistSession(): Promise<void> {
  const state = store();
  if (!hasWork(state) || sessionInFlight) return;
  sessionInFlight = true;
  try {
    await putSession(buildProject(state));
  } catch (error) {
    // Autosave must never interrupt editing; the next change retries.
    console.warn("[project] session autosave failed", error);
  } finally {
    sessionInFlight = false;
  }
}

/** Last-chance write when the window closes (the request outlives the page). */
export function persistSessionNow(): void {
  const state = store();
  if (!hasWork(state)) return;
  putSessionNow(buildProject(state));
}
