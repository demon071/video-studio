/**
 * Thin imperative handle on the single `<video>` element.
 *
 * The store holds the *displayed* playhead, but seeks must reach the media
 * element immediately. Keeping that reference in one module avoids threading a
 * ref through the timeline, the subtitle table and the transport bar.
 */

type TimeListener = (seconds: number) => void;

class PlayerBridge {
  private element: HTMLVideoElement | null = null;
  private audio: HTMLAudioElement | null = null;

  attach(element: HTMLVideoElement | null): void {
    this.element = element;
  }

  attachAudio(audio: HTMLAudioElement | null): void {
    this.audio = audio;
  }

  get ready(): boolean {
    return this.element !== null;
  }

  get time(): number {
    return this.element?.currentTime ?? 0;
  }

  get duration(): number {
    return this.element?.duration && Number.isFinite(this.element.duration) ? this.element.duration : 0;
  }

  get paused(): boolean {
    return this.element?.paused ?? true;
  }

  seek(seconds: number): void {
    if (!this.element) return;
    const limit = this.duration || seconds;
    this.element.currentTime = Math.min(Math.max(seconds, 0), limit);
    if (this.audio && this.audio.src) this.audio.currentTime = this.element.currentTime;
  }

  async play(): Promise<void> {
    if (!this.element) return;
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
    if (Math.abs(audio.currentTime - video.currentTime) > 0.12) {
      audio.currentTime = video.currentTime;
    }
    if (video.paused) audio.pause();
    else void audio.play().catch(() => undefined);
  }

  subscribe(listener: TimeListener): () => void {
    let frame = 0;
    let last = -1;
    const tick = () => {
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
