import test from 'node:test';
import assert from 'node:assert/strict';
import { diffDecks } from '../src/model/diff.ts';

const textEl = (id, text, props = {}) => ({
  id, type: 'text', x: 0, y: 0, w: 100, h: 50, rotation: 0, opacity: 1,
  html: `<p>${text}</p>`, style: {}, fill: '#fff', padding: 0, autoHeight: true, ...props,
});
const imageEl = (id) => ({
  id, type: 'image', x: 0, y: 0, w: 100, h: 50, rotation: 0, opacity: 1,
  assetId: 'a1', fit: 'cover', radius: 0, filters: {},
});
const slide = (id, elements = [], extra = {}) => ({
  id, layoutId: 'L', elements, notes: '', transition: 'none', ...extra,
});
const deck = (slides, title = 'Deck') => ({ id: 'd1', title, slides });

const statusOf = (d, id) => d.slides.find((s) => s.id === id)?.status;

test('identical decks report no changes', () => {
  const d = deck([slide('s1', [textEl('t1', 'Hello')])]);
  const diff = diffDecks(d, structuredClone(d));
  assert.equal(diff.hasChanges, false);
  assert.equal(diff.summary, 'No changes');
  assert.equal(statusOf(diff, 's1'), 'unchanged');
});

test('detects added and removed slides', () => {
  const from = deck([slide('s1'), slide('s2')]);
  const to = deck([slide('s1'), slide('s3')]);
  const diff = diffDecks(from, to);
  assert.equal(statusOf(diff, 's3'), 'added');
  assert.equal(statusOf(diff, 's2'), 'removed');
  assert.ok(diff.summary.includes('1 added'));
  assert.ok(diff.summary.includes('1 removed'));
});

test('detects a moved slide without content change', () => {
  const from = deck([slide('s1'), slide('s2')]);
  const to = deck([slide('s2'), slide('s1')]);
  const diff = diffDecks(from, to);
  assert.equal(statusOf(diff, 's1'), 'moved');
  assert.equal(statusOf(diff, 's2'), 'moved');
  assert.ok(diff.summary.includes('2 moved'));
});

const five = () => deck(['s1', 's2', 's3', 's4', 's5'].map((id) => slide(id)));
const movedIds = (d) => d.slides.filter((s) => s.status === 'moved').map((s) => s.id);

test('adding or deleting one slide does not list the slides after it as moved', () => {
  const from = five();
  const at = (i, ...insert) => deck([...from.slides.slice(0, i), ...insert, ...from.slides.slice(i + (insert.length ? 0 : 1))]);
  assert.equal(diffDecks(from, at(0, slide('new'))).summary, '1 added');
  assert.equal(diffDecks(from, at(2, slide('new'))).summary, '1 added');
  assert.equal(diffDecks(from, at(0)).summary, '1 removed');
  assert.equal(diffDecks(from, at(2)).summary, '1 removed');
});

test('a swap next to a deleted slide reports only the swapped slides as moved', () => {
  const from = five();
  const [, s2, s3, s4, s5] = from.slides;
  const diff = diffDecks(from, deck([s2, s3, s5, s4]));
  assert.equal(diff.summary, '1 removed, 2 moved');
  assert.deepEqual(movedIds(diff).sort(), ['s4', 's5']);
});

test('dragging the last slide to the front counts every shared slide as moved', () => {
  const from = five();
  const [s1, s2, s3, s4, s5] = from.slides;
  assert.equal(diffDecks(from, deck([s5, s1, s2, s3, s4])).summary, '5 moved');
});

test('a slide that is moved and edited is reported as changed', () => {
  const from = deck([slide('s1'), slide('s2')]);
  const to = deck([slide('s2', [], { notes: 'edited' }), slide('s1')]);
  const diff = diffDecks(from, to);
  assert.equal(statusOf(diff, 's2'), 'changed');
  assert.equal(statusOf(diff, 's1'), 'moved');
  assert.equal(diff.summary, '1 changed, 1 moved');
});

