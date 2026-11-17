import { BookmarkPlus, Download, Loader2, RotateCcw, Trash2, Type } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { runExportSubtitles } from "@/lib/tasks";
import { useEditor } from "@/store/editorStore";
import { DEFAULT_SUBTITLE_STYLE, type StylePresetItem, type SubtitleStyle } from "@/types/editor";

/** Fonts that are on every Windows install plus the usual design picks. */
const FONTS = [
  "Arial",
  "Segoe UI",
  "Tahoma",
  "Times New Roman",
  "Be Vietnam Pro",
  "Roboto",
  "Montserrat",
  "Oswald",
  "UTM Impact",
];

const POSITIONS: Array<{ value: SubtitleStyle["position"]; label: string }> = [
  { value: "top", label: "Trên" },
  { value: "middle", label: "Giữa" },
  { value: "bottom", label: "Dưới" },
];

const ALIGNMENTS: Array<{ value: SubtitleStyle["horizontal"]; label: string }> = [
  { value: "left", label: "Trái" },
  { value: "center", label: "Giữa" },
  { value: "right", label: "Phải" },
];

/**
 * Caption look popover.
 *
 * Font, colours and placement are written to the project document and used
 * twice: the preview overlay paints them live and `/api/subtitles/export`
 * turns them into the ASS style burned into the final render. The same panel
 * exports the subtitle file itself, because that is what a user wants after
 * getting the captions looking right.
 */
