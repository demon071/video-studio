/**
 * TypeScript mirror of `backend/app/schemas/*.py`.
 *
 * Keep field names identical so a JSON payload can be dropped straight into the
 * store without a mapping layer. The Python side is the source of truth.
 */

export type Aspect = "9:16" | "16:9" | "1:1";
/** `capcut` is cloud/PC recogniser, `ocr` reads burned-in subtitles, `local` is Whisper, `sensevoice` is offline CPU/ONNX. */
export type SttProvider = "local" | "sensevoice" | "capcut" | "ocr";
/** `google` / `bing` are the free web translators (no key); `deepseek` / `ollama` / `gemini` / `openai` from CapCap. */
export type TranslateProvider = "gemini" | "openai" | "deepseek" | "ollama" | "google" | "bing";
/** `capcut` & `elevenlabs` are cloud, `omnivoice`, `piper` & `vieneu` run offline (all ported from CapCap). */
export type TtsProvider = "capcut" | "elevenlabs" | "omnivoice" | "piper" | "vieneu";

export type SubtitlePresentationMode = "none" | "single" | "bilingual";
export type QualityProfile = "high" | "balanced" | "low";
export type LayerKind = "mask" | "blur" | "logo" | "text";

export interface VideoClip {
  id: string;
  name: string;
  source_start: number;
  source_end: number;
}

export interface VisualLayer {
  id: string;
  kind: LayerKind;
  label: string;
  start: number;
  end: number;
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
  opacity: number;
  blur_strength?: number;
  text: string;
  font_size: number;
  image_path?: string | null;
}

export interface TranslationPreset {
  id: string;
  name: string;
  description: string;
  category?: string;
}

export interface ContextCharacter {
  id: string;
  name: string;
  gender: string;
  role: string;
  self_pronoun: string;
  address_others: string;
}

export interface ContextAnalyzeResult {
  summary: string;
  characters: ContextCharacter[];
  pronoun_rules: Array<Record<string, string>>;
  term_glossary: Array<Record<string, string>>;
  context_guidance: string;
}

export interface SubtitleSegment {
  index: number;
  start: number;
  end: number;
  text: string;
  translation?: string | null;
  speaker?: string | null;
  no_speech_prob?: number | null;
  original_start?: number | null;
  original_end?: number | null;
  voice?: string | null;
  speed?: number | null;
  highlights?: Array<Record<string, unknown>> | null;
}

export interface ProbeResult {
  video_path: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  has_audio: boolean;
  aspect: Aspect;
}

export interface TranscribeResult {
  language: string;
  duration: number;
  segments: SubtitleSegment[];
  provider: string;
  model: string;
  device: string;
}

export interface TranslateResult {
  target_language: string;
  provider: string;
  model: string;
  segments: SubtitleSegment[];
}

/** One frame of a cue re-read (`POST /api/subtitles/ocr-frame`). */
export interface OcrFrameSample {
  time: number;
  text: string;
}

export interface OcrFrameResult {
  /** The line the frames agreed on; empty when nothing could be read. */
  text: string;
  /** Distinct readings, best first — the dialog offers them as choices. */
  candidates: string[];
  frames: OcrFrameSample[];
  engine: string;
  /** Where the reader really ran: `cpu` also when a GPU was requested. */
  device: "gpu" | "cpu";
  start: number;
  end: number;
  /** The cropped still the reader saw, so a wrong scan region is visible. */
  frame_path?: string | null;
  /** Scan rectangle used, `[left, top, right, bottom]` in percent of the frame. */
  region: number[];
}

export interface DubbingSegmentReport {
  index: number;
  start: number;
  end: number;
  target_duration: number;
  tts_duration: number;
  applied_ratio: number;
  clamped: boolean;
  overflow_s: number;
  /** The line was reworded so it fits the slot instead of being sped up. */
  rewritten?: boolean;
  /** Text actually sent to the voice (differs from the subtitle when rewritten). */
  spoken?: string | null;
  /** Line text before any timing rewrite; differs from the cue once the cue was edited. */
  source?: string | null;
  /** The fitted WAV of this line, kept so it can be replayed or re-assembled. */
  clip_path?: string | null;
}

export interface DubbingResult {
  track_path: string;
  duration: number;
  provider: string;
  voice: string;
  segments: DubbingSegmentReport[];
  skipped: number;
  rewritten?: number;
}

/** One re-spoken cue (`POST /api/dubbing/cue`). */
export interface DubbingCueResult {
  index: number;
  report: DubbingSegmentReport;
  provider: string;
  voice: string;
  audition: boolean;
}

