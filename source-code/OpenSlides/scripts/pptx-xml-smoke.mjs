// End-to-end check that a .pptx export stays well-formed XML when the deck holds
// characters XML 1.0 cannot carry (pasted vertical tabs, form feeds, ...).
//
//   npm run dev -- --port 5173          (in another shell)
//   PLAYWRIGHT_PACKAGE_JSON=.qa/package.json node scripts/pptx-xml-smoke.mjs
//
// SMOKE_URL overrides the address, SMOKE_OUT is a folder to keep the exported files in,
// and `--bench` times the export of a 100-slide deck instead (median of 5).
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const { chromium } = createRequire(resolve(process.env.PLAYWRIGHT_PACKAGE_JSON || '.qa/package.json'))('playwright');
const url = process.env.SMOKE_URL || 'http://127.0.0.1:5173';
const outDir = process.env.SMOKE_OUT;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 960 } });
page.on('dialog', (d) => d.accept()); // "leave without saving?" when going back to the home screen
let failed = 0;
const check = (name, fn) => {
  try {
    fn();
    console.log(`ok   - ${name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL - ${name}\n       ${String(e.message).split('\n')[0]}`);
  }
};
const keep = async (name, base64) => {
  if (!outDir) return;
  await mkdir(outDir, { recursive: true });
  await writeFile(resolve(outDir, name), Buffer.from(base64, 'base64'));
};

