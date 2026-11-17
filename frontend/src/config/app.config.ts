/**
 * Single source of truth for the backend connection.
 *
 * The Python engine always binds to loopback. In development it is started by
 * `npm run dev:backend` on the fixed port below. In the packaged app the Tauri
 * supervisor spawns the sidecar on a free port and hands it to the WebView via
 * the `backend_info` command — `@/lib/endpoint` then re-points every URL.
 */

const DEFAULT_PORT = 8756;
const DEFAULT_HOST = "127.0.0.1";

export const BACKEND_HOST = import.meta.env.VITE_BACKEND_HOST ?? DEFAULT_HOST;

export const BACKEND_PORT = Number(import.meta.env.VITE_BACKEND_PORT ?? DEFAULT_PORT);

export const API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 30_000);

export const ENDPOINTS = {
  health: "/api/health",
  hardware: "/api/system/hardware",
  openPath: "/api/system/open-path",
  models: "/api/models",
  model: (key: string) => `/api/models/${encodeURIComponent(key)}`,
  modelDownload: (key: string) => `/api/models/${encodeURIComponent(key)}/download`,
  aiPack: "/api/ai-pack",
  aiPackInstall: "/api/ai-pack/install",
  aiPackStaging: "/api/ai-pack/staging",
  settings: "/api/settings",
  settingsTest: (name: string) => `/api/settings/providers/${encodeURIComponent(name)}/test`,
  probe: "/api/media/probe",
  thumbnail: "/api/media/thumbnail",
  mediaCut: "/api/media/cut",
  mediaStripBgm: "/api/media/strip-bgm",
  mediaTrim: "/api/media/tools/trim",
  mediaBatchTrim: "/api/media/tools/batch-trim",
  mediaConcat: "/api/media/tools/concat",
  mediaDemux: "/api/media/tools/demux",
  mediaMux: "/api/media/tools/mux",
  transcribe: "/api/subtitles/transcribe",
  /** Second look at a single cue: read its frame(s) again, nothing is stored. */
  ocrFrame: "/api/subtitles/ocr-frame",
  ocrRegion: "/api/subtitles/ocr-region",
  importSrt: "/api/subtitles/import-srt",
  presets: "/api/subtitles/presets",
  analyzeContext: "/api/subtitles/analyze-context",
  translate: "/api/subtitles/translate",
  exportSubtitles: "/api/subtitles/export",
  tts: "/api/dubbing/synthesize",
  ttsVoices: "/api/dubbing/voices",
  /** Re-speak one cue after its text was edited. */
  ttsCue: "/api/dubbing/cue",
  /** Re-assemble the dub track from the takes already on disk. */
  ttsRemix: "/api/dubbing/remix",
  ttsPreview: "/api/dubbing/preview",
  render: "/api/render",
  fastPreview: "/api/render/fast-preview",
  exportAudio: "/api/render/export-audio",
  /** CapCut Desktop draft; `capcutTargets` reports where it would land. */
  capcut: "/api/export/capcut",
  capcutTargets: "/api/export/capcut/targets",
  queue: "/api/queue",
  queueAdd: "/api/queue/add",
  queueStart: "/api/queue/start",
  queueStop: "/api/queue/stop",
  queueClearCompleted: "/api/queue/clear-completed",
  queueItem: (id: string) => `/api/queue/${encodeURIComponent(id)}`,
  queueItemClean: (id: string) => `/api/queue/${encodeURIComponent(id)}/clean`,
  projectSave: "/api/project/save",
  projectOpen: "/api/project/open",
  projectList: "/api/project/list",
  projectFile: "/api/project/file",
  projectSession: "/api/project/session",
  jobs: "/api/jobs",
  job: (id: string) => `/api/jobs/${encodeURIComponent(id)}`,
  cancelJob: (id: string) => `/api/jobs/${encodeURIComponent(id)}/cancel`,
  progressSocket: "/ws/progress",
} as const;
