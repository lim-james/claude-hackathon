import test from 'node:test';
import assert from 'node:assert/strict';
import { cutText } from '../src/model/cutText.ts';

test('text within the limit comes back unchanged', () => {
  assert.equal(cutText('', 5), '');
  assert.equal(cutText('hello', 5), 'hello');
  assert.equal(cutText('😀😀', 4), '😀😀');
});

test('the issue #124 paste is cut before the emoji, never through it', () => {
  const text = 'Meeting notes. ' + 'x'.repeat(4984) + '😀 end of the notes';
  const kept = cutText(text, 5000);
  assert.equal(kept, 'Meeting notes. ' + 'x'.repeat(4984));
  assert.ok(kept.isWellFormed());
});

test('a cut never splits a surrogate pair, a combining sequence or an emoji sequence', () => {
  assert.equal(cutText('ab😀', 3), 'ab');
  assert.equal(cutText('abe\u0301', 3), 'ab'); // e + combining acute
  assert.equal(cutText('a👩\u200D💻', 4), 'a'); // woman technologist, 5 units
  assert.equal(cutText('a🇸🇬b', 3), 'a'); // flag, 4 units
  assert.equal(cutText('a\u1100\u1161\u11A8', 2), 'a'); // Hangul jamo syllable
  assert.equal(cutText('Z\u0353\u0351\u0352x', 2), ''); // one grapheme longer than the limit
});

test('every cut of a mixed text ends on a character boundary', () => {
  const text = 'C\u0327a 😀 e\u0301 👩\u200D💻 🇸🇬 \u1100\u1161\u11A8 日本 ';
  const graphemes = [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text)].map((s) => s.segment);
  for (let max = 0; max <= text.length; max++) {
    const kept = cutText(text, max);
    assert.ok(kept.length <= max, `max ${max}`);
    let n = 0;
    let joined = '';
    while (n < graphemes.length && joined.length + graphemes[n].length <= max) joined += graphemes[n++];
    assert.equal(kept, joined, `max ${max}`);
  }
});

test('cutting a very long paste only reads up to the limit', () => {
  const text = 'e\u0301'.repeat(2_500_000);
  const t = performance.now();
  assert.equal(cutText(text, 5000).length, 5000);
  assert.ok(performance.now() - t < 200);
});