export interface DubbingRemixResult {
  track_path: string;
  duration: number;
  lines: number;
}

/**
 * Caption look — mirror of `SubtitleStyle`. The preview overlay and the burned-in
 * ASS read the same numbers, so what is on screen is what gets exported.
 */
export interface SubtitleStyle {
  font: string;
  /** Multiplier on the frame-derived size (1 = 4.2 % of the height). */
  size: number;
  color: string;
  outline_color: string;
  /** Edge thickness multiplier; 0 turns the edge off. */
  outline: number;
  bold: boolean;
  background: boolean;
  background_color: string;
  background_opacity: number;
  position: "top" | "middle" | "bottom";
  horizontal: "left" | "center" | "right";
  /** Percent of the height from the chosen edge. */
  margin_v: number;
  /** Percent of the width from the sides. */
  margin_h: number;
  highlight_mode?: "none" | "color" | "scale";
  highlight_color?: string;
}

export interface StylePresetItem {
  id: string;
  name: string;
  style: SubtitleStyle;
}

export interface QueueItem {
  id: string;
  video_path: string;
  title: string;
  status: "pending" | "processing" | "ready_to_export" | "completed" | "error" | "stopped";
  progress: number;
  stage: string;
  error?: string | null;
  output_path?: string | null;
  work_dir?: string | null;
  source_mode: string;
  stt_provider?: string;
  target_language: string;
  translate_provider: string;
  translate_preset: string;
  tts_provider: string;
  tts_voice?: string | null;
  aspect: string;
  bgm_mode?: string;
  bgm_path?: string | null;
  segments_count?: number;
}

export interface QueuePreviewResult {
  item: QueueItem;
  video_path: string;
  segments: SubtitleSegment[];
  dub: DubbingResult | null;
}

export interface QueueSnapshot {
  running: boolean;
  stop_after_current: boolean;
  current_id?: string | null;
  items: QueueItem[];
}

export const DEFAULT_SUBTITLE_STYLE: SubtitleStyle = {
  font: "Arial",
  size: 1,
  color: "#FFFFFF",
  outline_color: "#101010",
  outline: 1,
  bold: true,
  background: true,
  background_color: "#000000",
  background_opacity: 0.45,
  position: "bottom",
  horizontal: "center",
  margin_v: 7,
  margin_h: 6,
};

export interface RenderResult {
  output_path: string;
  duration: number;
  size_bytes: number;
  width: number;
  height: number;
  elapsed_s: number;
}

/* -------------------------------------------------------------------------
 * CapCut hand-over — mirror of `backend/app/schemas/capcut.py`.
 *
 * A draft is a folder of JSON documents CapCut Desktop opens natively, so the
 * project keeps its tracks (clip, captions, dub) instead of being flattened.
 * ---------------------------------------------------------------------- */

/** Where a draft would land, and whether CapCut is installed right now. */
export interface CapCutTargets {
  /** CapCut's own drafts folder, `null` when the app is not installed. */
  drafts_root: string | null;
  exists: boolean;
  /** Where the draft goes when CapCut is not installed. */
  fallback_root: string;
  capcut_version: string | null;
  capcut_running: boolean;
  /** Projects already listed in CapCut's index. */
  drafts: number;
}

export interface CapCutExportResult {
  draft_path: string;
  draft_name: string;
  width: number;
  height: number;
  duration: number;
  tracks: number;
  captions: number;
  dub_lines: number;
  media_files: number;
  /** The draft was appended to CapCut's project list. */
  registered: boolean;
  /** Backup of the index taken before it was touched. */
  index_backup: string | null;
  /** What could not be translated exactly (approximate positions, fonts…). */
  warnings: string[];
}

export interface ModelStatus {
  key: string;
  /** `ocr` holds the PP-OCR detector/recogniser pairs used for burned-in subs. */
  kind: "stt" | "llm" | "tts" | "separator" | "ocr";
  label: string;
  description: string;
  size_mb: number;
  downloaded: boolean;
  installed_mb: number;
  path: string;
  missing_packages: string[];
  licence: string;
  /**
   * Set for models that have nothing to download (OmniVoice ships with its pip
   * package): the panel shows the command instead of a dead "Tải về" button.
   */
  pip_hint?: string;
}

/** Runtime of the *running* engine, as reported by `GET /api/ai-pack`. */
export interface AiPackStatus {
  installed: boolean;
  path: string;
  engine_path: string | null;
  manifest: Record<string, unknown> | null;
  version: string | null;
  profile: string | null;
  size_mb: number;
  ai_available: boolean;
  running_from_pack: boolean;
  engine: { path: string; frozen: boolean; ai: boolean };
  staging: boolean;
  download_url: string;
  free_disk_gb: number;
  min_free_gb: number;
  needs_restart: boolean;
}

