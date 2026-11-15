/**
 * The OCR scan rectangle, shared by the Settings picker and the video preview.
 *
 * A region is `[left, top, right, bottom]` in percent of the *source frame* —
 * the same shape `ocr.region` takes in `providers.json`. OCR reads whatever it
 * is given, so a channel bug, a logo or a "part 2" banner inside the region
 * becomes a subtitle line; the helpers here keep the box and the numbers in
 * agreement no matter where it was drawn.
 */

export type Region = [number, number, number, number];

/** What the engine uses until the user draws something (the lower 45%). */
export const DEFAULT_REGION: Region = [0, 55, 100, 100];

/** Mirrors `provider_config.MIN_REGION_SPAN`: a thinner box cannot hold a cue. */
export const MIN_REGION_SPAN = 4;

export const REGION_PRESETS: { label: string; region: Region }[] = [
  { label: "Dưới 45%", region: [0, 55, 100, 100] },
  { label: "Dưới 30%", region: [0, 70, 100, 100] },
  { label: "Trên 30%", region: [0, 0, 100, 30] },
  { label: "Toàn khung", region: [0, 0, 100, 100] },
];

export function roundPercent(value: number): number {
  return Math.round(value * 10) / 10;
}

/** Keep the box inside the frame and at least `span` percent wide and tall. */
export function fitRegion(region: Region, span = MIN_REGION_SPAN): Region {
  const left = Math.min(Math.max(region[0], 0), 100 - span);
  const top = Math.min(Math.max(region[1], 0), 100 - span);
  return [
    roundPercent(left),
    roundPercent(top),
    roundPercent(Math.min(Math.max(region[2], left + span), 100)),
    roundPercent(Math.min(Math.max(region[3], top + span), 100)),
  ];
}

/** The rectangle spanned by two dragged corners, already fitted. */
export function regionFrom(
  start: { x: number; y: number },
  end: { x: number; y: number },
  span = MIN_REGION_SPAN,
): Region {
  return fitRegion(
    [
      Math.min(start.x, end.x),
      Math.min(start.y, end.y),
      Math.max(start.x, end.x),
      Math.max(start.y, end.y),
    ],
    span,
  );
}

/** What `ocr.region` stores: `"left,top,right,bottom"`, or `""` to reset. */
export function formatRegion(region: Region | null): string {
  return region ? region.map(roundPercent).join(",") : "";
}

/** The panel chip: `12.5 · 60 · 88 · 94 %`. */
export function regionLabel(region: Region): string {
  return `${region.map((value) => String(roundPercent(value))).join(" · ")} %`;
}
