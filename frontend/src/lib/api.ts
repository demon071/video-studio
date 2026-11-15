import axios from "axios";

import { API_TIMEOUT_MS, ENDPOINTS } from "@/config/app.config";
import { backendHttpUrl, setBackendPort as setEndpointPort } from "@/lib/endpoint";
import type {
  AiPackStatus,
  AudioMode,
  CapCutExportResult,
  CapCutTargets,
  DubbingCueResult,
  DubbingRemixResult,
  DubbingResult,
  DubbingSegmentReport,
  HardwareReport,
  JobInfo,
  ModelStatus,
  OcrFrameResult,
  ProbeResult,
  ProjectEnvelope,
  ProjectFile,
  ProjectListResult,
  ProviderTestBody,
  ProviderTestResult,
  RenderResult,
  SettingsDocument,
  SettingsPatchBody,
  SubtitleSegment,
  SubtitleStyle,
  TranscribeResult,
  TranslateResult,
} from "@/types/editor";

/** Shared HTTP client for the local Python engine. */
export const api = axios.create({
  baseURL: backendHttpUrl(),
  timeout: API_TIMEOUT_MS,
  headers: { "Content-Type": "application/json" },
});

/**
 * Re-point the client after the Tauri supervisor reports the sidecar port.
 * Called exactly once at boot (see `bootstrapDesktopBackend`).
 */
export function setBackendPort(port: number): void {
  setEndpointPort(port);
  api.defaults.baseURL = backendHttpUrl();
}

/** AI stages (STT / LLM / TTS / encode) run for minutes — never time them out. */
const long = { timeout: 0 } as const;

export interface HealthResponse {
  status: string;
  version: string;
  device: string;
  ffmpeg: boolean;
  uptime_s: number;
}

/** Normalised error so panels can show the FastAPI `detail` verbatim. */
export class EngineError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "EngineError";
    this.status = status;
  }
}

function fail(error: unknown): never {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
    const text =
      typeof detail === "string"
        ? detail
        : error.code === "ECONNABORTED"
          ? "The engine took too long to answer."
          : error.message;
    throw new EngineError(text, error.response?.status);
  }
  throw error instanceof Error ? error : new EngineError(String(error));
}