export interface HardwareProfile {
  device: string;
  gpu_name: string | null;
  vram_total_gb: number;
  vram_free_gb: number;
  cuda_version: string | null;
  cpu_count: number;
  ram_total_gb: number;
  preset: string;
  whisper_model: string;
  whisper_compute_type: string;
  llm_gpu_layers: number;
  separator_device: string;
  notes: string[];
}

export interface HardwareReport {
  profile: HardwareProfile;
  device: "cuda" | "cpu";
  whisper: { model: string; compute_type: string };
  llm: { model: string; gpu_layers: number };
  tts: { provider: string; voice: string };
  paths: { data: string; models: string; renders: string; temp: string };
  ffmpeg: string | null;
  ffprobe: string | null;
  api_keys: Record<string, boolean>;
}

export type JobState = "queued" | "running" | "done" | "error" | "cancelled";

export interface JobInfo {
  id: string;
  kind: string;
  state: JobState;
  stage: string;
  progress: number;
  message?: string | null;
  error?: string | null;
  result?: Record<string, unknown> | null;
}

export type FrameType =
  | "hello"
  | "progress"
  | "subtitle"
  | "log"
  | "result"
  | "error"
  | "ping";

export interface ProgressFrame {
  type: FrameType;
  job_id?: string | null;
  kind?: string | null;
  stage?: string | null;
  progress?: number | null;
  message?: string | null;
  segment?: SubtitleSegment | null;
  data?: Record<string, unknown> | null;
  ts: number;
}

export interface HelloPayload {
  version: string;
  hardware: HardwareProfile;
  device: "cuda" | "cpu";
  models: ModelStatus[];
  whisper_cache: string[];
}

/* -------------------------------------------------------------------------
 * Project documents (`.svaproj`) — mirror of `backend/app/schemas/project.py`.
 * ---------------------------------------------------------------------- */

export interface ProjectSource {
  path: string;
  name: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  has_audio: boolean;
  aspect: string;
}

export interface ProjectSettings {
  stt_provider: string;
  whisper_size: string;
  source_language: string;
  target_language: string;
  translate_provider: string;
  translate_style: string;
  use_translation_for_dub: boolean;
  tts_provider: string;
  tts_voice: string;
  tts_rate: string;
  keep_original_audio: boolean;
  duck_gain: number;
}

export interface ProjectView {
  playhead: number;
  preview_subtitles: boolean;
  preview_dub: boolean;
  table_collapsed: boolean;
  timeline_collapsed: boolean;
}

export interface ProjectFile {
  format: "shortvideoai-project";
  version: number;
  name: string;
  /** The `.svaproj` this document was last written to (stamped by the engine). */
  origin?: string | null;
  saved_at?: string | null;
  source: ProjectSource | null;
  aspect: string;
  segments: SubtitleSegment[];
  dub: DubbingResult | null;
  /** Caption look, so a reopened project burns in the same subtitles. */
  subtitle_style?: SubtitleStyle;
  settings: ProjectSettings;
  view: ProjectView;
}

export interface ProjectEnvelope {
  path: string | null;
  project: ProjectFile | null;
  /** Assets the document points at that no longer exist: "video", "dub". */
  missing: string[];
}

export interface ProjectSummary {
  path: string;
  name: string;
  saved_at: string | null;
  modified: number;
  size_bytes: number;
  damaged: boolean;
  cached?: boolean;
}

export interface ProjectListResult {
  projects: ProjectSummary[];
  cached_projects?: ProjectSummary[];
  folder: string;
}

/* -------------------------------------------------------------------------
 * Settings document — mirror of `provider_config.describe()`.
 *
 * This is "the place to enter a key and a model name": the engine persists it
 * in `<data>/settings/providers.json` and every service overlays it at call
 * time. Secrets never come back out: the UI only learns `api_key_set`.
 * ---------------------------------------------------------------------- */

/** One model field of a provider (`model`, `stt_model`, …). */
export interface SettingSlot {
  key: string;
  label: string;
  /** Suggested values; the field is free text so a new model works immediately. */
  presets: string[];
  value: string;
  default: string;
  /** Environment variable the engine would fall back to, when one is set. */
  from_env: string;
}

export type ProviderKind = "translate" | "stt" | "tts";

export interface ProviderConfig {
  name: string;
  label: string;
  kind: ProviderKind;
  key_hint: string;
  key_url: string;
  base_url: string;
  base_url_default: string;
  notes: string;
  api_key_set: boolean;
  /** The key currently in effect came from the environment, not from the UI. */
  api_key_from_env: boolean;
  slots: SettingSlot[];
}

