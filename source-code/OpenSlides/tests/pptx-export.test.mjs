import test from 'node:test';
import assert from 'node:assert/strict';
import JSZip from 'jszip';
import './helpers/load-src.mjs';
import { installDom } from './helpers/dom.mjs';

installDom();
const { buildPptx, htmlToRuns } = await import('../src/io/export.tsx');
const { importPptx } = await import('../src/io/importPptx.ts');
const { makeChart, makeDeck, makeImage, makeShape, makeTable, makeText } = await import('../src/model/factory.ts');
const { THEME_PRESETS } = await import('../src/model/theme.ts');

// Graphite: background #ededed, surface #ffffff, text and primary #111111, muted #6b6b6b, accent #3a86ff.
const theme = THEME_PRESETS.find((t) => t.id === 'graphite');
const slideOf = (elements, extra = {}) => ({ id: 's', layoutId: 'blank', elements, notes: '', transition: 'fade', ...extra });
const deckOf = (slides, size = { w: 1920, h: 1080, preset: '16:9' }, title = 'Deck') => ({ ...makeDeck(title, theme, size, 'minimal'), slides });

/** Writes the deck as a real .pptx and returns readers for its XML parts. */
async function exported(deck) {
  const bytes = await (await buildPptx(deck)).write({ outputType: 'nodebuffer' });
  const zip = await JSZip.loadAsync(bytes);
  const part = async (path) => new DOMParser().parseFromString(await zip.file(path).async('string'), 'application/xml');
  return { bytes, zip, part, slide: (n = 1) => part(`ppt/slides/slide${n}.xml`) };
}
const all = (node, name) => node.getElementsByTagNameNS('*', name);
const one = (node, name) => all(node, name)[0];
const val = (node, name, attr = 'val') => one(node, name)?.getAttribute(attr) ?? null;
/** A shape's box in slide pixels (the export writes EMU; a 1920 px wide slide has 6350 EMU per pixel). */
const boxPx = (shape) => {
  const x = one(shape, 'xfrm');
  return [val(x, 'off', 'x'), val(x, 'off', 'y'), val(x, 'ext', 'cx'), val(x, 'ext', 'cy')].map((v) => Math.round(v / 6350));
};

/** Outline width in points (0.5 pt per slide pixel on a 1920 px wide slide). */
const widthPt = (spPr) => Math.round(one(spPr, 'ln').getAttribute('w') / 1270) / 10;

const base = { fontFace: 'Inter', fontSize: 20, color: '111111', bold: false, italic: false, underline: false };
const runs = (html, align = 'left') => htmlToRuns(html, base, 0.5, theme, align);

test('htmlToRuns keeps bold, italic, underline and strike from tags', () => {
  const out = runs('<p>plain <b>b</b><strong>s</strong><i>i</i><em>e</em><u>u</u><s>x</s><strike>y</strike><b><i>both</i></b></p>');
  const flags = out.map((r) => [r.text, r.options.bold, r.options.italic, !!r.options.underline, !!r.options.strike]);
  assert.deepEqual(flags, [
    ['plain ', false, false, false, false],
    ['b', true, false, false, false],
    ['s', true, false, false, false],
    ['i', false, true, false, false],
    ['e', false, true, false, false],
    ['u', false, false, true, false],
    ['x', false, false, false, true],
    ['y', false, false, false, true],
    ['both', true, true, false, false],
  ]);
  assert.deepEqual(out[5].options.underline, { style: 'sng' });
  assert.equal(out[6].options.strike, 'sngStrike');
  assert.deepEqual([out[0].options.fontFace, out[0].options.fontSize, out[0].options.color], ['Inter', 20, '111111']);
});

