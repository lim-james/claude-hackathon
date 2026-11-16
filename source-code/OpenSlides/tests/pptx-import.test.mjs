import test from 'node:test';
import assert from 'node:assert/strict';
import JSZip from 'jszip';
import './helpers/load-src.mjs';
import { installDom } from './helpers/dom.mjs';
import { NS, PNG_1X1, makePptx, para, sp, xfrm } from './helpers/pptx-fixture.mjs';

installDom();
const { importPptx } = await import('../src/io/importPptx.ts');

// Fixtures use a 16:9 slide, which imports at 1920 x 1080: 6350 EMU per pixel and 2 px per point.
const importSlide = async (shapes, slide = {}, opts = {}) => {
  const report = await importPptx(await makePptx([{ shapes, ...slide }], opts));
  return { report, deck: report.deck, slide: report.deck.slides[0], els: report.deck.slides[0].elements };
};
const fill = (hex, mods = '') => `<a:solidFill><a:srgbClr val="${hex}">${mods}</a:srgbClr></a:solidFill>`;
const geom = (prst) => `<a:prstGeom prst="${prst}"/>`;
const picture = (rid, descr = '') =>
  `<p:pic><p:nvPicPr><p:cNvPr id="9" name="Picture" descr="${descr}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${rid}"/></p:blipFill><p:spPr>${xfrm(10, 20, 300, 200)}</p:spPr></p:pic>`;
const frame = (graphicData) => `<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="7" name="Frame"/></p:nvGraphicFramePr><p:xfrm><a:off x="635000" y="635000"/><a:ext cx="3810000" cy="2540000"/></p:xfrm><a:graphic>${graphicData}</a:graphic></p:graphicFrame>`;
const C = 'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"';
const chartFrame = (rid) => frame(`<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart ${C} r:id="${rid}"/></a:graphicData>`);
const cache = (values) => `<c:strRef><c:strCache>${values.map((v, i) => (v === null ? '' : `<c:pt idx="${i}"><c:v>${v}</c:v></c:pt>`)).join('')}</c:strCache></c:strRef>`;
const ser = ({ name, cat, val, extra = '' }) => `<c:ser>${name ? `<c:tx>${cache([name])}</c:tx>` : ''}${extra}${cat ? `<c:cat>${cache(cat)}</c:cat>` : ''}<c:val>${cache(val)}</c:val></c:ser>`;
const chartXml = (kind, body, extra = '') => `<c:chartSpace ${C} ${NS}><c:chart>${extra}<c:plotArea><c:layout/><c:${kind}>${body}</c:${kind}></c:plotArea></c:chart></c:chartSpace>`;

test('rejects files that are not PowerPoint presentations, naming the file', async () => {
  await assert.rejects(importPptx(new File(['plain text'], 'notes.pptx')), /notes\.pptx isn’t a valid \.pptx file/);
  const zip = new JSZip();
  zip.file('word/document.xml', '<w/>');
  await assert.rejects(importPptx(new File([await zip.generateAsync({ type: 'uint8array' })], 'letter.pptx')), /letter\.pptx has no slides part/);
  await assert.rejects(importPptx(await makePptx([], { name: 'empty.pptx' })), /empty\.pptx has no slides\./);
});

test('slide size, title and preset follow the file', async () => {
  const wide = (await importPptx(await makePptx([{}], { name: 'Q3 Review.PPTX' }))).deck;
  assert.deepEqual([wide.title, wide.size], ['Q3 Review', { w: 1920, h: 1080, preset: '16:9' }]);
  const classic = (await importPptx(await makePptx([{}], { size: [9144000, 6858000] }))).deck;
  assert.deepEqual(classic.size, { w: 1920, h: 1440, preset: '4:3' });
  const portrait = (await importPptx(await makePptx([{}], { size: [6858000, 12192000] }))).deck;
  assert.deepEqual(portrait.size, { w: 1080, h: 1920, preset: 'custom' });
});