export type OcrProfileName = "small" | "tiny" | "medium" | "server";

export interface OcrProfileInfo {
  name: OcrProfileName;
  label: string;
  speed: string;
  detector: string;
  recognizer: string;
  note: string;
  /** Weights already on disk; `small` ships with the engine. */
  installed: boolean;
}

/** One ONNX Runtime distribution. All three provide the same `onnxruntime`. */
export interface OnnxPack {
  /** `onnxruntime` | `onnxruntime-directml` | `onnxruntime-gpu`. */
  name: string;
  label: string;
  /** Exact `pip install` command that swaps the wheel. */
  command: string;
  installed: boolean;
}

export interface OnnxRuntimeInfo {
  installed: boolean;
  /** Which distribution is installed (empty when none is). */
  distribution: string;
  version: string;
  providers: string[];
  /** True when a GPU provider is present, i.e. GPU OCR is possible at all. */
  gpu_ready: boolean;
  /** `CUDAExecutionProvider` or `DmlExecutionProvider`, empty on CPU. */
  gpu_provider: string;
  packs: OnnxPack[];
}

export interface OcrConfig {
  engine: string;
  profile: string;
  engines: string[];
  /** Where the reader runs; `"auto"` prefers the GPU and falls back to CPU. */
  device: string;
  /** What will really run: asking for a GPU on a CPU-only wheel gives `"cpu"`. */
  device_actual: "gpu" | "cpu";
  devices: string[];
  /** Execution providers the installed onnxruntime exposes (empty when absent). */
  onnx_providers: string[];
  /** The installed ONNX Runtime wheel and the commands that swap it. */
  onnx: OnnxRuntimeInfo;
  profiles: OcrProfileInfo[];
  /** Scan rectangle `[left, top, right, bottom]` in percent of the frame. */
  region: [number, number, number, number];
  /** False while the rectangle still comes from the default bottom band. */
  region_stored: boolean;
  /** Smallest width/height the engine accepts, in percent. */
  region_span: number;
  position: string;
  band: number;
  model_dir: string;
  /** True once the managed folder holds weights and the engine reads from it. */
  managed: boolean;
}

/** One offline GGUF the translation step can load. */
export interface LlmModelInfo {
  /** Catalogue key, e.g. `gemma3-4b-it-q4_k_m`. */
  key: string;
  label: string;
  description: string;
  /** The GGUF filename stored in `llm.model` — not the catalogue key. */
  filename: string;
  size_mb: number;
  /** Weights are on disk, so the translator can load them. */
  installed: boolean;
  missing_packages: string[];
  /** The model the next translation would load. */
  active: boolean;
  licence: string;
}

export interface LlmConfig {
  /** GGUF file name in effect (stored choice, or the engine default). */
  model: string;
  /** False while the active GGUF is not on disk. */
  ready: boolean;
  /** What an empty `llm.model` runs. */
  engine_default: string;
  options: LlmModelInfo[];
}

export type AudioMode = "fast" | "clean";

export interface AudioModeInfo {
  name: AudioMode;
  label: string;
  note: string;
  /** `clean` is only ready when the separator weights *and* the package exist. */
  ready: boolean;
}

export interface AudioConfig {
  mode: AudioMode;
  modes: AudioModeInfo[];
  separator_model: string;
  separator_installed: boolean;
  package_installed: boolean;
}

export interface SettingsDocument {
  /** `providers.json` on disk, shown at the bottom of the panel. */
  path: string;
  providers: ProviderConfig[];
  ocr: OcrConfig;
  audio: AudioConfig;
}

export interface ProviderTestResult {
  ok: boolean;
  message: string;
  /** Model ids the key can reach — the fastest way to find the right name. */
  models: string[];
  detail: string;
}

/** Body of `PUT /api/settings`: only the fields the user touched. */
export interface ProviderPatchBody {
  api_key?: string;
  base_url?: string;
  slots?: Record<string, string>;
}

export interface SettingsPatchBody {
  providers?: Record<string, ProviderPatchBody>;
  ocr?: {
    engine?: string;
    profile?: string;
    /** `"auto"` | `"gpu"` | `"cpu"` — a GPU preference, never a requirement. */
    device?: string;
    /** `"left,top,right,bottom"` in percent; `""` returns to the default band. */
    region?: string;
  };
  audio?: { mode?: string };
}

/** Body of `POST /api/settings/providers/{name}/test` — unsaved values allowed. */
export interface ProviderTestBody {
  api_key?: string;
  base_url?: string;
  slots?: Record<string, string>;
}
