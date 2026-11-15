import { useEffect } from "react";

import { ENDPOINTS } from "@/config/app.config";
import { backendWsUrl } from "@/lib/endpoint";
import { fetchAiPack, listModels } from "@/lib/api";
import { useEditor } from "@/store/editorStore";
import type { HardwareProfile, HelloPayload, ProgressFrame } from "@/types/editor";

/**
 * Single WebSocket consumer for the whole editor.
 *
 * Frames are funnelled into the Zustand store so any component can read the
 * live job progress or the streamed subtitle lines without owning a socket.
 */
export function useProgressSocket(): void {
  useEffect(() => {
    let stopped = false;
    let attempt = 0;
    let retryTimer = 0;
    let socket: WebSocket | null = null;

    const store = () => useEditor.getState();

    const handleHello = (payload: HelloPayload) => {
      store().update({
        engineOnline: true,
        hardware: { profile: payload.hardware as HardwareProfile, device: payload.device },
        models: payload.models ?? [],
      });
      store().pushLog(
        "success",
        `Engine ${payload.version} · ${payload.device} · whisper ${payload.hardware?.whisper_model}/${payload.hardware?.whisper_compute_type}`,
      );
    };

    const handle = (frame: ProgressFrame) => {
      const state = store();
      switch (frame.type) {
        case "hello":
          handleHello(frame.data as unknown as HelloPayload);
          break;
        case "progress":
          if (frame.job_id) {
            state.setJob({
              id: frame.job_id,
              kind: frame.kind ?? "",
              stage: frame.stage ?? "",
              progress: frame.progress ?? 0,
              message: frame.message ?? "",
              state: "running",
            });
          }
          break;
        case "subtitle": {
          const segment = frame.segment;
          if (!segment) break;
          if (frame.kind === "transcribe") state.upsertSegment(segment);
          else if (frame.kind === "translate" && segment.translation) {
            state.patchSegment(segment.index, { translation: segment.translation });
          }
          break;
        }
        case "result":
          if (frame.job_id) {
            state.setJob({
              id: frame.job_id,
              kind: frame.kind ?? "",
              stage: "done",
              progress: 1,
              message: frame.message ?? "done",
              state: "done",
            });
          }
          break;
        case "error":
          if (frame.job_id) {
            state.setJob({
              id: frame.job_id,
              kind: frame.kind ?? "",
              stage: "error",
              progress: 0,
              message: frame.message ?? "failed",
              state: "error",
            });
          }
          state.pushLog("error", frame.message ?? `Job ${frame.kind} failed`);
          break;
        case "log":
          if (frame.message) state.pushLog("info", frame.message);
          break;
        default:
          break;
      }
    };

    const connect = () => {
      store().setSocket("connecting");
      socket = new WebSocket(`${backendWsUrl()}${ENDPOINTS.progressSocket}`);

      socket.onopen = () => {
        attempt = 0;
        store().setSocket("open");
      };
      socket.onmessage = (event) => {
        try {
          handle(JSON.parse(event.data as string) as ProgressFrame);
        } catch {
          /* heartbeat or partial frame — ignore */
        }
      };
      socket.onclose = () => {
        store().setSocket("closed");
        if (!stopped) {
          retryTimer = window.setTimeout(connect, Math.min(8000, 400 * 2 ** attempt++));
        }
      };
      socket.onerror = () => socket?.close();
    };

    // Deferred so React StrictMode's throw-away first mount never opens (and
    // then closes) a socket that is still connecting — Chromium logs a warning.
    retryTimer = window.setTimeout(connect, 0);

    return () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  // Model downloads finish on the backend without a payload we can trust, so
  // refresh the catalogue whenever a download job reaches a terminal state.
  const jobKind = useEditor((state) => state.job?.kind ?? "");
  const jobState = useEditor((state) => state.job?.state ?? "");
  useEffect(() => {
    if (!jobKind.startsWith("model-download") || jobState === "running") return;
    void refreshModels();
  }, [jobKind, jobState]);

  // Same for the AI pack: an install changes the runtime, the folder on disk and
  // the warnings shown next to every model entry.
  useEffect(() => {
    if (!jobKind.startsWith("ai-pack") || jobState === "running") return;
    void refreshAiPack();
    void refreshModels();
  }, [jobKind, jobState]);
}

export async function refreshAiPack(): Promise<void> {
  try {
    const status = await fetchAiPack();
    useEditor.getState().update({ aiPack: status });
  } catch (error) {
    useEditor.getState().pushLog("warn", `Không đọc được trạng thái AI pack: ${(error as Error).message}`);
  }
}

export async function refreshModels(): Promise<void> {
  try {
    const catalogue = await listModels();
    useEditor.getState().update({ models: catalogue.models, modelsPath: catalogue.models_path });
  } catch (error) {
    useEditor.getState().pushLog("warn", `Không đọc được danh sách model: ${(error as Error).message}`);
  }
}