test('describes a text edit with before and after', () => {
  const from = deck([slide('s1', [textEl('t1', 'Old title')])]);
  const to = deck([slide('s1', [textEl('t1', 'New title')])]);
  const diff = diffDecks(from, to);
  const s = diff.slides.find((x) => x.id === 's1');
  assert.equal(s.status, 'changed');
  assert.equal(s.changes.length, 1);
  assert.match(s.changes[0], /Text changed.*Old title.*New title/);
});

test('detects element add and remove within a slide', () => {
  const from = deck([slide('s1', [textEl('t1', 'A')])]);
  const to = deck([slide('s1', [textEl('t1', 'A'), imageEl('i1')])]);
  const diff = diffDecks(from, to);
  const s = diff.slides.find((x) => x.id === 's1');
  assert.deepEqual(s.changes, ['Image added']);

  const back = diffDecks(to, from);
  assert.deepEqual(back.slides.find((x) => x.id === 's1').changes, ['Image removed']);
});

test('detects notes and title changes', () => {
  const from = deck([slide('s1', [], { notes: 'first' })], 'A');
  const to = deck([slide('s1', [], { notes: 'second' })], 'B');
  const diff = diffDecks(from, to);
  assert.ok(diff.slides.find((x) => x.id === 's1').changes.includes('Notes changed'));
  assert.deepEqual(diff.titleChange, { from: 'A', to: 'B' });
  assert.ok(diff.summary.includes('title changed'));
  assert.equal(diff.hasChanges, true);
});

// Deck-level fields: everything Restore would change apart from createdAt and updatedAt.
const asset = (id, dataUrl = 'data:image/png;base64,AAAA') => ({ id, dataUrl, w: 10, h: 10, name: `${id}.png` });
const fullDeck = () => ({
  id: 'd1', title: 'Deck', version: 1,
  size: { w: 1280, h: 720, preset: '16:9' },
  theme: {
    id: 'ink', name: 'Ink',
    colors: { background: '#ffffff', text: '#111111' },
    fonts: { heading: 'Inter', body: 'Inter' },
  },
  layouts: [
    { id: 'L', name: 'Title', background: { kind: 'solid', color: '#ffffff' }, elements: [textEl('m1', 'Confidential footer')] },
    { id: 'L2', name: 'Blank', background: { kind: 'solid', color: '#ffffff' }, elements: [] },
  ],
  slides: [1, 2, 3].map((n) => slide(`s${n}`, [textEl(`a${n}`, 'Front'), textEl(`b${n}`, 'Back')])),
  assets: { a1: asset('a1') },
  createdAt: 1, updatedAt: 2,
});
const rowOf = (d, kind) => d.deckChanges.find((c) => c.kind === kind);
const diffAfter = (edit) => {
  const to = fullDeck();
  edit(to);
  return diffDecks(fullDeck(), to);
};

test('identical full decks report no changes, ignoring timestamps', () => {
  const diff = diffAfter((to) => { to.createdAt = 100; to.updatedAt = 200; });
  assert.equal(diff.hasChanges, false);
  assert.equal(diff.summary, 'No changes');
  assert.deepEqual(diff.deckChanges, []);
});

test('detects a theme change and names what changed', () => {
  const diff = diffAfter((to) => {
    to.theme.colors = { background: '#000000', text: '#ffffff' };
    to.theme.fonts.heading = 'Lora';
  });
  assert.equal(diff.hasChanges, true);
  assert.equal(diff.summary, 'theme changed');
  assert.deepEqual(rowOf(diff, 'theme').changes, ['Colours changed', 'Heading font: Inter → Lora']);
});

test('detects a slide size change', () => {
  const diff = diffAfter((to) => { to.size = { w: 720, h: 1280, preset: 'portrait' }; });
  assert.equal(diff.hasChanges, true);
  assert.equal(diff.summary, 'slide size changed');
  assert.deepEqual(rowOf(diff, 'slide-size').changes, ['1280 × 720 → 720 × 1280']);
});

