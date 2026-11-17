/**
 * "Chỗ thêm model": the missing half of every online provider.
 *
 * Until this panel existed a key could only arrive through `SVA_*` variables in
 * `backend/.env`, which a packaged app does not have. Everything here is
 * persisted by the engine in `<data>/settings/providers.json` and overlayed onto
 * the settings of every service call, so what is typed is what runs.
 *
 * Four sections, one per decision the pipeline needs up front:
 *   1. keys + model names for Gemini / OpenAI / Groq / VoiceStudio (with a live
 *      "Kiểm tra" that lists the models the key can actually reach);
 *   2. which GGUF the offline translator loads (Qwen2.5 3B or Gemma 3 4B);
 *   3. OCR engine, device (GPU first, CPU fallback), scan rectangle and the
 *      speed/accuracy profile, with the optional tiny detector;
 *   4. audio mode for subtitle extraction — CapCap's vocal separation.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Cpu,
  Download,
  Eye,
  EyeOff,
  KeyRound,
  Languages,
  Loader2,
  PlugZap,
  ScanText,
  TriangleAlert,
  Waves,
  Zap,
} from "lucide-react";

import { refreshModels } from "@/hooks/useProgressSocket";
import { downloadModel, fetchSettings, saveSettings, testProvider } from "@/lib/api";
import {
  REGION_PRESETS,
  fitRegion,
  formatRegion,
  regionFrom,
  regionLabel,
  roundPercent,
  type Region,
} from "@/lib/region";
import { useEditor } from "@/store/editorStore";
import type {
  ProviderConfig,
  ProviderPatchBody,
  ProviderTestResult,
  SettingsDocument,
  SettingsPatchBody,
} from "@/types/editor";

const KIND_LABEL: Record<string, string> = {
  translate: "Dịch",
  stt: "Nhận dạng",
  tts: "Giọng đọc",
};

/** Log line after a device change; the engine names the real one back. */
const DEVICE_LABEL: Record<string, string> = {
  auto: "Tự động (GPU nếu có)",
  gpu: "GPU",
  cpu: "CPU",
};

