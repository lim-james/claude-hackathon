import test from 'node:test';
import assert from 'node:assert/strict';
import { chartPt, formatPt, ptPerPx, slideInches, smallestTextPt, tablePt, textPt } from '../src/model/units.ts';

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

// Deck check (#127, #129): the size it reports is the smallest size the export writes for the element.
const W169 = { w: 1920, h: 1080 };

test('a text box reports its smallest exported run, including a size set on a selection', () => {
  assert.equal(smallestTextPt(48, '<p><span style="font-size: 16px;">Inline sized text</span></p>', W169), 8);
  assert.equal(smallestTextPt(48, '<p>Big <span style="font-size: 16px;">small</span></p>', W169), 8);
  assert.equal(smallestTextPt(16, '<p>Small <span style="font-size: 48px;">big</span></p>', W169), 8);
  assert.equal(smallestTextPt(48, '<p>Big <span style="font-size: 40px;">bigger</span></p>', W169), 20);
  // Nested spans: the inner size wins; text after a closed span is back at the box size.
  assert.equal(smallestTextPt(48, '<p><span style="font-size: 30px;">a<span style="font-size: 20px;">b</span></span>c</p>', W169), 10);
  assert.equal(smallestTextPt(48, '<p><span style="font-size: 30px;">a<br>b</span>c</p>', W169), 15);
});

test('a sized span with no visible text, and an empty box, do not count', () => {
  assert.equal(smallestTextPt(48, '<p>Big<span style="font-size: 10px;"> </span></p>', W169), 24);
  assert.equal(smallestTextPt(48, '<p>Big<span style="font-size: 10px;">&nbsp;</span></p>', W169), 24);
  assert.equal(smallestTextPt(48, '<p><br></p>', W169), null);
  assert.equal(smallestTextPt(48, '', W169), null);
});

test('font-size on a paragraph or list item is ignored, as the export ignores it', () => {
  assert.equal(smallestTextPt(48, '<p style="font-size: 10px;">x</p><ul><li style="font-size: 10px;">y</li></ul>', W169), 24);
});

test("the issue's 16:9 slide: 16 px span, 16 px table and a new 26 px chart are each under 12 pt", () => {
  assert.ok(smallestTextPt(48, '<p><span style="font-size: 16px;">t</span></p>', W169) < 12);
  assert.equal(tablePt(16, W169), 8);
  assert.equal(chartPt(26, W169), 11);
});

test('table and chart sizes match the export floor and rounding', () => {
  assert.equal(tablePt(24, W169), 12);
  assert.equal(tablePt(4, W169), 6);
  assert.equal(chartPt(34, W169), 14);
  assert.equal(chartPt(4, W169), 6);
  // The export passes ptPerPx it worked out once; the result is the same.
  assert.equal(chartPt(26, ptPerPx(W169)), chartPt(26, W169));
  assert.equal(tablePt(23, ptPerPx(W169)), tablePt(23, W169));
});

for (const [name, w, h, px, shown] of [
  ['16:9', 1920, 1080, 23, '11.5'],
  ['9:16', 1080, 1920, 23, '11.5'],
  ['16:9', 1920, 1080, 22, '11'],
  ['4:3', 1440, 1080, 17, '11.3'],
]) {
  test(`${px} px on ${name} is flagged as ${shown} pt, the exported size`, () => {
    const pt = smallestTextPt(px, '<p>Body text</p>', { w, h });
    assert.ok(pt < 12);
    assert.equal(formatPt(pt), shown);
    assert.equal(pt, textPt(px, { w, h }));
  });
}

test('text exported at exactly 12 pt is not under 12', () => {
  assert.equal(smallestTextPt(24, '<p>x</p>', W169), 12);
  assert.equal(formatPt(12), '12');
});
