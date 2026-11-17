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

/** The deck size, or its `ptPerPx` already worked out (the export computes it once per deck). */
type Scale = Size | number;
const scaleOf = (s: Scale) => (typeof s === 'number' ? s : ptPerPx(s));

/**
 * The point size text of `px` slide pixels gets in the exported file, which stores tenths of a point.
 * Rounding here keeps 36 px on a 1920 px side at 18 pt, not 17.9996.
 */
export function textPt(px: number, size: Scale): number {
  return Math.round(px * scaleOf(size) * 10) / 10;
}

/** Table text in the exported file: `textPt`, but never under the 6 pt the export writes as a floor. */
export function tablePt(px: number, size: Scale): number {
  return Math.max(6, textPt(px, size));
}

/** Chart axis, legend and data-label text in the exported file: 85% of the text size, whole points, at least 6 pt. */
export function chartPt(px: number, size: Scale): number {
  return Math.max(6, Math.round(px * scaleOf(size) * 0.85));
}

const VOID_TAG = /^(br|wbr|hr|img|input|col|area|source|meta|link)$/i;
// The export reads font-size from inline elements only; paragraphs and list items keep the box's size.
const BLOCK_TAG = /^(p|div|ul|ol|li)$/i;
const SPAN_PX = /font-size\s*:\s*([\d.]+)px/i;
const isNameChar = (c: number) => (c >= 97 && c <= 122) || (c >= 65 && c <= 90) || (c >= 48 && c <= 57) || c === 45;

/** Whether html[from, to) holds a character other than whitespace and non-breaking spaces. */
function visible(html: string, from: number, to: number): boolean {
  for (let i = from; i < to; i++) {
    const c = html.charCodeAt(i);
    if (c <= 32 || c === 160) continue;
    if (c === 38 && (html.startsWith('&nbsp;', i) || html.startsWith('&#160;', i))) {
      i += 5;
      continue;
    }
    return true;
  }
  return false;
}

/**
 * The smallest size, in exported points, of any visible text in a text box's HTML: the box's own size
 * and every inline `font-size: Npx` (what the size control sets on a selection), each through `textPt`
 * as `htmlToRuns` in the export sizes its runs. A hand-rolled scan, not a DOM parse or `matchAll`:
 * the deck check runs it on every text box after every edit.
 * Null when the box has no visible text.
 */
export function smallestTextPt(px: number, html: string, size: Scale): number | null {
  const sized = html.includes('font-size'); // when false, every run is at the box's size
  const stack = [px];
  let min = Infinity;
  let i = 0;
  while (i < html.length) {
    const lt = html.indexOf('<', i);
    const textEnd = lt < 0 ? html.length : lt;
    const cur = stack[stack.length - 1];
    if (cur < min && visible(html, i, textEnd)) {
      min = cur;
      if (!sized) break;
    }
    if (lt < 0) break;
    const gt = html.indexOf('>', lt);
    if (gt < 0) break;
    const close = html.charCodeAt(lt + 1) === 47;
    let nameEnd = lt + (close ? 2 : 1);
    while (nameEnd < gt && isNameChar(html.charCodeAt(nameEnd))) nameEnd++;
    const tag = html.slice(lt + (close ? 2 : 1), nameEnd);
    if (tag && !VOID_TAG.test(tag)) {
      if (close) {
        if (stack.length > 1) stack.pop();
      } else if (html.charCodeAt(gt - 1) !== 47) {
        const fs = sized ? html.indexOf('font-size', nameEnd) : -1;
        const own = fs >= 0 && fs < gt && !BLOCK_TAG.test(tag) ? SPAN_PX.exec(html.slice(nameEnd, gt)) : null;
        stack.push(own ? parseFloat(own[1]) : cur);
      }
    }
    i = gt + 1;
  }
  return min === Infinity ? null : textPt(min, size);
}

/** A point size as a message shows it: whole sizes without a decimal ("11"), others to 0.1 pt ("11.5"). */
export function formatPt(pt: number): string {
  return String(Math.round(pt * 10) / 10);
}
