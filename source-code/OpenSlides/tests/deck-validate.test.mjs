import test from 'node:test';
import assert from 'node:assert/strict';
import { deckProblem } from '../src/model/validate.ts';

const colors = {
  background: '#fff', surface: '#eee', text: '#111', muted: '#666',
  primary: '#246', secondary: '#468', accent: '#fc0',
};
const box = { id: 'e1', x: 0, y: 0, w: 100, h: 50, rotation: 0, opacity: 1 };
const el = (extra = {}) => ({ ...box, type: 'shape', shape: 'rect', fill: 'theme:primary', stroke: 'transparent', strokeWidth: 0, radius: 0, ...extra });
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

// One element of each type, as the app writes them, so each type's own checks are exercised.
const textStyle = () => ({ fontFamily: 'theme:body', fontSize: 40, color: 'theme:text', bold: false, italic: false, underline: false, align: 'left', valign: 'top', lineHeight: 1.2, letterSpacing: 0 });
const tableStyle = () => ({ fontFamily: 'theme:body', fontSize: 28, color: 'theme:text', headerFill: 'theme:primary', headerColor: '#ffffff', borderColor: 'theme:muted', stripe: true, stripeFill: 'theme:surface', fill: 'transparent' });
const text = () => ({ ...box, type: 'text', html: '<p>Hi</p>', style: textStyle(), fill: 'transparent', padding: 8, autoHeight: false });
const image = () => ({ ...box, type: 'image', assetId: '', fit: 'cover', radius: 0, filters: { brightness: 1, contrast: 1, saturate: 1, grayscale: 0 } });
const chart = () => ({ ...box, type: 'chart', chartType: 'column', data: { labels: ['Q1'], series: [{ name: '2026', values: [3] }] }, title: '', showLegend: true, showValues: false, showGrid: true, colors: ['theme:primary'], fontFamily: 'theme:body', fontSize: 26, textColor: 'theme:text' });
const table = () => ({ ...box, type: 'table', rows: [['A', 'B'], ['1', '2']], headerRow: true, style: tableStyle() });
const withElement = (element, change) => {
  const d = good();
  d.slides[0].elements = [element];
  change?.(element, d);
  return d;
};

test('a deck file with a well-formed element of every type is fine', () => {
  for (const make of [el, text, image, chart, table]) assert.equal(deckProblem(withElement(make())), null);
  const d = good();
  d.slides[0].background = { kind: 'gradient', from: '#000', to: 'theme:primary', angle: 90 };
  assert.equal(deckProblem(d), null);
});