test('theme colours and fonts come from the file, with defaults when it has no theme', async () => {
  const { deck } = await importSlide('');
  assert.deepEqual(deck.theme.colors, { background: '#ffffff', surface: '#eeece1', text: '#000000', muted: '#1f497d', primary: '#4f81bd', secondary: '#c0504d', accent: '#9bbb59' });
  assert.deepEqual(deck.theme.fonts, { heading: 'Georgia', body: 'Verdana' });
  assert.equal(deck.theme.id, 'imported');
  const bare = (await importSlide('', {}, { theme: null })).deck;
  assert.deepEqual(bare.theme.colors, { background: '#ffffff', surface: '#eeeeee', text: '#111111', muted: '#555555', primary: '#3a6ea5', secondary: '#c0504d', accent: '#9bbb59' });
  assert.deepEqual(bare.theme.fonts, { heading: 'Inter', body: 'Inter' });
});

test('placeholders inherit position, anchor and type size from the layout and master', async () => {
  const layout = sp({ ph: '<p:ph type="title"/>', spPr: xfrm(100, 200, 1000, 100), text: '', bodyPr: '<a:bodyPr anchor="b"/>' });
  const master = sp({ ph: '<p:ph type="body" idx="1"/>', spPr: xfrm(100, 400, 1600, 500), text: '' });
  const txStyles = '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="4000"/></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr><a:defRPr sz="2000"/></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:lvl1pPr><a:defRPr sz="1200"/></a:lvl1pPr></p:otherStyle></p:txStyles>';
  const shapes =
    sp({ ph: '<p:ph type="ctrTitle"/>', text: para('Costs &lt; revenue') }) +
    sp({ ph: '<p:ph idx="1"/>', text: para('First') + para('Second') }) +
    sp({ spPr: xfrm(0, 0, 400, 100), text: para('Free box') }) +
    sp({ ph: '<p:ph type="ftr" idx="11"/>', text: para('No position anywhere') });
  const { els } = await importSlide(shapes, {}, { layout, master, txStyles });
  assert.equal(els.length, 3, 'a placeholder with no position in slide, layout or master is dropped');
  const [title, body, free] = els;
  assert.deepEqual([title.x, title.y, title.w, title.h], [100, 200, 1000, 100]);
  assert.equal(title.html, '<p>Costs &lt; revenue</p>');
  assert.deepEqual([title.style.fontSize, title.style.fontFamily, title.style.valign], [80, 'theme:heading', 'bottom']);
  assert.deepEqual([body.x, body.y, body.w, body.h], [100, 400, 1600, 500]);
  assert.equal(body.html, '<ul><li>First</li><li>Second</li></ul>', 'body placeholders are bulleted unless told otherwise');
  assert.deepEqual([body.style.fontSize, body.style.fontFamily, body.style.valign], [40, 'theme:body', 'top']);
  assert.deepEqual([free.html, free.style.fontSize, free.autoHeight], ['<p>Free box</p>', 24, false]);
});

test('without master type sizes, titles are 44 pt, placeholders 24 pt and other text 18 pt', async () => {
  const shapes = sp({ ph: '<p:ph type="title"/>', spPr: xfrm(0, 0, 500, 100), text: para('T') }) + sp({ ph: '<p:ph type="body"/>', spPr: xfrm(0, 100, 500, 100), text: para('B') }) + sp({ spPr: xfrm(0, 200, 500, 100), text: para('O') });
  const { els } = await importSlide(shapes);
  assert.deepEqual(els.map((e) => e.style.fontSize), [88, 48, 36]);
  assert.equal(els[0].style.valign, 'middle', 'titles are centred vertically by default');
});