try {
  // Helpers that run in the page, next to the app's own modules. A page load drops them.
  const install = () => page.evaluate(async () => {
    const { default: JSZip } = await import('/node_modules/.vite/deps/jszip.js');
    const { buildPptx } = await import('/src/io/export.tsx');
    const { importPptx } = await import('/src/io/importPptx.ts');
    const { htmlToText } = await import('/src/model/resolve.ts');
    const toB64 = (buf) => {
      const bytes = new Uint8Array(buf);
      let s = '';
      for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      return btoa(s);
    };
    // Parses every XML part of a zip, and of the workbooks embedded in it (chart data).
    const audit = async (buf, prefix = '') => {
      const zip = await JSZip.loadAsync(buf);
      const out = { parts: 0, bad: [], text: '' };
      for (const name of Object.keys(zip.files)) {
        if (zip.files[name].dir) continue;
        if (/\.xlsx$/i.test(name)) {
          const inner = await audit(await zip.files[name].async('arraybuffer'), `${prefix}${name}!`);
          out.parts += inner.parts;
          out.bad.push(...inner.bad);
          out.text += inner.text;
        }
        if (!/\.(xml|rels)$/i.test(name)) continue;
        out.parts++;
        const xml = await zip.files[name].async('string');
        const raw = [...new Set(xml.match(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g) ?? [])].map((c) => `U+${c.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0')}`);
        const doc = new DOMParser().parseFromString(xml, 'application/xml');
        const broken = doc.getElementsByTagName('parsererror').length > 0;
        if (raw.length || broken) out.bad.push(`${prefix}${name}${broken ? ' does not parse' : ''}${raw.length ? ` raw ${raw.join(' ')}` : ''}`);
        // One entry per text run and alt text, so a dropped character shows as two words glued together.
        else out.text += ` ${[...doc.querySelectorAll('*')].filter((n) => !n.children.length).map((n) => n.textContent).join(' ')} ${[...doc.querySelectorAll('[descr]')].map((n) => n.getAttribute('descr')).join(' ')}`;
      }
      return out;
    };
    const describe = (report) => ({
      slides: report.deck.slides.length,
      skipped: report.skipped,
      texts: report.deck.slides.map((s) => s.elements.filter((e) => e.type === 'text').map((e) => htmlToText(e.html))),
      html: report.deck.slides.map((s) => s.elements.filter((e) => e.type === 'text').map((e) => e.html)),
    });
    window.__t = {
      async roundTrip(deck) {
        const buf = await (await buildPptx(deck)).write({ outputType: 'arraybuffer' });
        const res = { b64: toB64(buf), audit: await audit(buf), reimport: null, reimportError: null };
        try {
          res.reimport = describe(await importPptx(new File([buf], 'export.pptx')));
        } catch (e) {
          res.reimportError = e.message;
        }
        return res;
      },
      // Returns a copy of a .pptx whose named part has a raw vertical tab in its first text run.
      async damage(b64, part) {
        const zip = await JSZip.loadAsync(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
        const xml = await zip.file(part).async('string');
        zip.file(part, xml.replace('<a:t>', '<a:t>\u000B'));
        const buf = await zip.generateAsync({ type: 'arraybuffer' });
        try {
          return { report: describe(await importPptx(new File([buf], 'damaged.pptx'))) };
        } catch (e) {
          return { error: e.message };
        }
      },
    };
  });
  await page.goto(url);
  await install();

  if (process.argv.includes('--bench')) {
    const times = await page.evaluate(async () => {
      const { buildPptx } = await import('/src/io/export.tsx');
      const { makeDeck, makeText, makeShape, makeTable, makeChart } = await import('/src/model/factory.ts');
      const { THEME_PRESETS } = await import('/src/model/theme.ts');
      const deck = makeDeck('Bench', THEME_PRESETS[0], { w: 1920, h: 1080, preset: '16:9' });
      for (let i = 0; i < 100; i++) {
        const elements = [];
        for (let j = 0; j < 8; j++) elements.push(makeText(80 + j * 20, 60 + j * 90, 900, 80, `<p>Slide ${i} line ${j} with <b>bold</b> and <i>italic</i> text &amp; more</p><ul><li>one</li><li>two</li></ul>`));
        elements.push(makeShape('rect', 1100, 80, 300, 200), makeShape('ellipse', 1450, 80, 300, 200));
        elements.push(makeTable([['Region', 'Q1', 'Q2'], ['North', '10', '12'], ['South', '8', '9']], 1100, 320, 700, 240));
        elements.push(makeChart('column', 1100, 600, 700, 400, { title: `Chart ${i}` }));
        deck.slides.push({ id: `s${i}`, layoutId: 'blank', elements, notes: `Notes for slide ${i}`, transition: 'none' });
      }
      const run = async () => {
        const t = performance.now();
        await (await buildPptx(deck)).write({ outputType: 'arraybuffer' });
        return performance.now() - t;
      };
      await run(); // warm-up
      const out = [];
      for (let i = 0; i < 5; i++) out.push(Math.round(await run()));
      return out;
    });
    const sorted = [...times].sort((a, b) => a - b);
    console.log(`100 slides x 12 elements, buildPptx + write, ms: ${times.join(' ')} | median ${sorted[2]}`);
  } else {
    // 1. The reported steps: paste text holding a control character into the title of a blank deck.
    for (const [label, pasted, words] of [
      ['vertical tab', 'Q3 results\u000BNorth region', ['Q3 results', 'North region']],
      ['form feed', 'Budget\u000C2027', ['Budget', '2027']],
    ]) {
      await page.goto(`${url}/#/`);
      await page.reload();
      await install();
      await page.locator('.template-card', { hasText: 'Blank deck' }).first().click();
      await page.getByText('Click to add a title').first().dblclick();
      await page.locator('[data-richtext="true"]').first().waitFor();
      await page.evaluate((text) => {
        const dt = new DataTransfer();
        dt.setData('text/plain', text);
        document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
      }, pasted);
      await page.keyboard.press('Escape');
      const res = await page.evaluate(async () => {
        const { useEditor } = await import('/src/store/editor.ts');
        const deck = useEditor.getState().deck;
        return { stored: deck.slides[0].elements.filter((e) => e.type === 'text').map((e) => e.html), ...(await window.__t.roundTrip(deck)) };
      });
      await keep(`pasted-${label.replace(' ', '-')}.pptx`, res.b64);
      console.log(`# pasted ${label}: stored ${JSON.stringify(res.stored)}`);
      console.log(`# pasted ${label}: parts that fail ${JSON.stringify(res.audit.bad)}`);
      check(`pasted ${label}: all ${res.audit.parts} XML parts of the export parse, no raw control characters`, () => assert.deepEqual(res.audit.bad, []));
      check(`pasted ${label}: the export holds ${JSON.stringify(words)}`, () => words.forEach((w) => assert.ok(res.audit.text.includes(w), `missing ${w}`)));
      check(`pasted ${label}: the words are not glued together`, () => assert.ok(!res.audit.text.includes(words.join(''))));
      console.log(`# pasted ${label}: reimport ${JSON.stringify(res.reimport ?? res.reimportError)}`);
      check(`pasted ${label}: reopening the export gives the title back`, () => {
        const text = (res.reimport?.texts ?? []).flat().join(' | ');
        words.forEach((w) => assert.ok(text.includes(w), `title text is ${JSON.stringify(text)}`));
      });
    }

    // 2. A deck stored before the fix: control characters already in every text field.
    const stored = await page.evaluate(async () => {
      const { makeDeck, makeText, makeTable, makeChart, makeImage } = await import('/src/model/factory.ts');
      const { THEME_PRESETS } = await import('/src/model/theme.ts');
      const VT = '\u000B';
      const deck = makeDeck(`Deck${VT}title \u0001&<>"'`, THEME_PRESETS[0], { w: 1920, h: 1080, preset: '16:9' });
      const c = document.createElement('canvas');
      c.width = c.height = 4;
      deck.assets.a1 = { id: 'a1', dataUrl: c.toDataURL('image/png'), w: 4, h: 4, name: 'dot' };
      deck.slides.push({
        id: 's1',
        layoutId: 'blank',
        transition: 'none',
        notes: `Notes${VT}line \u0002two`,
        elements: [
          makeText(100, 100, 800, 200, `<p>Box${VT}text \u0008&amp; &lt;South&gt; "quoted" 'single' 😀 日本語 Ünï</p><ul><li>Item${VT}one\u000Ctwo</li></ul>`),
          makeText(100, 320, 800, 100, '<p>Deck is {{title}}</p>'),
          makeTable([[`Cell${VT}head`, 'B'], [`Cell\u000Cbody`, '\u001Fx']], 100, 450, 800, 200),
          makeChart('column', 1000, 100, 800, 400, { title: `Chart${VT}title`, alt: `Chart${VT}alt`, data: { labels: [`Label${VT}one`, 'Two'], series: [{ name: `Series${VT}name`, values: [1, 2] }] } }),
          makeChart('pie', 1000, 520, 400, 400, { title: '', alt: '', data: { labels: [`Pie${VT}label`, 'Other'], series: [{ name: `Pie${VT}series`, values: [3, 4] }] } }),
          makeImage('a1', 1450, 520, 300, 300, { alt: `Image${VT}alt` }),
        ],
      });
      return window.__t.roundTrip(deck);
    });
    await keep('stored-deck.pptx', stored.b64);
    console.log(`# stored deck: parts that fail ${JSON.stringify(stored.audit.bad)}`);
    check(`stored deck: all ${stored.audit.parts} XML parts of the export parse, no raw control characters`, () => assert.deepEqual(stored.audit.bad, []));
    check('stored deck: the words of every field survive', () => {
      for (const w of ['Deck', 'title', 'Notes', 'line', 'two', 'Box', 'text', 'Item', 'one', 'Cell', 'head', 'body', 'Chart', 'alt', 'Label', 'Series', 'name', 'Pie', 'label', 'series', 'Image']) assert.ok(stored.audit.text.includes(w), `missing ${w}`);
      for (const glued of ['Boxtext', 'Itemone', 'onetwo', 'Cellhead', 'Cellbody', 'Charttitle', 'Chartalt', 'Labelone', 'Seriesname', 'Pielabel', 'Imagealt', 'Notesline', 'Decktitle']) assert.ok(!stored.audit.text.includes(glued), `glued ${glued}`);
    });
    check('stored deck: & < > quotes, emoji and non-Latin text come out unchanged', () => assert.ok(stored.audit.text.includes(`& <South> "quoted" 'single' 😀 日本語 Ünï`)));
    console.log(`# stored deck: reimport ${JSON.stringify(stored.reimport ?? stored.reimportError)}`);
    check('stored deck: reopening the export gives the text box back', () => {
      const text = (stored.reimport?.texts ?? []).flat().join(' | ');
      for (const w of ['Box', 'text', '& <South>', '日本語']) assert.ok(text.includes(w), `text is ${JSON.stringify(text)}`);
    });

    // 3. Importing a .pptx with a part that is not well-formed XML.
    const clean = await page.evaluate(async () => {
      const { createFromTemplate } = await import('/src/model/templates.ts');
      const deck = createFromTemplate('blank');
      deck.slides[0].elements.forEach((e) => {
        if (e.type === 'text') e.html = '<p>Plain title</p>';
      });
      deck.slides.push({ ...structuredClone(deck.slides[0]), id: 'second' });
      return window.__t.roundTrip(deck);
    });
    check('clean two-slide deck imports with nothing skipped', () => assert.deepEqual([clean.reimport?.slides, clean.reimport?.skipped], [2, []]));
    const brokenSlide = await page.evaluate(([b64, part]) => window.__t.damage(b64, part), [clean.b64, 'ppt/slides/slide1.xml']);
    console.log(`# damaged slide1.xml: ${JSON.stringify(brokenSlide.error ?? { slides: brokenSlide.report.slides, skipped: brokenSlide.report.skipped })}`);
    check('damaged slide1.xml: the import fails with a message or lists the slide as not carried over', () => assert.ok(brokenSlide.error || brokenSlide.report.skipped.some((s) => /slide 1\b/.test(s))));
    check('damaged slide1.xml: the damaged slide is not imported as an empty slide', () => assert.ok(brokenSlide.error || brokenSlide.report.slides === 1));
    const brokenOnly = await page.evaluate(async (b64) => {
      const { default: JSZip } = await import('/node_modules/.vite/deps/jszip.js');
      const zip = await JSZip.loadAsync(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
      zip.remove('ppt/slides/slide2.xml');
      return window.__t.damage(btoa(String.fromCharCode(...new Uint8Array(await zip.generateAsync({ type: 'arraybuffer' })))), 'ppt/slides/slide1.xml');
    }, clean.b64);
    console.log(`# every slide damaged: ${JSON.stringify(brokenOnly.error ?? brokenOnly.report)}`);
    check('every slide damaged: the import fails with a message', () => assert.ok(brokenOnly.error));
  }
} finally {
  await browser.close();
}
if (failed) {
  console.log(`${failed} check(s) failed`);
  process.exit(1);
}