test('htmlToRuns reads inline styles: weight, colour, size in points, first font, decoration', () => {
  const out = runs(
    `<p><span style="font-weight: 700; font-style: italic; color: rgb(255, 0, 0); font-size: 41px; font-family: 'Courier New', monospace; text-decoration: underline line-through">a</span>` +
      '<b><span style="font-weight: normal; color: #00ff00">b</span></b><span style="font-weight: 400; font-size: 2em; color: papayawhip">c</span><span style="font-weight: bold">d</span>&nbsp;e</p>',
  );
  assert.deepEqual(out[0].options, { fontFace: 'Courier New', fontSize: 20.5, color: 'FF0000', bold: true, italic: true, underline: { style: 'sng' }, strike: 'sngStrike', align: 'left', breakLine: undefined });
  assert.deepEqual([out[1].options.bold, out[1].options.color], [false, '00FF00'], 'an inline weight overrides the surrounding tag');
  assert.deepEqual([out[2].options.bold, out[2].options.fontSize, out[2].options.color], [false, 20, '111111'], 'sizes and colours it cannot convert fall back to the box style');
  assert.equal(out[3].options.bold, true);
  assert.equal(out[4].text, ' e', 'non-breaking spaces become plain spaces');
});

test('htmlToRuns turns paragraphs, line breaks and nested lists into PowerPoint paragraphs', () => {
  const out = runs('<p style="text-align: center">One</p><p>Two<br>lines</p><ul><li>a<ol><li>a1</li></ol></li><li style="text-align: right">b</li></ul><div style="text-align: justify">tail</div><p></p>', 'left');
  const shape = out.map((r) => [r.text, r.options.align, r.options.breakLine, r.options.bullet, r.options.indentLevel]);
  assert.deepEqual(shape, [
    ['One', 'center', true, undefined, undefined],
    ['Two', 'left', undefined, undefined, undefined],
    ['', 'left', true, undefined, undefined],
    ['lines', 'left', true, undefined, undefined],
    ['a', 'left', true, true, 0],
    ['a1', 'left', true, { type: 'number' }, 1],
    ['b', 'right', true, true, 0],
    ['tail', 'justify', true, undefined, undefined],
    ['', 'left', undefined, undefined, undefined],
  ]);
  assert.equal(out[8].options.fontSize, 20, 'an empty paragraph keeps the box’s type size so its line has height');
});

test('htmlToRuns wraps loose text, converts font tags and uses the box alignment by default', () => {
  const out = runs('loose <font color="#0000ff" face="Georgia">blue</font><p>after</p>', 'right');
  assert.deepEqual(out.map((r) => [r.text, r.options.align, r.options.breakLine]), [['loose ', 'right', undefined], ['blue', 'right', true], ['after', 'right', undefined]]);
  assert.deepEqual([out[1].options.color, out[1].options.fontFace], ['0000FF', 'Georgia']);
  assert.deepEqual(runs(''), []);
});

test('the file carries the deck’s title, fonts and slide proportions', async () => {
  const wide = await exported(deckOf([slideOf([])], undefined, 'Q3 <Review>'));
  const sz = one(await wide.part('ppt/presentation.xml'), 'sldSz');
  assert.deepEqual([sz.getAttribute('cx') / 914400, sz.getAttribute('cy') / 914400].map((n) => +n.toFixed(2)), [13.33, 7.5]);
  assert.equal(one(await wide.part('docProps/core.xml'), 'title').textContent, 'Q3 <Review>');
  assert.equal(one(await wide.part('docProps/app.xml'), 'Company').textContent, 'Made with OpenSlides');
  const fonts = await wide.part('ppt/theme/theme1.xml');
  assert.deepEqual([val(one(fonts, 'majorFont'), 'latin', 'typeface'), val(one(fonts, 'minorFont'), 'latin', 'typeface')], [theme.fonts.heading, theme.fonts.body]);

  const portrait = one(await (await exported(deckOf([slideOf([])], { w: 1080, h: 1920, preset: 'custom' }))).part('ppt/presentation.xml'), 'sldSz');
  assert.deepEqual([portrait.getAttribute('cx') / 914400, portrait.getAttribute('cy') / 914400].map((n) => +n.toFixed(2)), [7.5, 13.33], 'the long side is always 13.33 inches');
});

