import { create } from "zustand";

import { player } from "@/lib/player";
import { DEFAULT_SUBTITLE_STYLE } from "@/types/editor";
import type {
  AiPackStatus,
  Aspect,
  AudioMode,
  ContextAnalyzeResult,
  DubbingResult,
  FitVoiceMode,
  HardwareProfile,
  JobState,
  ModelStatus,
  QualityProfile,
  ScaleMode,
  SettingsDocument,
  SttProvider,
  StylePresetItem,
  SubtitlePresentationMode,
  SubtitleSegment,
  SubtitleStyle,
  TranslateProvider,
  TtsProvider,
  VideoClip,
  VisualLayer,
} from "@/types/editor";

export interface SourceClip {
  /** Absolute Windows path — empty when the clip came from a browser file input. */
  path: string;
  url: string;
  name: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  hasAudio: boolean;
  aspect: Aspect;
}

export interface LogLine {
  id: number;
  level: "info" | "success" | "warn" | "error";
  text: string;
  ts: number;
}

export interface ActiveJob {
  id: string;
  kind: string;
  stage: string;
  progress: number;
  message: string;
  state: JobState;
}

export type SocketState = "connecting" | "open" | "closed";

/** Which left column the sidebar shows; panels can jump (see `SettingsLink`). */
export type SidebarTab = "media" | "ai" | "models" | "settings" | "log";

/** Everything a project document restores, already mapped to editor shapes. */
export interface ProjectSnapshot {
  source: SourceClip | null;
  aspect: Aspect;
  segments: SubtitleSegment[];
  dub: DubbingResult | null;
  /** Caption look; projects saved before it existed fall back to the default. */
  subtitleStyle?: SubtitleStyle;
  presentationMode?: SubtitlePresentationMode;
  secondarySegments?: SubtitleSegment[];
  secondaryStyle?: SubtitleStyle;
  layers?: VisualLayer[];
  videoClips?: VideoClip[];
  backgroundAudioPath?: string | null;
  voiceGain?: number;
  flipHorizontal?: boolean;
  scaleMode?: ScaleMode;
  fitVoiceMode?: FitVoiceMode;
  brightness?: number;
  contrast?: number;
  saturation?: number;
  speakerVoices?: Record<string, string>;
  qualityProfile?: QualityProfile;
  useNvenc?: boolean;
  playhead: number;
  previewSubtitles: boolean;
  previewDub: boolean;
  tableCollapsed: boolean;
  timelineCollapsed: boolean;
  settings: {
    sttProvider: SttProvider;
    whisperSize: string;
    sourceLanguage: string;
    targetLanguage: string;
    translateProvider: TranslateProvider;
    translateStyle: string;
    useTranslationForDub: boolean;
    ttsProvider: TtsProvider;
    ttsVoice: string;
    ttsRate: string;
    keepOriginalAudio: boolean;
    duckGain: number;
  };
  /** `.svaproj` this work belongs to; null while it has never been saved. */
  projectPath: string | null;
  projectName: string;
  projectSavedAt: string | null;
}