test('run formatting: the common style moves to the box, differences stay inline', async () => {
  const run = (text, rPr = '') => `<a:r>${rPr}<a:t>${text}</a:t></a:r>`;
  const text =
    `<a:p><a:pPr algn="ctr"/>${run('Plain ')}${run('bold', '<a:rPr b="1" i="1"/>')}${run('big', `<a:rPr sz="3600"><a:solidFill><a:schemeClr val="accent1"/></a:solidFill></a:rPr>`)}` +
    `${run('gone', '<a:rPr u="sng" strike="sngStrike"/>')}${run('kept', '<a:rPr u="none" strike="noStrike"><a:latin typeface="+mn-lt"/></a:rPr>')}</a:p>` +
    `<a:p><a:pPr algn="r"/>${run('a')}<a:br/><a:fld type="slidenum"><a:t>7</a:t></a:fld></a:p>` +
    `<a:p><a:pPr algn="ctr"><a:buAutoNum type="arabicPeriod"/></a:pPr>${run('one')}</a:p><a:p><a:pPr algn="ctr" lvl="1"><a:buAutoNum type="arabicPeriod"/></a:pPr>${run('two')}</a:p>` +
    `<a:p><a:pPr algn="ctr"><a:buChar char="•"/></a:pPr>${run('dot')}</a:p><a:p><a:pPr algn="just"/></a:p>`;
  const { els } = await importSlide(sp({ spPr: xfrm(0, 0, 800, 600), text, bodyPr: '<a:bodyPr anchor="ctr" lIns="63500"/>' }));
  const [t] = els;
  assert.equal(
    t.html,
    '<p>Plain <span style="font-weight: bold; font-style: italic">bold</span><span style="font-size: 72px; color: #4f81bd">big</span>' +
      '<span style="text-decoration: underline line-through">gone</span>kept</p>' +
      '<p style="text-align: right">a<br>7</p><ol><li>one</li><li style="margin-left: 1.2em">two</li></ol><ul><li>dot</li></ul><p style="text-align: justify"><br></p>',
  );
  assert.deepEqual(
    { size: t.style.fontSize, color: t.style.color, font: t.style.fontFamily, bold: t.style.bold, align: t.style.align, valign: t.style.valign, padding: t.padding },
    { size: 36, color: 'theme:text', font: 'theme:body', bold: false, align: 'center', valign: 'middle', padding: 10 },
  );
});

test('text that is all bold, coloured and in one font carries that on the box itself', async () => {
  const rPr = `<a:rPr sz="2000" b="1" i="1">${fill('00FF00')}<a:latin typeface="Arial"/></a:rPr>`;
  const { els } = await importSlide(sp({ spPr: xfrm(0, 0, 800, 100), text: `<a:p><a:r>${rPr}<a:t>A</a:t></a:r><a:r>${rPr}<a:t>B</a:t></a:r></a:p>`, bodyPr: '<a:bodyPr anchor="b"/>' }));
  assert.equal(els[0].html, '<p>AB</p>');
  const s = els[0].style;
  assert.deepEqual([s.fontSize, s.color, s.fontFamily, s.bold, s.italic, s.valign], [40, '#00ff00', 'Arial', true, true, 'bottom']);
  assert.equal(els[0].padding, 14, 'PowerPoint’s default inset of 0.1 inch');
});

// Known bug, reported in the PR that added this test: the font name is wrapped in double quotes
// inside a double-quoted style attribute, so the browser cuts the attribute short and drops the font.
test('a run in a different font from the rest keeps its font', { todo: 'importPptx.ts:337 nests double quotes in style="…"' }, async () => {
  const run = (text, font) => `<a:r><a:rPr><a:latin typeface="${font}"/></a:rPr><a:t>${text}</a:t></a:r>`;
  const { els } = await importSlide(sp({ spPr: xfrm(0, 0, 800, 100), text: `<a:p>${run('a', 'Arial')}${run('b', 'Arial')}${run('code', 'Courier New')}</a:p>` }));
  assert.equal(els[0].style.fontFamily, 'Arial');
  const box = document.createElement('div');
  box.innerHTML = els[0].html;
  assert.match(box.querySelectorAll('span')[0].style.fontFamily, /Courier New/);
});