test('each slide keeps its background, hidden flag, notes and order', async () => {
  const deck = deckOf([
    slideOf([], { background: { kind: 'solid', color: '#102030' }, notes: 'Open with the numbers.' }),
    slideOf([], { hidden: true, background: { kind: 'solid', color: 'theme:accent' } }),
    slideOf([]),
  ]);
  const out = await exported(deck);
  const [a, b, c] = [await out.slide(1), await out.slide(2), await out.slide(3)];
  assert.deepEqual([a, b, c].map((s) => val(one(s, 'bgPr'), 'srgbClr')), ['102030', '3A86FF', 'EDEDED'], 'theme colours are resolved; no background means the layout’s');
  assert.deepEqual([a, b, c].map((s) => s.documentElement.getAttribute('show')), [null, '0', null]);
  const notesOf = async (n) => Array.from(all(await out.part(`ppt/notesSlides/notesSlide${n}.xml`), 'sp')).find((sp) => val(sp, 'ph', 'type') === 'body').textContent;
  assert.deepEqual([await notesOf(1), await notesOf(2), await notesOf(3)], ['Open with the numbers.', '', ''], 'a slide without notes exports an empty notes body');
});

test('text boxes keep position, rotation, type, alignment, padding, fill and slide tokens', async () => {
  const styled = makeText(100, 200, 800, 300, '<p>{{n}} of {{total}}: {{title}}</p>', { fontSize: 40, align: 'center', valign: 'bottom', lineHeight: 1.5, letterSpacing: 0.1, color: 'theme:accent', fontFamily: 'theme:heading', bold: true }, { fill: '#ff0000', opacity: 0.4, rotation: 30, padding: 16 });
  const deck = deckOf([slideOf([]), slideOf([styled])], undefined, 'A & B');
  deck.theme.fonts.heading = 'Georgia';
  const [box] = all(await (await exported(deck)).slide(2), 'sp');
  assert.deepEqual(boxPx(box), [100, 200, 800, 300]);
  assert.equal(one(box, 'xfrm').getAttribute('rot'), '1800000');
  assert.equal(one(box, 't').textContent, '2 of 2: A & B');
  const rPr = one(box, 'rPr');
  // 1920 px across 13.33 in is 0.5 pt per px: 40 px type is 20 pt, 16 px padding is 8 pt (101600 EMU).
  assert.deepEqual([rPr.getAttribute('sz'), rPr.getAttribute('b'), rPr.getAttribute('spc'), val(rPr, 'srgbClr'), val(rPr, 'latin', 'typeface')], ['2000', '1', '200', '3A86FF', 'Georgia']);
  assert.deepEqual([val(box, 'bodyPr', 'anchor'), val(box, 'bodyPr', 'lIns'), val(box, 'pPr', 'algn'), val(box, 'spcPct')], ['b', '101600', 'ctr', '150000']);
  const fill = one(one(box, 'spPr'), 'solidFill');
  assert.deepEqual([val(fill, 'srgbClr'), val(fill, 'alpha')], ['FF0000', '40000'], 'opacity 0.4 is written as 60% transparency');
});

test('empty text boxes are left out unless they have a fill', async () => {
  const shapes = all(await (await exported(deckOf([slideOf([makeText(0, 0, 100, 50, '<p> </p>'), makeText(0, 100, 100, 50, '', {}, { fill: '#00ff00' }), makeText(0, 200, 100, 50, 'kept')])]))).slide(), 'sp');
  assert.deepEqual(shapes.map((s) => boxPx(s)[1]), [100, 200]);
});

test('shapes keep their kind, fill and outline; lines and arrows run through the middle of their box', async () => {
  const els = [
    makeShape('ellipse', 200, 400, 300, 200, { fill: '#00ff00', stroke: '#0000ff', strokeWidth: 4, opacity: 0.5 }),
    makeShape('star', 0, 0, 100, 100, { fill: 'transparent', stroke: 'theme:accent', strokeWidth: 0 }),
    makeShape('line', 100, 700, 300, 20, { stroke: '#123456', strokeWidth: 6, rotation: 45 }),
    makeShape('arrow', 100, 800, 300, 20, { stroke: 'transparent', strokeWidth: 0 }),
    ...['rect', 'roundRect', 'triangle', 'diamond', 'hexagon', 'chevron'].map((k) => makeShape(k, 0, 0, 100, 100)),
  ];
  const shapes = all(await (await exported(deckOf([slideOf(els)]))).slide(), 'sp');
  assert.deepEqual(shapes.map((s) => val(s, 'prstGeom', 'prst')), ['ellipse', 'star5', 'line', 'line', 'rect', 'roundRect', 'triangle', 'diamond', 'hexagon', 'chevron']);
  const [ellipse, star, line, arrow, rect] = shapes.map((s) => one(s, 'spPr'));
  assert.deepEqual(boxPx(shapes[0]), [200, 400, 300, 200]);
  assert.deepEqual([val(ellipse.children[2], 'srgbClr'), val(ellipse.children[2], 'alpha'), val(one(ellipse, 'ln'), 'srgbClr'), widthPt(ellipse)], ['00FF00', '50000', '0000FF', 2]);
  assert.deepEqual([all(star, 'solidFill').length, one(star, 'ln').children.length], [0, 0], 'a transparent fill and a zero-width outline write no colour');
  assert.deepEqual([boxPx(shapes[2]), one(line, 'xfrm').getAttribute('rot'), val(one(line, 'ln'), 'srgbClr'), widthPt(line), one(line, 'tailEnd')], [[100, 710, 300, 0], '2700000', '123456', 3, undefined]);
  assert.deepEqual([val(one(arrow, 'ln'), 'srgbClr'), widthPt(arrow), val(arrow, 'tailEnd', 'type')], ['000000', 0.5, 'triangle'], 'an arrow always gets a visible black half-point line');
  assert.equal(val(rect.children[2], 'srgbClr'), '111111', 'theme:primary');
});

