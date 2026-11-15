import { create } from "zustand";

import { player } from "@/lib/player";
import { DEFAULT_SUBTITLE_STYLE } from "@/types/editor";
import type {
  AiPackStatus,
  Aspect,
  AudioMode,
  DubbingResult,
  HardwareProfile,
  JobState,
  ModelStatus,
  SettingsDocument,
  SttProvider,
  SubtitleSegment,
  SubtitleStyle,
  TranslateProvider,
  TtsProvider,
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
  /**
   * Read the picture again on every stretch that carries sound but produced no
   * line (the reference project's hole pass, answered by OCR instead of a
   * second listen). Engine-wide habit, not part of the project document.
   */
  ocrFill: boolean;
  /** Optional heavy-engine add-on: what is installed and what is running. */
  aiPack: AiPackStatus | null;
  socket: SocketState;
  job: ActiveJob | null;
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
  /** Merge a change into the caption look (marks the document as unsaved). */
  setSubtitleStyle: (patch: Partial<SubtitleStyle>) => void;
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
  "ocrFill",
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
  // OmniVoice: `auto` lets the model pick; a description or `clone:<wav>` also work.
  omnivoice: "auto",
  voicestudio: "alloy",
};

export const useEditor = create<EditorState>((set, get) => ({
  projectPath: null,
  projectName: "",
  projectSavedAt: null,
  projectDirty: false,
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
  // On by default: it costs nothing when the pass finds no hole, and a clip
  // with burned-in subtitles is exactly the case the audio pass loses lines in.
  ocrFill: true,
  aiPack: null,
  socket: "connecting",
  job: null,
  logs: [],
  pending: {},
  sttProvider: "local",
  whisperSize: "medium",
  sourceLanguage: "auto",
  targetLanguage: "vi",
  translateProvider: "local",
  translateStyle: "short-form video subtitle, natural spoken Vietnamese",
  useTranslationForDub: true,
  ttsProvider: "capcut",
  ttsVoice: DEFAULT_VOICES.capcut,
  ttsRate: "+0%",
  keepOriginalAudio: true,
  // 0.35 leaves the music and ambience of the original audible without
  // competing with the voice on top of it; the sidechain dips it further.
  duckGain: 0.35,

  update: (patch) =>
    set(() => {
      const touchesDocument = Object.keys(patch).some((key) => !TRANSIENT_KEYS.has(key));
      if (!touchesDocument) return patch;
      return { ...patch, projectDirty: true };
    }),

  setSource: (source) =>
    set({
      source,
      segments: [],
      selected: null,
      dub: null,
      dubDirty: false,
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