test('shapes keep their geometry, fill and outline', async () => {
  const shapes =
    sp({ spPr: xfrm(100, 100, 200, 100) + geom('roundRect') + fill('FF0000') + `<a:ln w="12700">${fill('0000FF')}</a:ln>` }) +
    sp({ spPr: xfrm(0, 0, 50, 50) + geom('ellipse') + '<a:noFill/>' }) +
    sp({ spPr: xfrm(0, 0, 50, 50) + geom('star5'), style: '<p:style><a:lnRef idx="1"><a:schemeClr val="accent2"/></a:lnRef><a:fillRef idx="1"><a:schemeClr val="accent3"/></a:fillRef></p:style>' }) +
    sp({ spPr: xfrm(0, 0, 0, 0) + geom('diamond') + fill('000000') });
  const { els, report } = await importSlide(shapes);
  assert.equal(els.length, 3, 'a shape with no fill and no outline draws nothing');
  const [round, star, tiny] = els;
  assert.deepEqual(
    { shape: round.shape, x: round.x, y: round.y, w: round.w, h: round.h, fill: round.fill, stroke: round.stroke, strokeWidth: round.strokeWidth },
    { shape: 'roundRect', x: 100, y: 100, w: 200, h: 100, fill: '#ff0000', stroke: '#0000ff', strokeWidth: 2 },
  );
  assert.ok(Math.abs(round.radius - 16.67) < 0.01);
  assert.deepEqual([star.shape, star.fill, star.stroke], ['star', '#9bbb59', 'transparent'], 'theme style fill applies; its outline needs a width to show');
  assert.deepEqual([tiny.shape, tiny.w, tiny.h], ['diamond', 2, 2], 'degenerate sizes are raised to 2 px');
  assert.deepEqual(report.skipped, []);
});

test('a filled rectangle with text becomes one text box; other filled shapes get a shape plus text', async () => {
  const shapes =
    sp({ spPr: xfrm(0, 0, 400, 100) + geom('rect') + fill('FFFF00'), text: para('Label') }) +
    sp({ spPr: xfrm(0, 200, 400, 100, ' rot="5400000"') + geom('ellipse') + fill('00FFFF'), text: para('Round') }) +
    sp({ spPr: xfrm(0, 400, 400, 100) + geom('rect') + fill('FFFF00') + `<a:ln w="6350">${fill('000000')}</a:ln>`, text: para('Framed') });
  const { els } = await importSlide(shapes);
  assert.deepEqual(els.map((e) => e.type), ['text', 'shape', 'text', 'shape', 'text']);
  assert.equal(els[0].fill, '#ffff00');
  assert.deepEqual([els[1].shape, els[1].rotation, els[2].rotation, els[2].fill], ['ellipse', 90, 90, 'transparent']);
  assert.deepEqual([els[3].stroke, els[3].strokeWidth, els[4].fill], ['#000000', 1, 'transparent']);
});

test('shapes the model cannot draw become rectangles and are reported', async () => {
  const shapes = sp({ spPr: xfrm(0, 0, 100, 100) + '<a:custGeom/>' + fill('FF0000') }) + sp({ spPr: xfrm(0, 0, 100, 100) + geom('cloud') + fill('FF0000') });
  const { els, report } = await importSlide(shapes);
  assert.deepEqual(els.map((e) => e.shape), ['rect', 'rect']);
  assert.deepEqual(report.skipped, ['custom or unusual shapes (drawn as rectangles)']);
});