export function SubtitleStylePanel() {
  const activeTrack = useEditor((state) => state.activeSubtitleTrack);
  const presentationMode = useEditor((state) => state.presentationMode);
  const primaryStyle = useEditor((state) => state.subtitleStyle);
  const secondaryStyle = useEditor((state) => state.secondaryStyle);
  const setPrimaryStyle = useEditor((state) => state.setSubtitleStyle);
  const setSecondaryStyle = useEditor((state) => state.setSecondaryStyle);
  const stylePresets = useEditor((state) => state.stylePresets);
  const update = useEditor((state) => state.update);

  const isSecondary = presentationMode === "bilingual" && activeTrack === 2;
  const style = isSecondary ? secondaryStyle : primaryStyle;
  const setStyle = isSecondary ? setSecondaryStyle : setPrimaryStyle;

  const lines = useEditor((state) => state.segments.length);
  const exporting = useEditor((state) => Boolean(state.pending.subtitles));
  const [open, setOpen] = useState(false);
  const [presetName, setPresetName] = useState("");
  const root = useRef<HTMLDivElement>(null);

  const persistPresets = (next: StylePresetItem[]) => {
    update({ stylePresets: next });
    try {
      window.localStorage.setItem("sva_style_presets", JSON.stringify(next));
    } catch {
      // ignore storage errors
    }
  };

  const handleSavePreset = () => {
    const name = presetName.trim() || `Preset ${stylePresets.length + 1}`;
    const item: StylePresetItem = {
      id: `custom_${Date.now()}`,
      name,
      style: { ...style },
    };
    persistPresets([...stylePresets, item]);
    setPresetName("");
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={root}>
      <button
        className="text-slate-500 hover:text-brand"
        title="Kiểu chữ, vị trí phụ đề và xuất tệp .srt / .ass"
        onClick={() => setOpen(!open)}
      >
        <Type size={11} />
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 max-h-[calc(100vh-12rem)] w-[19rem] space-y-2.5 overflow-y-auto rounded-lg border border-border bg-surface-2 p-2.5 shadow-panel">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
              Kiểu phụ đề {isSecondary ? "(TS2 Song ngữ)" : "(TS1 Chính)"}
            </span>
            <button
              className="rounded px-1 py-0.5 text-[10px] text-slate-500 hover:bg-surface-3 hover:text-slate-200"
              title="Trở về kiểu mặc định"
              onClick={() => setStyle(DEFAULT_SUBTITLE_STYLE)}
            >
              <RotateCcw size={9} className="mr-1 inline" />
              Mặc định
            </button>
          </div>

          {/* My Presets (CapCap Style Preset Manager) */}
          <div className="space-y-1 rounded border border-border/70 bg-surface-1 p-1.5">
            <span className="text-[9px] font-semibold uppercase tracking-wider text-slate-500">
              My Presets ({stylePresets.length})
            </span>
            <div className="flex flex-wrap gap-1">
              {stylePresets.map((p) => (
                <div key={p.id} className="flex items-center overflow-hidden rounded border border-border bg-surface-2">
                  <button
                    className="px-1.5 py-0.5 text-[10px] text-slate-300 hover:text-brand"
                    onClick={() => setStyle(p.style)}
                  >
                    {p.name}
                  </button>
                  {p.id.startsWith("custom_") && (
                    <button
                      className="px-1 text-slate-500 hover:text-rose-400"
                      onClick={() => persistPresets(stylePresets.filter((item) => item.id !== p.id))}
                    >
                      <Trash2 size={9} />
                    </button>
                  )}
                </div>
              ))}
            </div>
            <div className="flex gap-1 pt-0.5">
              <input
                className="field flex-1 !px-1.5 !py-0.5 !text-[10px]"
                value={presetName}
                placeholder="Tên preset mới…"
                onChange={(e) => setPresetName(e.target.value)}
              />
              <button
                className="btn-ghost !px-1.5 !py-0.5 !text-[10px]"
                onClick={handleSavePreset}
                title="Lưu kiểu chữ hiện tại vào My Presets"
              >
                <BookmarkPlus size={10} />
                Lưu
              </button>
            </div>
          </div>

          <label className="block space-y-1">
            <span className="text-[10px] text-slate-500">Phông chữ</span>
            <input
              className="field w-full !px-1.5 !py-1 !text-[11px]"
              list="subtitle-fonts"
              value={style.font}
              spellCheck={false}
              onChange={(event) => setStyle({ font: event.target.value })}
            />
            <datalist id="subtitle-fonts">
              {FONTS.map((font) => (
                <option key={font} value={font} />
              ))}
            </datalist>
          </label>

          <Slider
            label="Cỡ chữ"
            value={style.size}
            min={0.6}
            max={2.2}
            step={0.05}
            display={`${Math.round(style.size * 100)}%`}
            onChange={(size) => setStyle({ size })}
          />

          <div className="flex items-center gap-3">
            <Swatch label="Chữ" color={style.color} onChange={(color) => setStyle({ color })} />
            <Swatch
              label="Viền"
              color={style.outline_color}
              onChange={(outline_color) => setStyle({ outline_color })}
            />
            <button
              className={`ml-auto rounded border px-1.5 py-0.5 text-[10px] ${
                style.bold
                  ? "border-brand/60 bg-brand/15 font-bold text-brand"
                  : "border-border text-slate-400 hover:text-slate-200"
              }`}
              title="In đậm"
              onClick={() => setStyle({ bold: !style.bold })}
            >
              B
            </button>
          </div>

          <Slider
            label="Độ dày viền"
            value={style.outline}
            min={0}
            max={3}
            step={0.25}
            display={style.outline === 0 ? "tắt" : style.outline.toFixed(2)}
            onChange={(outline) => setStyle({ outline })}
          />

          <div className="space-y-1.5 rounded border border-border/70 bg-surface-1 p-1.5">
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-[10px] text-slate-400">
                <input
                  type="checkbox"
                  checked={style.background}
                  onChange={(event) => setStyle({ background: event.target.checked })}
                />
                Nền chữ
              </label>
              <Swatch
                label=""
                color={style.background_color}
                onChange={(background_color) => setStyle({ background_color })}
              />
            </div>
            <Slider
              label="Độ mờ nền"
              value={style.background_opacity}
              min={0}
              max={1}
              step={0.05}
              display={`${Math.round(style.background_opacity * 100)}%`}
              onChange={(background_opacity) => setStyle({ background_opacity })}
              disabled={!style.background}
            />
          </div>

          <div className="space-y-1">
            <span className="text-[10px] text-slate-500">Vị trí</span>
            <div className="grid grid-cols-3 gap-1">
              {POSITIONS.map((item) => (
                <SegButton
                  key={item.value}
                  active={style.position === item.value}
                  label={item.label}
                  onClick={() => setStyle({ position: item.value })}
                />
              ))}
            </div>
            <div className="grid grid-cols-3 gap-1">
              {ALIGNMENTS.map((item) => (
                <SegButton
                  key={item.value}
                  active={style.horizontal === item.value}
                  label={item.label}
                  onClick={() => setStyle({ horizontal: item.value })}
                />
              ))}
            </div>
          </div>

          <Slider
            label={style.position === "top" ? "Cách mép trên" : "Cách mép dưới"}
            value={style.margin_v}
            min={0}
            max={40}
            step={1}
            display={`${style.margin_v}%`}
            onChange={(margin_v) => setStyle({ margin_v })}
            disabled={style.position === "middle"}
          />
          <Slider
            label="Lề hai bên"
            value={style.margin_h}
            min={0}
            max={30}
            step={1}
            display={`${style.margin_h}%`}
            onChange={(margin_h) => setStyle({ margin_h })}
          />

          <div className="flex items-center gap-1 border-t border-border pt-2">
            <span className="text-[10px] text-slate-500">Xuất phụ đề</span>
            <div className="ml-auto flex gap-1">
              <button
                className="btn-ghost !px-2 !py-0.5 !text-[10px]"
                disabled={!lines || exporting}
                title="Tệp .ass mang đúng kiểu chữ và vị trí ở trên"
                onClick={() => void runExportSubtitles("ass")}
              >
                {exporting ? <Loader2 size={10} className="animate-spin" /> : <Download size={10} />}
                .ass
              </button>
              <button
                className="btn-ghost !px-2 !py-0.5 !text-[10px]"
                disabled={!lines || exporting}
                title="Tệp .srt chữ thuần cho YouTube / CapCut / Premiere"
                onClick={() => void runExportSubtitles("srt")}
              >
                {exporting ? <Loader2 size={10} className="animate-spin" /> : <Download size={10} />}
                .srt
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  display,
  onChange,
  disabled,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  return (
    <label className={`block space-y-0.5 ${disabled ? "opacity-40" : ""}`}>
      <span className="flex items-center justify-between text-[10px] text-slate-500">
        {label}
        <span className="font-mono text-slate-400">{display}</span>
      </span>
      <input
        type="range"
        className="h-1 w-full accent-brand"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function Swatch({
  label,
  color,
  onChange,
}: {
  label: string;
  color: string;
  onChange: (color: string) => void;
}) {
  return (
    <label className="flex items-center gap-1 text-[10px] text-slate-500">
      {label && <span>{label}</span>}
      <input
        type="color"
        className="h-5 w-6 cursor-pointer rounded border border-border bg-transparent p-0"
        value={color}
        onChange={(event) => onChange(event.target.value.toUpperCase())}
      />
    </label>
  );
}

function SegButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      className={`rounded border px-1 py-0.5 text-[10px] ${
        active
          ? "border-brand/60 bg-brand/15 text-brand"
          : "border-border text-slate-400 hover:text-slate-200"
      }`}
      onClick={onClick}
    >
      {label}
    </button>
  );
}
