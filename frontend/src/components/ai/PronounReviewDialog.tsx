import { Check, Loader2, Sparkles, Users, X } from "lucide-react";
import { useState } from "react";

import { runAnalyzeContext } from "@/lib/tasks";
import { useEditor } from "@/store/editorStore";
import type { ContextCharacter } from "@/types/editor";

export function PronounReviewDialog({ onClose }: { onClose: () => void }) {
  const contextResult = useEditor((state) => state.contextResult);
  const contextGuidance = useEditor((state) => state.contextGuidance);
  const pending = useEditor((state) => Boolean(state.pending.analyzeContext));
  const update = useEditor((state) => state.update);

  const [characters, setCharacters] = useState<ContextCharacter[]>(() =>
    Array.isArray(contextResult?.characters) ? contextResult.characters : [],
  );
  const [guidanceText, setGuidanceText] = useState(() => contextGuidance || "");

  const handleAnalyze = async () => {
    await runAnalyzeContext();
    const fresh = useEditor.getState().contextResult;
    if (fresh) {
      setCharacters(Array.isArray(fresh.characters) ? fresh.characters : []);
      setGuidanceText(fresh.context_guidance ?? fresh.context ?? "");
    }
  };

  const updateChar = (idx: number, patch: Partial<ContextCharacter>) => {
    setCharacters((prev) => prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  const applyAndClose = () => {
    const safeChars = Array.isArray(characters) ? characters : [];
    const charLines = safeChars
      .map(
        (c) =>
          `- ${c.name || c.id} (${c.gender || "?"}, ${c.role || ""}): tự xưng "${c.self_pronoun}", gọi đối phương "${c.address_others}"`,
      )
      .join("\n");
    const combined = [(guidanceText ?? "").trim(), charLines ? `BẢNG XƯNG HÔ ĐÃ DUYỆT:\n${charLines}` : ""]
      .filter(Boolean)
      .join("\n\n");
    update({
      contextGuidance: combined,
      contextResult: contextResult ? { ...contextResult, characters: safeChars, context_guidance: combined } : null,
    });
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border border-border bg-surface-1 shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <Users size={15} className="text-brand" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
              Phân tích & Duyệt Xưng Hô / Nhân Vật (CapCap Context Engine)
            </h3>
          </div>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose}>
            <X size={15} />
          </button>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto p-4 text-xs">
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-surface-2 p-2.5">
            <span className="text-[11px] text-slate-400">
              Quét toàn bộ thoại để nhận diện nhân vật, giới tính, quan hệ và cố định đại từ xưng hô xuyên suốt các batch.
            </span>
            <button
              className="btn-primary shrink-0"
              onClick={() => void handleAnalyze()}
              disabled={pending}
            >
              {pending ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
              Phân tích ngay
            </button>
          </div>

          {contextResult?.summary && (
            <div className="rounded border border-border bg-surface-2 p-2.5 text-[11px] text-slate-300">
              <span className="font-semibold text-brand">Tóm tắt kịch bản: </span>
              {contextResult.summary}
            </div>
          )}

          {characters.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                Bảng nhân vật & Đại từ xưng hô ({characters.length})
              </div>
              <div className="space-y-1.5">
                {characters.map((ch, idx) => (
                  <div
                    key={ch.id || idx}
                    className="grid grid-cols-5 gap-1.5 rounded border border-border bg-surface-2 p-2 text-[11px]"
                  >
                    <input
                      className="field !py-1 !text-[11px]"
                      value={ch.name}
                      placeholder="Tên / ID"
                      onChange={(e) => updateChar(idx, { name: e.target.value })}
                    />
                    <input
                      className="field !py-1 !text-[11px]"
                      value={ch.gender}
                      placeholder="Giới tính"
                      onChange={(e) => updateChar(idx, { gender: e.target.value })}
                    />
                    <input
                      className="field !py-1 !text-[11px]"
                      value={ch.role}
                      placeholder="Vai trò"
                      onChange={(e) => updateChar(idx, { role: e.target.value })}
                    />
                    <input
                      className="field !py-1 !text-[11px]"
                      value={ch.self_pronoun}
                      placeholder="Tự xưng (ta, anh...)"
                      onChange={(e) => updateChar(idx, { self_pronoun: e.target.value })}
                    />
                    <input
                      className="field !py-1 !text-[11px]"
                      value={ch.address_others}
                      placeholder="Gọi người khác"
                      onChange={(e) => updateChar(idx, { address_others: e.target.value })}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          <label className="block space-y-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Hướng dẫn ngữ cảnh & Quy tắc xưng hô (Gửi kèm vào System Prompt)
            </span>
            <textarea
              className="field min-h-[7rem] w-full font-mono !text-[11px]"
              value={guidanceText}
              placeholder="Ví dụ: Nam chính (Lục Thần) xưng 'tôi' - gọi nữ chính (Tô Nhiên) là 'cô', sau khi kết hôn đổi thành 'anh - em'..."
              onChange={(e) => setGuidanceText(e.target.value)}
            />
          </label>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-4 py-2.5">
          <button className="btn-ghost" onClick={onClose}>
            Đóng
          </button>
          <button className="btn-primary" onClick={applyAndClose}>
            <Check size={12} />
            Áp dụng quy tắc xưng hô
          </button>
        </footer>
      </div>
    </div>
  );
}
