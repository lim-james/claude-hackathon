import test from 'node:test';
import assert from 'node:assert/strict';
import { ptPerPx, slideInches, textPt } from '../src/model/units.ts';

// The deck check (src/doctor/checks.ts) and the PowerPoint export
// (src/io/export.tsx) both take their scale from these two functions.
// Expected sizes are what the export writes to the .pptx for 20 px text.
const SIZES = [
  ['16:9', 1920, 1080, 10.0],
  ['4:3', 1440, 1080, 13.3],
  ['a4', 1754, 1240, 10.9],
  ['1:1', 1080, 1080, 17.8],
  ['4:5', 1080, 1350, 14.2],
  ['9:16', 1080, 1920, 10.0],
  ['custom portrait', 1000, 1500, 12.8],
];
const near = (actual, expected, tol, msg) => assert.ok(Math.abs(actual - expected) <= tol, `${msg}: got ${actual}, expected ${expected}`);

for (const [name, w, h, pt] of SIZES) {
  test(`20 px text is ${pt} pt on a ${name} slide (${w}x${h})`, () => {
    near(20 * ptPerPx({ w, h }), pt, 0.05, 'point size');
  });

  test(`${name} slide has its longer side at 13.333 in and keeps its aspect ratio`, () => {
    const { inW, inH } = slideInches({ w, h });
    near(Math.max(inW, inH), 13.333, 1e-9, 'longer side');
    near(inW / inH, w / h, 1e-9, 'aspect ratio');
  });

  test(`${name}: the point scale matches the slide size in inches`, () => {
    const { inW, inH } = slideInches({ w, h });
    near(ptPerPx({ w, h }), (inW * 72) / w, 1e-9, 'across the width');
    near(ptPerPx({ w, h }), (inH * 72) / h, 1e-9, 'across the height');
  });
}

test('portrait slides scale by their height, not their width', () => {
  assert.ok(20 * ptPerPx({ w: 1080, h: 1920 }) < 12, '9:16 at 20 px is under the 12 pt warning');
  assert.ok(20 * ptPerPx({ w: 1080, h: 1350 }) >= 12, '4:5 at 20 px is not under 12 pt');
  assert.ok(16 * ptPerPx({ w: 1080, h: 1350 }) < 12, '4:5 at 16 px is under 12 pt');
});

test('text sizes are in tenths of a point, as the .pptx stores them', () => {
  assert.equal(textPt(20, { w: 1080, h: 1920 }), 10);
  assert.equal(textPt(20, { w: 1080, h: 1350 }), 14.2);
  assert.equal(textPt(16, { w: 1080, h: 1350 }), 11.4);
  assert.equal(textPt(20, { w: 1000, h: 1500 }), 12.8);
});

test('large-text threshold: 36 px on a 1920 px side is 18 pt either way up, 34 px is 17 pt', () => {
  assert.equal(textPt(36, { w: 1080, h: 1920 }), 18);
  assert.equal(textPt(36, { w: 1920, h: 1080 }), 18);
  assert.equal(textPt(34, { w: 1080, h: 1920 }), 17);
});