test('colour adjustments: luminance, tint, shade, scheme aliases, system and preset colours', async () => {
  const box = (colour) => sp({ spPr: xfrm(0, 0, 100, 100) + geom('rect') + colour });
  const shapes =
    box(fill('FF0000', '<a:lumMod val="50000"/>')) +
    box(fill('000000', '<a:lumMod val="75000"/><a:lumOff val="25000"/>')) +
    box(fill('000000', '<a:tint val="25000"/>')) +
    box(fill('FFFFFF', '<a:shade val="50000"/>')) +
    box('<a:solidFill><a:schemeClr val="bg2"/></a:solidFill>') +
    box('<a:solidFill><a:schemeClr val="bg1"><a:lumMod val="50000"/></a:schemeClr></a:solidFill>') +
    box('<a:solidFill><a:sysClr val="window" lastClr="336699"/></a:solidFill>') +
    box('<a:solidFill><a:prstClr val="green"/></a:solidFill>') +
    box('<a:gradFill><a:gsLst><a:gs pos="0"><a:srgbClr val="0000FF"/></a:gs><a:gs pos="100000"><a:srgbClr val="FF0000"/></a:gs></a:gsLst></a:gradFill>') +
    box('<a:solidFill><a:prstClr val="papayaWhip"/></a:solidFill>') +
    box('<a:solidFill><a:schemeClr val="hlink"/></a:solidFill>');
  const { els } = await importSlide(shapes);
  assert.deepEqual(els.map((e) => e.fill), ['#800000', '#404040', '#bfbfbf', '#808080', '#eeece1', '#808080', '#336699', '#008000', '#0000ff']);
});

test('connectors become lines or arrows running between their true end points', async () => {
  const cxn = (attrs, ln) => `<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="5" name="Line"/></p:nvCxnSpPr><p:spPr>${xfrm(100, 100, 300, 400, attrs)}${geom('straightConnector1')}${ln}</p:spPr></p:cxnSp>`;
  const shapes = cxn(' flipV="1"', `<a:ln w="25400">${fill('FF0000')}<a:tailEnd type="triangle"/></a:ln>`) + cxn(' flipH="1"', '<a:ln><a:tailEnd type="none"/></a:ln>') + sp({ spPr: xfrm(0, 50, 200, 0) + geom('line') });
  const { els } = await importSlide(shapes);
  const [arrow, plain, flat] = els;
  // From (100, 500) up to (400, 100): 500 px long, centred on (250, 300).
  assert.deepEqual([arrow.shape, arrow.x, arrow.y, arrow.w, arrow.h, arrow.stroke, arrow.strokeWidth], ['arrow', 0, 294, 500, 12, '#ff0000', 4]);
  assert.ok(Math.abs(arrow.rotation - -53.13) < 0.01);
  assert.deepEqual([plain.shape, plain.stroke, plain.strokeWidth], ['line', '#000000', 2]);
  assert.ok(Math.abs(plain.rotation - 126.87) < 0.01);
  assert.deepEqual([flat.shape, flat.x, flat.y, flat.w, flat.rotation], ['line', 0, 44, 200, 0]);
});

test('grouped shapes are scaled into the group’s frame and stay grouped', async () => {
  const child = (x, y) => sp({ spPr: xfrm(x, y, 200, 100) + geom('rect') + fill('FF0000') });
  const group = (kids, frame = '<a:off x="635000" y="635000"/><a:ext cx="1270000" cy="1270000"/><a:chOff x="0" y="0"/><a:chExt cx="2540000" cy="2540000"/>') => `<p:grpSp><p:nvGrpSpPr/><p:grpSpPr><a:xfrm>${frame}</a:xfrm></p:grpSpPr>${kids}</p:grpSp>`;
  const { els } = await importSlide(group(child(200, 200) + child(0, 300)) + group(child(40, 40), '') + child(5, 5));
  assert.deepEqual([els[0].x, els[0].y, els[0].w, els[0].h], [200, 200, 100, 50]);
  assert.deepEqual([els[1].x, els[1].y], [100, 250]);
  assert.ok(els[0].groupId && els[0].groupId === els[1].groupId);
  assert.deepEqual([els[2].x, els[2].groupId, els[3].groupId], [40, undefined, undefined], 'a group of one, or without a frame, is left as it is');
});