// Known bug, reported in the PR that added this test: PptxGenJS documents rectRadius as 0 to 1 but
// reads it as inches, so the exported corner depends on the shape's physical size.
test('a rounded rectangle keeps its corner radius', { todo: 'export.tsx:327 passes a ratio where PptxGenJS uses inches' }, async () => {
  const [shape] = all(await (await exported(deckOf([slideOf([makeShape('roundRect', 0, 0, 300, 200, { radius: 50 })])]))).slide(), 'sp');
  // OOXML: corner radius = shorter side x adj / 100000, so 50 px on a 200 px side is 25000.
  const adj = +one(shape, 'gd').getAttribute('fmla').replace('val ', '');
  assert.ok(Math.abs(adj - 25000) <= 5, `adj is ${adj}`);
});

test('charts are written as native charts with the deck’s data, colours and labels', async () => {
  const data = { labels: ['Q1', 'Q2', 'Q3'], series: [{ name: 'North', values: [1, 2, 3] }, { name: 'South', values: [4] }] };
  const els = [
    makeChart('bar', 100, 100, 600, 400, { data, title: 'Sales', showValues: true, colors: ['#ff0000'] }),
    makeChart('column', 0, 0, 600, 400, { data: { labels: ['a', 'b'], series: [{ name: 'Only', values: [5, 6] }] }, showGrid: false }),
    makeChart('pie', 0, 0, 600, 400, { data, showValues: true }),
    makeChart('line', 0, 0, 600, 400, { data, showLegend: false }),
    makeChart('area', 0, 0, 600, 400, { data }),
    makeChart('doughnut', 0, 0, 600, 400, { data }),
  ];
  const out = await exported(deckOf([slideOf(els)]));
  const frames = all(await out.slide(), 'graphicFrame');
  assert.deepEqual(boxPx(frames[0]), [100, 100, 600, 400]);
  const [bar, column, pie, line, area, doughnut] = await Promise.all([1, 2, 3, 4, 5, 6].map((n) => out.part(`ppt/charts/chart${n}.xml`)));
  const series = (doc) => all(doc, 'ser').map((s) => [one(one(s, 'tx'), 'v').textContent, all(one(s, 'cat'), 'v').map((v) => v.textContent), all(one(s, 'val'), 'v').map((v) => +v.textContent)]);

  assert.deepEqual(series(bar), [['North', ['Q1', 'Q2', 'Q3'], [1, 2, 3]], ['South', ['Q1', 'Q2', 'Q3'], [4, 0, 0]]], 'short series are padded with zeros');
  assert.deepEqual([val(bar, 'barDir'), val(column, 'barDir')], ['bar', 'col']);
  assert.deepEqual(all(bar, 'ser').map((s) => val(one(s, 'spPr'), 'srgbClr')), ['FF0000', '3A86FF'], 'stated colours first, then the theme palette');
  assert.equal(one(bar, 'title').textContent.trim(), 'Sales');
  assert.deepEqual([one(bar, 'legend') !== undefined, one(column, 'legend'), one(line, 'legend')], [true, undefined, undefined], 'a legend needs more than one series and showLegend');
  assert.deepEqual([one(column, 'title'), all(column, 'majorGridlines').length, all(bar, 'majorGridlines').length], [undefined, 0, 1]);
  assert.deepEqual([val(one(bar, 'dLbls'), 'showVal'), val(one(column, 'dLbls'), 'showVal')], ['1', '0']);

  assert.deepEqual(series(pie), [['North', ['Q1', 'Q2', 'Q3'], [1, 2, 3]]], 'a pie shows the first series only');
  assert.deepEqual(all(pie, 'dPt').map((d) => val(d, 'srgbClr')), ['111111', '3A86FF', '5C5C5C']);
  assert.deepEqual(all(pie, 'dLbl').map((d) => val(d, 'showPercent')), ['1', '1', '1']);
  assert.ok(one(pie, 'legend'), 'a pie keeps its legend with one series');
  assert.deepEqual([line, area, doughnut].map((d) => ['lineChart', 'areaChart', 'doughnutChart'].filter((k) => one(d, k)).join()), ['lineChart', 'areaChart', 'doughnutChart']);
});