interface EditorState {
  // project document
  /** `.svaproj` the editor is bound to; null until the first save. */
  projectPath: string | null;
  projectName: string;
  projectSavedAt: string | null;
  /** True when the document on disk is older than what is on screen. */
  projectDirty: boolean;
  /** Queue item currently loaded via "Xem lại" so edits save back to the batch queue. */
  activeQueueItemId: string | null;
  // project
  source: SourceClip | null;
  aspect: Aspect;
  // subtitles
  segments: SubtitleSegment[];
  selected: number | null;
  subtitlePath: string | null;
  /** Font, colours and placement of the captions; drives preview and export. */
  subtitleStyle: SubtitleStyle;
  // dubbing
  dub: DubbingResult | null;
  dubDirty: boolean;
  // transport
  playhead: number;
  playing: boolean;
  zoom: number;
  followPlayhead: boolean;
  previewSubtitles: boolean;
  previewDub: boolean;
  tableCollapsed: boolean;
  timelineCollapsed: boolean;
  // engine
  hardware: { profile: HardwareProfile; device: "cuda" | "cpu" } | null;
  engineOnline: boolean | null;
  sidebarTab: SidebarTab;
  models: ModelStatus[];
  modelsPath: string;
  /**
   * The engine's settings document (`providers.json`): online keys, model
   * names, OCR profile and the audio mode. App-wide, not part of a project.
   */
  settings: SettingsDocument | null;
  /** Mirror of `settings.audio.mode` so `runTranscribe` can send it. */
  audioMode: AudioMode;
  /** Optional heavy-engine add-on: what is installed and what is running. */
  aiPack: AiPackStatus | null;
  socket: SocketState;
  job: ActiveJob | null;
  lastExport: {
    output_path: string;
    width: number;
    height: number;
    size_bytes: number;
    elapsed_s: number;
  } | null;
  logs: LogLine[];
  pending: Record<string, boolean>;
  // tool settings
  sttProvider: SttProvider;
  whisperSize: string;
  sourceLanguage: string;
  targetLanguage: string;
  translateProvider: TranslateProvider;
  translateStyle: string;
  useTranslationForDub: boolean;
  ttsProvider: TtsProvider;
  ttsVoice: string;
  ttsRate: string;
  /** Keep the clip's own soundtrack under the dub, turned down to `duckGain`. */
  keepOriginalAudio: boolean;
  /** Level of the original track while the dub speaks (0 = mute, 1 = untouched). */
  duckGain: number;
  voiceGain: number;
  backgroundAudioPath: string | null;
  // CapCap v9.0.1 ASR & Diarization settings
  dedupEnabled: boolean;
  dedupSimilarity: number;
  hallucinationFilter: boolean;
  hallucinationSilence: number;
  diarize: boolean;
  numSpeakers: number;
  // CapCap Translation & Context settings
  translatePreset: string;
  autoContext: boolean;
  contextGuidance: string;
  contextResult: ContextAnalyzeResult | null;
  // CapCap Bilingual & Style Presets
  presentationMode: SubtitlePresentationMode;
  secondarySegments: SubtitleSegment[];
  secondaryStyle: SubtitleStyle;
  activeSubtitleTrack: 1 | 2;
  stylePresets: StylePresetItem[];
  // CapCap Multi-Speaker TTS & Audio Policy
  speakerVoices: Record<string, string>;
  trimSilence: boolean;
  fitVoiceMode: FitVoiceMode;
  // CapCap Visual Layers & Render settings
  layers: VisualLayer[];
  selectedLayerId: string | null;
  flipHorizontal: boolean;
  scaleMode: ScaleMode;
  brightness: number;
  contrast: number;
  saturation: number;
  qualityProfile: QualityProfile;
  useNvenc: boolean;
  selectionRange: [number, number] | null;
  videoClips: VideoClip[];
  selectedClipId: string | null;
  cutUndoStack: Array<{
    videoClips: VideoClip[];
    segments: SubtitleSegment[];
    secondarySegments: SubtitleSegment[];
    layers: VisualLayer[];
    dub?: DubbingResult | null;
  }>;
  // actions
  update: (patch: Partial<EditorState>) => void;
  setSource: (source: SourceClip | null) => void;
  /** Point a restored project at a clip that was re-picked on disk. */
  relinkSource: (source: SourceClip) => void;
  hydrate: (snapshot: ProjectSnapshot) => void;
  setSegments: (segments: SubtitleSegment[]) => void;
  /** Adopt the engine's settings document (boot, save, or a finished download). */
  setSettings: (settings: SettingsDocument) => void;
  upsertSegment: (segment: SubtitleSegment) => void;
  patchSegment: (index: number, patch: Partial<SubtitleSegment>) => void;
  restoreSegmentTiming: () => void;
  /** Merge a change into the caption look (marks the document as unsaved). */
  setSubtitleStyle: (patch: Partial<SubtitleStyle>) => void;
  setSecondaryStyle: (patch: Partial<SubtitleStyle>) => void;
  addLayer: (layer: VisualLayer) => void;
  patchLayer: (id: string, patch: Partial<VisualLayer>) => void;
  removeLayer: (id: string) => void;
  removeSegment: (index: number) => void;
  setPlayhead: (seconds: number, options?: { seek?: boolean }) => void;
  setPlaying: (playing: boolean) => void;
  setDub: (dub: DubbingResult | null) => void;
  setJob: (job: ActiveJob | null) => void;
  setSocket: (socket: SocketState) => void;
  setSidebarTab: (tab: SidebarTab) => void;
  pushLog: (level: LogLine["level"], text: string) => void;
  clearLogs: () => void;
  beginTask: (task: string) => void;
  endTask: (task: string) => void;
}

let logId = 0;

/**
 * Fields that are not part of the saved document. `update()` flips
 * `projectDirty` for every other key, so scrubbing the timeline during
 * playback never marks the project as changed.
 */
