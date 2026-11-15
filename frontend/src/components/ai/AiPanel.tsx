import {
  AudioLines,
  Captions,
  KeyRound,
  Languages,
  Loader2,
  PlayCircle,
  Sparkles,
  TriangleAlert,
  Volume2,
} from "lucide-react";
import { useMemo, useState } from "react";

import { previewVoice, saveSettings } from "@/lib/api";
import { clamp } from "@/lib/format";
import { toAssetUrl } from "@/lib/media";
import { runDub, runTranscribe, runTranslate } from "@/lib/tasks";
import { DEFAULT_VOICES, useEditor, type SidebarTab } from "@/store/editorStore";
import type { AudioMode, SttProvider, TranslateProvider, TtsProvider } from "@/types/editor";

const LANGUAGES = [
  { value: "auto", label: "Tự nhận" },
  { value: "vi", label: "Tiếng Việt" },
  { value: "en", label: "English" },
  { value: "zh", label: "中文" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "th", label: "ไทย" },
  { value: "id", label: "Indonesia" },
];

const WHISPER_SIZES = [
  { value: "small", label: "small · nhanh" },
  { value: "medium", label: "medium · cân bằng" },
  { value: "large-v3-turbo", label: "large-v3-turbo · chính xác" },
];

const VOICES: Record<TtsProvider, Array<{ value: string; label: string }>> = {
  // Mirrors capcut_client.CAPCUT_VOICES on the engine side.
  capcut: [
    { value: "BV421_vivn_streaming", label: "Nhỏ Ngọt Ngào (nữ, VI)" },
    { value: "vi_female_huong", label: "Giọng Nữ Phổ Thông (VI)" },
    { value: "BV074_streaming", label: "Cô Gái Hoạt Ngôn (nữ, VI)" },
    { value: "BV074_streaming_dsp", label: "Giọng Bé (nữ, VI)" },
    { value: "BV562_streaming", label: "Mai (nữ, VI)" },
    { value: "multi_female_yangguangnv_uranus_bigtts", label: "Ban Mai (nữ, VI)" },
    { value: "BV075_streaming", label: "Thanh Niên Tự Tin (nam, VI)" },
    { value: "BV560_streaming", label: "Alex Đại Đế (nam, VI)" },
  ],
  omnivoice: [{ value: "auto", label: "Tự động (model chọn giọng)" }],
  voicestudio: [{ value: "alloy", label: "alloy (mặc định của server)" }],
};

/** Free-text voices: a described voice, a sample path or a server voice id. */
const FREE_VOICE: TtsProvider[] = ["omnivoice", "voicestudio"];

const VOICE_PLACEHOLDER: Partial<Record<TtsProvider, string>> = {
  omnivoice: "auto · design:nữ, giọng trầm, chậm · clone:D:\\giong.wav",
  voicestudio: "alloy",
};

/** Providers that accept a speaking rate. */
const RATE_PROVIDERS: TtsProvider[] = ["capcut"];