export async function fetchHealth(): Promise<HealthResponse> {
  try {
    const { data } = await api.get<HealthResponse>(ENDPOINTS.health);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function fetchHardware(): Promise<HardwareReport> {
  try {
    const { data } = await api.get<HardwareReport>(ENDPOINTS.hardware);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export interface ModelCatalogue {
  models: ModelStatus[];
  models_path: string;
  downloaded: string[];
}

export async function listModels(): Promise<ModelCatalogue> {
  try {
    const { data } = await api.get<ModelCatalogue>(ENDPOINTS.models);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function downloadModel(key: string, force = false): Promise<{ job: JobInfo; model: ModelStatus }> {
  try {
    const { data } = await api.post(ENDPOINTS.modelDownload(key), null, { params: { force } });
    return data as { job: JobInfo; model: ModelStatus };
  } catch (error) {
    return fail(error);
  }
}

export async function removeModel(key: string): Promise<void> {
  try {
    await api.delete(ENDPOINTS.model(key));
  } catch (error) {
    fail(error);
  }
}

/** Heavy engine add-on ("AI pack"): status, install job, staging cleanup. */
export async function fetchAiPack(): Promise<AiPackStatus> {
  try {
    const { data } = await api.get<AiPackStatus>(ENDPOINTS.aiPack);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export interface AiPackInstallBody {
  url?: string;
  path?: string;
}

export async function installAiPack(body: AiPackInstallBody): Promise<{ job: JobInfo; status: AiPackStatus }> {
  try {
    const { data } = await api.post(ENDPOINTS.aiPackInstall, body);
    return data as { job: JobInfo; status: AiPackStatus };
  } catch (error) {
    return fail(error);
  }
}

export async function discardAiPackStaging(): Promise<{ removed: boolean }> {
  try {
    const { data } = await api.delete<{ removed: boolean }>(ENDPOINTS.aiPackStaging);
    return data;
  } catch (error) {
    return fail(error);
  }
}

/* --------------------------------------------------------------------------
 * Settings document (`providers.json`): keys, model names, OCR, audio mode.
 * ----------------------------------------------------------------------- */

/**
 * Everything the Settings panel needs. Secrets stay on the engine: the payload
 * carries `api_key_set` rather than the key itself.
 */
export async function fetchSettings(): Promise<SettingsDocument> {
  try {
    const { data } = await api.get<SettingsDocument>(ENDPOINTS.settings);
    return data;
  } catch (error) {
    return fail(error);
  }
}

/** Save only the fields the user touched; the engine merges and echoes back. */
export async function saveSettings(patch: SettingsPatchBody): Promise<SettingsDocument> {
  try {
    const { data } = await api.put<SettingsDocument>(ENDPOINTS.settings, patch);
    return data;
  } catch (error) {
    return fail(error);
  }
}

/**
 * Probe one provider and list the models its key can reach.
 *
 * `body` may carry a key the user typed but has not saved yet, so "Kiểm tra"
 * works before "Lưu".
 */
export async function testProvider(name: string, body: ProviderTestBody = {}): Promise<ProviderTestResult> {
  try {
    const { data } = await api.post<ProviderTestResult>(ENDPOINTS.settingsTest(name), body, long);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function fetchJob(id: string): Promise<JobInfo> {
  try {
    const { data } = await api.get<JobInfo>(ENDPOINTS.job(id));
    return data;
  } catch (error) {
    return fail(error);
  }
}

/**
 * Wait for a background job (model / AI pack download) to finish.
 *
 * Downloads report progress over the WebSocket, but the caller still needs the
 * terminal state before it can act (e.g. ask the shell to swap the AI pack in).
 */
export async function waitForJob(
  id: string,
  options: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<JobInfo> {
  const interval = options.intervalMs ?? 1000;
  const deadline = Date.now() + (options.timeoutMs ?? 90 * 60_000);
  for (;;) {
    const job = await fetchJob(id);
    if (job.state !== "queued" && job.state !== "running") return job;
    if (Date.now() > deadline) throw new EngineError("The job did not finish in time.");
    await new Promise((resolve) => window.setTimeout(resolve, interval));
  }
}

export async function probeVideo(videoPath: string): Promise<ProbeResult> {
  try {
    const { data } = await api.post<ProbeResult>(ENDPOINTS.probe, { video_path: videoPath });
    return data;
  } catch (error) {
    return fail(error);
  }
}

export interface TranscribeBody {
  video_path: string;
  language?: string;
  provider?: string | null;
  model?: string | null;
  vad?: boolean;
  /** Drop hallucinations, glue fragments and split long cues (default true). */
  polish?: boolean;
  /**
   * `clean` separates the vocal from the music (UVR-MDX) before recognition —
   * CapCap's trick for clips whose soundtrack drowns the dialogue.
   */
  audio_mode?: AudioMode | null;
  /**
   * Read the burned-in subtitles inside every stretch that carries sound but
   * produced no line. Runs after the speech pass, so it never touches the
   * lines the engine already found.
   */
  ocr_fill?: boolean;
}

export async function transcribeVideo(body: TranscribeBody): Promise<TranscribeResult> {
  try {
    const { data } = await api.post<TranscribeResult>(ENDPOINTS.transcribe, body, long);
    return data;
  } catch (error) {
    return fail(error);
  }
}

/**
 * Read one cue's frame(s) again — the fix for a line the OCR (or the speech
 * engine) got wrong. The engine stores nothing: it answers, the editor asks.
 */
export interface OcrFrameBody {
  video_path: string;
  start: number;
  end: number;
  /** Frames to read inside the window; 3 is enough for a one-second line. */
  samples?: number;
  /** Recognition language for this read; `null`/`auto` keeps the setting. */
  language?: string | null;
}

export async function ocrFrame(body: OcrFrameBody): Promise<OcrFrameResult> {
  try {
    const { data } = await api.post<OcrFrameResult>(ENDPOINTS.ocrFrame, body, long);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export interface TranslateBody {
  segments: SubtitleSegment[];
  target_language: string;
  source_language?: string;
  provider?: string | null;
  style?: string;
}

export async function translateSegments(body: TranslateBody): Promise<TranslateResult> {
  try {
    const { data } = await api.post<TranslateResult>(ENDPOINTS.translate, body, long);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export interface ExportSubtitlesBody {
  segments: SubtitleSegment[];
  output_path: string;
  fmt?: "srt" | "ass";
  aspect?: string;
  use_translation?: boolean;
  /** Caption look; omitting it keeps the engine's plain default. */
  style?: SubtitleStyle | null;
}

export async function exportSubtitles(
  body: ExportSubtitlesBody,
): Promise<{ output_path: string; fmt: string; lines: number }> {
  try {
    const { data } = await api.post(ENDPOINTS.exportSubtitles, body, long);
    return data as { output_path: string; fmt: string; lines: number };
  } catch (error) {
    return fail(error);
  }
}

export interface DubbingBody {
  segments: SubtitleSegment[];
  use_translation?: boolean;
  provider?: string | null;
  voice?: string | null;
  rate?: string | null;
  /** Borrow trailing silence and reword long lines instead of speeding them up. */
  timing_rewrite?: boolean;
  language?: string | null;
  video_path?: string | null;
}

export async function synthesizeDub(body: DubbingBody): Promise<DubbingResult> {
  try {
    const { data } = await api.post<DubbingResult>(ENDPOINTS.tts, body, long);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export interface DubbingCueBody {
  /** The whole track: a cue may borrow the silence that follows it. */
  segments: SubtitleSegment[];
  index: number;
  /** Hand the take back without touching the track (just to listen to it). */
  audition?: boolean;
  use_translation?: boolean;
  provider?: string | null;
  voice?: string | null;
  rate?: string | null;
  timing_rewrite?: boolean;
  language?: string | null;
}

export async function synthesizeCue(body: DubbingCueBody): Promise<DubbingCueResult> {
  try {
    const { data } = await api.post<DubbingCueResult>(ENDPOINTS.ttsCue, body, long);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function remixDub(
  segments: DubbingSegmentReport[],
  duration: number,
): Promise<DubbingRemixResult> {
  try {
    const { data } = await api.post<DubbingRemixResult>(ENDPOINTS.ttsRemix, { segments, duration }, long);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function previewVoice(
  text: string,
  options: { provider?: string; voice?: string; rate?: string } = {},
): Promise<{ path?: string; url?: string; duration?: number }> {
  try {
    const { data } = await api.post(ENDPOINTS.ttsPreview, null, { params: { text, ...options }, ...long });
    return data as { path?: string; url?: string; duration?: number };
  } catch (error) {
    return fail(error);
  }
}

export interface RenderBody {
  video_path: string;
  output_path: string;
  aspect?: string;
  subtitle_path?: string | null;
  dub_track_path?: string | null;
  background_path?: string | null;
  /** Level of the original audio under the dub (0 mutes it). */
  background_gain?: number;
  keep_original_audio?: boolean;
  crf?: number;
}

export async function renderVideo(body: RenderBody): Promise<RenderResult> {
  try {
    const { data } = await api.post<RenderResult>(ENDPOINTS.render, body, long);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export interface CapCutExportBody {
  video_path: string;
  aspect?: string;
  segments?: SubtitleSegment[];
  use_translation?: boolean;
  style?: SubtitleStyle | null;
  /** Assembled dub track, used when the per-line takes are not available. */
  dub_track_path?: string | null;
  /** One take per cue: CapCut gets an editable clip per line, not one long file. */
  dub_segments?: DubbingSegmentReport[];
  keep_original_audio?: boolean;
  background_gain?: number;
  copy_video?: boolean;
  draft_name?: string | null;
  /** CapCut drafts folder; `null` detects the installed app. */
  output_dir?: string | null;
  /** Append the draft to CapCut's project list. */
  add_to_index?: boolean;
}

/** Where a CapCut draft would land on this machine. */
export async function fetchCapCutTargets(): Promise<CapCutTargets> {
  try {
    const { data } = await api.get<CapCutTargets>(ENDPOINTS.capcutTargets);
    return data;
  } catch (error) {
    return fail(error);
  }
}

/** Write the project as an editable CapCut Desktop draft. */
export async function exportCapCut(body: CapCutExportBody): Promise<CapCutExportResult> {
  try {
    const { data } = await api.post<CapCutExportResult>(ENDPOINTS.capcut, body, long);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function listJobs(): Promise<JobInfo[]> {
  try {
    const { data } = await api.get<{ jobs: JobInfo[] }>(ENDPOINTS.jobs);
    return data.jobs ?? [];
  } catch (error) {
    return fail(error);
  }
}

export async function cancelJob(id: string): Promise<void> {
  try {
    await api.post(ENDPOINTS.cancelJob(id));
  } catch (error) {
    fail(error);
  }
}

/* --------------------------------------------------------------------------
 * Project documents (`.svaproj`) and the session autosave.
 * ----------------------------------------------------------------------- */

export async function saveProject(body: {
  project: ProjectFile;
  path?: string | null;
}): Promise<ProjectEnvelope> {
  try {
    const { data } = await api.post<ProjectEnvelope>(ENDPOINTS.projectSave, body);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function openProject(path: string): Promise<ProjectEnvelope> {
  try {
    const { data } = await api.post<ProjectEnvelope>(ENDPOINTS.projectOpen, { path });
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function listProjects(): Promise<ProjectListResult> {
  try {
    const { data } = await api.get<ProjectListResult>(ENDPOINTS.projectList);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function deleteProject(path: string): Promise<void> {
  try {
    await api.delete(ENDPOINTS.projectFile, { params: { path } });
  } catch (error) {
    fail(error);
  }
}

/** The snapshot left by the previous run; an empty envelope when there is none. */
export async function getSession(): Promise<ProjectEnvelope> {
  try {
    const { data } = await api.get<ProjectEnvelope>(ENDPOINTS.projectSession);
    return data;
  } catch (error) {
    return fail(error);
  }
}

export async function putSession(project: ProjectFile): Promise<ProjectEnvelope> {
  try {
    const { data } = await api.put<ProjectEnvelope>(ENDPOINTS.projectSession, { project });
    return data;
  } catch (error) {
    return fail(error);
  }
}

/**
 * Last-chance save while the window is closing: `keepalive` lets the request
 * outlive the page, and `fetch` is used directly because axios cannot.
 */
export function putSessionNow(project: ProjectFile): void {
  try {
    void fetch(`${api.defaults.baseURL}${ENDPOINTS.projectSession}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project }),
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    // The WebView is going away; there is nobody left to tell.
  }
}

export async function clearSession(): Promise<void> {
  try {
    await api.delete(ENDPOINTS.projectSession);
  } catch (error) {
    fail(error);
  }
}
