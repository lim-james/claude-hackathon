import type { ElementType, ThemeColorKey } from './types';

// Type imports only, so `node --test` can load this file without a bundler.

const ELEMENT_TYPES: Record<ElementType, true> = { text: true, shape: true, image: true, chart: true, table: true };
const COLOR_KEYS: Record<ThemeColorKey, true> = {
  background: true,
  surface: true,
  text: true,
  muted: true,
  primary: true,
  secondary: true,
  accent: true,
};

const isObj = (v: unknown): v is Record<string, any> => typeof v === 'object' && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

function elementsProblem(owner: string, elements: unknown, strict: boolean): string | null {
  if (!Array.isArray(elements)) return `${owner} has no list of elements`;
  for (let i = 0; i < elements.length; i++) {
    const el = elements[i];
    const name = `element ${i + 1} of ${owner}`;
    if (!isObj(el)) return `${name} is not an element`;
    if (!strict) continue;
    if (typeof el.type !== 'string' || !Object.hasOwn(ELEMENT_TYPES, el.type)) return `${name} has no known type`;
    if (!isNum(el.x) || !isNum(el.y) || !isNum(el.w) || !isNum(el.h)) return `${name} has no position or size`;
  }
  return null;
}

/**
 * Says in plain words why a deck can't be drawn, or returns null when it can.
 *
 * By default this checks a deck file someone is opening, and is strict. With
 * `stored: true` it checks a deck already saved in this browser and only looks
 * for the structure that drawing depends on, so a deck that works today is
 * never flagged for a value that merely looks odd.
 */
export function deckProblem(data: unknown, opts: { stored?: boolean } = {}): string | null {
  const strict = !opts.stored;
  if (!isObj(data)) return 'it is not a deck';

  if (!isObj(data.size)) return 'it has no slide size';
  if (strict && !(isNum(data.size.w) && isNum(data.size.h) && data.size.w > 0 && data.size.h > 0)) {
    return 'its slide size is not a width and height above zero';
  }

  if (!isObj(data.theme)) return 'it has no theme';
  if (!isObj(data.theme.colors)) return 'its theme has no colours';
  if (!isObj(data.theme.fonts)) return 'its theme has no fonts';
  if (strict) {
    for (const key of Object.keys(COLOR_KEYS)) {
      if (typeof data.theme.colors[key] !== 'string') return `its theme has no ${key} colour`;
    }
    if (typeof data.theme.fonts.heading !== 'string' || typeof data.theme.fonts.body !== 'string') {
      return 'its theme has no heading or body font';
    }
  }

  if (data.assets !== undefined && !isObj(data.assets)) return 'its images are not readable';

  if (!Array.isArray(data.layouts)) return 'it has no layouts';
  const layoutIds = new Set<unknown>();
  for (let i = 0; i < data.layouts.length; i++) {
    const layout = data.layouts[i];
    if (!isObj(layout)) return `layout ${i + 1} is not a layout`;
    const problem = elementsProblem(`layout ${i + 1}`, layout.elements, strict);
    if (problem) return problem;
    layoutIds.add(layout.id);
  }

  if (!Array.isArray(data.slides)) return 'it has no slides';
  if (strict && data.slides.length === 0) return 'it has no slides';
  for (let i = 0; i < data.slides.length; i++) {
    const slide = data.slides[i];
    if (!isObj(slide)) return `slide ${i + 1} is not a slide`;
    const problem = elementsProblem(`slide ${i + 1}`, slide.elements, strict);
    if (problem) return problem;
    // A stored deck with a stale layoutId still draws (it falls back to another layout).
    if (strict && !layoutIds.has(slide.layoutId)) return `slide ${i + 1} uses a layout the deck doesn't have`;
  }
  return null;
}