export function AiPanel() {
  const source = useEditor((state) => state.source);
  const segments = useEditor((state) => state.segments);
  const selected = useEditor((state) => state.selected);
  const dub = useEditor((state) => state.dub);
  const dubDirty = useEditor((state) => state.dubDirty);
  const pending = useEditor((state) => state.pending);
  const models = useEditor((state) => state.models);

  const sttProvider = useEditor((state) => state.sttProvider);
  const whisperSize = useEditor((state) => state.whisperSize);
  const sourceLanguage = useEditor((state) => state.sourceLanguage);
  const targetLanguage = useEditor((state) => state.targetLanguage);
  const translateProvider = useEditor((state) => state.translateProvider);
  const translateStyle = useEditor((state) => state.translateStyle);
  const useTranslationForDub = useEditor((state) => state.useTranslationForDub);
  const ttsProvider = useEditor((state) => state.ttsProvider);
  const ttsVoice = useEditor((state) => state.ttsVoice);
  const ttsRate = useEditor((state) => state.ttsRate);
  const keepOriginalAudio = useEditor((state) => state.keepOriginalAudio);
  const duckGain = useEditor((state) => state.duckGain);
  const audioMode = useEditor((state) => state.audioMode);
  const ocrFill = useEditor((state) => state.ocrFill);
  const settings = useEditor((state) => state.settings);
  const setSettings = useEditor((state) => state.setSettings);
  const update = useEditor((state) => state.update);

  const [previewing, setPreviewing] = useState(false);

  const busy = Object.keys(pending).length > 0;
  const canRun = Boolean(source?.path) && !busy;
  const translated = segments.filter((segment) => segment.translation?.trim()).length;

  const whisperModel = useMemo(
    () => models.find((model) => model.key === `whisper-${whisperSize}`),
    [models, whisperSize],
  );

  const sensevoiceModel = useMemo(
    () => models.find((model) => model.key === "sensevoice-zh-en-ja-ko-yue"),
    [models],
  );

  const activeLine = segments.find((segment) => segment.index === selected) ?? segments[0];

  const cleanReady = settings?.audio.modes.find((mode) => mode.name === "clean")?.ready ?? false;

  // The audio mode is engine-wide (providers.json), so the choice is saved here
  // and there: flipping it in Settings or next to the button stays in sync.
  const changeAudioMode = async (mode: AudioMode) => {
    update({ audioMode: mode });
    try {
      setSettings(await saveSettings({ audio: { mode } }));
    } catch (error) {
      useEditor.getState().pushLog("error", `Không lưu được chế độ âm thanh: ${(error as Error).message}`);
    }
  };

  const handlePreview = async () => {
    if (!activeLine) return;
    const text = (useTranslationForDub ? activeLine.translation : activeLine.text) || activeLine.text;
    setPreviewing(true);
    try {
      const result = await previewVoice(text, {
        provider: ttsProvider,
        voice: ttsVoice || undefined,
        rate: RATE_PROVIDERS.includes(ttsProvider) ? ttsRate : undefined,
      });
      if (result.path) {
        const audio = new Audio(await toAssetUrl(result.path));
        await audio.play();
      }
    } catch (error) {
      useEditor.getState().pushLog("error", `Nghe thử thất bại: ${(error as Error).message}`);
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      {/* 1 — Speech to text ------------------------------------------------- */}
      <Section icon={<Captions size={12} />} title="1 · Tạo phụ đề">
        <div className="grid grid-cols-2 gap-2">
          <Labelled label="Nguồn">
            <select
              className="field"
              value={sttProvider}
              onChange={(event) => update({ sttProvider: event.target.value as SttProvider })}
            >
              <option value="local">Offline (faster-whisper)</option>
              <option value="sensevoice">SenseVoice (offline, nhanh)</option>
              <option value="groq">Groq API</option>
              <option value="openai">OpenAI API</option>
              <option value="capcut">CapCut (cloud, không cần key)</option>
              <option value="bilibili">Bilibili BCut (cloud)</option>
              <option value="ocr">OCR — đọc phụ đề cứng</option>
            </select>
          </Labelled>
          <Labelled label="Ngôn ngữ gốc">
            <select
              className="field"
              value={sourceLanguage}
              onChange={(event) => update({ sourceLanguage: event.target.value })}
            >
              {LANGUAGES.map((language) => (
                <option key={language.value} value={language.value}>
                  {language.label}
                </option>
              ))}
            </select>
          </Labelled>
        </div>

        {sttProvider === "local" && (
          <Labelled label="Model Whisper">
            <select
              className="field"
              value={whisperSize}
              onChange={(event) => update({ whisperSize: event.target.value })}
            >
              {WHISPER_SIZES.map((size) => (
                <option key={size.value} value={size.value}>
                  {size.label}
                </option>
              ))}
            </select>
          </Labelled>
        )}

        {sttProvider === "local" && whisperModel && !whisperModel.downloaded && (
          <Hint tone="warn" icon={<TriangleAlert size={10} />}>
            Model chưa tải — sẽ tự tải {whisperModel.size_mb} MB trong lần chạy đầu (tab Model).
          </Hint>
        )}

        {sttProvider === "sensevoice" && (
          <Hint tone="info" icon={<TriangleAlert size={10} />}>
            SenseVoice đọc tiếng Trung, Anh, Nhật, Hàn, Quảng Đông — nhanh trên CPU và tự thêm dấu câu.
            Nó không đoán ngôn ngữ nên không có chuyện audio tiếng Trung mà ra chữ tiếng Nga; ngôn
            ngữ khác thì chọn Offline (whisper). Model {sensevoiceModel?.size_mb ?? 240} MB tự tải ở
            lần chạy đầu, và cần{" "}
            <code className="text-slate-300">pip install sherpa-onnx</code> trong môi trường engine.
          </Hint>
        )}

        {(sttProvider === "capcut" || sttProvider === "bilibili") && (
          <Hint tone="info" icon={<TriangleAlert size={10} />}>
            Gửi âm thanh lên {sttProvider === "capcut" ? "CapCut" : "Bilibili BCut"} — cần internet, không cần API key.
            Nhận dạng tiếng Trung tốt hơn whisper small.
          </Hint>
        )}

        {sttProvider === "ocr" && (
          <Hint tone="info" icon={<TriangleAlert size={10} />}>
            Đọc phụ đề in sẵn trên hình, không nghe audio. Cần chọn đúng "Ngôn ngữ gốc" để engine đọc
            đúng bảng chữ; vùng quét, tốc độ và bộ dò đặt trong tab Cài đặt. Muốn chọn vùng ngay trên
            hình thì bấm "Vùng OCR" ở thanh xem trước.
            {settings && (
              <span className="mt-1 block text-slate-500">
                Đang đọc bằng {settings.ocr.device_actual === "gpu" ? "GPU" : "CPU"} · {settings.ocr.engine}{" "}
                · {settings.ocr.profile === "tiny" ? "Tiny Det" : "Small"}
              </span>
            )}
          </Hint>
        )}

        {sttProvider === "ocr" && <PanelLink>OCR: vùng quét, thiết bị & model</PanelLink>}

        {sttProvider !== "ocr" && (
          <Labelled label="Âm thanh đưa vào nhận dạng">
            <select
              className="field"
              value={audioMode}
              onChange={(event) => void changeAudioMode(event.target.value as AudioMode)}
            >
              <option value="fast">Nhanh — nhận dạng nguyên bản âm thanh</option>
              <option value="clean">Giọng sạch — tách nhạc trước (chậm hơn)</option>
            </select>
          </Labelled>
        )}

        {sttProvider !== "ocr" && audioMode === "clean" && !cleanReady && (
          <Hint tone="warn" icon={<TriangleAlert size={10} />}>
            Chưa cài model tách giọng — lần chạy này sẽ tự lùi về chế độ Nhanh. Tải model trong tab Cài
            đặt.
          </Hint>
        )}

        {sttProvider !== "ocr" && (
          <div className="space-y-1.5 text-[11px] text-slate-400">
            <Toggle
              checked={ocrFill}
              onChange={(value) => update({ ocrFill: value })}
              label="Đọc lại bằng OCR đoạn có tiếng mà thiếu chữ"
            />
            {ocrFill && (
              <Hint tone="info" icon={<TriangleAlert size={10} />}>
                Sau khi nhận dạng giọng nói, engine tìm những quãng vẫn có âm thanh nhưng chưa ra
                dòng nào rồi đọc phụ đề in sẵn trên hình đúng ở quãng đó. Vùng quét và ngôn ngữ lấy
                từ tab Cài đặt — phim Trung có phụ đề Việt thì chọn ngôn ngữ OCR là Tiếng Việt, đọc
                bằng model Trung sẽ mất dấu. Thiếu engine OCR thì bước này tự bỏ qua, phần đã nhận
                dạng giữ nguyên.
              </Hint>
            )}
          </div>
        )}

        <KeyNotice provider={sttProvider} />

        <button className="btn-primary w-full" onClick={() => void runTranscribe()} disabled={!canRun}>
          {pending.transcribe ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
          {segments.length ? "Tạo lại phụ đề" : "Tạo phụ đề tự động"}
        </button>
      </Section>

      {/* 2 — Translation ---------------------------------------------------- */}
      <Section icon={<Languages size={12} />} title="2 · Dịch phụ đề">
        <div className="grid grid-cols-2 gap-2">
          <Labelled label="Sang">
            <select
              className="field"
              value={targetLanguage}
              onChange={(event) => update({ targetLanguage: event.target.value })}
            >
              {LANGUAGES.filter((language) => language.value !== "auto").map((language) => (
                <option key={language.value} value={language.value}>
                  {language.label}
                </option>
              ))}
            </select>
          </Labelled>
          <Labelled label="Nguồn">
            <select
              className="field"
              value={translateProvider}
              onChange={(event) => update({ translateProvider: event.target.value as TranslateProvider })}
            >
              <option value="local">Offline (GGUF trên máy)</option>
              <option value="gemini">Gemini API</option>
              <option value="openai">OpenAI API</option>
              <option value="google">Google Dịch (miễn phí)</option>
              <option value="bing">Bing Dịch (miễn phí)</option>
            </select>
          </Labelled>
        </div>

        {(translateProvider === "google" || translateProvider === "bing") && (
          <Hint tone="info" icon={<TriangleAlert size={10} />}>
            Dịch máy theo từng câu: sát nghĩa nhưng văn phong khô hơn model offline. Không cần key, không
            dùng tới GPU.
          </Hint>
        )}

        <KeyNotice provider={translateProvider} />

        <Labelled label="Văn phong">
          <input
            className="field"
            value={translateStyle}
            onChange={(event) => update({ translateStyle: event.target.value })}
            placeholder="short-form video subtitle"
          />
        </Labelled>

        <div className="flex items-center justify-between text-[10px] text-slate-500">
          <span>
            {segments.length} dòng · đã dịch <span className="text-slate-300">{translated}</span>
          </span>
          <span className="font-mono">{clamp(segments.length ? (translated / segments.length) * 100 : 0, 0, 100).toFixed(0)}%</span>
        </div>

        <button
          className="btn-ghost w-full"
          onClick={() => void runTranslate()}
          disabled={!canRun || !segments.length}
        >
          {pending.translate ? <Loader2 size={12} className="animate-spin" /> : <Languages size={12} />}
          Dịch {segments.length || ""} dòng
        </button>
      </Section>

      {/* 3 — Dubbing -------------------------------------------------------- */}
      <Section icon={<Volume2 size={12} />} title="3 · Lồng tiếng">
        <div className="grid grid-cols-2 gap-2">
          <Labelled label="Giọng đọc">
            <select
              className="field"
              value={ttsProvider}
              onChange={(event) => {
                const provider = event.target.value as TtsProvider;
                update({ ttsProvider: provider, ttsVoice: DEFAULT_VOICES[provider] });
              }}
            >
              <option value="capcut">CapCut (cloud, giọng Việt)</option>
              <option value="omnivoice">OmniVoice (offline, 600+ ngôn ngữ)</option>
              <option value="voicestudio">VoiceStudio (server nội bộ)</option>
            </select>
          </Labelled>
          <Labelled label="Tốc độ">
            <select
              className="field"
              value={ttsRate}
              onChange={(event) => update({ ttsRate: event.target.value })}
              disabled={!RATE_PROVIDERS.includes(ttsProvider)}
            >
              <option value="-10%">Chậm hơn 10%</option>
              <option value="+0%">Bình thường</option>
              <option value="+10%">Nhanh hơn 10%</option>
              <option value="+20%">Nhanh hơn 20%</option>
            </select>
          </Labelled>
        </div>

        <Labelled label="Voice">
          {FREE_VOICE.includes(ttsProvider) ? (
            <input
              className="field font-mono"
              value={ttsVoice}
              onChange={(event) => update({ ttsVoice: event.target.value })}
              placeholder={VOICE_PLACEHOLDER[ttsProvider]}
            />
          ) : (
            <select
              className="field"
              value={ttsVoice}
              onChange={(event) => update({ ttsVoice: event.target.value })}
            >
              {VOICES[ttsProvider].map((voice) => (
                <option key={voice.value} value={voice.value}>
                  {voice.label}
                </option>
              ))}
            </select>
          )}
        </Labelled>

        {ttsProvider === "capcut" && (
          <Hint tone="info" icon={<TriangleAlert size={10} />}>
            Giọng đọc lấy từ máy chủ CapCut — cần internet, không cần tài khoản.
          </Hint>
        )}

        {ttsProvider === "omnivoice" && (
          <Hint tone="info" icon={<TriangleAlert size={10} />}>
            OmniVoice chạy offline trên GPU/CPU (cần cài gói omnivoice; lần đầu tải model về
            máy). Gõ &quot;auto&quot;, một mô tả giọng (&quot;nữ, giọng trầm&quot;) hoặc
            &quot;clone:&lt;đường dẫn wav 3-10s&gt;&quot;.
          </Hint>
        )}

        {ttsProvider === "omnivoice" && (
          <PanelLink tab="models">Xem gói OmniVoice trong tab Model</PanelLink>
        )}

        {ttsProvider === "voicestudio" && (
          <Hint tone="info" icon={<TriangleAlert size={10} />}>
            Cần server VoiceStudio chạy sẵn ở 127.0.0.1:3900 (VoiceStudio → Local API). Đổi địa
            chỉ trong Settings nếu chạy Docker hoặc máy khác.
          </Hint>
        )}

        {ttsProvider === "voicestudio" && (
          <PanelLink>Cấu hình VoiceStudio trong tab Cài đặt</PanelLink>
        )}

        {/* The local VoiceStudio server needs no key; a remote one is set up in Settings. */}
        {ttsProvider !== "voicestudio" && <KeyNotice provider={ttsProvider} />}

        <div className="space-y-1.5 text-[11px] text-slate-400">
          <Toggle
            checked={useTranslationForDub}
            onChange={(value) => update({ useTranslationForDub: value })}
            label="Dùng bản dịch để đọc"
          />
          <Toggle
            checked={keepOriginalAudio}
            onChange={(value) => update({ keepOriginalAudio: value })}
            label="Giữ âm thanh gốc dưới giọng lồng tiếng"
          />
          {keepOriginalAudio && (
            <label className="flex items-center gap-2 pl-5">
              <span className="shrink-0 text-[10px] text-slate-500">Âm gốc</span>
              <input
                type="range"
                className="h-1 flex-1 accent-brand"
                min={0}
                max={1}
                step={0.05}
                value={duckGain}
                onChange={(event) => update({ duckGain: Number(event.target.value) })}
                title="Âm lượng của clip gốc khi giọng lồng tiếng đang nói"
              />
              <span className="w-8 shrink-0 text-right font-mono text-[10px] text-slate-300">
                {Math.round(duckGain * 100)}%
              </span>
            </label>
          )}
        </div>

        <div className="flex gap-2">
          <button
            className="btn-ghost flex-1"
            onClick={() => void handlePreview()}
            disabled={!activeLine || previewing || !source?.path}
          >
            {previewing ? <Loader2 size={12} className="animate-spin" /> : <PlayCircle size={12} />}
            Nghe thử
          </button>
          <button className="btn-primary flex-1" onClick={() => void runDub()} disabled={!canRun || !segments.length}>
            {pending.dub ? <Loader2 size={12} className="animate-spin" /> : <AudioLines size={12} />}
            Lồng tiếng
          </button>
        </div>

        {dub && (
          <div className="rounded-md border border-border bg-surface-1 p-2 text-[10px] leading-relaxed text-slate-400">
            <div className="flex items-center justify-between">
              <span className="font-medium text-slate-200">Track lồng tiếng</span>
              <span className="font-mono">{dub.duration.toFixed(2)}s</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
              <span>{dub.provider} · {dub.voice}</span>
              <span>
                bị cắt: <span className="text-amber-300">{dub.segments.filter((item) => item.clamped).length}</span>
              </span>
              <span>
                mất:{" "}
                <span className="text-amber-300">
                  {dub.segments.reduce((total, item) => total + item.overflow_s, 0).toFixed(2)}s
                </span>
              </span>
            </div>
            {dubDirty && <p className="mt-1 text-amber-300">Phụ đề đã sửa — cần lồng tiếng lại.</p>}
            {keepOriginalAudio && (
              <p className="mt-1 text-slate-500">
                Bản render giữ âm thanh gốc ở {Math.round(duckGain * 100)}%, tự hạ thêm khi có giọng đọc.
              </p>
            )}
          </div>
        )}
      </Section>
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
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <p
      className={`flex items-start gap-1 rounded px-2 py-1.5 text-[10px] leading-relaxed ${
        tone === "warn" ? "bg-amber-500/10 text-amber-300" : "bg-surface-3 text-slate-400"
      }`}
    >
      <span className="mt-px shrink-0">{icon}</span>
      {children}
    </p>
  );
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2">
      <input
        type="checkbox"
        className="h-3 w-3 accent-brand"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}

/** Jump to another column of the sidebar (keys, models). */
function PanelLink({ tab = "settings", children }: { tab?: SidebarTab; children: React.ReactNode }) {
  const setTab = useEditor((state) => state.setSidebarTab);
  return (
    <button
      className="flex w-full items-center justify-center gap-1 rounded border border-border bg-surface-1 px-2 py-1 text-[10px] text-slate-400 transition-colors hover:border-slate-500 hover:text-slate-200"
      onClick={() => setTab(tab)}
    >
      <KeyRound size={9} />
      {children}
    </button>
  );
}

/**
 * The provider is selected but has no key: say so here instead of letting the
 * first API call fail with a 401 that looks like a bug.
 */
function KeyNotice({ provider }: { provider: string }) {
  const settings = useEditor((state) => state.settings);
  const info = settings?.providers.find((entry) => entry.name === provider);
  if (!info || info.api_key_set) return null;
  return (
    <PanelLink>
      Chưa có API key cho {info.label} — thêm key & model
    </PanelLink>
  );
}
