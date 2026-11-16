import test from 'node:test';
import assert from 'node:assert/strict';
import { produce } from 'immer';
import { thumbEqual } from '../src/editor/thumbEqual.ts';

const el = (id, x = 0) => ({ id, type: 'shape', x, y: 0, w: 10, h: 10 });
const slide = (id) => ({ id, layoutId: 'L1', elements: [el(`${id}-a`), el(`${id}-b`)], notes: '' });
const makeDeck = () => ({
  id: 'd1',
  title: 'Deck',
  version: 1,
  size: { w: 1280, h: 720, preset: '16:9' },
  theme: { name: 'Plain', colors: { accent: '#123456' }, fonts: { heading: 'Inter', body: 'Inter' } },
  layouts: [{ id: 'L1', name: 'Blank', background: { kind: 'solid', color: '#fff' }, elements: [el('ph')] }],
  slides: [slide('s1'), slide('s2'), slide('s3')],
  assets: { a1: { id: 'a1', dataUrl: 'data:image/png;base64,AAAA' } },
  createdAt: 1,
  updatedAt: 1,
});

// The store applies every edit with immer (src/store/editor.ts, mutate), so the tests do the same.
const props = (deck, i) => ({ deck, slide: deck.slides[i], index: i });
const same = (before, after, i) => thumbEqual(props(before, i), props(after, i));

test('an edit on another slide leaves a thumbnail equal', () => {
  const before = makeDeck();
  const after = produce(before, (d) => { d.slides[0].elements[0].x += 1; });
  assert.notEqual(after, before);
  assert.equal(same(before, after, 1), true);
  assert.equal(same(before, after, 2), true);
});

test('an edit on the slide itself changes its thumbnail', () => {
  const before = makeDeck();
  const after = produce(before, (d) => { d.slides[2].elements[0].x += 1; });
  assert.equal(same(before, after, 2), false);
});

test('hiding a slide changes its thumbnail only', () => {
  const before = makeDeck();
  const after = produce(before, (d) => { d.slides[1].hidden = true; });
  assert.equal(same(before, after, 1), false);
  assert.equal(same(before, after, 0), true);
});

test('undo back to the earlier deck object changes only the edited slide', () => {
  const before = makeDeck();
  const after = produce(before, (d) => { d.slides[0].elements[0].x += 1; });
  assert.equal(same(after, before, 0), false);
  assert.equal(same(after, before, 1), true);
});

const everyThumb = {
  'theme colour': (d) => { d.theme.colors.accent = '#654321'; },
  'theme font': (d) => { d.theme.fonts.heading = 'Lora'; },
  'whole theme': (d) => { d.theme = { name: 'Other', colors: {}, fonts: {} }; },
  'layout element': (d) => { d.layouts[0].elements[0].x = 5; },
  'layout background': (d) => { d.layouts[0].background.color = '#000'; },
  'image replaced': (d) => { d.assets.a1.dataUrl = 'data:image/png;base64,BBBB'; },
  'image deleted': (d) => { delete d.assets.a1; },
  'image added': (d) => { d.assets.a2 = { id: 'a2', dataUrl: 'data:,' }; },
  'deck resized': (d) => { d.size.w = 1080; d.size.h = 1080; },
  'deck renamed': (d) => { d.title = 'Renamed'; },
  'slide added at the end': (d) => { d.slides.push({ id: 's4', layoutId: 'L1', elements: [], notes: '' }); },
  'last slide deleted': (d) => { d.slides.pop(); },
};

for (const [name, recipe] of Object.entries(everyThumb)) {
  test(`${name} changes every thumbnail`, () => {
    const before = makeDeck();
    const after = produce(before, recipe);
    for (const i of [0, 1]) assert.equal(same(before, after, i), false, `thumbnail ${i + 1}`);
  });
}

test('a slide that moved to another position changes (its number is drawn)', () => {
  const before = makeDeck();
  const after = produce(before, (d) => { d.slides.reverse(); });
  assert.equal(thumbEqual({ deck: before, slide: before.slides[0], index: 0 }, { deck: after, slide: after.slides[2], index: 2 }), false);
  assert.equal(after.slides[2], before.slides[0]);
});

test('only updatedAt changing leaves every thumbnail equal', () => {
  const before = makeDeck();
  const after = produce(before, (d) => { d.updatedAt = 2; });
  assert.equal(same(before, after, 0), true);
});