test('tables keep cells, header and stripe fills, borders and type size', async () => {
  const table = makeTable([['Name', 'Qty'], ['Apples'], ['Pears', '3'], ['Plums', '4']], 100, 500, 800, 400);
  table.style.fill = '#fafafa';
  const plain = makeTable([['x']], 0, 0, 200, 100, { headerRow: false });
  Object.assign(plain.style, { stripe: false, borderColor: 'transparent', fontSize: 8 });
  const [t, p] = all(await (await exported(deckOf([slideOf([table, plain])]))).slide(), 'tbl');
  const cells = (tbl) => all(tbl, 'tr').map((tr) => all(tr, 'tc').map((tc) => [all(tc, 't').map((x) => x.textContent).join(''), val(one(tc, 'tcPr').children.at(-1), 'srgbClr'), val(tc, 'rPr', 'b')]));
  assert.deepEqual(cells(t), [
    [['Name', '111111', '1'], ['Qty', '111111', '1']],
    [['Apples', 'FAFAFA', null], ['', 'FAFAFA', null]],
    [['Pears', 'FFFFFF', null], ['3', 'FFFFFF', null]],
    [['Plums', 'FAFAFA', null], ['4', 'FAFAFA', null]],
  ]);
  assert.deepEqual(all(t, 'gridCol').map((c) => Math.round(c.getAttribute('w') / 6350)), [400, 400]);
  assert.deepEqual(all(t, 'tr').map((r) => Math.round(r.getAttribute('h') / 6350)), [100, 100, 100, 100]);
  assert.deepEqual([val(one(t, 'tc'), 'rPr', 'sz'), val(one(one(t, 'tc'), 'rPr'), 'srgbClr'), val(one(t, 'lnL'), 'srgbClr')], ['1400', 'EDEDED', 'FFFFFF']);
  assert.deepEqual([val(one(p, 'tc'), 'rPr', 'sz'), one(p, 'lnL')?.getAttribute('w') ?? '0', val(p, 'rPr', 'b')], ['600', '0', null], 'type never goes below 6 pt; no border colour means no border');
});

test('layout decorations are exported on every slide and placeholders take the layout’s geometry', async () => {
  const deck = deckOf([]);
  const slot = { ...makeText(300, 400, 900, 200, '', { fontSize: 60 }), placeholder: 'title', prompt: 'Click to add a title' };
  deck.layouts.push({ id: 'custom', name: 'Custom', background: { kind: 'solid', color: '#abcdef' }, elements: [makeShape('rect', 0, 0, 1920, 40), slot] });
  deck.slides.push(slideOf([{ ...makeText(0, 0, 10, 10, 'Heading'), ph: { ref: slot.id, geom: true, style: true } }, makeImage('missing-asset', 0, 0, 100, 100), makeImage('theme:logo', 0, 0, 100, 100)], { layoutId: 'custom' }));
  const out = await exported(deck);
  const slide = await out.slide();
  const shapes = all(slide, 'sp');
  assert.equal(val(one(slide, 'bgPr'), 'srgbClr'), 'ABCDEF');
  assert.deepEqual(shapes.map(boxPx), [[0, 0, 1920, 40], [300, 400, 900, 200]], 'the decoration, then the slide’s text in the placeholder’s box; the empty placeholder itself is not drawn');
  assert.deepEqual([one(shapes[1], 't').textContent, val(shapes[1], 'rPr', 'sz')], ['Heading', '3000']);
  assert.deepEqual([all(slide, 'pic').length, Object.keys(out.zip.files).filter((f) => f.startsWith('ppt/media/') && !f.endsWith('/'))], [0, []], 'images whose asset is missing are skipped');
});