test('pictures become assets, once per media file, with unsupported formats reported', async () => {
  const shapes = picture('rId2', 'Company logo') + picture('rId2') + picture('rId3') + picture('rId4') + picture('rId5') + picture('rId9');
  const rels = [['rId2', 'image', '../media/image1.png'], ['rId3', 'image', '../media/photo.JPG'], ['rId4', 'image', '../media/drawing.emf'], ['rId5', 'image', 'https://example.com/x.png', true]];
  const { els, deck, report } = await importSlide(shapes, { rels }, { files: { 'ppt/media/image1.png': PNG_1X1, 'ppt/media/photo.JPG': 'not really a jpeg' } });
  assert.deepEqual(els.map((e) => e.type), ['image', 'image', 'image'], 'linked, missing and unsupported pictures are left out');
  const assets = Object.values(deck.assets);
  assert.equal(assets.length, 2);
  assert.equal(els[0].assetId, els[1].assetId);
  assert.deepEqual([els[0].alt, els[0].fit, els[0].x, els[0].y, els[0].w, els[0].h], ['Company logo', 'cover', 10, 20, 300, 200]);
  const png = deck.assets[els[0].assetId];
  assert.deepEqual([png.name, png.w, png.h, png.dataUrl], ['ppt/media/image1.png', 1, 1, `data:image/png;base64,${PNG_1X1.toString('base64')}`]);
  const jpg = deck.assets[els[2].assetId];
  assert.deepEqual([jpg.w, jpg.h], [800, 600], 'an image that will not decode gets a fallback size');
  assert.ok(jpg.dataUrl.startsWith('data:image/jpeg;base64,'));
  assert.deepEqual(report.skipped, ['EMF images (not supported by browsers)']);
});

test('tables keep their cells, header flag and type size', async () => {
  const cell = (text, rPr = '') => `<a:tc><a:txBody><a:bodyPr/><a:p><a:r>${rPr}<a:t>${text}</a:t></a:r></a:p></a:txBody></a:tc>`;
  const tbl = (tblPr, rows) => frame(`<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl>${tblPr}${rows}</a:tbl></a:graphicData>`);
  const two = `<a:tc><a:txBody><a:p><a:r><a:t>line 1</a:t></a:r></a:p><a:p><a:r><a:t>line </a:t></a:r><a:r><a:t>2</a:t></a:r></a:p></a:txBody></a:tc>`;
  const shapes = tbl('<a:tblPr firstRow="1"/>', `<a:tr>${cell('Name', '<a:rPr sz="1600"/>')}${cell('Qty')}</a:tr><a:tr>${two}${cell('3')}</a:tr>`) + tbl('<a:tblPr/>', '');
  const { els } = await importSlide(shapes);
  const [t, empty] = els;
  assert.deepEqual(t.rows, [['Name', 'Qty'], ['line 1\nline 2', '3']]);
  assert.deepEqual([t.headerRow, t.style.fontSize, t.x, t.y, t.w, t.h], [true, 32, 100, 100, 600, 400]);
  assert.deepEqual([empty.rows, empty.headerRow, empty.style.fontSize], [[['']], false, 28]);
});