const TRANSIENT_KEYS = new Set<string>([
  "playhead",
  "playing",
  "zoom",
  "followPlayhead",
  "selected",
  "hardware",
  "engineOnline",
  "sidebarTab",
  "models",
  "modelsPath",
  "settings",
  "audioMode",
  "aiPack",
  "socket",
  "job",
  "logs",
  "pending",
  "projectPath",
  "projectName",
  "projectSavedAt",
  "projectDirty",
]);

const DEFAULT_VOICES: Record<TtsProvider, string> = {
  // CapCut voice *type* — the backend maps it to the cloud resource id.
  capcut: "BV421_vivn_streaming",
  elevenlabs: "21m00Tcm4TlvDq8ikWAM",
  // OmniVoice: `auto` lets the model pick; a description or `clone:<wav>` also work.
  omnivoice: "auto",
  piper: "vi_VN-vais1000-medium",
  vieneu: "Ngọc Huyền",
};

const DEFAULT_SECONDARY_STYLE: SubtitleStyle = {
  ...DEFAULT_SUBTITLE_STYLE,
  size: 0.78,
  color: "#FFE082",
  margin_v: 15,
};

function loadSavedStylePresets(): StylePresetItem[] {
  try {
    const raw = window.localStorage.getItem("sva_style_presets");
    if (raw) return JSON.parse(raw) as StylePresetItem[];
  } catch {
    // ignore storage errors
  }
  return [
    { id: "default", name: "Mặc định (Vàng/Trắng)", style: DEFAULT_SUBTITLE_STYLE },
    {
      id: "drama_gold",
      name: "Phim ngắn Vàng rực",
      style: { ...DEFAULT_SUBTITLE_STYLE, color: "#FFD54F", outline: 1.5, background: false },
    },
    {
      id: "clean_box",
      name: "Hộp đen tối giản",
      style: { ...DEFAULT_SUBTITLE_STYLE, outline: 0, background: true, background_opacity: 0.65 },
    },
  ];
}

