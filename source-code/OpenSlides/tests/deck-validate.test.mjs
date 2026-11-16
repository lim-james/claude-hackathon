import test from 'node:test';
import assert from 'node:assert/strict';
import { deckProblem } from '../src/model/validate.ts';

const colors = {
  background: '#fff', surface: '#eee', text: '#111', muted: '#666',
  primary: '#246', secondary: '#468', accent: '#fc0',
};
const el = (extra = {}) => ({ id: 'e1', type: 'shape', x: 0, y: 0, w: 100, h: 50, rotation: 0, opacity: 1, ...extra });
const good = () => ({
  title: 'Budget',
  size: { w: 1280, h: 720, preset: '16:9' },
  theme: { id: 't', name: 'T', colors: { ...colors }, fonts: { heading: 'Inter', body: 'Inter' } },
  layouts: [{ id: 'L', name: 'Blank', background: { kind: 'solid', color: 'theme:background' }, elements: [] }],
  slides: [{ id: 's1', layoutId: 'L', elements: [el()], notes: '', transition: 'none' }],
  assets: {},
});
const broken = (change) => {
  const d = good();
  change(d);
  return d;
};

test('a well-formed deck has no problem, as a file or stored', () => {
  assert.equal(deckProblem(good()), null);
  assert.equal(deckProblem(good(), { stored: true }), null);
});

test('a deck file without assets is fine (the importer fills them in)', () => {
  assert.equal(deckProblem(broken((d) => delete d.assets)), null);
});

test('the file from issue #37 is refused, as a file and as a stored deck', () => {
  const file = JSON.parse('{"title":"Budget","slides":[{}],"layouts":[],"theme":{},"size":{}}');
  assert.equal(typeof deckProblem(file), 'string');
  assert.equal(typeof deckProblem(file, { stored: true }), 'string');
});

const refusedFiles = {
  'a slide without elements': (d) => delete d.slides[0].elements,
  'a slide whose layoutId names no layout': (d) => (d.slides[0].layoutId = 'nope'),
  'size without w and h': (d) => (d.size = { preset: '16:9' }),
  'size with a zero width': (d) => (d.size.w = 0),
  'size with a negative height': (d) => (d.size.h = -720),
  'size with a width that is not a number': (d) => (d.size.w = '1280'),
  'theme without its colours': (d) => delete d.theme.colors,
  'theme missing one colour': (d) => delete d.theme.colors.accent,
  'theme without fonts': (d) => delete d.theme.fonts,
  'an element without type': (d) => delete d.slides[0].elements[0].type,
  'an element of an unknown type': (d) => (d.slides[0].elements[0].type = 'toString'),
  'an element without a size': (d) => delete d.slides[0].elements[0].w,
  'a layout element without type': (d) => d.layouts[0].elements.push(el({ type: undefined })),
  'a layout without elements': (d) => delete d.layouts[0].elements,
  'slides: []': (d) => (d.slides = []),
  'a slide that is null': (d) => (d.slides = [null]),
  'assets that are not an object': (d) => (d.assets = 'x'),
};
for (const [name, change] of Object.entries(refusedFiles)) {
  test(`a deck file with ${name} is refused with a reason`, () => {
    const problem = deckProblem(broken(change));
    assert.equal(typeof problem, 'string');
    assert.ok(problem.length > 0);
  });
}

test('values that are not decks are refused, not thrown on', () => {
  for (const v of [null, undefined, 'deck', 7, [], {}]) {
    assert.equal(typeof deckProblem(v), 'string');
    assert.equal(typeof deckProblem(v, { stored: true }), 'string');
  }
});

// Stored decks that draw today must stay openable.
const stillUsableWhenStored = {
  'a stale layoutId': (d) => (d.slides[0].layoutId = 'gone'),
  'no slides': (d) => (d.slides = []),
  'a theme missing one colour': (d) => delete d.theme.colors.accent,
  'an element of an unknown type': (d) => (d.slides[0].elements[0].type = 'video'),
};
for (const [name, change] of Object.entries(stillUsableWhenStored)) {
  test(`a stored deck with ${name} is still usable`, () => {
    assert.equal(deckProblem(broken(change), { stored: true }), null);
  });
}

const damagedWhenStored = {
  'a slide without elements': (d) => delete d.slides[0].elements,
  'no size': (d) => delete d.size,
  'no theme colours': (d) => delete d.theme.colors,
  'no layouts': (d) => delete d.layouts,
  'assets that are not an object': (d) => (d.assets = null),
};
for (const [name, change] of Object.entries(damagedWhenStored)) {
  test(`a stored deck with ${name} is flagged as damaged`, () => {
    assert.equal(typeof deckProblem(broken(change), { stored: true }), 'string');
  });
}