// Each of these passed the first version of the check and then threw while being drawn.
const undrawableFiles = {
  'a text element without style and html': [text, (e) => (delete e.style, delete e.html)],
  'a text element without style': [text, (e) => delete e.style],
  'a text element whose html is a number': [text, (e) => (e.html = 5)],
  'a text element whose colour is a number': [text, (e) => (e.style.color = 5)],
  'a shape whose fill is an object': [el, (e) => (e.fill = {})],
  'an image element without filters': [image, (e) => delete e.filters],
  'a chart element without data': [chart, (e) => delete e.data],
  'a chart whose labels are not a list': [chart, (e) => (e.data.labels = 'Q1')],
  'a chart series without values': [chart, (e) => delete e.data.series[0].values],
  'a chart without its list of colours': [chart, (e) => delete e.colors],
  'a table whose rows is a string': [table, (e) => (e.rows = 'A,B')],
  'a table with a cell that is an object': [table, (e) => (e.rows[0][0] = {})],
  'a table without style': [table, (e) => delete e.style],
  'a layout without background': [el, (_e, d) => delete d.layouts[0].background],
  'a layout background of an unknown kind': [el, (_e, d) => (d.layouts[0].background = { kind: 'video' })],
  'a slide background that is a string': [el, (_e, d) => (d.slides[0].background = '#fff')],
  // Found by the review of 2bcb12e: the deck check and the layout list read these.
  'a chart without a title': [chart, (e) => delete e.title],
  'a chart whose title is null': [chart, (e) => (e.title = null)],
  'a layout whose name is an object': [el, (_e, d) => (d.layouts[0].name = {})],
  'a layout whose name is a list': [el, (_e, d) => (d.layouts[0].name = [])],
};
for (const [name, [make, change]] of Object.entries(undrawableFiles)) {
  test(`a deck file with ${name} is refused with a reason`, () => {
    const d = withElement(make(), change);
    const problem = deckProblem(d);
    assert.equal(typeof problem, 'string');
    assert.ok(problem.length > 0);
    // Saved decks are only checked for their containers, so none of these is flagged in storage.
    assert.equal(deckProblem(d, { stored: true }), null);
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

// The check and the code that reads a deck agree on its shape field by field. This file holds every field
// `src/model/types.ts` declares; each is dropped, set to null and given every wrong type, one at a time.
const everyField = () => {
  const more = { locked: false, groupId: 'g1', name: 'Named', alt: 'Described', anim: { effect: 'fade', trigger: 'click', duration: 400, delay: 0, order: 0 } };
  const d = good();
  d.theme.logoAssetId = 'a1';
  d.assets = { a1: { id: 'a1', dataUrl: 'data:image/png;base64,AAAA', w: 1, h: 1, name: 'dot.png' } };
  d.layouts[0].elements = [
    { ...text(), id: 'p1', placeholder: 'title', prompt: 'Add a title' },
    { ...image(), id: 'p2', placeholder: 'image', prompt: 'Add a picture' },
  ];
  d.layouts.push({ id: 'L2', name: 'Photo', background: { kind: 'image', assetId: 'a1', dim: 0.3 }, elements: [] });
  d.slides[0].elements = [
    { ...text(), id: 't1', ph: { ref: 'p1', geom: true, style: true }, ...more },
    { ...image(), id: 'i1', assetId: 'a1', ...more },
    { ...el(), id: 's1', ...more },
    { ...chart(), id: 'c1', ...more },
    { ...table(), id: 'b1', ...more },
  ];
  d.slides[0].background = { kind: 'gradient', from: '#000000', to: 'theme:primary', angle: 90 };
  d.slides[0].hidden = false;
  d.slides.push({ id: 's2', layoutId: 'L2', elements: [], notes: 'Say hello', transition: 'fade', background: { kind: 'solid', color: '#ffffff' } });
  return { format: 'openslides', id: 'd1', version: 1, createdAt: 1, updatedAt: 2, ...d };
};
// Fields a deck may leave out (the `?` fields of types.ts), and the ones `readDeckFile` sets itself.
const mayBeAbsent = new Set(['locked', 'groupId', 'name', 'alt', 'anim', 'ph', 'placeholder', 'prompt', 'hidden', 'logoAssetId', 'assets']);
const setByReader = new Set(['format', 'id', 'title', 'version', 'createdAt', 'updatedAt']);
const wrongTypes = { text: 'x', number: 7, boolean: true, object: {}, list: [] };
const kindOf = (v) => (v === null ? 'null' : Array.isArray(v) ? 'list' : typeof v === 'object' ? 'object' : typeof v === 'string' ? 'text' : typeof v);

test('the file with every field is fine', () => {
  assert.equal(deckProblem(everyField()), null);
});

test('every field of a deck file, dropped, null or of the wrong type, is refused', () => {
  const root = everyField();
  const wronglyAccepted = [];
  let tried = 0;
  const visit = (node, path) => {
    const keys = Array.isArray(node) ? node.map((_, i) => i) : Object.keys(node);
    for (const key of keys) {
      const here = [...path, key];
      const name = here.join('.');
      const topLevel = path.length === 0;
      if (topLevel && setByReader.has(key)) continue;
      // `name` may be left out of an element, and of nothing else.
      const inElement = path.at(-2) === 'elements';
      // A slide may have no background of its own, and an image may be gone from the deck's images.
      const optional = (mayBeAbsent.has(key) && (key !== 'name' || inElement)) || (key === 'background' && path[0] === 'slides') || path.at(-1) === 'assets';
      const changes = { null: null, ...wrongTypes };
      delete changes[kindOf(node[key])];
      const variants = Object.entries(changes).map(([label, value]) => [label, (parent) => (parent[key] = value)]);
      if (!Array.isArray(node)) variants.push(['dropped', (parent) => delete parent[key]]);
      for (const [label, change] of variants) {
        const copy = structuredClone(root);
        change(path.reduce((v, k) => v[k], copy));
        tried++;
        const refused = typeof deckProblem(copy) === 'string';
        if (refused === (label === 'dropped' && optional)) wronglyAccepted.push(`${name} ${label}: ${refused ? 'refused' : 'accepted'}`);
      }
      if (node[key] && typeof node[key] === 'object') visit(node[key], here);
    }
  };
  visit(root, []);
  assert.ok(tried > 1500, `only ${tried} variants`);
  assert.deepEqual(wronglyAccepted, []);
});

test('a value from the file never appears in the reason', () => {
  const d = everyField();
  d.assets = { '<img src=x onerror=alert(1)>': { id: 5 } };
  assert.equal(deckProblem(d), 'image 1 has a "id" that is not text');
});

test('a field that sits on Object.prototype does not stand in for a missing one', () => {
  const d = everyField();
  delete d.slides[0].elements[0].style;
  d.slides[0].elements[0].__proto__ = { style: textStyle() };
  assert.equal(typeof deckProblem(d), 'string');
});
