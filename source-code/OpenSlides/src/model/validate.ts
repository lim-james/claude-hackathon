import type { Background, ElementType, ThemeColorKey } from './types';

// Type imports only, so `node --test` can load this file without a bundler.

const ELEMENT_TYPES: Record<ElementType, true> = { text: true, shape: true, image: true, chart: true, table: true };
const BACKGROUND_KINDS: Record<Background['kind'], true> = { solid: true, gradient: true, image: true };
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

const isText = (v: unknown) => v === undefined || v === null || typeof v === 'string';
const allText = (...values: unknown[]) => values.every(isText);

// The checks below cover what the drawing code reads from a background or an
// element (SlideView, ElementView, ChartView): anything that would throw there.

function backgroundProblem(owner: string, bg: unknown): string | null {
  if (!isObj(bg) || !Object.hasOwn(BACKGROUND_KINDS, String(bg.kind))) return `${owner} has no background`;
  if (!allText(bg.color, bg.from, bg.to)) return `${owner} has a background colour that is not text`;
  return null;
}

function contentProblem(name: string, el: Record<string, any>): string | null {
  if (!isText(el.prompt)) return `${name} has a prompt that is not text`;
  switch (el.type as ElementType) {
    case 'text':
      if (typeof el.html !== 'string') return `${name} has no text`;
      if (!isObj(el.style)) return `${name} has no text style`;
      if (!allText(el.fill, el.style.color, el.style.fontFamily)) return `${name} has a colour or font that is not text`;
      return null;
    case 'shape':
      return allText(el.fill, el.stroke) ? null : `${name} has a colour that is not text`;
    case 'image':
      return isObj(el.filters) ? null : `${name} has no image filters`;
    case 'chart': {
      if (!isObj(el.data)) return `${name} has no chart data`;
      const { labels, series } = el.data;
      if (!Array.isArray(labels) || !labels.every((l) => typeof l === 'string')) return `${name} has no list of chart labels`;
      if (!Array.isArray(series) || !series.every((s) => isObj(s) && typeof s.name === 'string' && Array.isArray(s.values))) {
        return `${name} has a chart series without a name or values`;
      }
      if (!Array.isArray(el.colors) || !allText(...el.colors, el.textColor, el.fontFamily)) return `${name} has a colour or font that is not text`;
      return isText(el.title) ? null : `${name} has a title that is not text`;
    }
    case 'table': {
      const cell = (c: unknown) => c === null || typeof c !== 'object';
      if (!Array.isArray(el.rows) || !el.rows.every((r) => Array.isArray(r) && r.every(cell))) return `${name} has no rows of cells`;
      if (!isObj(el.style)) return `${name} has no table style`;
      const s = el.style;
      if (!allText(s.fontFamily, s.color, s.fill, s.borderColor, s.headerFill, s.headerColor, s.stripeFill)) {
        return `${name} has a colour or font that is not text`;
      }
      return null;
    }
  }
}

function elementsProblem(owner: string, elements: unknown, strict: boolean): string | null {
  if (!Array.isArray(elements)) return `${owner} has no list of elements`;
  for (let i = 0; i < elements.length; i++) {
    const el = elements[i];
    const name = `element ${i + 1} of ${owner}`;
    if (!isObj(el)) return `${name} is not an element`;
    if (!strict) continue;
    if (typeof el.type !== 'string' || !Object.hasOwn(ELEMENT_TYPES, el.type)) return `${name} has no known type`;
    if (!isNum(el.x) || !isNum(el.y) || !isNum(el.w) || !isNum(el.h)) return `${name} has no position or size`;
    const problem = contentProblem(name, el);
    if (problem) return problem;
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
    const problem = elementsProblem(`layout ${i + 1}`, layout.elements, strict) ?? (strict ? backgroundProblem(`layout ${i + 1}`, layout.background) : null);
    if (problem) return problem;
    layoutIds.add(layout.id);
  }

  if (!Array.isArray(data.slides)) return 'it has no slides';
  if (strict && data.slides.length === 0) return 'it has no slides';
  for (let i = 0; i < data.slides.length; i++) {
    const slide = data.slides[i];
    if (!isObj(slide)) return `slide ${i + 1} is not a slide`;
    const ownBackground = strict && slide.background !== undefined && slide.background !== null;
    const problem = elementsProblem(`slide ${i + 1}`, slide.elements, strict) ?? (ownBackground ? backgroundProblem(`slide ${i + 1}`, slide.background) : null);
    if (problem) return problem;
    // A stored deck with a stale layoutId still draws (it falls back to another layout).
    if (strict && !layoutIds.has(slide.layoutId)) return `slide ${i + 1} uses a layout the deck doesn't have`;
  }
  return null;
}