test('a deck exported to .pptx imports back with the same content', async () => {
  const text = makeText(100, 100, 800, 300, '<p>Hello <b>bold</b> <span style="color: #ff0000; font-size: 80px">red</span></p><ul><li>one</li><li>two<ul><li>deep</li></ul></li></ul>', { align: 'center', valign: 'middle' });
  const els = [
    text,
    makeShape('ellipse', 200, 500, 300, 200, { fill: '#00ff00', stroke: '#0000ff', strokeWidth: 4 }),
    makeShape('arrow', 200, 800, 300, 12, { stroke: '#123456' }),
    makeChart('bar', 1000, 100, 600, 400, { title: 'Sales', data: { labels: ['a', 'b'], series: [{ name: 'S1', values: [1, 2] }, { name: 'S2', values: [3, 4] }] }, showValues: true }),
    makeTable([['h1', 'h2'], ['a', 'b']], 1000, 600, 600, 200),
  ];
  const deck = deckOf([slideOf(els, { background: { kind: 'solid', color: '#102030' }, notes: 'Speaker notes', hidden: true }), slideOf([])], undefined, 'Round trip');
  const { bytes } = await exported(deck);
  const report = await importPptx(new File([bytes], 'Round trip.pptx'));
  assert.deepEqual(report.skipped, []);
  const back = report.deck;
  assert.deepEqual([back.title, back.size, back.slides.length], ['Round trip', deck.size, 2]);
  const [first, second] = back.slides;
  assert.deepEqual([first.background, first.notes, first.hidden, second.notes, second.hidden], [{ kind: 'solid', color: '#102030' }, 'Speaker notes', true, '', undefined]);
  const [t, ellipse, arrow, chart, table] = first.elements;
  assert.deepEqual(first.elements.map((e) => e.type), ['text', 'shape', 'shape', 'chart', 'table']);
  assert.equal(t.html, '<p>Hello <span style="font-weight: bold">bold</span> <span style="font-size: 80px; color: #ff0000">red</span></p><ul><li>one</li><li>two</li><li style="margin-left: 1.2em">deep</li></ul>');
  assert.deepEqual([t.style.fontSize, t.style.align, t.style.valign, t.padding], [40, 'center', 'middle', 8]);
  assert.deepEqual([t.x, t.y, t.w, t.h].map(Math.round), [100, 100, 800, 300]);
  assert.deepEqual([ellipse.shape, ellipse.fill, ellipse.stroke, Math.round(ellipse.strokeWidth), ...[ellipse.x, ellipse.y, ellipse.w, ellipse.h].map(Math.round)], ['ellipse', '#00ff00', '#0000ff', 4, 200, 500, 300, 200]);
  assert.deepEqual([arrow.shape, arrow.stroke, Math.round(arrow.strokeWidth), Math.round(arrow.x), Math.round(arrow.w), arrow.rotation], ['arrow', '#123456', 6, 200, 300, 0]);
  assert.deepEqual([chart.chartType, chart.title, chart.data, chart.showValues], ['bar', 'Sales', els[3].data, true]);
  assert.deepEqual(table.rows, [['h1', 'h2'], ['a', 'b']]);
});

// Known bug, reported in the PR that added this test: PptxGenJS always writes a series-level
// showPercent="1" for pies, and the importer reads that as "value labels on".
test('a pie chart exported with value labels off imports with them off', { todo: 'importPptx.ts:576 reads the series default, not the per-slice labels' }, async () => {
  const pie = makeChart('pie', 0, 0, 600, 400, { showValues: false });
  const { bytes } = await exported(deckOf([slideOf([pie])]));
  const back = (await importPptx(new File([bytes], 'pie.pptx'))).deck.slides[0].elements[0];
  assert.equal(back.showValues, false);
});
