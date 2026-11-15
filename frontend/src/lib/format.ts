/** Timecode and size helpers shared by the timeline, table and status bars. */

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** 01:23.45 (mm:ss.cc) — the format editors use on a playhead. */
export function formatTime(seconds: number): string {
  const safe = Math.max(seconds || 0, 0);
  const minutes = Math.floor(safe / 60);
  const secs = Math.floor(safe % 60);
  const centis = Math.floor((safe % 1) * 100);
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(centis).padStart(2, "0")}`;
}

/** 00:00:01,250 — the SRT convention used in the subtitle table. */
export function formatSrtTime(seconds: number): string {
  const safe = Math.max(seconds || 0, 0);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = Math.floor(safe % 60);
  const millis = Math.round((safe % 1) * 1000);
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")},${String(millis).padStart(3, "0")}`;
}

/** Accepts 00:00:01,250 / 0:00:01.25 / 12.5 and returns seconds, or null. */
export function parseTimecode(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  const match = text.match(/^(\d+):(\d{1,2}):(\d{1,2})[.,](\d{1,3})$/) ?? text.match(/^(\d{1,2}):(\d{1,2})[.,](\d{1,3})$/);
  if (match) {
    if (match.length === 5) {
      const [, h, m, s, ms] = match;
      return Number(h) * 3600 + Number(m) * 60 + Number(s) + Number(ms.padEnd(3, "0")) / 1000;
    }
    const [, m, s, ms] = match;
    return Number(m) * 60 + Number(s) + Number(ms.padEnd(3, "0")) / 1000;
  }
  const plain = Number(text.replace(",", "."));
  return Number.isFinite(plain) ? plain : null;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** exponent).toFixed(exponent >= 2 ? 1 : 0)} ${units[exponent]}`;
}

export function formatPercent(fraction: number | null | undefined): string {
  return `${Math.round(clamp((fraction ?? 0) * 100, 0, 100))}%`;
}
