import type { Asset, Deck, Layout, Slide, SlideElement, Theme } from './types';

export interface SlideDiff {
  id: string;
  status: 'added' | 'removed' | 'moved' | 'changed' | 'unchanged';
  fromIndex: number | null;
  toIndex: number | null;
  changes: string[];
}

/** A deck-level difference: one row in the compare dialog, e.g. Theme or Slide size. */
export interface DeckChange {
  kind: 'properties' | 'slide-size' | 'theme' | 'layouts' | 'images';
  label: string;
  changes: string[];
}

export interface DeckDiff {
  titleChange: { from: string; to: string } | null;
  deckChanges: DeckChange[];
  slides: SlideDiff[];
  summary: string;
  hasChanges: boolean;
}

const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

const TYPE_LABEL: Record<string, string> = {
  text: 'Text', shape: 'Shape', image: 'Image', chart: 'Chart', table: 'Table',
};
const label = (type: string) => TYPE_LABEL[type] ?? type;

function plainText(html: string): string {
  return html.replace(/<[^>]+>/g, ' ').replace(/&[a-z]+;/gi, ' ').replace(/\s+/g, ' ').trim();
}

function truncate(text: string, max = 32): string {
  return text.length > max ? text.slice(0, max - 1) + '…' : text;
}

function describeChange(prev: SlideElement, next: SlideElement): string {
  if (prev.type === 'text' && next.type === 'text') {
    const before = plainText(prev.html);
    const after = plainText(next.html);
    if (before !== after) return `Text changed: “${truncate(before)}” → “${truncate(after)}”`;
  }
  if (prev.type === 'table' && next.type === 'table' && !equal(prev.rows, next.rows)) {
    return 'Table contents changed';
  }
  if (prev.type === 'chart' && next.type === 'chart' && !equal(prev.data, next.data)) {
    return 'Chart data changed';
  }
  const moved = prev.x !== next.x || prev.y !== next.y || prev.w !== next.w || prev.h !== next.h;
  if (moved && equal({ ...prev, x: 0, y: 0, w: 0, h: 0 }, { ...next, x: 0, y: 0, w: 0, h: 0 })) {
    return `${label(next.type)} moved or resized`;
  }
  return `${label(next.type)} changed`;
}

/** True when the items present on both sides appear in a different order. */
function orderChanged(from: { id: string }[], to: { id: string }[]): boolean {
  const inFrom = new Set(from.map((x) => x.id));
  const inTo = new Set(to.map((x) => x.id));
  const a = from.filter((x) => inTo.has(x.id));
  const b = to.filter((x) => inFrom.has(x.id));
  return a.some((x, i) => x.id !== b[i]?.id);
}

function slideChanges(from: Slide, to: Slide): string[] {
  const changes: string[] = [];
  const fromEls = new Map(from.elements.map((e) => [e.id, e]));
  const toEls = new Map(to.elements.map((e) => [e.id, e]));
  for (const e of to.elements) if (!fromEls.has(e.id)) changes.push(`${label(e.type)} added`);
  for (const e of from.elements) if (!toEls.has(e.id)) changes.push(`${label(e.type)} removed`);
  for (const e of to.elements) {
    const prev = fromEls.get(e.id);
    if (prev && !equal(prev, e)) changes.push(describeChange(prev, e));
  }
  if (orderChanged(from.elements, to.elements)) changes.push('Element order (front to back) changed');
  if ((from.notes || '') !== (to.notes || '')) changes.push('Notes changed');
  if (from.layoutId !== to.layoutId) changes.push('Layout changed');
  if (!equal(from.background, to.background)) changes.push('Background changed');
  if (from.transition !== to.transition) changes.push('Transition changed');
  if ((from.hidden ?? false) !== (to.hidden ?? false)) changes.push(to.hidden ? 'Slide hidden' : 'Slide shown');
  return changes;
}

function themeChanges(from: Theme | undefined, to: Theme | undefined): string[] {
  if (equal(from, to)) return [];
  const changes: string[] = [];
  if (!equal(from?.colors, to?.colors)) changes.push('Colours changed');
  for (const font of ['heading', 'body'] as const) {
    const before = from?.fonts?.[font];
    const after = to?.fonts?.[font];
    if (before !== after) changes.push(`${font === 'heading' ? 'Heading' : 'Body'} font: ${before ?? 'none'} → ${after ?? 'none'}`);
  }
  if (from?.logoAssetId !== to?.logoAssetId) changes.push('Logo changed');
  return changes.length ? changes : ['Theme changed'];
}

