import { useEffect, useRef } from "react";

import { isDesktop, pickProjectFile } from "@/lib/media";
import {
  hasWork,
  openProjectFrom,
  persistSession,
  persistSessionNow,
  restoreLastSession,
  saveCurrentProject,
  syncActiveQueueItem,
} from "@/lib/project";
import { useEditor } from "@/store/editorStore";

type EditorState = ReturnType<typeof useEditor.getState>;

/** Quiet period after the last edit before the snapshot is written. */
const AUTOSAVE_MS = 1200;

/**
 * Project persistence: restore on boot, autosave while editing, shortcuts.
 *
 * The autosave is deliberately silent but frequent: it writes the same
 * `_session.json` every time the timeline, the settings or the dub change, so
 * a crash, a closed window or a power cut costs at most a couple of seconds of
 * typing. The named `.svaproj` stays under the user's control (Ctrl+S).
 */
export function useProjectPersistence(): void {
  const online = useEditor((state) => state.engineOnline);
  const restored = useRef(false);

  // 1. Resume the previous session, once the engine is reachable and only when
  //    the editor is still empty (a fast user may already have imported).
  useEffect(() => {
    if (online !== true || restored.current) return;
    restored.current = true;
    let cancelled = false;

    void (async () => {
      try {
        if (hasWork(useEditor.getState())) return;
        await restoreLastSession();
      } catch (error) {
        if (!cancelled) console.warn("[project] session restore failed", error);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [online]);

  // 2. Debounced autosave on document changes (the playhead is ignored).
  useEffect(() => {
    let timer = 0;
    const unsubscribe = useEditor.subscribe((state, previous) => {
      if (!restored.current || !hasWork(state)) return;
      if (!touchesDocument(state, previous)) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        void persistSession();
        if (useEditor.getState().activeQueueItemId) {
          void syncActiveQueueItem().catch(() => null);
        }
      }, AUTOSAVE_MS);
    });
    return () => {
      window.clearTimeout(timer);
      unsubscribe();
    };
  }, []);

  // 3. Last chance: the WebView is closing, send what is on screen right now.
  useEffect(() => {
    const flush = () => persistSessionNow();
    window.addEventListener("beforeunload", flush);
    return () => window.removeEventListener("beforeunload", flush);
  }, []);

  // 4. Shortcuts: Ctrl+S / Ctrl+Shift+S / Ctrl+O.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "s") {
        event.preventDefault();
        if (event.repeat) return;
        void saveCurrentProject({ as: event.shiftKey });
      } else if (key === "o") {
        event.preventDefault();
        if (event.repeat) return;
        void (async () => {
          const path = isDesktop ? await pickProjectFile() : null;
          await openProjectFrom(path);
        })();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

/** Cheap reference comparison: only the fields a document stores matter. */
function touchesDocument(state: EditorState, previous: EditorState): boolean {
  return (
    state.source !== previous.source ||
    state.segments !== previous.segments ||
    state.dub !== previous.dub ||
    state.aspect !== previous.aspect ||
    state.subtitleStyle !== previous.subtitleStyle ||
    state.presentationMode !== previous.presentationMode ||
    state.secondarySegments !== previous.secondarySegments ||
    state.secondaryStyle !== previous.secondaryStyle ||
    state.layers !== previous.layers ||
    state.videoClips !== previous.videoClips ||
    state.backgroundAudioPath !== previous.backgroundAudioPath ||
    state.voiceGain !== previous.voiceGain ||
    state.flipHorizontal !== previous.flipHorizontal ||
    state.speakerVoices !== previous.speakerVoices ||
    state.sttProvider !== previous.sttProvider ||
    state.whisperSize !== previous.whisperSize ||
    state.sourceLanguage !== previous.sourceLanguage ||
    state.targetLanguage !== previous.targetLanguage ||
    state.translateProvider !== previous.translateProvider ||
    state.translateStyle !== previous.translateStyle ||
    state.useTranslationForDub !== previous.useTranslationForDub ||
    state.ttsProvider !== previous.ttsProvider ||
    state.ttsVoice !== previous.ttsVoice ||
    state.ttsRate !== previous.ttsRate ||
    state.keepOriginalAudio !== previous.keepOriginalAudio ||
    state.duckGain !== previous.duckGain ||
    state.previewSubtitles !== previous.previewSubtitles ||
    state.previewDub !== previous.previewDub ||
    state.tableCollapsed !== previous.tableCollapsed ||
    state.timelineCollapsed !== previous.timelineCollapsed
  );
}
