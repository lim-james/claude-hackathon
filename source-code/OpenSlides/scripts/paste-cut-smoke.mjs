// End-to-end check for issue #124: long pastes are cut on a character boundary with a message, and
// text typed or pasted into every field reaches the .pptx as valid UTF-8 XML that OpenSlides reopens.
//
//   npm run dev -- --port 5424          (in another shell)
//   PLAYWRIGHT_PACKAGE_JSON=.qa/package.json SMOKE_URL=http://127.0.0.1:5424 node scripts/paste-cut-smoke.mjs
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const { chromium } = createRequire(resolve(process.env.PLAYWRIGHT_PACKAGE_JSON || '.qa/package.json'))('playwright');
const url = process.env.SMOKE_URL || 'http://127.0.0.1:5424';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1500, height: 960 }, acceptDownloads: true });
await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: url });
const page = await context.newPage();
page.on('dialog', (d) => d.accept());
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
const hasLone = (s) => /[\uD800-\uDFFF]/u.test(s);

// Parses every XML part of a .pptx (and of the chart workbooks inside it) from its raw bytes: strict
// UTF-8 first, since a lone surrogate is written as bytes no XML parser accepts, then as XML. Then reopens it.
const audit = (b64) =>
  page.evaluate(async (b64) => {
    const { default: JSZip } = await import('/node_modules/.vite/deps/jszip.js');
    const { importPptx } = await import('/src/io/importPptx.ts');
    const { htmlToText } = await import('/src/model/resolve.ts');
    const buf = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const strict = new TextDecoder('utf-8', { fatal: true });
    const walk = async (bytes, prefix = '') => {
      const zip = await JSZip.loadAsync(bytes);
      const out = { parts: 0, bad: [], text: '' };
      for (const name of Object.keys(zip.files)) {
        if (zip.files[name].dir) continue;
        if (/\.xlsx$/i.test(name)) {
          const inner = await walk(await zip.files[name].async('uint8array'), `${prefix}${name}!`);
          out.parts += inner.parts;
          out.bad.push(...inner.bad);
          out.text += inner.text;
        }
        if (!/\.(xml|rels)$/i.test(name)) continue;
        out.parts++;
        let xml;
        try {
          xml = strict.decode(await zip.files[name].async('uint8array'));
        } catch {
          out.bad.push(`${prefix}${name}: not valid UTF-8`);
          continue;
        }
        const doc = new DOMParser().parseFromString(xml, 'application/xml');
        if (doc.getElementsByTagName('parsererror').length) out.bad.push(`${prefix}${name}: does not parse`);
        else out.text += ` ${[...doc.querySelectorAll('*')].filter((n) => !n.children.length).map((n) => n.textContent).join(' ')} ${[...doc.querySelectorAll('[descr],[title]')].map((n) => n.getAttribute('descr') ?? n.getAttribute('title')).join(' ')}`;
      }
      return out;
    };
    const res = await walk(buf);
    try {
      const r = await importPptx(new File([buf], 'export.pptx'));
      res.reopen = { slides: r.deck.slides.length, skipped: r.skipped, texts: r.deck.slides.flatMap((s) => s.elements.filter((e) => e.type === 'text').map((e) => htmlToText(e.html))) };
    } catch (e) {
      res.reopen = { error: e.message };
    }
    return res;
  }, b64);

// Export > PowerPoint (.pptx) through the menu, as a user would, and return the downloaded file.
const exportPptx = async () => {
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: /^Export/ }).first().click();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: /PowerPoint \(\.pptx\)/ }).click()]);
  return (await readFile(await download.path())).toString('base64');
};
const deckState = () => page.evaluate(async () => (await import('/src/store/editor.ts')).useEditor.getState().deck);
const toastText = async () => ((await page.locator('.toast[role=status]').count()) ? await page.locator('.toast[role=status]').innerText() : null);
const newBlankDeck = async () => {
  await page.goto(`${url}/#/`);
  await page.locator('.template-card', { hasText: 'Blank deck' }).first().click();
  await page.getByText('Click to add a title').first().waitFor();
  await page.keyboard.press('Escape');
};
const pasteOnSlide = async (text) => {
  await page.evaluate((t) => navigator.clipboard.writeText(t), text);
  await page.keyboard.press('Escape');
  await page.keyboard.press('ControlOrMeta+V');
  await page.waitForTimeout(300);
};
const pastedBox = async () => {
  const deck = await deckState();
  const el = deck.slides[0].elements.findLast((e) => e.type === 'text' && e.html);
  return el ? await page.evaluate(async (html) => (await import('/src/model/resolve.ts')).htmlToText(html), el.html) : null;
};

