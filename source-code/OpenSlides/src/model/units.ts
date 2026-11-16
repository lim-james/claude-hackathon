// Slide pixels to PowerPoint units. The deck check and the exports both take
// their scale from here, so the sizes they report cannot drift apart.
// Type imports only: the unit tests load this file without a bundler.
import type { DeckSize } from './types';

/** The longer side of every exported slide, in inches. */
export const LONG_SIDE_IN = 13.333;

type Size = Pick<DeckSize, 'w' | 'h'>;

/** Exported slide size in inches: the longer side is 13.333 in, the other keeps the aspect ratio. */
export function slideInches(size: Size): { inW: number; inH: number } {
  const { w, h } = size;
  return w >= h ? { inW: LONG_SIDE_IN, inH: (LONG_SIDE_IN * h) / w } : { inW: (LONG_SIDE_IN * w) / h, inH: LONG_SIDE_IN };
}

/** Points per slide pixel in the exported file. */
export function ptPerPx(size: Size): number {
  return (72 * LONG_SIDE_IN) / Math.max(size.w, size.h);
}

/**
 * The point size text of `px` slide pixels gets in the exported file, which stores tenths of a point.
 * Rounding here keeps 36 px on a 1920 px side at 18 pt, not 17.9996.
 */
export function textPt(px: number, size: Size): number {
  return Math.round(px * ptPerPx(size) * 10) / 10;
}
