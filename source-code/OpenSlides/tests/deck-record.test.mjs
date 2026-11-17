import test from 'node:test';
import assert from 'node:assert/strict';
import { produce } from 'immer';
import { isSplitRecord, joinDeck, splitDeck } from '../src/store/deckRecord.ts';

const photo = (id) => ({ id, dataUrl: `data:image/jpeg;base64,${id.repeat(1000)}`, w: 1200, h: 800, name: `${id}.jpg` });
const deck = () => ({
  id: 'd1',
  title: 'Photos',
  slides: [{ id: 's1', elements: [{ id: 'e1', type: 'image', assetId: 'p1', x: 0 }] }],
  assets: { p1: photo('p1'), p2: photo('p2') },
  createdAt: 1,
  updatedAt: 1,
});

// What storage.ts does after a successful save: remember the objects written.
const remember = (written, d, { put, removed }) => {
  for (const [id, a] of put) written.set(id, a);
  for (const id of removed) written.delete(id);
};

test('the first save of a deck writes every image, and the record holds none', () => {
  const d = deck();
  const out = splitDeck(d, new Map());
  assert.deepEqual(out.put.map(([id]) => id), ['p1', 'p2']);
  assert.equal('assets' in out.record, false);
  assert.deepEqual(out.record.assetIds, ['p1', 'p2']);
  assert.ok(!JSON.stringify(out.record).includes('base64'), 'no image data in the record');
});

test('an edit that does not touch images saves no image (#134)', () => {
  const written = new Map();
  let d = deck();
  remember(written, d, splitDeck(d, written));
  for (let n = 1; n <= 30; n++) {
    d = produce(d, (x) => void (x.slides[0].elements[0].x = n)); // a held arrow key
    const out = splitDeck(d, written);
    assert.deepEqual(out.put, []);
    assert.deepEqual(out.removed, []);
    assert.equal(out.record.slides[0].elements[0].x, n);
  }
});

test('an added or replaced image is the only one written; a dropped one is removed', () => {
  const written = new Map();
  let d = deck();
  remember(written, d, splitDeck(d, written));
  d = produce(d, (x) => void (x.assets.p3 = photo('p3')));
  assert.deepEqual(splitDeck(d, written).put.map(([id]) => id), ['p3']);
  remember(written, d, splitDeck(d, written));
  d = produce(d, (x) => void (x.assets.p1.dataUrl = 'data:image/png;base64,AAAA'));
  assert.deepEqual(splitDeck(d, written).put.map(([id]) => id), ['p1']);
  remember(written, d, splitDeck(d, written));
  d = produce(d, (x) => void delete x.assets.p2);
  const out = splitDeck(d, written);
  assert.deepEqual(out.put, []);
  assert.deepEqual(out.removed, ['p2']);
  assert.deepEqual(out.record.assetIds, ['p1', 'p3']);
});

test('split then join gives the deck back', () => {
  const d = deck();
  const { record, put } = splitDeck(d, new Map());
  const stored = new Map(put);
  assert.equal(isSplitRecord(record), true);
  assert.deepEqual(joinDeck(record, record.assetIds.map((id) => stored.get(id))), d);
});

test('a deck saved before the split (images inline) loads unchanged', () => {
  const legacy = deck(); // the shape every deck saved by 0.4.0 has: assets inline, no assetIds
  assert.equal(isSplitRecord(legacy), false);
  assert.equal(joinDeck(legacy, []), legacy);
  // ...and its first save moves every image out, so none is lost.
  const out = splitDeck(legacy, new Map());
  assert.deepEqual(out.put.map(([id, a]) => [id, a]), Object.entries(legacy.assets));
});

test('a missing stored image is left out instead of breaking the deck', () => {
  const { record } = splitDeck(deck(), new Map());
  const joined = joinDeck(record, [photo('p1'), undefined]);
  assert.deepEqual(Object.keys(joined.assets), ['p1']);
});
