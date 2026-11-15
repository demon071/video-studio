import { useEffect, useState } from "react";

import { desktopInfo } from "@/lib/desktop";
import { fetchHardware, fetchHealth, fetchSettings, listModels } from "@/lib/api";
import { useEditor } from "@/store/editorStore";

/** How long the packaged app waits for the sidecar to answer after launch. */
const SIDECAR_BOOT_MS = 90_000;

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

/**
 * Boot probe: version, compute plan and model catalogue.
 *
 * In the packaged app the shell has just spawned the Python sidecar, which
 * needs a few seconds to import torch and open its port — so the probe retries
 * for up to 90 s. In development a single attempt is enough (the engine is
 * already running, or it is not).
 */
export function useEngine(): { probing: boolean; retry: () => void } {
  const [nonce, setNonce] = useState(0);
  const [probing, setProbing] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const store = useEditor.getState();
    const managed = desktopInfo()?.managed ?? false;
    const deadline = Date.now() + (managed ? SIDECAR_BOOT_MS : 0);
    setProbing(true);

    const probe = async (): Promise<void> => {
      const [health, hardware, catalogue] = await Promise.all([
        fetchHealth(),
        fetchHardware(),
        listModels(),
      ]);
      if (cancelled) return;
      store.update({
        engineOnline: true,
        hardware: { profile: hardware.profile, device: hardware.device },
        models: catalogue.models,
        modelsPath: catalogue.models_path,
      });
      store.pushLog(
        "info",
        `Engine ${health.version} sẵn sàng · ${hardware.device.toUpperCase()} · preset ${hardware.profile.preset}`,
      );
      // Fetched apart from the boot trio: the panel opens pre-filled and the
      // first transcription already uses the saved audio mode, but a settings
      // glitch must not make the editor look offline.
      try {
        const settings = await fetchSettings();
        if (!cancelled) store.setSettings(settings);
      } catch (error) {
        store.pushLog("warn", `Không đọc được cấu hình: ${(error as Error).message}`);
      }
    };

    void (async () => {
      let wait = 350;
      let announced = false;
      for (;;) {
        try {
          await probe();
          break;
        } catch (error) {
          if (cancelled) return;
          if (Date.now() >= deadline) {
            store.update({ engineOnline: false });
            store.pushLog("error", `Không kết nối được engine: ${(error as Error).message}`);
            break;
          }
          if (!announced) {
            announced = true;
            store.pushLog("info", "Đang chờ engine khởi động…");
          }
          await sleep(wait);
          wait = Math.min(wait * 1.6, 2000);
        }
      }
      if (!cancelled) setProbing(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [nonce]);

  return { probing, retry: () => setNonce((value) => value + 1) };
}