try {
  // 1. The issue's paste: 5,018 units with an emoji across the 5,000 cut.
  const issueText = 'Meeting notes. ' + 'x'.repeat(4984) + '😀 end of the notes';
  for (const [label, text, expected, cut] of [
    ['issue #124 paste (emoji across the cut)', issueText, 'Meeting notes. ' + 'x'.repeat(4984), true],
    ['accent across the cut (e + U+0301)', 'x'.repeat(4999) + 'e\u0301 and the rest', 'x'.repeat(4999), true],
    ['flag across the cut', 'x'.repeat(4998) + '🇸🇬 rest', 'x'.repeat(4998), true],
    ['4,500 units with emoji (under the limit)', 'Notes 😀 👩\u200D💻 e\u0301 '.repeat(250).trim(), 'Notes 😀 👩\u200D💻 e\u0301 '.repeat(250).trim(), false],
  ]) {
    await newBlankDeck();
    await pasteOnSlide(text);
    const kept = await pastedBox();
    const toast = await toastText();
    console.log(`# ${label}: pasted ${text.length} units, kept ${kept?.length}, last unit U+${kept?.charCodeAt(kept.length - 1).toString(16).toUpperCase()}, toast ${JSON.stringify(toast)}`);
    check(`${label}: the box holds ${cut ? 'the text up to the last whole character' : 'the whole text'}`, () => assert.equal(kept, expected));
    check(`${label}: no half character kept`, () => assert.ok(!hasLone(kept)));
    check(`${label}: ${cut ? 'a message says the text was shortened' : 'no message'}`, () => (cut ? assert.match(toast ?? '', /first 5,000 characters/) : assert.equal(toast, null)));
    if (label.startsWith('issue')) {
      const res = await audit(await exportPptx());
      console.log(`# ${label}: ${res.parts} XML parts, bad ${JSON.stringify(res.bad)}, reopen ${JSON.stringify({ ...res.reopen, texts: undefined })}`);
      check(`${label}: every XML part of the export is valid UTF-8 and parses`, () => assert.deepEqual(res.bad, []));
      check(`${label}: OpenSlides reopens the export with the pasted text`, () => assert.ok(res.reopen.texts?.some((t) => t.startsWith('Meeting notes. xxx')), JSON.stringify(res.reopen.error)));
      // Neighbouring paths: undo and redo the paste, then the saved deck after a reload.
      await page.keyboard.press('ControlOrMeta+Z');
      const undone = await pastedBox();
      await page.keyboard.press('ControlOrMeta+Y');
      const redone = await pastedBox();
      await page.evaluate(async () => (await import('/src/store/storage.ts')).flushSave());
      await page.reload();
      await page.getByText('Click to add a title').first().waitFor();
      const reloaded = await pastedBox();
      console.log(`# ${label}: after undo ${undone?.length ?? 'no box'}, after redo ${redone?.length}, after reload ${reloaded?.length}`);
      check(`${label}: undo removes the pasted box, redo brings back the same text`, () => assert.deepEqual([undone, redone], [null, expected]));
      check(`${label}: the saved deck holds the same text after a reload`, () => assert.equal(reloaded, expected));
    }
  }

  // 2. Typed text reaches the export intact from every field a user can type into.
  await newBlankDeck();
  const typed = 'Caf\u00e9 😀 👩\u200D💻 🇸🇬 e\u0301 日本';
  // Slide title (title placeholder)
  await page.getByText('Click to add a title').first().dblclick();
  await page.locator('[data-richtext="true"]').first().waitFor();
  await page.keyboard.insertText(`Title ${typed}`);
  await page.keyboard.press('Escape');
  // Text box: paste into a box being edited (no cap there)
  await page.getByText('Click to add a subtitle').first().dblclick();
  await page.locator('[data-richtext="true"]').first().waitFor();
  await page.evaluate((t) => navigator.clipboard.writeText(t), `Pasted ${typed}`);
  await page.keyboard.press('ControlOrMeta+V');
  await page.keyboard.press('Escape');
  // Speaker notes
  await page.locator('.notes-toggle').click();
  await page.locator('.notes-text').click();
  await page.keyboard.insertText(`Notes ${typed}`);
  await page.keyboard.press('Escape');
  // Deck title
  await page.getByLabel('Deck title').fill(`Deck ${typed}`);
  await page.getByLabel('Deck title').press('Enter');
  // Table cell
  await page.getByTitle('Add a table').click();
  await page.getByRole('button', { name: '2 by 2', exact: true }).click();
  let deck = await deckState();
  const table = deck.slides[0].elements.find((e) => e.type === 'table');
  await page.locator(`.stage [data-el-id="${table.id}"]`).dblclick();
  const cell = page.locator(`.stage [data-el-id="${table.id}"] td`).first();
  await cell.click();
  await page.keyboard.press('ControlOrMeta+A');
  await page.keyboard.insertText(`Cell ${typed}`);
  await page.locator(`.stage [data-el-id="${table.id}"] td`).nth(3).click();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  // Chart labels, series name and title
  await page.getByTitle('Add a chart').click();
  await page.locator('.menu button').first().click();
  // (the Chart data dialog opens on insert)
  await page.getByLabel('Label 1', { exact: true }).fill(`Label ${typed}`);
  await page.getByLabel('Series 1 name', { exact: true }).fill(`Series ${typed}`);
  await page.getByRole('button', { name: 'Apply data' }).click();
  if ((await page.getByRole('button', { name: 'Toggle properties' }).getAttribute('aria-pressed')) !== 'true') await page.getByRole('button', { name: 'Toggle properties' }).click();
  const chartTitle = page.locator('label.alt-field', { has: page.locator('.field-label', { hasText: /^Title$/ }) }).locator('input');
  await chartTitle.fill(`Chart ${typed}`);
  await chartTitle.blur();

  deck = await deckState();
  const stored = JSON.stringify({ title: deck.title, notes: deck.slides[0].notes, els: deck.slides[0].elements });
  const res = await audit(await exportPptx());
  console.log(`# typed text: ${res.parts} XML parts, bad ${JSON.stringify(res.bad)}, reopen ${JSON.stringify({ slides: res.reopen.slides, skipped: res.reopen.skipped, error: res.reopen.error })}`);
  check('typed text: every XML part of the export is valid UTF-8 and parses', () => assert.deepEqual(res.bad, []));
  for (const field of ['Title', 'Pasted', 'Notes', 'Deck', 'Cell', 'Chart', 'Label', 'Series']) {
    check(`typed text: ${field} field stored and exported unchanged`, () => {
      assert.ok(stored.includes(`${field} ${typed}`), 'not in the deck');
      assert.ok(res.text.includes(`${field} ${typed}`), 'not in the export');
    });
  }
  check('typed text: OpenSlides reopens the export', () => assert.ok(res.reopen.slides === 1 && res.reopen.texts.some((t) => t.includes(`Title ${typed}`)), JSON.stringify(res.reopen)));

  // 3. A deck already holding half an emoji in every field (stored before the fix, or pasted into a
  //    table cell or the notes): opened from a deck file and exported through the menu.
  const lone = await page.evaluate(async () => {
    const { makeDeck, makeText, makeTable, makeChart, makeImage } = await import('/src/model/factory.ts');
    const { THEME_PRESETS } = await import('/src/model/theme.ts');
    const H = '\uD83D';
    const L = '\uDE00';
    const deck = makeDeck(`Deck ${H}title`, THEME_PRESETS[0], { w: 1920, h: 1080, preset: '16:9' });
    const c = document.createElement('canvas');
    c.width = c.height = 4;
    deck.assets.a1 = { id: 'a1', dataUrl: c.toDataURL('image/png'), w: 4, h: 4, name: 'dot' };
    deck.slides.push({
      id: 's1', layoutId: 'blank', transition: 'none', notes: `Notes ${H}end ${L}x 😀`,
      elements: [
        makeText(100, 100, 800, 200, `<p>Box ${H}end 😀</p>`),
        makeTable([[`Cell ${H}head`, `B${L}`], ['x', 'y']], 100, 450, 800, 200),
        makeChart('column', 1000, 100, 800, 400, { title: `Chart ${H}title`, alt: `Alt ${L}`, data: { labels: [`Label ${H}one`, 'Two'], series: [{ name: `Series ${L}name`, values: [1, 2] }] } }),
        makeImage('a1', 1450, 520, 300, 300, { alt: `Image ${H}alt` }),
      ],
    });
    return JSON.stringify({ format: 'openslides', ...deck });
  });
  await page.goto(`${url}/#/`);
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: 'Open PowerPoint or deck file' }).click()]);
  await chooser.setFiles({ name: 'old.openslides.json', mimeType: 'application/json', buffer: Buffer.from(lone) }); // JSON.stringify writes a lone half as \udXXX
  await page.getByText('Box').first().waitFor();
  deck = await deckState();
  check('lone surrogates: the opened deck still holds them (the case under test)', () => assert.equal((JSON.stringify({ t: deck.title, n: deck.slides.map((s) => s.notes), e: deck.slides.map((s) => s.elements) }).match(/\\ud83d|\\ude00/g) ?? []).length, 11)); // JSON.stringify escapes a lone half
  const res3 = await audit(await exportPptx());
  console.log(`# lone surrogates: ${res3.parts} XML parts, bad ${JSON.stringify(res3.bad)}, reopen ${JSON.stringify({ slides: res3.reopen.slides, skipped: res3.reopen.skipped, error: res3.reopen.error })}`);
  check('lone surrogates: every XML part of the export is valid UTF-8 and parses', () => assert.deepEqual(res3.bad, []));
  check('lone surrogates: each half became U+FFFD and the rest of every field survives', () => {
    for (const w of ['Deck �title', 'Notes �end �x 😀', 'Box �end 😀', 'Cell �head', 'B�', 'Chart �title', 'Label �one', 'Series �name', 'Image �alt']) assert.ok(res3.text.includes(w), `missing ${w}`);
  });
  check('lone surrogates: OpenSlides reopens the export', () => assert.ok(res3.reopen.slides >= 1 && !res3.reopen.error && res3.reopen.texts.some((t) => t.includes('Box')), JSON.stringify(res3.reopen)));
} finally {
  await browser.close();
}
if (failed) {
  console.log(`${failed} check(s) failed`);
  process.exit(1);
}
