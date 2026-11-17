/**
 * Thin imperative handle on the single `<video>` element.
 *
 * The store holds the *displayed* playhead, but seeks must reach the media
 * element immediately. Keeping that reference in one module avoids threading a
 * ref through the timeline, the subtitle table and the transport bar.
 */

import { clipTimelineBounds, timelineToSource } from "@/lib/clips";
import type { VideoClip } from "@/types/editor";

type TimeListener = (seconds: number) => void;

class PlayerBridge {
  private element: HTMLVideoElement | null = null;
  private audio: HTMLAudioElement | null = null;
  private clipsProvider: (() => VideoClip[]) | null = null;
  private activeClipIndex = 0;

  attach(element: HTMLVideoElement | null): void {
    this.element = element;
    this.activeClipIndex = 0;
  }

  attachAudio(audio: HTMLAudioElement | null): void {
    this.audio = audio;
  }

  setClipsProvider(provider: (() => VideoClip[]) | null): void {
    this.clipsProvider = provider;
  }

  private getClips(): VideoClip[] {
    return this.clipsProvider ? this.clipsProvider() : [];
  }

  private toTimelineTime(sourceTime: number): number {
    const clips = this.getClips();
    if (!clips.length) return sourceTime;
    const bounds = clipTimelineBounds(clips);
    const idx = Math.min(Math.max(this.activeClipIndex, 0), bounds.length - 1);
    const current = bounds[idx];
    if (
      sourceTime >= current.clip.source_start - 0.05 &&
      sourceTime <= current.clip.source_end + 0.05
    ) {
      return Number((current.start + Math.max(0, sourceTime - current.clip.source_start)).toFixed(3));
    }
    for (const b of bounds) {
      if (sourceTime >= b.clip.source_start - 0.02 && sourceTime <= b.clip.source_end + 0.02) {
        this.activeClipIndex = b.index;
        return Number((b.start + Math.max(0, sourceTime - b.clip.source_start)).toFixed(3));
      }
    }
    return sourceTime;
  }

  get ready(): boolean {
    return this.element !== null;
  }

  get time(): number {
    return this.toTimelineTime(this.element?.currentTime ?? 0);
  }

  get duration(): number {
    return this.element?.duration && Number.isFinite(this.element.duration) ? this.element.duration : 0;
  }

  get paused(): boolean {
    return this.element?.paused ?? true;
  }

  seek(seconds: number): void {
    if (!this.element) return;
    const clips = this.getClips();
    if (clips.length) {
      const mapped = timelineToSource(clips, seconds);
      if (mapped) {
        this.activeClipIndex = mapped.clipIndex;
        const limit = this.duration || mapped.sourceTime;
        this.element.currentTime = Math.min(Math.max(mapped.sourceTime, 0), limit);
        if (this.audio && this.audio.src) this.audio.currentTime = Math.max(0, seconds);
        return;
      }
    }
    const limit = this.duration || seconds;
    this.element.currentTime = Math.min(Math.max(seconds, 0), limit);
    if (this.audio && this.audio.src) this.audio.currentTime = this.element.currentTime;
  }

  async play(): Promise<void> {
    if (!this.element) return;
    const clips = this.getClips();
    if (clips.length) {
      const idx = Math.min(Math.max(this.activeClipIndex, 0), clips.length - 1);
      const cur = clips[idx];
      if (this.element.currentTime < cur.source_start || this.element.currentTime >= cur.source_end - 0.03) {
        this.activeClipIndex = 0;
        this.element.currentTime = clips[0].source_start;
      }
    }
    try {
      await this.element.play();
    } catch {
      /* autoplay blocked until the first click — the button state recovers via onPause */
    }
  }

  pause(): void {
    this.element?.pause();
  }

  toggle(): void {
    if (this.paused) void this.play();
    else this.pause();
  }

  /** Keeps the dubbed track glued to the video (only used when a dub exists). */
  syncDub(enabled: boolean): void {
    const audio = this.audio;
    const video = this.element;
    if (!audio || !video) return;
    if (!enabled || !audio.src) {
      audio.pause();
      return;
    }
    const tlTime = this.time;
    if (Math.abs(audio.currentTime - tlTime) > 0.12) {
      audio.currentTime = tlTime;
    }
    if (video.paused) audio.pause();
    else void audio.play().catch(() => undefined);
  }

  subscribe(listener: TimeListener): () => void {
    let frame = 0;
    let last = -1;
    const tick = () => {
      const video = this.element;
      const clips = this.getClips();
      if (video && !video.paused && clips.length) {
        const idx = Math.min(Math.max(this.activeClipIndex, 0), clips.length - 1);
        const cur = clips[idx];
        if (video.currentTime >= cur.source_end - 0.03) {
          if (idx + 1 < clips.length) {
            this.activeClipIndex = idx + 1;
            video.currentTime = clips[idx + 1].source_start;
          } else {
            video.pause();
          }
        }
      }
      const current = this.time;
      if (current !== last) {
        last = current;
        listener(current);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }
}

export const player = new PlayerBridge();