function layoutChanges(from: Layout[] = [], to: Layout[] = []): string[] {
  const changes: string[] = [];
  const fromById = new Map(from.map((l) => [l.id, l]));
  const toIds = new Set(to.map((l) => l.id));
  for (const l of to) {
    const prev = fromById.get(l.id);
    if (!prev) changes.push(`“${l.name}” added`);
    else if (!equal(prev, l)) changes.push(`“${l.name}” changed`);
  }
  for (const l of from) if (!toIds.has(l.id)) changes.push(`“${l.name}” removed`);
  if (orderChanged(from, to)) changes.push('Layout order changed');
  return changes;
}

// Image data is compared as strings, never serialised: data URLs run to megabytes.
function assetChanges(from: Record<string, Asset> = {}, to: Record<string, Asset> = {}): string[] {
  let added = 0;
  let removed = 0;
  let changed = 0;
  for (const id of Object.keys(to)) {
    const a = from[id];
    const b = to[id];
    if (!a) added++;
    else if (a.dataUrl !== b.dataUrl || a.w !== b.w || a.h !== b.h || a.name !== b.name) changed++;
  }
  for (const id of Object.keys(from)) if (!to[id]) removed++;
  const n = (count: number, what: string) => `${count} image${count === 1 ? '' : 's'} ${what}`;
  const changes: string[] = [];
  if (added) changes.push(n(added, 'added'));
  if (removed) changes.push(n(removed, 'removed'));
  if (changed) changes.push(n(changed, 'replaced'));
  return changes;
}

function deckChanges(from: Deck, to: Deck): DeckChange[] {
  const rows: DeckChange[] = [];
  const add = (kind: DeckChange['kind'], label: string, changes: string[]) => {
    if (changes.length) rows.push({ kind, label, changes });
  };
  add('properties', 'Deck properties', from.id !== to.id || from.version !== to.version ? ['Deck id or format version changed'] : []);
  add('slide-size', 'Slide size', equal(from.size, to.size) ? [] : [`${sizeText(from.size)} → ${sizeText(to.size)}`]);
  add('theme', 'Theme', themeChanges(from.theme, to.theme));
  add('layouts', 'Layouts', layoutChanges(from.layouts, to.layouts));
  add('images', 'Images', assetChanges(from.assets, to.assets));
  return rows;
}

const sizeText = (size: Deck['size'] | undefined) => (size ? `${size.w} × ${size.h}` : 'none');

/** Structural diff between two decks: what changed going from `from` to `to`. */
export function diffDecks(from: Deck, to: Deck): DeckDiff {
  const fromIndex = new Map(from.slides.map((s, i) => [s.id, i]));
  const toIndex = new Map(to.slides.map((s, i) => [s.id, i]));
  const fromById = new Map(from.slides.map((s) => [s.id, s]));
  const slides: SlideDiff[] = [];
  // "Moved" compares a slide's rank among the slides both versions share, so
  // adding or deleting a slide does not mark every slide after it as moved.
  const fromRank = new Map(from.slides.filter((s) => toIndex.has(s.id)).map((s, i) => [s.id, i]));
  let rank = 0;

  to.slides.forEach((slide, i) => {
    const prev = fromById.get(slide.id);
    if (!prev) {
      slides.push({ id: slide.id, status: 'added', fromIndex: null, toIndex: i, changes: ['Slide added'] });
      return;
    }
    const changes = slideChanges(prev, slide);
    const fi = fromIndex.get(slide.id)!;
    const moved = fromRank.get(slide.id) !== rank++;
    const status: SlideDiff['status'] = changes.length ? 'changed' : moved ? 'moved' : 'unchanged';
    slides.push({ id: slide.id, status, fromIndex: fi, toIndex: i, changes });
  });
  for (const slide of from.slides) {
    if (!toIndex.has(slide.id)) {
      slides.push({ id: slide.id, status: 'removed', fromIndex: fromIndex.get(slide.id)!, toIndex: null, changes: ['Slide removed'] });
    }
  }

  const count = (s: SlideDiff['status']) => slides.filter((d) => d.status === s).length;
  const parts: string[] = [];
  for (const s of ['added', 'removed', 'changed', 'moved'] as const) {
    const n = count(s);
    if (n) parts.push(`${n} ${s}`);
  }
  const titleChange = from.title !== to.title ? { from: from.title, to: to.title } : null;
  const deck = deckChanges(from, to);
  const hasChanges = !!titleChange || deck.length > 0 || slides.some((s) => s.status !== 'unchanged');
  const deckParts = [...(titleChange ? ['title'] : []), ...deck.map((c) => c.label.toLowerCase())].map((p) => `${p} changed`);
  const summary = !hasChanges ? 'No changes' : [parts.join(', '), deckParts.join(', ')].filter(Boolean).join('; ');
  return { titleChange, deckChanges: deck, slides, summary, hasChanges };
}
