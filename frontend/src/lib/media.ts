/**
 * Bridging helpers between the WebView and Windows media paths.
 *
 * A `<video>` element can only play a URL, while the Python engine needs the
 * absolute path. Every file is granted to the `asset://` scope first, so the
 * static scope can stay empty in `tauri.conf.json`.
 */

import { grantMediaAccess, isDesktop } from "@/lib/desktop";

export { isDesktop };

/** Turns a Windows path into a WebView-safe URL (no-op in the browser preview). */
export async function toAssetUrl(path: string): Promise<string> {
  if (!isDesktop) return path;
  await grantMediaAccess(path);
  const { convertFileSrc } = await import("@tauri-apps/api/core");
  return convertFileSrc(path);
}

export async function pickVideo(): Promise<string | null> {
  if (!isDesktop) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selection = await open({
    title: "Chọn video để dựng",
    multiple: false,
    directory: false,
    filters: [{ name: "Video", extensions: ["mp4", "mov", "mkv", "webm", "avi", "m4v"] }],
  });
  return typeof selection === "string" ? selection : null;
}

export async function pickVideos(): Promise<string[]> {
  if (!isDesktop) return [];
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selection = await open({
    title: "Chọn nhiều video vào hàng đợi",
    multiple: true,
    directory: false,
    filters: [{ name: "Video", extensions: ["mp4", "mov", "mkv", "webm", "avi", "m4v"] }],
  });
  if (Array.isArray(selection)) return selection.filter((v): v is string => typeof v === "string");
  return typeof selection === "string" ? [selection] : [];
}

export async function pickImage(): Promise<string | null> {
  if (!isDesktop) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selection = await open({
    title: "Chọn ảnh logo / hình chèn",
    multiple: false,
    directory: false,
    filters: [{ name: "Image", extensions: ["png", "jpg", "jpeg", "webp", "bmp"] }],
  });
  return typeof selection === "string" ? selection : null;
}

export async function pickAudio(): Promise<string | null> {
  if (!isDesktop) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selection = await open({
    title: "Chọn file nhạc nền (BGM)",
    multiple: false,
    directory: false,
    filters: [{ name: "Audio", extensions: ["mp3", "wav", "m4a", "flac", "aac", "ogg"] }],
  });
  return typeof selection === "string" ? selection : null;
}

export async function pickSavePath(defaultName: string): Promise<string | null> {
  if (!isDesktop) return null;
  const { save } = await import("@tauri-apps/plugin-dialog");
  return (await save({
    title: "Xuất video",
    defaultPath: defaultName,
    filters: [{ name: "Video MP4", extensions: ["mp4"] }],
  })) ?? null;
}

/** Where a subtitle file (`sub.srt`) should be written. */
export async function pickSubtitleSavePath(defaultName: string, fmt: "srt" | "ass"): Promise<string | null> {
  if (!isDesktop) return null;
  const { save } = await import("@tauri-apps/plugin-dialog");
  const label = fmt === "ass" ? "Phụ đề ASS (có style)" : "Phụ đề SRT (chữ thuần)";
  return (
    (await save({
      title: "Xuất phụ đề",
      defaultPath: defaultName,
      filters: [{ name: label, extensions: [fmt] }],
    })) ?? null
  );
}

/** Pick a previously downloaded AI pack (`*_ai-pack-full.zip`). */
export async function pickAiPackFile(): Promise<string | null> {
  if (!isDesktop) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selection = await open({
    title: "Chọn tệp AI pack",
    multiple: false,
    directory: false,
    filters: [{ name: "AI pack", extensions: ["zip"] }],
  });
  return typeof selection === "string" ? selection : null;
}

/** Pick a saved project document (`.svaproj`). */
export async function pickProjectFile(): Promise<string | null> {
  if (!isDesktop) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selection = await open({
    title: "Mở dự án",
    multiple: false,
    directory: false,
    filters: [{ name: "Dự án ShortVideoAI", extensions: ["svaproj"] }],
  });
  return typeof selection === "string" ? selection : null;
}

/** Where a new project document should be written. */
export async function pickProjectSavePath(defaultName: string): Promise<string | null> {
  if (!isDesktop) return null;
  const { save } = await import("@tauri-apps/plugin-dialog");
  return (
    (await save({
      title: "Lưu dự án",
      defaultPath: defaultName,
      filters: [{ name: "Dự án ShortVideoAI", extensions: ["svaproj"] }],
    })) ?? null
  );
}

/** Pick a folder: the CapCut drafts root, or anywhere else to keep a draft. */
export async function pickDirectory(title = "Chọn thư mục"): Promise<string | null> {
  if (!isDesktop) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selection = await open({ title, multiple: false, directory: true });
  return typeof selection === "string" ? selection : null;
}

/** Show a folder in Explorer; nothing happens in the browser preview. */
export async function openFolder(path: string): Promise<void> {
  if (!isDesktop) return;
  try {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(path);
  } catch (error) {
    console.error("[desktop] open folder failed", error);
  }
}

export function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export function stem(path: string): string {
  const name = baseName(path);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

export function directoryOf(path: string): string {
  const index = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return index > 0 ? path.slice(0, index) : "";
}

export function joinPath(directory: string, name: string): string {
  if (!directory) return name;
  const separator = directory.includes("\\") || /^[a-zA-Z]:/.test(directory) ? "\\" : "/";
  return `${directory.replace(/[\\/]+$/, "")}${separator}${name}`;
}