export const useEditor = create<EditorState>((set, get) => ({
  projectPath: null,
  projectName: "",
  projectSavedAt: null,
  projectDirty: false,
  activeQueueItemId: null,
  source: null,
  aspect: "9:16",
  segments: [],
  selected: null,
  subtitlePath: null,
  subtitleStyle: DEFAULT_SUBTITLE_STYLE,
  dub: null,
  dubDirty: false,
  playhead: 0,
  playing: false,
  zoom: 70,
  followPlayhead: true,
  previewSubtitles: true,
  previewDub: true,
  tableCollapsed: false,
  timelineCollapsed: false,
  hardware: null,
  engineOnline: null,
  sidebarTab: "ai",
  models: [],
  modelsPath: "",
  settings: null,
  audioMode: "fast",
  aiPack: null,
  socket: "connecting",
  job: null,
  lastExport: null,
  logs: [],
  pending: {},
  sttProvider: "local",
  whisperSize: "medium",
  sourceLanguage: "auto",
  targetLanguage: "vi",
  translateProvider: "google",
  translateStyle: "short-form video subtitle, natural spoken Vietnamese",
  useTranslationForDub: true,
  ttsProvider: "capcut",
  ttsVoice: DEFAULT_VOICES.capcut,
  ttsRate: "+0%",
  keepOriginalAudio: true,
  // 0.35 leaves the music and ambience of the original audible without
  // competing with the voice on top of it; the sidechain dips it further.
  duckGain: 0.35,
  voiceGain: 1.0,
  backgroundAudioPath: null,
  dedupEnabled: true,
  dedupSimilarity: 0.82,
  hallucinationFilter: true,
  hallucinationSilence: 1.2,
  diarize: false,
  numSpeakers: 0,
  translatePreset: "general_default",
  autoContext: true,
  contextGuidance: "",
  contextResult: null,
  presentationMode: "single",
  secondarySegments: [],
  secondaryStyle: DEFAULT_SECONDARY_STYLE,
  activeSubtitleTrack: 1,
  stylePresets: loadSavedStylePresets(),
  speakerVoices: {},
  trimSilence: false,
  fitVoiceMode: "off",
  layers: [],
  selectedLayerId: null,
  flipHorizontal: false,
  scaleMode: "fill",
  brightness: 0,
  contrast: 1,
  saturation: 1,
  qualityProfile: "high",
  useNvenc: false,
  selectionRange: null,
  videoClips: [],
  selectedClipId: null,
  cutUndoStack: [],

  update: (patch) =>
    set(() => {
      const touchesDocument = Object.keys(patch).some((key) => !TRANSIENT_KEYS.has(key));
      if (!touchesDocument) return patch;
      return { ...patch, projectDirty: true };
    }),

  setSource: (source) =>
    set({
      source,
      videoClips: source
        ? [{ id: "clip_0", name: source.name, source_start: 0, source_end: source.duration }]
        : [],
      selectedClipId: null,
      cutUndoStack: [],
      segments: [],
      selected: null,
      dub: null,
      dubDirty: false,
      backgroundAudioPath: null,
      subtitlePath: null,
      playhead: 0,
      playing: false,
      aspect: source?.aspect ?? get().aspect,
      // A newly imported clip starts a new document: the previous project file
      // still holds its own timeline and must not be overwritten by accident.
      projectPath: null,
      projectName: "",
      projectSavedAt: null,
      projectDirty: true,
      activeQueueItemId: null,
    }),

  relinkSource: (source) => set({ source, projectDirty: true }),

  hydrate: (snapshot) =>
    set({
      source: snapshot.source,
      aspect: snapshot.aspect,
      segments: [...snapshot.segments].sort((a, b) => a.start - b.start),
      selected: snapshot.segments[0]?.index ?? null,
      subtitlePath: null,
      subtitleStyle: snapshot.subtitleStyle ?? get().subtitleStyle,
      presentationMode: snapshot.presentationMode ?? "single",
      secondarySegments: snapshot.secondarySegments
        ? [...snapshot.secondarySegments].sort((a, b) => a.start - b.start)
        : [],
      secondaryStyle: snapshot.secondaryStyle ?? get().secondaryStyle,
      layers: snapshot.layers ?? [],
      videoClips:
        snapshot.videoClips && snapshot.videoClips.length > 0
          ? snapshot.videoClips
          : snapshot.source
            ? [
                {
                  id: "clip_0",
                  name: snapshot.source.name,
                  source_start: 0,
                  source_end: snapshot.source.duration,
                },
              ]
            : [],
      selectedClipId: null,
      selectedLayerId: null,
      cutUndoStack: [],
      backgroundAudioPath: snapshot.backgroundAudioPath ?? null,
      voiceGain: snapshot.voiceGain ?? 1.0,
      flipHorizontal: snapshot.flipHorizontal ?? false,
      scaleMode: snapshot.scaleMode ?? "fill",
      fitVoiceMode: snapshot.fitVoiceMode ?? "off",
      brightness: snapshot.brightness ?? 0,
      contrast: snapshot.contrast ?? 1,
      saturation: snapshot.saturation ?? 1,
      speakerVoices: snapshot.speakerVoices ?? {},
      qualityProfile: snapshot.qualityProfile ?? get().qualityProfile,
      useNvenc: snapshot.useNvenc ?? get().useNvenc,
      dub: snapshot.dub,
      dubDirty: false,
      playhead: snapshot.playhead,
      playing: false,
      previewSubtitles: snapshot.previewSubtitles,
      previewDub: snapshot.previewDub,
      tableCollapsed: snapshot.tableCollapsed,
      timelineCollapsed: snapshot.timelineCollapsed,
      sttProvider: snapshot.settings.sttProvider,
      whisperSize: snapshot.settings.whisperSize,
      sourceLanguage: snapshot.settings.sourceLanguage,
      targetLanguage: snapshot.settings.targetLanguage,
      translateProvider: snapshot.settings.translateProvider,
      translateStyle: snapshot.settings.translateStyle,
      useTranslationForDub: snapshot.settings.useTranslationForDub,
      ttsProvider: snapshot.settings.ttsProvider,
      ttsVoice: snapshot.settings.ttsVoice,
      ttsRate: snapshot.settings.ttsRate,
      keepOriginalAudio: snapshot.settings.keepOriginalAudio,
      duckGain: snapshot.settings.duckGain,
      projectPath: snapshot.projectPath,
      projectName: snapshot.projectName,
      projectSavedAt: snapshot.projectSavedAt,
      projectDirty: false,
      activeQueueItemId: null,
    }),

  setSegments: (segments) =>
    set({
      segments: [...segments].sort((a, b) => a.start - b.start),
      // New lines invalidate the previous dub track (the text it was spoken from).
      dub: null,
      dubDirty: false,
      selected: segments.length ? (get().selected ?? segments[0].index) : null,
      projectDirty: true,
    }),

  setSettings: (settings) => set({ settings, audioMode: settings.audio.mode }),

  upsertSegment: (segment) =>
    set((state) => {
      const existing = state.segments.findIndex((item) => item.index === segment.index);
      if (existing === -1) {
        return {
          segments: [...state.segments, segment].sort((a, b) => a.start - b.start),
          projectDirty: true,
        };
      }
      const next = [...state.segments];
      next[existing] = { ...next[existing], ...segment };
      return { segments: next, projectDirty: true };
    }),

  patchSegment: (index, patch) =>
    set((state) => {
      const position = state.segments.findIndex((item) => item.index === index);
      if (position === -1) return {};
      const next = [...state.segments];
      const merged = { ...next[position], ...patch };
      if (merged.end <= merged.start) merged.end = merged.start + 0.2;
      next[position] = merged;
      return { segments: next, dubDirty: state.dub !== null, projectDirty: true };
    }),

  setSubtitleStyle: (patch) =>
    set((state) => ({ subtitleStyle: { ...state.subtitleStyle, ...patch }, projectDirty: true })),

  setSecondaryStyle: (patch) =>
    set((state) => ({ secondaryStyle: { ...state.secondaryStyle, ...patch }, projectDirty: true })),

  restoreSegmentTiming: () =>
    set((state) => ({
      segments: state.segments.map((seg) => ({
        ...seg,
        start: seg.original_start ?? seg.start,
        end: seg.original_end ?? seg.end,
      })),
      projectDirty: true,
    })),

  addLayer: (layer) =>
    set((state) => ({
      layers: [...state.layers, layer],
      selectedLayerId: layer.id,
      projectDirty: true,
    })),

  patchLayer: (id, patch) =>
    set((state) => ({
      layers: state.layers.map((l) => (l.id === id ? { ...l, ...patch } : l)),
      projectDirty: true,
    })),

  removeLayer: (id) =>
    set((state) => ({
      layers: state.layers.filter((l) => l.id !== id),
      selectedLayerId: state.selectedLayerId === id ? null : state.selectedLayerId,
      projectDirty: true,
    })),

  removeSegment: (index) =>
    set((state) => {
      const next = state.segments
        .filter((item) => item.index !== index)
        .map((item, position) => ({ ...item, index: position + 1 }));
      return {
        segments: next,
        dub: null,
        dubDirty: false,
        selected: state.selected === index ? (next[0]?.index ?? null) : state.selected,
        projectDirty: true,
      };
    }),

  setPlayhead: (seconds, options) => {
    const clamped = Math.max(seconds, 0);
    if (options?.seek) player.seek(clamped);
    set({ playhead: clamped });
  },

  setPlaying: (playing) => set({ playing }),
  setDub: (dub) => set({ dub, dubDirty: false, projectDirty: true }),
  setJob: (job) => set({ job }),
  setSocket: (socket) => set({ socket }),
  setSidebarTab: (sidebarTab) => set({ sidebarTab }),

  pushLog: (level, text) =>
    set((state) => ({
      logs: [...state.logs.slice(-119), { id: ++logId, level, text, ts: Date.now() }],
    })),

  clearLogs: () => set({ logs: [] }),

  beginTask: (task) => set((state) => ({ pending: { ...state.pending, [task]: true } })),
  endTask: (task) =>
    set((state) => {
      const next = { ...state.pending };
      delete next[task];
      return { pending: next };
    }),
}));

