import { BACKEND_HOST, BACKEND_PORT } from "@/config/app.config";

/**
 * The engine port is only known at runtime in the packaged app: the Tauri
 * supervisor picks a free loopback port. Every request URL is built from this
 * module so a single `setBackendPort()` call re-points HTTP and WebSocket.
 */

let port = BACKEND_PORT;

export function backendPort(): number {
  return port;
}

export function setBackendPort(next: number): void {
  if (Number.isFinite(next) && next > 0 && next < 65_536) {
    port = Math.trunc(next);
  }
}

export function backendHttpUrl(): string {
  return `http://${BACKEND_HOST}:${port}`;
}

export function backendWsUrl(): string {
  return `ws://${BACKEND_HOST}:${port}`;
}