/** Fallback frame ratios when no clip is loaded, so the picker keeps its shape. */
const ASPECT_RATIO: Record<string, number> = { "9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1 };

export function SettingsPanel() {
  const settings = useEditor((state) => state.settings);
  const setSettings = useEditor((state) => state.setSettings);
  const pushLog = useEditor((state) => state.pushLog);
  const models = useEditor((state) => state.models);
  const job = useEditor((state) => state.job);
  const source = useEditor((state) => state.source);
  const aspect = useEditor((state) => state.aspect);
  // The OCR runs on the source frame, so the picker's shape follows the clip,
  // not the exported crop.
  const frameAspect = source && source.width > 0 ? source.width / source.height : ASPECT_RATIO[aspect];

  const [busyModel, setBusyModel] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setSettings(await fetchSettings());
      setFailed(false);
    } catch (error) {
      setFailed(true);
      pushLog("error", `Không đọc được cấu hình: ${(error as Error).message}`);
    }
  }, [setSettings, pushLog]);

  // The panel can be opened before the boot probe finished (or after it failed).
  useEffect(() => {
    if (!settings) void load();
  }, [settings, load]);

  // A finished weight download changes the "đã cài" badges on this panel.
  useEffect(() => {
    if (job?.state !== "done" || !job.kind.startsWith("model-download:")) return;
    void load();
    void refreshModels();
  }, [job?.id, job?.state, job?.kind, load]);

  const persist = async (patch: SettingsPatchBody, note?: string): Promise<SettingsDocument> => {
    const document = await saveSettings(patch);
    setSettings(document);
    if (note) pushLog("success", note);
    return document;
  };

  const saveRegion = (region: Region | null) => {
    const value = formatRegion(region);
    void persist(
      { ocr: { region: value } },
      region ? `Vùng quét OCR: ${value}` : "Vùng quét OCR: dải mặc định",
    ).catch((error: Error) => pushLog("error", `Lưu vùng quét thất bại: ${error.message}`));
  };

  const install = async (key: string, label: string) => {
    setBusyModel(key);
    try {
      await downloadModel(key);
      pushLog("info", `Đang tải ${label}…`);
    } catch (error) {
      pushLog("error", `Tải model thất bại: ${(error as Error).message}`);
    } finally {
      setBusyModel(null);
    }
  };

  if (!settings) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-[11px] text-slate-500">
        {failed ? (
          <>
            <span>Không đọc được cấu hình engine.</span>
            <button className="btn-ghost !text-[10px]" onClick={() => void load()}>
              Thử lại
            </button>
          </>
        ) : (
          <>
            <Loader2 size={12} className="animate-spin" />
            Đang đọc cấu hình engine…
          </>
        )}
      </div>
    );
  }

  const downloading = job?.state === "running" && job.kind.startsWith("model-download:") ? job : null;
  const progress = downloading ? Math.round(downloading.progress * 100) : 0;
  const tinyProfile = settings.ocr.profiles.find((profile) => profile.name === "tiny");
  const korean = models.find((model) => model.key === "ocr-korean-v5");
  const separatorKey = settings.audio.separator_model;

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      {/* 1 — online providers ------------------------------------------------ */}
      <Section icon={<KeyRound size={12} />} title="1 · API key & model online">
        <p className="text-[10px] leading-relaxed text-slate-500">
          Key được lưu trong <span className="font-mono">providers.json</span> của engine và chỉ dùng ở máy
          này. Ô model là văn bản tự do: gõ đúng tên model mới là dùng được ngay, không cần cập nhật app.
        </p>
        {settings.providers.map((provider) => (
          <ProviderCard key={provider.name} provider={provider} pushLog={pushLog} persist={persist} />
        ))}
      </Section>

      {/* 2 — OCR speed / accuracy ------------------------------------------- */}
      <Section icon={<ScanText size={12} />} title="2 · OCR — đọc phụ đề cứng">
        <Labelled label="Engine">
          <select
            className="field"
            value={settings.ocr.engine}
            onChange={(event) => {
              const engine = event.target.value;
              void persist({ ocr: { engine } }, `Engine OCR: ${engine}`).catch((error: Error) =>
                pushLog("error", `Lưu engine thất bại: ${error.message}`),
              );
            }}
          >
            {settings.ocr.engines.map((engine) => (
              <option key={engine} value={engine}>
                {engine === "rapidocr"
                  ? "RapidOCR (ONNX, khuyên dùng)"
                  : engine === "winocr"
                    ? "Windows Media OCR (hệ thống)"
                    : engine}
              </option>
            ))}
          </select>
        </Labelled>

        {/* Where the reading runs: reading frames is the slow half of OCR. */}
        <Labelled label="Thiết bị chạy">
          <select
            className="field"
            value={settings.ocr.device}
            onChange={(event) => {
              const device = event.target.value;
              void persist({ ocr: { device } }, `Thiết bị OCR: ${DEVICE_LABEL[device] ?? device}`).catch(
                (error: Error) => pushLog("error", `Lưu thiết bị thất bại: ${error.message}`),
              );
            }}
          >
            {settings.ocr.devices.map((device) => (
              <option key={device} value={device}>
                {device === "auto"
                  ? "Tự động — GPU nếu có, không thì CPU"
                  : device === "gpu"
                    ? "GPU — CUDA hoặc DirectML"
                    : "CPU — chậm nhất nhưng luôn chạy"}
              </option>
            ))}
          </select>
        </Labelled>

        <div className="flex items-center justify-between gap-2 rounded bg-surface-1 px-2 py-1.5 text-[10px]">
          {settings.ocr.device_actual === "gpu" ? (
            <span className="flex items-center gap-1 font-medium text-emerald-400">
              <Zap size={10} /> Đang đọc bằng GPU
            </span>
          ) : (
            <span className="flex items-center gap-1 font-medium text-amber-300">
              <Cpu size={10} /> Đang đọc bằng CPU
            </span>
          )}
          <span
            className="truncate font-mono text-[9px] text-slate-500"
            title={settings.ocr.onnx.providers.join(", ")}
          >
            {settings.ocr.onnx.installed
              ? `${settings.ocr.onnx.distribution} ${settings.ocr.onnx.version}`
              : "chưa có onnxruntime"}
          </span>
        </div>

        {settings.ocr.device !== "cpu" && !settings.ocr.onnx.gpu_ready && (
          <Hint tone="warn" icon={<TriangleAlert size={11} />}>
            <span className="block">
              Gói ONNX đang cài chỉ có CPU nên phụ đề vẫn được đọc bằng CPU. Ba bản dưới đây cài{" "}
              <span className="font-medium">cùng một</span> gói <span className="font-mono">onnxruntime</span>,
              nên chọn một rồi cài đè (gỡ bản cũ trước), không cài song song:
            </span>
            {settings.ocr.onnx.packs
              .filter((pack) => pack.name !== "onnxruntime")
              .map((pack) => (
                <span key={pack.name} className="mt-1 block">
                  <span className="text-slate-300">{pack.label}</span>
                  <span className="block break-all font-mono text-[9px] text-slate-400">{pack.command}</span>
                </span>
              ))}
            <span className="mt-1 block text-[9px] text-slate-500">
              Gỡ bản cũ: <span className="font-mono">pip uninstall -y onnxruntime onnxruntime-directml onnxruntime-gpu</span>{" "}
              — hoặc chạy <span className="font-mono">npm run setup -- -OcrGpu</span> /{" "}
              <span className="font-mono">-OcrCuda</span> rồi khởi động lại engine.
            </span>
          </Hint>
        )}

        {settings.ocr.device_actual === "gpu" && (
          <Hint tone="info" icon={<Zap size={11} />}>
            ONNX đang chạy trên GPU ({settings.ocr.onnx.gpu_provider}): đọc khung hình nhanh hơn nhiều lần so
            với CPU. Chọn <span className="font-medium text-slate-300">CPU</span> ở trên thì GPU không được
            đụng tới; nếu driver đồ hoạ gặp sự cố giữa chừng, engine tự quay về CPU cho xong việc thay vì
            báo lỗi.
          </Hint>
        )}

        {/* Where to look: a whole-frame scan reads logos and titles as lines. */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[9px] uppercase tracking-wide text-slate-500">Vùng quét phụ đề</span>
            <span className="text-[9px] text-slate-500">
              {settings.ocr.region_stored ? (
                <span className="font-mono">{regionLabel(settings.ocr.region)}</span>
              ) : (
                "dải mặc định"
              )}
            </span>
          </div>

          <RegionPicker
            region={settings.ocr.region}
            aspect={frameAspect}
            span={settings.ocr.region_span}
            stored={settings.ocr.region_stored}
            onChange={saveRegion}
            onReset={() => saveRegion(null)}
          />

          <Hint tone="info">
            Chỉ đọc đúng vùng có phụ đề: logo, tên kênh hay chữ quảng cáo nằm ngoài vùng sẽ không bị đọc
            thành dòng phụ đề. Kéo trên khung để vẽ lại, sửa 4 ô phần trăm, hoặc bấm{" "}
            <span className="font-medium text-slate-300">Vùng OCR</span> ở thanh xem trước để vẽ trực
            tiếp trên video thật.
          </Hint>
        </div>

        <div className="space-y-1.5">
          {settings.ocr.profiles.map((profile) => {
            const active = settings.ocr.profile === profile.name;
            return (
              <button
                key={profile.name}
                onClick={() => {
                  void persist({ ocr: { profile: profile.name } }, `OCR: ${profile.label}`).catch(
                    (error: Error) => pushLog("error", `Lưu profile thất bại: ${error.message}`),
                  );
                }}
                className={`w-full rounded-md border px-2.5 py-2 text-left transition-colors ${
                  active
                    ? "border-brand/60 bg-brand/10"
                    : "border-border bg-surface-1 hover:border-slate-500"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1 text-[11px] font-medium text-slate-200">
                    {active ? (
                      <CheckCircle2 size={10} className="shrink-0 text-brand" />
                    ) : (
                      <span className="h-2.5 w-2.5 shrink-0 rounded-full border border-slate-600" />
                    )}
                    {profile.label}
                  </span>
                  <span className="shrink-0 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-slate-400">
                    {profile.speed}
                  </span>
                </div>
                <div className="mt-1 space-y-0.5 pl-3.5 font-mono text-[9px] text-slate-500">
                  <div>{profile.detector}</div>
                  <div>{profile.recognizer}</div>
                </div>
                <p className="mt-1 pl-3.5 text-[10px] leading-relaxed text-slate-500">{profile.note}</p>
              </button>
            );
          })}
        </div>

        {tinyProfile && !tinyProfile.installed && (
          <button
            className="btn-ghost w-full !text-[10px]"
            onClick={() => void install("ocr-ppocrv6-tiny", "PP-OCRv6 Tiny")}
            disabled={busyModel === "ocr-ppocrv6-tiny" || Boolean(downloading)}
          >
            {busyModel === "ocr-ppocrv6-tiny" ? (
              <Loader2 size={10} className="animate-spin" />
            ) : (
              <Download size={10} />
            )}
            Tải bộ dò Tiny (31 MB) cho chế độ nhanh nhất
          </button>
        )}

        <div className="flex items-center justify-between rounded bg-surface-1 px-2 py-1.5 text-[10px]">
          <span className="text-slate-500">Phụ đề tiếng Hàn (PP-OCRv5)</span>
          {korean?.downloaded ? (
            <span className="flex items-center gap-1 text-emerald-400">
              <CheckCircle2 size={9} /> đã cài
            </span>
          ) : (
            <button
              className="text-brand hover:underline disabled:text-slate-600"
              onClick={() => void install("ocr-korean-v5", "PP-OCRv5 Korean")}
              disabled={Boolean(downloading)}
            >
              Tải về
            </button>
          )}
        </div>

        <Hint tone="info">
          Đọc theo bảng chữ của "Ngôn ngữ gốc" ở tab AI. Tiếng Hàn dùng PP-OCRv5 vì PP-OCRv6 chưa có bộ
          nhận dạng tiếng Hàn; tiếng Nhật với chế độ Tiny tự lùi về bộ dò Small.
        </Hint>

        <p className="break-all text-[9px] leading-relaxed text-slate-600">
          Model: <span className="font-mono">{settings.ocr.model_dir}</span>
          {settings.ocr.managed ? " · đang đọc từ thư mục này" : " · dùng bộ đi kèm"}
        </p>
      </Section>

      {/* 4 — audio mode for subtitle extraction ------------------------------ */}
      <Section icon={<Waves size={12} />} title="4 · Âm thanh khi lấy phụ đề">
        <div className="space-y-1.5">
          {settings.audio.modes.map((mode) => {
            const active = settings.audio.mode === mode.name;
            return (
              <button
                key={mode.name}
                onClick={() => {
                  void persist({ audio: { mode: mode.name } }, `Chế độ âm thanh: ${mode.label}`).catch(
                    (error: Error) => pushLog("error", `Lưu chế độ thất bại: ${error.message}`),
                  );
                }}
                className={`w-full rounded-md border px-2.5 py-2 text-left transition-colors ${
                  active ? "border-brand/60 bg-brand/10" : "border-border bg-surface-1 hover:border-slate-500"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1 text-[11px] font-medium text-slate-200">
                    {active ? (
                      <CheckCircle2 size={10} className="shrink-0 text-brand" />
                    ) : (
                      <span className="h-2.5 w-2.5 shrink-0 rounded-full border border-slate-600" />
                    )}
                    {mode.label}
                  </span>
                  <span
                    className={`shrink-0 text-[9px] uppercase tracking-wide ${
                      mode.ready ? "text-emerald-400" : "text-amber-300"
                    }`}
                  >
                    {mode.ready ? "sẵn sàng" : "chưa cài"}
                  </span>
                </div>
                <p className="mt-1 pl-3.5 text-[10px] leading-relaxed text-slate-500">{mode.note}</p>
              </button>
            );
          })}
        </div>

        {!settings.audio.package_installed && (
          <Hint tone="warn" icon={<TriangleAlert size={10} />}>
            Thiếu gói <span className="font-mono">audio-separator</span> — chế độ Giọng sạch sẽ tự lùi về
            Nhanh. Cài trong <span className="font-mono">requirements-ai.txt</span>.
          </Hint>
        )}
        {!settings.audio.separator_installed && (
          <button
            className="btn-ghost w-full !text-[10px]"
            onClick={() => void install(separatorKey, "UVR-MDX-NET-Inst_HQ_3")}
            disabled={busyModel === separatorKey || Boolean(downloading)}
          >
            {busyModel === separatorKey ? (
              <Loader2 size={10} className="animate-spin" />
            ) : (
              <Download size={10} />
            )}
            Tải model tách giọng (UVR MDX-Net)
          </button>
        )}

        {downloading && (
          <div>
            <div className="h-1 overflow-hidden rounded bg-surface-4">
              <div className="h-full bg-brand transition-[width]" style={{ width: `${progress}%` }} />
            </div>
            <div className="mt-0.5 truncate text-[9px] text-slate-500">{downloading.message}</div>
          </div>
        )}
      </Section>

      <p className="break-all text-[9px] leading-relaxed text-slate-600">
        Lưu tại: <span className="font-mono">{settings.path}</span>
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * The subtitle scan rectangle, drawn on a schematic of the frame.
 *
 * OCR reads whatever it is given, so anything else printed on the frame — a
 * channel bug, a logo, a "part 2" banner — becomes a subtitle line. Drawing
 * the box on the clip's real aspect is the clearest way to say "read here and
 * nowhere else"; the four percentages are for the last few pixels.
 */
function RegionPicker({
  region,
  aspect,
  span,
  stored,
  onChange,
  onReset,
}: {
  region: Region;
  aspect: number;
  span: number;
  stored: boolean;
  onChange: (region: Region) => void;
  onReset: () => void;
}) {
  const key = region.join(",");
  const [draft, setDraft] = useState<Region>(region);
  const [seen, setSeen] = useState(key);
  const [drawing, setDrawing] = useState(false);
  const origin = useRef<{ x: number; y: number } | null>(null);
  // Committing on pointer-up would read the state from the render the drag
  // started in, so the latest value is kept here as well.
  const latest = useRef<Region>(region);

  if (seen !== key) {
    setSeen(key);
    latest.current = region;
    setDraft(region);
  }

  const width = Math.round(aspect >= 1 ? 180 : Math.max(84, 150 * aspect));
  const height = Math.round(width / Math.max(aspect, 0.1));

  const show = (value: Region) => {
    latest.current = value;
    setDraft(value);
  };

  const point = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(100, Math.max(0, ((event.clientX - rect.left) / rect.width) * 100)),
      y: Math.min(100, Math.max(0, ((event.clientY - rect.top) / rect.height) * 100)),
    };
  };

  const drawTo = (start: { x: number; y: number }, end: { x: number; y: number }) =>
    show(regionFrom(start, end, span));

  const commit = (value: Region) => {
    if (value.join(",") !== key) onChange(value);
  };

  const fields: { label: string; index: 0 | 1 | 2 | 3 }[] = [
    { label: "Trái", index: 0 },
    { label: "Trên", index: 1 },
    { label: "Phải", index: 2 },
    { label: "Dưới", index: 3 },
  ];

  return (
    <div className="space-y-1.5">
      <div className="flex justify-center rounded-md border border-border bg-surface-1 px-2 py-1.5">
        <div
          className="relative shrink-0 cursor-crosshair touch-none overflow-hidden rounded border border-slate-600 bg-black"
          style={{ width, height }}
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture(event.pointerId);
            origin.current = point(event);
            setDrawing(true);
            drawTo(origin.current, origin.current);
          }}
          onPointerMove={(event) => {
            if (drawing && origin.current) drawTo(origin.current, point(event));
          }}
          onPointerUp={() => {
            if (!drawing) return;
            setDrawing(false);
            origin.current = null;
            commit(latest.current);
          }}
          onPointerCancel={() => {
            setDrawing(false);
            origin.current = null;
          }}
        >
          {[25, 50, 75].map((line) => (
            <div
              key={line}
              className="pointer-events-none absolute inset-x-0 border-t border-dashed border-white/10"
              style={{ top: `${line}%` }}
            />
          ))}
          <div
            className="pointer-events-none absolute border border-brand bg-brand/20"
            style={{
              left: `${draft[0]}%`,
              top: `${draft[1]}%`,
              width: `${draft[2] - draft[0]}%`,
              height: `${draft[3] - draft[1]}%`,
            }}
          />
        </div>
      </div>

      <div className="grid grid-cols-4 gap-1">
        {fields.map((field) => (
          <label key={field.label} className="block space-y-0.5">
            <span className="block text-center text-[9px] text-slate-500">{field.label}</span>
            <input
              className="field !px-1 !py-0.5 text-center !text-[10px]"
              type="number"
              min={0}
              max={100}
              step={0.5}
              value={draft[field.index]}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (!Number.isFinite(value)) return;
                const next = [...draft] as Region;
                next[field.index] = Math.min(100, Math.max(0, roundPercent(value)));
                show(next);
              }}
              onBlur={() => {
                const value = fitRegion(latest.current, span);
                show(value);
                commit(value);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur();
              }}
            />
          </label>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-1">
        {REGION_PRESETS.map((preset) => (
          <button
            key={preset.label}
            className="rounded border border-border bg-surface-1 px-1.5 py-0.5 text-[9px] text-slate-400 transition-colors hover:border-slate-500 hover:text-slate-200"
            onClick={() => {
              show(preset.region);
              onChange(preset.region);
            }}
          >
            {preset.label}
          </button>
        ))}
        {stored && (
          <button className="text-[9px] text-slate-500 hover:text-slate-300" onClick={onReset}>
            về mặc định
          </button>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

function ProviderCard({
  provider,
  persist,
  pushLog,
}: {
  provider: ProviderConfig;
  persist: (patch: SettingsPatchBody, note?: string) => Promise<SettingsDocument>;
  pushLog: (level: "info" | "success" | "warn" | "error", text: string) => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [reveal, setReveal] = useState(false);
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [slots, setSlots] = useState<Record<string, string>>(() =>
    Object.fromEntries(provider.slots.map((slot) => [slot.key, slot.value])),
  );
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [result, setResult] = useState<ProviderTestResult | null>(null);

  // A save echoes the merged document back: adopt its values (e.g. after the
  // engine normalises an empty base URL to the default host). The *content*
  // signature is what triggers the sync, so typing here survives a save made in
  // another card of this panel.
  const signature = `${provider.base_url}@${provider.slots.map((slot) => slot.value).join("|")}`;
  const [seen, setSeen] = useState(signature);
  if (seen !== signature) {
    setSeen(signature);
    setBaseUrl(provider.base_url);
    setSlots(Object.fromEntries(provider.slots.map((slot) => [slot.key, slot.value])));
  }

  const patch = (): ProviderPatchBody => {
    const values: ProviderPatchBody = { slots };
    if (apiKey.trim()) values.api_key = apiKey.trim();
    if (baseUrl.trim() !== provider.base_url) values.base_url = baseUrl.trim();
    return values;
  };

  const save = async () => {
    setBusy("save");
    try {
      await persist({ providers: { [provider.name]: patch() } }, `${provider.label}: đã lưu cấu hình`);
      setApiKey("");
      setResult(null);
    } catch (error) {
      pushLog("error", `Lưu ${provider.label} thất bại: ${(error as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const clearKey = async () => {
    try {
      await persist({ providers: { [provider.name]: { api_key: "" } } }, `${provider.label}: đã xoá API key`);
      setApiKey("");
      setResult(null);
    } catch (error) {
      pushLog("error", `Xoá key thất bại: ${(error as Error).message}`);
    }
  };

  const test = async () => {
    setBusy("test");
    try {
      setResult(await testProvider(provider.name, { ...patch(), api_key: apiKey.trim() || undefined }));
    } catch (error) {
      setResult({ ok: false, message: (error as Error).message, models: [], detail: "" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className="rounded-md border border-border bg-surface-1 px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[11px] font-medium text-slate-200">
          <Languages size={10} className="shrink-0 text-slate-500" />
          {provider.label}
        </span>
        <span className="shrink-0 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-slate-400">
          {KIND_LABEL[provider.kind] ?? provider.kind}
        </span>
      </div>

      <div className="mt-1.5 space-y-1.5">
        <div className="flex items-center gap-1">
          <div className="relative flex-1">
            <input
              className="field !py-1 pr-7 font-mono !text-[10px]"
              type={reveal ? "text" : "password"}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={provider.api_key_set ? "•••••••• (đã lưu — nhập để thay)" : "dán API key"}
              autoComplete="off"
              spellCheck={false}
            />
            <button
              type="button"
              className="absolute right-1 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              onClick={() => setReveal((value) => !value)}
              title={reveal ? "Ẩn key" : "Hiện key"}
            >
              {reveal ? <EyeOff size={10} /> : <Eye size={10} />}
            </button>
          </div>
          <button
            className="btn-ghost !px-2 !py-1 !text-[10px]"
            onClick={() => void test()}
            disabled={busy !== null}
            title="Gửi một yêu cầu thật để kiểm tra key và liệt kê model"
          >
            {busy === "test" ? <Loader2 size={10} className="animate-spin" /> : <PlugZap size={10} />}
            Kiểm tra
          </button>
        </div>

        <div className="flex items-center justify-between text-[9px] text-slate-600">
          <span>
            {provider.api_key_set ? (
              provider.api_key_from_env ? (
                "đang dùng key từ biến môi trường"
              ) : (
                <span className="text-emerald-400">đã lưu key</span>
              )
            ) : (
              "chưa có key"
            )}
          </span>
          <span className="flex items-center gap-2">
            {provider.api_key_set && (
              <button className="hover:text-rose-300" onClick={() => void clearKey()}>
                xoá
              </button>
            )}
            <a className="hover:text-brand" href={provider.key_url} target="_blank" rel="noreferrer">
              lấy key
            </a>
          </span>
        </div>

        {provider.slots.map((slot) => (
          <label key={slot.key} className="block space-y-0.5">
            <span className="flex items-center justify-between text-[9px] uppercase tracking-wide text-slate-500">
              {slot.label}
              {slot.from_env && <span className="font-mono normal-case text-amber-400/80">{slot.from_env}</span>}
            </span>
            <input
              className="field !py-1 font-mono !text-[10px]"
              value={slots[slot.key] ?? ""}
              onChange={(event) => setSlots((values) => ({ ...values, [slot.key]: event.target.value }))}
              placeholder={slot.default}
              list={`${provider.name}-${slot.key}-presets`}
              spellCheck={false}
            />
            <datalist id={`${provider.name}-${slot.key}-presets`}>
              {slot.presets.map((preset) => (
                <option key={preset} value={preset} />
              ))}
            </datalist>
          </label>
        ))}

        <button
          className="text-[9px] text-slate-500 hover:text-slate-300"
          onClick={() => setAdvanced((value) => !value)}
        >
          {advanced ? "− Ẩn endpoint" : "+ Endpoint tuỳ chỉnh (proxy / gateway)"}
        </button>
        {advanced && (
          <input
            className="field !py-1 font-mono !text-[10px]"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder={provider.base_url_default}
            spellCheck={false}
          />
        )}

        {provider.notes && <p className="text-[9px] leading-relaxed text-slate-600">{provider.notes}</p>}

        {result && <TestReport result={result} onPick={(name) => pickModel(slots, provider, name, setSlots)} />}

        <div className="flex justify-end">
          <button className="btn-primary !px-2 !py-1 !text-[10px]" onClick={() => void save()} disabled={busy !== null}>
            {busy === "save" ? <Loader2 size={10} className="animate-spin" /> : <CheckCircle2 size={10} />}
            Lưu
          </button>
        </div>
      </div>
    </article>
  );
}

/** Clicking a model id fills the provider's first slot — the usual next step. */
function pickModel(
  slots: Record<string, string>,
  provider: ProviderConfig,
  name: string,
  setSlots: (values: Record<string, string>) => void,
) {
  const target = provider.slots[0];
  if (!target) return;
  setSlots({ ...slots, [target.key]: name });
}

function TestReport({ result, onPick }: { result: ProviderTestResult; onPick: (name: string) => void }) {
  return (
    <div
      className={`rounded px-2 py-1.5 text-[10px] leading-relaxed ${
        result.ok ? "bg-emerald-500/10 text-emerald-300" : "bg-rose-500/10 text-rose-300"
      }`}
    >
      <div className="flex items-start gap-1">
        {result.ok ? (
          <CheckCircle2 size={9} className="mt-0.5 shrink-0" />
        ) : (
          <TriangleAlert size={9} className="mt-0.5 shrink-0" />
        )}
        <span>{result.message}</span>
      </div>
      {result.detail && (
        <p className="mt-1 break-all font-mono text-[9px] text-slate-500">{result.detail}</p>
      )}
      {result.models.length > 0 && (
        <div className="mt-1.5 flex max-h-24 flex-wrap gap-1 overflow-y-auto">
          {result.models.slice(0, 40).map((name) => (
            <button
              key={name}
              className="rounded bg-surface-3 px-1 py-0.5 font-mono text-[9px] text-slate-300 hover:bg-brand/20 hover:text-brand"
              onClick={() => onPick(name)}
              title="Dùng model này"
            >
              {name}
            </button>
          ))}
          {result.models.length > 40 && (
            <span className="px-1 text-[9px] text-slate-500">+{result.models.length - 40} nữa</span>
          )}
        </div>
      )}
    </div>
  );
}

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-surface-2">
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        {icon}
        {title}
      </div>
      <div className="space-y-2 p-3">{children}</div>
    </section>
  );
}

function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function Hint({
  tone,
  icon,
  children,
}: {
  tone: "warn" | "info";
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <p
      className={`flex items-start gap-1 rounded px-2 py-1.5 text-[10px] leading-relaxed ${
        tone === "warn" ? "bg-amber-500/10 text-amber-300" : "bg-surface-3 text-slate-400"
      }`}
    >
      {icon && <span className="mt-px shrink-0">{icon}</span>}
      {children}
    </p>
  );
}
