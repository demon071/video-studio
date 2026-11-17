import type { VideoClip } from "@/types/editor";

export function clipDuration(clip: VideoClip): number {
  return Math.max(0, clip.source_end - clip.source_start);
}

export function timelineDuration(clips: VideoClip[]): number {
  return clips.reduce((sum, c) => sum + clipDuration(c), 0);
}

export function clipTimelineBounds(clips: VideoClip[]): Array<{
  clip: VideoClip;
  index: number;
  start: number;
  end: number;
}> {
  let cursor = 0;
  return clips.map((clip, index) => {
    const start = cursor;
    const end = cursor + clipDuration(clip);
    cursor = end;
    return { clip, index, start, end };
  });
}

export function timelineToSource(
  clips: VideoClip[],
  timelineTime: number,
): { clipIndex: number; clip: VideoClip; sourceTime: number } | null {
  if (!clips.length) return null;
  let cursor = 0;
  const clamped = Math.max(0, timelineTime);
  for (let i = 0; i < clips.length; i++) {
    const clip = clips[i];
    const dur = clipDuration(clip);
    if (clamped <= cursor + dur || i === clips.length - 1) {
      const offset = Math.min(Math.max(0, clamped - cursor), dur);
      return {
        clipIndex: i,
        clip,
        sourceTime: Number((clip.source_start + offset).toFixed(3)),
      };
    }
    cursor += dur;
  }
  return null;
}

export function isClipsEdited(clips: VideoClip[], sourceDuration: number): boolean {
  if (clips.length === 0) return false;
  if (clips.length > 1) return true;
  const only = clips[0];
  return only.source_start > 0.02 || only.source_end < sourceDuration - 0.02;
}

export function splitClipsAt(clips: VideoClip[], timelineTime: number): VideoClip[] {
  const mapped = timelineToSource(clips, timelineTime);
  if (!mapped) return clips;
  const { clipIndex, clip, sourceTime } = mapped;
  if (sourceTime - clip.source_start < 0.1 || clip.source_end - sourceTime < 0.1) {
    return clips;
  }
  const left: VideoClip = {
    ...clip,
    id: `${clip.id}_a_${Date.now()}`,
    source_end: sourceTime,
  };
  const right: VideoClip = {
    ...clip,
    id: `${clip.id}_b_${Date.now()}`,
    source_start: sourceTime,
  };
  return [...clips.slice(0, clipIndex), left, right, ...clips.slice(clipIndex + 1)];
}

export function trimLeftClipsAt(clips: VideoClip[], timelineTime: number): VideoClip[] {
  const bounds = clipTimelineBounds(clips);
  const next: VideoClip[] = [];
  for (const { clip, start, end } of bounds) {
    if (end <= timelineTime + 0.05) continue;
    if (start < timelineTime) {
      const cutOffset = timelineTime - start;
      const newStart = Number((clip.source_start + cutOffset).toFixed(3));
      if (clip.source_end - newStart >= 0.05) {
        next.push({ ...clip, source_start: newStart });
      }
    } else {
      next.push(clip);
    }
  }
  return next.length ? next : clips;
}

export function trimRightClipsAt(clips: VideoClip[], timelineTime: number): VideoClip[] {
  const bounds = clipTimelineBounds(clips);
  const next: VideoClip[] = [];
  for (const { clip, start, end } of bounds) {
    if (start >= timelineTime - 0.05) continue;
    if (end > timelineTime) {
      const keepDur = timelineTime - start;
      const newEnd = Number((clip.source_start + keepDur).toFixed(3));
      if (newEnd - clip.source_start >= 0.05) {
        next.push({ ...clip, source_end: newEnd });
      }
    } else {
      next.push(clip);
    }
  }
  return next.length ? next : clips;
}

export function deleteClipsRange(clips: VideoClip[], a: number, b: number): VideoClip[] {
  if (b <= a + 0.05) return clips;
  const bounds = clipTimelineBounds(clips);
  const next: VideoClip[] = [];
  for (const { clip, start, end } of bounds) {
    if (end <= a + 0.02 || start >= b - 0.02) {
      next.push(clip);
      continue;
    }
    if (start < a - 0.02) {
      const leftEnd = Number((clip.source_start + (a - start)).toFixed(3));
      if (leftEnd - clip.source_start >= 0.05) {
        next.push({ ...clip, id: `${clip.id}_l`, source_end: leftEnd });
      }
    }
    if (end > b + 0.02) {
      const rightStart = Number((clip.source_start + (b - start)).toFixed(3));
      if (clip.source_end - rightStart >= 0.05) {
        next.push({ ...clip, id: `${clip.id}_r`, source_start: rightStart });
      }
    }
  }
  return next.length ? next : clips;
}