test('charts keep type, labels, series, title, legend and stated colours', async () => {
  const red = '<c:spPr><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill></c:spPr>';
  const pie = chartXml(
    'pieChart',
    ser({ name: '2024', cat: ['A', 'B'], val: [60, 40], extra: `<c:dPt><c:idx val="0"/>${red}</c:dPt><c:dPt><c:idx val="1"/></c:dPt><c:dLbls><c:showVal val="0"/><c:showPercent val="1"/></c:dLbls>` }),
    '<c:title><c:tx><c:rich><a:p><a:r><a:t>Market </a:t></a:r><a:r><a:t>share</a:t></a:r></a:p></c:rich></c:tx></c:title><c:legend/>',
  );
  const bars = chartXml('barChart', `<c:barDir val="bar"/>${ser({ name: 'North', cat: ['Q1', 'Q2', 'Q3'], val: [1, 2, 3] })}${ser({ cat: ['Q1', 'Q2', 'Q3'], val: [null, 5] })}`);
  const rels = [['rId2', 'chart', '../charts/chart1.xml'], ['rId3', 'chart', '../charts/chart2.xml']];
  const { els, report } = await importSlide(chartFrame('rId2') + chartFrame('rId3'), { rels }, { files: { 'ppt/charts/chart1.xml': pie, 'ppt/charts/chart2.xml': bars } });
  const [p, b] = els;
  assert.deepEqual(
    { type: p.chartType, data: p.data, title: p.title, legend: p.showLegend, values: p.showValues, colors: p.colors },
    { type: 'pie', data: { labels: ['A', 'B'], series: [{ name: '2024', values: [60, 40] }] }, title: 'Market share', legend: true, values: true, colors: ['#ff0000', 'theme:accent'] },
  );
  assert.deepEqual([p.x, p.y, p.w, p.h, p.fontSize], [100, 100, 600, 400, 28]);
  assert.deepEqual(
    { type: b.chartType, data: b.data, title: b.title, legend: b.showLegend, values: b.showValues, colors: b.colors },
    { type: 'bar', data: { labels: ['Q1', 'Q2', 'Q3'], series: [{ name: 'North', values: [1, 2, 3] }, { name: 'Series 2', values: [0, 5, 0] }] }, title: '', legend: false, values: false, colors: [] },
  );
  assert.deepEqual(report.skipped, []);
});

test('each PowerPoint chart family maps to the nearest chart type', async () => {
  const kinds = { barChart: 'column', bar3DChart: 'column', lineChart: 'line', scatterChart: 'line', areaChart: 'area', doughnutChart: 'doughnut', pie3DChart: 'pie', radarChart: 'column' };
  const names = Object.keys(kinds);
  const files = Object.fromEntries(names.map((k, i) => [`ppt/charts/chart${i}.xml`, chartXml(k, ser({ name: 'S', val: [4, 5] }))]));
  const rels = names.map((_, i) => [`rId${i + 2}`, 'chart', `../charts/chart${i}.xml`]);
  const { els } = await importSlide(names.map((_, i) => chartFrame(`rId${i + 2}`)).join(''), { rels }, { files });
  assert.deepEqual(els.map((e) => e.chartType), Object.values(kinds));
  assert.deepEqual(els[0].data.labels, ['1', '2'], 'categories are numbered when the file has none');
});

test('charts that cannot be read and embedded objects are reported, not imported', async () => {
  const rels = [['rId2', 'chart', '../charts/empty.xml'], ['rId3', 'chart', '../charts/noseries.xml']];
  const files = { 'ppt/charts/empty.xml': `<c:chartSpace ${C}/>`, 'ppt/charts/noseries.xml': chartXml('barChart', '') };
  const smartArt = frame('<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram"/>');
  const { els, report } = await importSlide(chartFrame('rId2') + chartFrame('rId3') + chartFrame('rId404') + smartArt, { rels }, { files });
  assert.deepEqual(els, []);
  assert.deepEqual(report.skipped, ['some charts', 'embedded objects (SmartArt, OLE, video)']);
});

test('slide background: solid, gradient, inherited from layout or master, else the theme', async () => {
  const bg = (inner) => `<p:bg><p:bgPr>${inner}</p:bgPr></p:bg>`;
  const gradient = '<a:gradFill><a:gsLst><a:gs pos="0"><a:srgbClr val="FF0000"/></a:gs><a:gs pos="50000"><a:srgbClr val="00FF00"/></a:gs><a:gs pos="100000"><a:srgbClr val="0000FF"/></a:gs></a:gsLst><a:lin ang="2700000"/></a:gradFill>';
  const only = async (slide, opts) => (await importSlide('', slide, opts)).slide.background;
  assert.deepEqual(await only({ bg: bg(fill('102030')) }), { kind: 'solid', color: '#102030' });
  assert.deepEqual(await only({ bg: bg(gradient) }), { kind: 'gradient', from: '#ff0000', to: '#0000ff', angle: 135 });
  assert.deepEqual(await only({}, { layoutBg: '<p:bg><p:bgRef idx="1001"><a:schemeClr val="bg2"/></p:bgRef></p:bg>', masterBg: bg(fill('000000')) }), { kind: 'solid', color: '#eeece1' });
  assert.deepEqual(await only({}, { masterBg: bg(fill('000000')) }), { kind: 'solid', color: '#000000' });
  assert.deepEqual(await only({}), { kind: 'solid', color: 'theme:background' });
});

