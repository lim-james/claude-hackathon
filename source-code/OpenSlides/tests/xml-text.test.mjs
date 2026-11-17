import test from 'node:test';
import assert from 'node:assert/strict';
import { xmlSafe } from '../src/io/xmlText.ts';

test('a vertical tab or form feed becomes a line break', () => {
  assert.equal(xmlSafe('Q3 results\u000BNorth region'), 'Q3 results\nNorth region');
  assert.equal(xmlSafe('Budget\u000C2027'), 'Budget\n2027');
  assert.equal(xmlSafe('\u000Ba\u000B\u000Cb\u000C'), '\na\n\nb\n');
});

test('every other character XML 1.0 cannot carry is dropped', () => {
  const forbidden = [];
  for (let c = 0; c < 0x20; c++) if (![0x09, 0x0a, 0x0d, 0x0b, 0x0c].includes(c)) forbidden.push(c);
  forbidden.push(0xfffe, 0xffff);
  assert.equal(forbidden.length, 29);
  for (const c of forbidden) {
    const hex = `U+${c.toString(16).toUpperCase().padStart(4, '0')}`;
    assert.equal(xmlSafe(`a${String.fromCharCode(c)}b`), 'ab', hex);
    assert.equal(xmlSafe(String.fromCharCode(c)), '', hex);
  }
  assert.equal(xmlSafe('\u0000\u0001x\u001F\uFFFEy\uFFFF'), 'xy');
});

test('the result holds no character XML 1.0 forbids, whatever goes in', () => {
  let all = '';
  for (let c = 0; c < 0x100; c++) all += String.fromCharCode(c);
  all += '\uFFFD\uFFFE\uFFFF';
  assert.doesNotMatch(xmlSafe(all), /[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/);
});

test('text XML can carry comes back unchanged', () => {
  for (const s of ['', 'plain', 'North & <South>', `"double" 'single'`, 'tab\there', 'line\nfeed', 'carriage\r\nreturn', '😀 👩‍💻', '日本語 العربية Ünïcödé', '  \uFFFD\uD7FF']) {
    assert.equal(xmlSafe(s), s);
  }
});

test('half of an emoji (a lone surrogate) becomes U+FFFD, a whole emoji is kept', () => {
  assert.equal(xmlSafe('end \uD83D'), 'end �');
  assert.equal(xmlSafe('\uDE00 start'), '� start');
  assert.equal(xmlSafe('a\uDE00\uD83Db'), 'a��b');
  assert.equal(xmlSafe('\uD83D\u0001'), '�');
  assert.equal(xmlSafe('ok 😀 \uD83D'), 'ok 😀 �');
  assert.ok(xmlSafe('x'.repeat(4999) + '\uD83D').isWellFormed());
});

test('xmlSafe stays fast on a long text full of emoji', () => {
  const text = ('word 😀 ').repeat(1_114_112 / 8);
  const times = [];
  for (let i = 0; i < 21; i++) {
    const t = performance.now();
    assert.equal(xmlSafe(text), text);
    times.push(performance.now() - t);
  }
  times.sort((a, b) => a - b);
  assert.ok(times[10] <= 10, `median ${times[10].toFixed(2)} ms`);
});