/** Subtitle line covering the playhead (overlay + table highlight). */
export function selectActiveSegment(state: EditorState): SubtitleSegment | null {
  const time = state.playhead;
  return state.segments.find((segment) => time >= segment.start && time < segment.end) ?? null;
}

/** True while any long-running task is in flight (disables the AI buttons). */
export function selectBusy(state: EditorState): boolean {
  return Object.keys(state.pending).length > 0;
}

/** The words a take for this cue should speak right now. */
function cueSpeech(segment: SubtitleSegment, useTranslation: boolean): string {
  const translated = useTranslation ? segment.translation?.trim() : "";
  return (translated || segment.text).trim();
}

/**
 * Cues whose words changed after their take was recorded.
 *
 * `DubbingSegmentReport.source` is the text handed to the voice before any
 * timing rewrite, so this comparison stays exact for shortened lines too.
 * Projects dubbed by an older build carry no `source` and report nothing,
 * which leaves the plain "needs a re-dub" flag in charge.
 */
export function staleCues(
  segments: SubtitleSegment[],
  dub: DubbingResult | null,
  useTranslation: boolean,
): Set<number> {
  const stale = new Set<number>();
  if (!dub) return stale;
  const byIndex = new Map(segments.map((segment) => [segment.index, segment]));
  for (const report of dub.segments) {
    const segment = byIndex.get(report.index);
    if (!segment || !report.source) continue;
    if (cueSpeech(segment, useTranslation) !== report.source.trim()) stale.add(report.index);
  }
  return stale;
}

export { DEFAULT_VOICES };