test('a picture background on the master is imported as an image background', async () => {
  const masterBg = '<p:bg><p:bgPr><a:blipFill><a:blip r:embed="rId7"/></a:blipFill></p:bgPr></p:bg>';
  const { slide, deck } = await importSlide('', {}, { masterBg, masterRels: [['rId7', 'image', '../media/bg.png']], files: { 'ppt/media/bg.png': PNG_1X1 } });
  assert.equal(slide.background.kind, 'image');
  assert.equal(deck.assets[slide.background.assetId].name, 'ppt/media/bg.png');
  const missing = await importSlide('', {}, { masterBg, masterRels: [] });
  assert.deepEqual(missing.slide.background, { kind: 'solid', color: 'theme:background' });
});

test('default-coloured text, tables and charts switch to the readable theme colour on a dark slide', async () => {
  const tbl = frame('<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>x</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData>');
  const rels = [['rId2', 'chart', '../charts/c.xml']];
  const files = { 'ppt/charts/c.xml': chartXml('barChart', ser({ name: 'S', val: [1] })) };
  const explicit = sp({ spPr: xfrm(0, 0, 100, 50), text: `<a:p><a:r><a:rPr>${fill('FF0000')}</a:rPr><a:t>red</a:t></a:r></a:p>` });
  const shapes = sp({ spPr: xfrm(0, 0, 100, 50), text: para('auto') }) + explicit + tbl + chartFrame('rId2');
  const colours = (els) => [els[0].style.color, els[1].style.color, els[2].style.color, els[3].textColor];
  const dark = await importSlide(shapes, { rels, bg: `<p:bg><p:bgPr>${fill('101010')}</p:bgPr></p:bg>` }, { files });
  assert.deepEqual(colours(dark.els), ['theme:background', '#ff0000', 'theme:background', 'theme:background']);
  const light = await importSlide(shapes, { rels }, { files });
  assert.deepEqual(colours(light.els), ['theme:text', '#ff0000', 'theme:text', 'theme:text']);
});

test('speaker notes, hidden slides and slide order are kept; a missing slide part is skipped', async () => {
  const notesBody = sp({ ph: '<p:ph type="body" idx="1"/>', text: para('Open with the numbers.') + '<a:p><a:r><a:t>Then </a:t></a:r><a:r><a:t>pause.</a:t></a:r></a:p>' });
  const notes = `<p:notes ${NS}><p:cSld><p:spTree>${sp({ ph: '<p:ph type="sldNum" idx="2"/>', text: para('1') })}${notesBody}</p:spTree></p:cSld></p:notes>`;
  const slides = [
    { shapes: sp({ spPr: xfrm(0, 0, 100, 50), text: para('one') }), rels: [['rId2', 'notesSlide', '../notesSlides/notesSlide1.xml']] },
    { missing: true },
    { shapes: sp({ spPr: xfrm(0, 0, 100, 50), text: para('three') }), attrs: ' show="0"' },
  ];
  const { deck } = await importPptx(await makePptx(slides, { files: { 'ppt/notesSlides/notesSlide1.xml': notes } }));
  assert.deepEqual(deck.slides.map((s) => s.elements[0].html), ['<p>one</p>', '<p>three</p>']);
  assert.deepEqual(deck.slides.map((s) => [s.notes, s.hidden, s.layoutId, s.transition]), [['Open with the numbers.\nThen pause.', undefined, 'blank', 'fade'], ['', true, 'blank', 'fade']]);
});