test('detects edited, added, removed and reordered layouts', () => {
  const edited = diffAfter((to) => {
    to.layouts[0].elements[0].html = '<p>Public footer</p>';
    to.layouts[0].background.color = '#ff0000';
  });
  assert.equal(edited.hasChanges, true);
  assert.equal(edited.summary, 'layouts changed');
  assert.deepEqual(rowOf(edited, 'layouts').changes, ['“Title” changed']);

  const added = diffAfter((to) => { to.layouts.push({ ...to.layouts[1], id: 'L3', name: 'Quote' }); });
  assert.deepEqual(rowOf(added, 'layouts').changes, ['“Quote” added']);

  const removed = diffAfter((to) => { to.layouts.pop(); });
  assert.deepEqual(rowOf(removed, 'layouts').changes, ['“Blank” removed']);

  const reordered = diffAfter((to) => { to.layouts.reverse(); });
  assert.deepEqual(rowOf(reordered, 'layouts').changes, ['Layout order changed']);
});

test('detects element stacking order changes on a slide', () => {
  const diff = diffAfter((to) => { to.slides[0].elements.reverse(); });
  assert.equal(diff.hasChanges, true);
  assert.equal(diff.summary, '1 changed');
  assert.equal(statusOf(diff, 's1'), 'changed');
  assert.deepEqual(diff.slides.find((s) => s.id === 's1').changes, ['Element order (front to back) changed']);
});

test('adding or removing an element is not reported as a reorder', () => {
  const diff = diffAfter((to) => { to.slides[0].elements.unshift(textEl('new', 'New')); });
  assert.deepEqual(diff.slides.find((s) => s.id === 's1').changes, ['Text added']);
});

test('detects replaced, added and removed images', () => {
  const replaced = diffAfter((to) => { to.assets.a1.dataUrl = 'data:image/png;base64,BBBB'; });
  assert.equal(replaced.hasChanges, true);
  assert.equal(replaced.summary, 'images changed');
  assert.deepEqual(rowOf(replaced, 'images').changes, ['1 image replaced']);

  const added = diffAfter((to) => { to.assets.a2 = asset('a2'); to.assets.a3 = asset('a3'); });
  assert.deepEqual(rowOf(added, 'images').changes, ['2 images added']);

  const removed = diffAfter((to) => { to.assets = {}; });
  assert.deepEqual(rowOf(removed, 'images').changes, ['1 image removed']);
});

test('every Deck field except the timestamps counts as a change', () => {
  const edits = {
    id: (to) => { to.id = 'd2'; },
    title: (to) => { to.title = 'Renamed'; },
    version: (to) => { to.version = 2; },
    size: (to) => { to.size.w = 1024; },
    theme: (to) => { to.theme.name = 'Ink 2'; },
    layouts: (to) => { to.layouts[1].name = 'Empty'; },
    slides: (to) => { to.slides[0].notes = 'note'; },
    assets: (to) => { to.assets.a1.name = 'renamed.png'; },
  };
  assert.deepEqual(Object.keys(edits).sort(), Object.keys(fullDeck()).filter((k) => k !== 'createdAt' && k !== 'updatedAt').sort());
  for (const [field, edit] of Object.entries(edits)) {
    const diff = diffAfter(edit);
    assert.equal(diff.hasChanges, true, field);
    assert.notEqual(diff.summary, 'No changes', field);
  }
});

test('summary lists slide counts, then the title and deck-level changes', () => {
  const diff = diffAfter((to) => {
    to.title = 'Renamed';
    to.size.w = 1024;
    to.slides[0].notes = 'note';
  });
  assert.equal(diff.summary, '1 changed; title changed, slide size changed');
});

test('comparing two 100-slide decks with large images stays under 50 ms', () => {
  const big = () => {
    const d = fullDeck();
    d.slides = Array.from({ length: 100 }, (_, n) => slide(`s${n}`, Array.from({ length: 10 }, (_, i) => textEl(`e${n}-${i}`, `Text ${i}`))));
    for (let i = 0; i < 20; i++) d.assets[`img${i}`] = asset(`img${i}`, 'data:image/png;base64,' + 'A'.repeat(1_000_000) + i);
    return d;
  };
  // structuredClone gives each deck its own flat copy of the image data, as a deck read from storage has.
  const from = structuredClone(big());
  const to = structuredClone(big());
  let best = Infinity;
  for (let run = 0; run < 3; run++) {
    const start = performance.now();
    const diff = diffDecks(from, to);
    best = Math.min(best, performance.now() - start);
    assert.equal(diff.hasChanges, false);
  }
  assert.ok(best < 50, `took ${best.toFixed(1)} ms`);
});
