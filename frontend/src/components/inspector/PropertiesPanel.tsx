import {
  Film,
  Image as ImageIcon,
  Layers,
  Sliders,
  Sparkles,
  Square,
  Trash2,
  Type,
  Volume2,
} from "lucide-react";
import { useEffect, useState } from "react";

import { clipTimelineBounds, timelineDuration } from "@/lib/clips";
import { clamp, formatTime } from "@/lib/format";
import { pickAudio, pickImage } from "@/lib/media";
import { deleteTimelineClip, runStripBgm, splitTimelineClip } from "@/lib/tasks";
import { useEditor } from "@/store/editorStore";
import type { Aspect, LayerKind, QualityProfile } from "@/types/editor";

type InspectorSection = "video" | "layer" | "audio";

export function PropertiesPanel() {
  const source = useEditor((state) => state.source);
  const videoClips = useEditor((state) => state.videoClips);
  const selectedClipId = useEditor((state) => state.selectedClipId);
  const aspect = useEditor((state) => state.aspect);
  const flipHorizontal = useEditor((state) => state.flipHorizontal);
  const qualityProfile = useEditor((state) => state.qualityProfile);
  const useNvenc = useEditor((state) => state.useNvenc);

  const layers = useEditor((state) => state.layers);
  const selectedLayerId = useEditor((state) => state.selectedLayerId);
  const addLayer = useEditor((state) => state.addLayer);
  const patchLayer = useEditor((state) => state.patchLayer);
  const removeLayer = useEditor((state) => state.removeLayer);

  const keepOriginalAudio = useEditor((state) => state.keepOriginalAudio);
  const duckGain = useEditor((state) => state.duckGain);
  const voiceGain = useEditor((state) => state.voiceGain);
  const backgroundAudioPath = useEditor((state) => state.backgroundAudioPath);
  const previewDub = useEditor((state) => state.previewDub);
  const trimSilence = useEditor((state) => state.trimSilence);
  const dub = useEditor((state) => state.dub);
  const pending = useEditor((state) => state.pending);
  const update = useEditor((state) => state.update);

  const [section, setSection] = useState<InspectorSection>("video");

  useEffect(() => {
    if (selectedLayerId) setSection("layer");
  }, [selectedLayerId]);

  useEffect(() => {
    if (selectedClipId) setSection("video");
  }, [selectedClipId]);

  const totalDur = videoClips.length ? timelineDuration(videoClips) : (source?.duration ?? 0);
  const selectedLayer = layers.find((l) => l.id === selectedLayerId) ?? layers[0] ?? null;
  const activeClipBound =
    clipTimelineBounds(videoClips).find((b) => b.clip.id === selectedClipId) ??
    clipTimelineBounds(videoClips)[0] ??
    null;

  const handleQuickAddLayer = async (kind: LayerKind) => {
    let imagePath: string | null = null;
    if (kind === "logo") imagePath = await pickImage();
    const d = Math.max(totalDur, 5);
    addLayer({
      id: `layer_${Date.now()}`,
      kind,
      label:
        kind === "mask"
          ? "Khung che"
          : kind === "blur"
            ? "Làm mờ"
            : kind === "logo"
              ? "Logo"
              : "Chữ nổi",
      start: 0,
      end: d,
      x: kind === "logo" ? 0.72 : 0.1,
      y: kind === "text" ? 0.12 : kind === "logo" ? 0.08 : 0.72,
      w: kind === "logo" ? 0.2 : 0.8,
      h: 0.14,
      color: kind === "text" ? "#FFE082" : "#000000",
      opacity: kind === "mask" ? 0.85 : 1.0,
      blur_strength: 24,
      text: kind === "text" ? "Tiêu đề / Ghi chú" : "",
      font_size: 32,
      image_path: imagePath,
    });
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 gap-1 border-b border-border bg-surface-2 px-2 py-1.5">
        {(
          [
            { id: "video", label: "Video", icon: Film },
            { id: "layer", label: `Lớp phủ (${layers.length})`, icon: Layers },
            { id: "audio", label: "Audio", icon: Volume2 },
          ] as const
        ).map((tab) => (
          <button
            key={tab.id}
            onClick={() => setSection(tab.id)}
            className={`flex flex-1 items-center justify-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors ${
              section === tab.id
                ? "bg-brand/15 text-brand"
                : "text-slate-400 hover:bg-surface-3 hover:text-slate-200"
            }`}
          >
            <tab.icon size={11} />
            {tab.label}
          </button>
        ))}
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-3 text-xs">
        {section === "video" && (
          <>
            <div className="rounded-lg border border-border bg-surface-2 p-2.5">
              <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-300">
                <Sliders size={11} className="text-brand" />
                Thông số Video & Xuất
              </div>
              {source ? (
                <div className="mb-2.5 space-y-0.5 text-[11px] text-slate-400">
                  <div className="truncate font-medium text-slate-200">{source.name}</div>
                  <div className="font-mono text-[10px] text-slate-500">
                    {source.width}×{source.height} · {source.fps.toFixed(0)} fps · Timeline {formatTime(totalDur)}
                  </div>
                </div>
              ) : (
                <p className="mb-2 text-[11px] text-slate-500">Chưa mở video.</p>
              )}

              <div className="space-y-2">
                <label className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="text-slate-400">Tỉ lệ khung hình</span>
                  <select
                    className="field !w-36 !py-1 !text-[11px]"
                    value={aspect}
                    onChange={(e) => update({ aspect: e.target.value as Aspect })}
                  >
                    <option value="9:16">9:16 (Dọc · Short/TikTok)</option>
                    <option value="16:9">16:9 (Ngang · YouTube)</option>
                    <option value="1:1">1:1 (Vuông)</option>
                  </select>
                </label>

                <label className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="text-slate-400">Chất lượng xuất</span>
                  <select
                    className="field !w-36 !py-1 !text-[11px]"
                    value={qualityProfile}
                    onChange={(e) => update({ qualityProfile: e.target.value as QualityProfile })}
                  >
                    <option value="high">Cao (CRF 20)</option>
                    <option value="balanced">Cân bằng (CRF 22)</option>
                    <option value="low">Nhẹ / Nhanh (CRF 26)</option>
                  </select>
                </label>

                <label className="flex cursor-pointer items-center justify-between pt-1 text-[11px] text-slate-300">
                  <span>Lật ngang video (Mirror)</span>
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 accent-brand"
                    checked={flipHorizontal}
                    onChange={(e) => update({ flipHorizontal: e.target.checked })}
                  />
                </label>

                <label className="flex cursor-pointer items-center justify-between text-[11px] text-slate-300">
                  <span>Ưu tiên GPU NVIDIA (NVENC)</span>
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 accent-brand"
                    checked={useNvenc}
                    onChange={(e) => update({ useNvenc: e.target.checked })}
                  />
                </label>
              </div>
            </div>

            {activeClipBound && (
              <div className="rounded-lg border border-border bg-surface-2 p-2.5">
                <div className="mb-2 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wider text-slate-300">
                  <span>Đoạn Clip #{activeClipBound.index + 1}</span>
                  <span className="font-mono text-[10px] text-brand">
                    {(activeClipBound.clip.source_end - activeClipBound.clip.source_start).toFixed(2)}s
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  <div className="rounded bg-surface-1 px-2 py-1">
                    <span className="block text-[10px] text-slate-500">Bắt đầu nguồn</span>
                    <span className="font-mono text-slate-200">{activeClipBound.clip.source_start.toFixed(2)}s</span>
                  </div>
                  <div className="rounded bg-surface-1 px-2 py-1">
                    <span className="block text-[10px] text-slate-500">Kết thúc nguồn</span>
                    <span className="font-mono text-slate-200">{activeClipBound.clip.source_end.toFixed(2)}s</span>
                  </div>
                </div>
                <div className="mt-2 flex gap-1.5">
                  <button className="btn-ghost flex-1 !py-1 !text-[10px]" onClick={() => splitTimelineClip()}>
                    Tách tại playhead
                  </button>
                  {videoClips.length > 1 && (
                    <button
                      className="btn-ghost !py-1 !text-[10px] text-rose-300"
                      onClick={() => deleteTimelineClip(activeClipBound.clip.id)}
                    >
                      <Trash2 size={10} />
                      Xóa đoạn
                    </button>
                  )}
                </div>
              </div>
            )}
          </>
        )}

        {section === "layer" && (
          <>
            <div className="flex flex-wrap gap-1">
              <button className="btn-ghost !px-2 !py-1 !text-[10px]" onClick={() => void handleQuickAddLayer("blur")}>
                <Sparkles size={10} />+ Làm mờ
              </button>
              <button className="btn-ghost !px-2 !py-1 !text-[10px]" onClick={() => void handleQuickAddLayer("mask")}>
                <Square size={10} />+ Khung che
              </button>
              <button className="btn-ghost !px-2 !py-1 !text-[10px]" onClick={() => void handleQuickAddLayer("text")}>
                <Type size={10} />+ Chữ
              </button>
              <button className="btn-ghost !px-2 !py-1 !text-[10px]" onClick={() => void handleQuickAddLayer("logo")}>
                <ImageIcon size={10} />+ Logo
              </button>
            </div>

            {layers.length > 0 && (
              <div className="space-y-1">
                {layers.map((l) => (
                  <button
                    key={l.id}
                    onClick={() => update({ selectedLayerId: l.id })}
                    className={`flex w-full items-center justify-between rounded border px-2 py-1 text-left text-[11px] ${
                      selectedLayer?.id === l.id
                        ? "border-brand bg-brand/10 text-brand"
                        : "border-border bg-surface-2 text-slate-300 hover:bg-surface-3"
                    }`}
                  >
                    <span className="truncate font-medium">
                      {l.label}
                      {l.kind === "text" && l.text ? `: ${l.text}` : ""}
                    </span>
                    <span className="font-mono text-[10px] opacity-75">
                      {l.start.toFixed(1)}s–{l.end.toFixed(1)}s
                    </span>
                  </button>
                ))}
              </div>
            )}

            {selectedLayer ? (
              <div className="space-y-2.5 rounded-lg border border-border bg-surface-2 p-2.5">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-brand">{selectedLayer.label}</span>
                  <button
                    className="text-slate-400 hover:text-rose-400"
                    onClick={() => removeLayer(selectedLayer.id)}
                    title="Xóa lớp phủ này"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>

                <label className="block text-[11px]">
                  <span className="text-slate-400">Tên lớp</span>
                  <input
                    className="field mt-0.5 !py-1 !text-[11px]"
                    value={selectedLayer.label}
                    onChange={(e) => patchLayer(selectedLayer.id, { label: e.target.value })}
                  />
                </label>

                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  <label>
                    <span className="text-slate-400">Bắt đầu (s)</span>
                    <input
                      type="number"
                      step={0.1}
                      min={0}
                      className="field mt-0.5 !py-1 font-mono !text-[11px]"
                      value={selectedLayer.start}
                      onChange={(e) =>
                        patchLayer(selectedLayer.id, {
                          start: Math.max(0, Math.min(Number(e.target.value), selectedLayer.end - 0.1)),
                        })
                      }
                    />
                  </label>
                  <label>
                    <span className="text-slate-400">Kết thúc (s)</span>
                    <input
                      type="number"
                      step={0.1}
                      min={0.1}
                      className="field mt-0.5 !py-1 font-mono !text-[11px]"
                      value={selectedLayer.end}
                      onChange={(e) =>
                        patchLayer(selectedLayer.id, {
                          end: Math.max(selectedLayer.start + 0.1, Number(e.target.value)),
                        })
                      }
                    />
                  </label>
                </div>

                <div className="grid grid-cols-4 gap-1.5 text-[10px]">
                  <label>
                    <span className="text-slate-400">X%</span>
                    <input
                      type="number"
                      min={0}
                      max={96}
                      className="field mt-0.5 !px-1 !py-0.5 font-mono !text-[10px]"
                      value={Math.round(selectedLayer.x * 100)}
                      onChange={(e) =>
                        patchLayer(selectedLayer.id, { x: clamp(Number(e.target.value) / 100, 0, 1 - selectedLayer.w) })
                      }
                    />
                  </label>
                  <label>
                    <span className="text-slate-400">Y%</span>
                    <input
                      type="number"
                      min={0}
                      max={96}
                      className="field mt-0.5 !px-1 !py-0.5 font-mono !text-[10px]"
                      value={Math.round(selectedLayer.y * 100)}
                      onChange={(e) =>
                        patchLayer(selectedLayer.id, { y: clamp(Number(e.target.value) / 100, 0, 1 - selectedLayer.h) })
                      }
                    />
                  </label>
                  <label>
                    <span className="text-slate-400">Rộng%</span>
                    <input
                      type="number"
                      min={4}
                      max={100}
                      className="field mt-0.5 !px-1 !py-0.5 font-mono !text-[10px]"
                      value={Math.round(selectedLayer.w * 100)}
                      onChange={(e) =>
                        patchLayer(selectedLayer.id, { w: clamp(Number(e.target.value) / 100, 0.04, 1 - selectedLayer.x) })
                      }
                    />
                  </label>
                  <label>
                    <span className="text-slate-400">Cao%</span>
                    <input
                      type="number"
                      min={4}
                      max={100}
                      className="field mt-0.5 !px-1 !py-0.5 font-mono !text-[10px]"
                      value={Math.round(selectedLayer.h * 100)}
                      onChange={(e) =>
                        patchLayer(selectedLayer.id, { h: clamp(Number(e.target.value) / 100, 0.04, 1 - selectedLayer.y) })
                      }
                    />
                  </label>
                </div>

                <label className="block text-[11px]">
                  <div className="flex justify-between text-slate-400">
                    <span>Độ đậm / Trong suốt</span>
                    <span className="font-mono text-slate-200">{Math.round(selectedLayer.opacity * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min={5}
                    max={100}
                    value={Math.round(selectedLayer.opacity * 100)}
                    onChange={(e) => patchLayer(selectedLayer.id, { opacity: Number(e.target.value) / 100 })}
                    className="mt-1 h-1 w-full accent-brand"
                  />
                </label>

                {selectedLayer.kind === "blur" && (
                  <label className="block text-[11px]">
                    <div className="flex justify-between text-slate-400">
                      <span>Cường độ làm mờ</span>
                      <span className="font-mono text-slate-200">{selectedLayer.blur_strength ?? 24}</span>
                    </div>
                    <input
                      type="range"
                      min={4}
                      max={80}
                      value={selectedLayer.blur_strength ?? 24}
                      onChange={(e) => patchLayer(selectedLayer.id, { blur_strength: Number(e.target.value) })}
                      className="mt-1 h-1 w-full accent-brand"
                    />
                  </label>
                )}

                {(selectedLayer.kind === "mask" || selectedLayer.kind === "text") && (
                  <label className="flex items-center justify-between text-[11px]">
                    <span className="text-slate-400">Màu sắc</span>
                    <input
                      type="color"
                      value={selectedLayer.color || "#000000"}
                      onChange={(e) => patchLayer(selectedLayer.id, { color: e.target.value })}
                      className="h-6 w-12 cursor-pointer rounded border border-border bg-transparent"
                    />
                  </label>
                )}

                {selectedLayer.kind === "text" && (
                  <>
                    <label className="block text-[11px]">
                      <span className="text-slate-400">Nội dung chữ</span>
                      <textarea
                        rows={2}
                        className="field mt-0.5 !py-1 !text-[11px]"
                        value={selectedLayer.text}
                        onChange={(e) => patchLayer(selectedLayer.id, { text: e.target.value })}
                      />
                    </label>
                    <label className="block text-[11px]">
                      <div className="flex justify-between text-slate-400">
                        <span>Cỡ chữ</span>
                        <span className="font-mono text-slate-200">{selectedLayer.font_size}px</span>
                      </div>
                      <input
                        type="range"
                        min={12}
                        max={96}
                        value={selectedLayer.font_size}
                        onChange={(e) => patchLayer(selectedLayer.id, { font_size: Number(e.target.value) })}
                        className="mt-1 h-1 w-full accent-brand"
                      />
                    </label>
                  </>
                )}

                {selectedLayer.kind === "logo" && (
                  <div className="space-y-1.5 text-[11px]">
                    <div className="truncate font-mono text-[10px] text-slate-400">
                      {selectedLayer.image_path || "Chưa chọn file ảnh"}
                    </div>
                    <button
                      className="btn-ghost w-full !py-1 !text-[11px] text-emerald-300"
                      onClick={async () => {
                        const p = await pickImage();
                        if (p) patchLayer(selectedLayer.id, { image_path: p });
                      }}
                    >
                      <ImageIcon size={11} />
                      Chọn ảnh Logo…
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <p className="py-6 text-center text-[11px] text-slate-500">
                Chưa có lớp phủ nào. Bấm nút ở trên để thêm Làm mờ, Khung che, Chữ hoặc Logo.
              </p>
            )}
          </>
        )}

        {section === "audio" && (
          <div className="space-y-3 rounded-lg border border-border bg-surface-2 p-2.5">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-300">
              <Volume2 size={11} className="text-brand" />
              Âm thanh gốc & Lồng tiếng
            </div>

            <label className="flex cursor-pointer items-center justify-between text-[11px] text-slate-300">
              <span>Giữ âm thanh gốc khi xuất</span>
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-brand"
                checked={keepOriginalAudio}
                onChange={(e) => update({ keepOriginalAudio: e.target.checked })}
              />
            </label>

            <label className="block text-[11px]">
              <div className="flex justify-between text-slate-400">
                <span>Âm lượng gốc (khi có lồng tiếng)</span>
                <span className="font-mono text-slate-200">{Math.round(duckGain * 100)}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={Math.round(duckGain * 100)}
                onChange={(e) => update({ duckGain: Number(e.target.value) / 100 })}
                className="mt-1 h-1 w-full accent-brand"
              />
            </label>

            <label className="block text-[11px]">
              <div className="flex justify-between text-slate-400">
                <span>Âm lượng giọng lồng tiếng</span>
                <span className="font-mono text-slate-200">{Math.round(voiceGain * 100)}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={200}
                value={Math.round(voiceGain * 100)}
                onChange={(e) => update({ voiceGain: Number(e.target.value) / 100 })}
                className="mt-1 h-1 w-full accent-brand"
              />
            </label>

            <label className="flex cursor-pointer items-center justify-between text-[11px] text-slate-300">
              <span>Nghe track lồng tiếng khi xem thử</span>
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-brand"
                checked={previewDub}
                onChange={(e) => update({ previewDub: e.target.checked })}
              />
            </label>

            <label className="flex cursor-pointer items-center justify-between text-[11px] text-slate-300">
              <span>Cắt khoảng lặng đầu/cuối câu TTS</span>
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-brand"
                checked={trimSilence}
                onChange={(e) => update({ trimSilence: e.target.checked })}
              />
            </label>

            <div className="space-y-1.5 border-t border-border pt-2">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-amber-300/90">
                Chống bản quyền nhạc nền
              </div>
              <div className="flex gap-1.5">
                <button
                  className="btn-ghost flex-1 !py-1 !text-[10px]"
                  disabled={!source?.path || Boolean(pending.stripBgm)}
                  onClick={() => void runStripBgm()}
                  title="Dùng AI UVR-MDX tách bỏ nhạc nền bản quyền khỏi âm thanh gốc"
                >
                  <Sparkles size={10} />
                  {pending.stripBgm ? "Đang tách nhạc…" : "Xóa nhạc nền gốc (AI)"}
                </button>
                <button
                  className="btn-ghost flex-1 !py-1 !text-[10px] text-emerald-300"
                  onClick={async () => {
                    const p = await pickAudio();
                    if (p) update({ backgroundAudioPath: p });
                  }}
                  title="Chọn file nhạc nền không bản quyền trộn dưới giọng lồng tiếng"
                >
                  Chọn nhạc nền…
                </button>
              </div>
              {backgroundAudioPath && (
                <div className="flex items-center justify-between gap-2 rounded bg-surface-1 px-2 py-1 font-mono text-[10px] text-emerald-300">
                  <span className="truncate" title={backgroundAudioPath}>
                    {backgroundAudioPath}
                  </span>
                  <button
                    className="shrink-0 text-slate-400 hover:text-rose-400"
                    onClick={() => update({ backgroundAudioPath: null })}
                    title="Bỏ nhạc nền thay thế"
                  >
                    ×
                  </button>
                </div>
              )}
            </div>

            {dub && (
              <div className="rounded border border-border bg-surface-1 p-2 text-[10px] text-slate-400">
                <div>
                  Track lồng tiếng: <span className="font-mono text-emerald-400">{dub.provider}</span> ·{" "}
                  <span className="font-mono text-slate-200">{dub.voice}</span>
                </div>
                <div className="mt-0.5">
                  {dub.segments.length} câu · {dub.duration.toFixed(2)}s
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
