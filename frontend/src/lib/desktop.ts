import { setBackendPort } from "@/lib/api";

/**
 * Thin wrapper around the Tauri commands added in Steps 4-5.
 *
 * Everything is imported dynamically so the plain browser build (Vite dev)
 * keeps working without the Tauri runtime.
 */

/** True inside the Tauri WebView, false in a normal browser tab. */
export const isDesktop =
  typeof window !== "undefined" &&
  "__TAURI_INTERNALS__" in (window as unknown as Record<string, unknown>);

export interface BackendInfo {
  port: number;
  managed: boolean;
  packaged: boolean;
  /** `ai-pack` when the optional heavy engine runs, `bundled` otherwise. */
  runtime: "bundled" | "ai-pack" | "external" | "none" | string;
  /** True when an AI pack is installed on disk. */
  ai_pack: boolean;
  log_path: string | null;
}

let info: BackendInfo | null = null;

export function desktopInfo(): BackendInfo | null {
  return info;
}

async function invokeCommand<T>(command: string): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command);
}

/** Adopt a new `BackendInfo` after the shell restarted the sidecar. */
function adopt(next: BackendInfo): BackendInfo {
  info = next;
  setBackendPort(next.port);
  return next;
}

/** Ask the shell which port the sidecar listens on and re-point the client. */
export async function bootstrapDesktopBackend(): Promise<BackendInfo | null> {
  if (!isDesktop) return null;
  try {
    return adopt(await invokeCommand<BackendInfo>("backend_info"));
  } catch (error) {
    console.error("[desktop] backend_info failed", error);
    return null;
  }
}

/** Allow the asset protocol to serve one file the user explicitly picked. */
export async function grantMediaAccess(path: string): Promise<void> {
  if (!isDesktop) return;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("grant_media_access", { path });
  } catch (error) {
    console.error("[desktop] grant_media_access failed", error);
  }
}

/**
 * Move the freshly extracted AI pack into place and restart the engine into it.
 * The Python engine cannot do this itself: Windows locks the running `.exe`.
 */
export async function applyAiPack(): Promise<BackendInfo | null> {
  if (!isDesktop) return null;
  try {
    return adopt(await invokeCommand<BackendInfo>("apply_ai_pack"));
  } catch (error) {
    console.error("[desktop] apply_ai_pack failed", error);
    throw error;
  }
}

/** Delete the AI pack and fall back to the bundled core engine. */
export async function removeAiPack(): Promise<BackendInfo | null> {
  if (!isDesktop) return null;
  try {
    return adopt(await invokeCommand<BackendInfo>("remove_ai_pack"));
  } catch (error) {
    console.error("[desktop] remove_ai_pack failed", error);
    throw error;
  }
}

/** Restart the sidecar on the same runtime (after a manual change on disk). */
export async function restartEngine(): Promise<BackendInfo | null> {
  if (!isDesktop) return null;
  try {
    return adopt(await invokeCommand<BackendInfo>("restart_engine"));
  } catch (error) {
    console.error("[desktop] restart_engine failed", error);
    throw error;
  }
}

/** The WebView must re-bootstrap after the engine moved to another port. */
export function reloadShell(): void {
  window.setTimeout(() => window.location.reload(), 300);
}
