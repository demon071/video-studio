/**
 * Dev-only fixture: `npm run dev:web` then open
 * `http://localhost:1420/?demo` to get a populated timeline without a clip on
 * disk. The sample video lives in `public/sample-9x16.mp4`.
 *
 * Not reachable in a production build (`import.meta.env.DEV` is folded to
 * `false`), so this never ships inside the Tauri bundle.
 */
import { useEditor } from "@/store/editorStore";
import type { SubtitleSegment } from "@/types/editor";

const LINES: SubtitleSegment[] = [
  {
    index: 1,
    start: 0.2,
    end: 2.0,
    text: "Xin chào, đây là bản demo của ShortVideoAI.",
    translation: "Hi, this is the ShortVideoAI demo.",
  },
  {
    index: 2,
    start: 2.2,
    end: 4.0,
    text: "Phụ đề được nhận dạng offline bằng faster-whisper.",
    translation: "Subtitles are recognised offline with faster-whisper.",
  },
  {
    index: 3,
    start: 4.2,
    end: 5.8,
    text: "Lồng tiếng tự canh tốc độ để khớp từng khung hình.",
    translation: "Dubbing time-stretches every line to fit its slot.",
  },
];

export function seedDemoProject(): void {
  useEditor.getState().update({
    source: {
      path: "",
      url: "/sample-9x16.mp4",
      name: "sample-9x16.mp4",
      duration: 6,
      width: 540,
      height: 960,
      fps: 30,
      hasAudio: true,
      aspect: "9:16",
    },
    segments: LINES,
    selected: 2,
    playhead: 2.6,
    aspect: "9:16",
    zoom: 90,
  });
}
