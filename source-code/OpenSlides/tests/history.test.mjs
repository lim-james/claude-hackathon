import test from 'node:test';
import assert from 'node:assert/strict';
import {
  armCheckpoint,
  checkpoint,
  disarmCheckpoint,
  mutate,
  openDeck,
  redo,
  undo,
  useEditor,
} from '../src/store/editor.ts';

const deck = () => ({ slides: [{ id: 's1', notes: '', elements: [] }], layouts: [], assets: {}, theme: {} });
const S = () => useEditor.getState();
const setNotes = (v) => mutate((d) => void (d.slides[0].notes = v), false);

function afterUndo() {
  openDeck(deck());
  mutate((d) => void (d.slides[0].notes = 'heart'));
  undo();
  assert.equal(S().past.length, 0);
  assert.equal(S().future.length, 1);
}

test('arming without a change keeps undo and redo (#136)', () => {
  afterUndo();
  armCheckpoint();
  disarmCheckpoint();
  assert.equal(S().past.length, 0);
  assert.equal(S().future.length, 1);
  redo();
  assert.equal(S().deck.slides[0].notes, 'heart');
});

test('an armed run of changes is one undo step and clears redo', () => {
  afterUndo();
  armCheckpoint();
  setNotes('a');
  setNotes('ab');
  assert.equal(S().past.length, 1);
  assert.equal(S().future.length, 0);
  undo();
  assert.equal(S().deck.slides[0].notes, '');
});

test('a change after disarm records no step', () => {
  openDeck(deck());
  armCheckpoint();
  disarmCheckpoint();
  setNotes('measured');
  assert.equal(S().past.length, 0);
});

test('checkpoint and history mutations clear the arm, so no extra step follows', () => {
  openDeck(deck());
  armCheckpoint();
  mutate((d) => void (d.slides[0].notes = 'x'));
  setNotes('xy');
  assert.equal(S().past.length, 1);
  armCheckpoint();
  checkpoint();
  setNotes('xyz');
  assert.equal(S().past.length, 2);
});

test('a re-armed second run is its own undo step', () => {
  openDeck(deck());
  armCheckpoint();
  setNotes('one');
  disarmCheckpoint();
  armCheckpoint();
  setNotes('one two');
  assert.equal(S().past.length, 2);
  undo();
  assert.equal(S().deck.slides[0].notes, 'one');
});
