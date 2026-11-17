// #123: text typed into a text box or table cell is stored while the box still has focus,
// and the top bar never says "Saved" while typed text is not stored.
//   npx vite --port 5173 &   (or set OPENSLIDES_URL)
//   PLAYWRIGHT_PACKAGE_JSON=<dir with playwright>/package.json node scripts/typing-autosave-smoke.mjs
import {createRequire} from 'node:module';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';

const URL = process.env.OPENSLIDES_URL || 'http://127.0.0.1:5173';
const {chromium} = createRequire(resolve(process.env.PLAYWRIGHT_PACKAGE_JSON || '.qa/package.json'))('playwright');
const browser = await chromium.launch({headless: true});
const results = [];
const check = async (name, fn) => {
  if (process.env.ONLY && !name.includes(process.env.ONLY)) return;
  const ctx = await browser.newContext({viewport: {width: 1500, height: 960}, serviceWorkers: 'block'});
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  try {
    await fn(page, ctx);
    assert.deepEqual(errors, []);
    results.push(`ok   ${name}`);
  } catch (e) {
    results.push(`FAIL ${name}: ${e.message.split('\n').filter(Boolean).slice(0, 4).join(' | ')}`);
  } finally {
    await ctx.close();
  }
};

const status = (page) => page.locator('.topbar').getByText(/Saved on this device|Saving…|Not saved/).innerText();
const titleBox = (page) => page.locator('.stage .el-text').first();
// The deck record as IndexedDB holds it now.
const stored = (page) => page.evaluate(() => new Promise((done, fail) => {
  const open = indexedDB.open('keyval-store');
  open.onerror = () => fail(open.error);
  open.onsuccess = () => {
    const req = open.result.transaction('keyval').objectStore('keyval').getAll();
    req.onsuccess = () => { open.result.close(); done(JSON.stringify(req.result.filter((r) => r && Array.isArray(r.slides)))); };
    req.onerror = () => fail(req.error);
  };
}));
const blankDeck = async (page) => {
  await page.goto(URL);
  await page.getByRole('button', {name: 'New blank deck', exact: true}).click();
  await page.getByRole('navigation', {name: 'Creation tools'}).waitFor();
  await page.waitForTimeout(1500); // the new deck's first save
};
// The deck's URL reopens it from storage.
const openDeck = async (page, url) => {
  if (url) await page.goto(url);
  else await page.reload();
  await page.getByRole('navigation', {name: 'Creation tools'}).waitFor();
  await page.waitForTimeout(300);
};
const editTitle = async (page) => {
  await titleBox(page).dblclick();
  await page.locator('.rt-editing').waitFor();
};
// A 2 x 2 table from the top bar: [['Heading 1', 'Heading 2'], ['', '']].
const addTable = async (page) => {
  await page.getByTitle('Add a table').click();
  await page.getByRole('button', {name: '2 by 2', exact: true}).click();
  await page.locator('.stage table.el-table').waitFor();
};
const editCell = async (page, row, col) => {
  const table = page.locator('.stage table.el-table').first();
  await table.dblclick({position: {x: 5, y: 5}});
  const cell = table.locator('tr').nth(row).locator('td').nth(col);
  await cell.click();
  await page.keyboard.press('End');
  return cell;
};
const TEXT = 'Quarterly results for the board';

await check('text box: type, wait 2 s without leaving the box, stored; reload shows it', async (page) => {
  await blankDeck(page);
  await editTitle(page);
  await page.keyboard.type(TEXT, {delay: 30});
  await page.waitForTimeout(2000);
  assert.equal(await status(page), 'Saved on this device');
  assert.ok((await stored(page)).includes(TEXT), 'stored deck lacks the typed text');
  await openDeck(page);
  assert.equal(await titleBox(page).innerText(), TEXT);
});

await check('text box: the bar never says Saved while typed text is not stored', async (page) => {
  await blankDeck(page);
  await editTitle(page);
  for (const ch of TEXT) {
    await page.keyboard.type(ch);
    const typed = await page.locator('.rt-editing').innerText();
    if ((await status(page)) === 'Saved on this device') assert.ok((await stored(page)).includes(typed), `bar says Saved, storage lacks "${typed}"`);
    await page.waitForTimeout(40);
  }
  for (let i = 0; i < 40 && (await status(page)) !== 'Saved on this device'; i++) await page.waitForTimeout(100);
  assert.ok((await stored(page)).includes(TEXT));
});

await check('text box: closing the tab right after typing keeps the text', async (page, ctx) => {
  await blankDeck(page);
  await editTitle(page);
  await page.keyboard.type(TEXT, {delay: 30});
  const url = page.url();
  await page.close({runBeforeUnload: true});
  // Reopening within milliseconds can read storage before the closed tab's last write lands.
  await new Promise((r) => setTimeout(r, 1000));
  const again = await ctx.newPage();
  await openDeck(again, url);
  assert.equal(await titleBox(again).innerText(), TEXT);
});

await check('text box: Escape, then one Undo removes the typing and Redo brings it back', async (page) => {
  await blankDeck(page);
  await editTitle(page);
  await page.keyboard.type(TEXT, {delay: 30});
  await page.waitForTimeout(800);
  await page.keyboard.press('Escape');
  const state = () => page.evaluate(async () => {
    const {useEditor} = await import('/src/store/editor.ts');
    const s = useEditor.getState();
    return {html: s.deck.slides[0].elements[0].html, past: s.past.length};
  });
  const committed = await state();
  assert.ok(committed.html.includes(TEXT));
  await page.keyboard.press('Control+z');
  assert.equal((await state()).html, '');
  assert.equal((await state()).past, committed.past - 1, 'typing took more than one undo step');
  await page.keyboard.press('Control+Shift+z');
  assert.ok((await state()).html.includes(TEXT));
  await page.waitForTimeout(2000);
  await openDeck(page);
  assert.equal(await titleBox(page).innerText(), TEXT);
});

await check('text box: clicking away commits once; switching slide mid-typing keeps the text', async (page) => {
  await blankDeck(page);
  await page.evaluate(async () => { const a = await import('/src/store/actions.ts'); const {useEditor} = await import('/src/store/editor.ts'); a.addSlide(useEditor.getState().deck.slides[0].layoutId); a.goToSlide(useEditor.getState().deck.slides[0].id); });
  const past = () => page.evaluate(async () => (await import('/src/store/editor.ts')).useEditor.getState().past.length);
  const p0 = await past();
  await editTitle(page);
  await page.keyboard.type('First', {delay: 30});
  await page.locator('.stage').click({position: {x: 5, y: 5}});
  assert.equal(await past(), p0 + 1, 'click away: not exactly one undo step');
  await editTitle(page);
  await page.keyboard.press('End');
  await page.keyboard.type(' slide', {delay: 30});
  await page.getByRole('button', {name: 'Slide 2', exact: true}).click();
  await page.waitForTimeout(2000);
  assert.equal(await past(), p0 + 2, 'slide switch: not exactly one undo step');
  await openDeck(page);
  assert.equal(await titleBox(page).innerText(), 'First slide');
});

await check('table cell: type, wait 2 s, stored; reload shows it; one Undo after leaving', async (page) => {
  await blankDeck(page);
  await addTable(page);
  const cell = await editCell(page, 1, 1);
  await page.keyboard.type('42 units', {delay: 30});
  await page.waitForTimeout(2000);
  assert.equal(await cell.innerText(), '42 units', 'caret moved or text doubled while typing');
  assert.equal(await status(page), 'Saved on this device');
  assert.ok((await stored(page)).includes('42 units'), 'stored deck lacks the cell text');
  const past = () => page.evaluate(async () => (await import('/src/store/editor.ts')).useEditor.getState().past.length);
  const p0 = await past();
  await page.locator('.stage').click({position: {x: 5, y: 5}});
  await page.keyboard.press('Control+z');
  assert.equal(await past(), p0 - 1);
  const rows = await page.evaluate(async () => (await import('/src/store/editor.ts')).useEditor.getState().deck.slides[0].elements.find((e) => e.type === 'table').rows);
  assert.deepEqual(rows, [['Heading 1', 'Heading 2'], ['', '']]);
  await page.keyboard.press('Control+Shift+z');
  await page.waitForTimeout(2000);
  await openDeck(page);
  assert.equal(await page.locator('.stage .el-table tr').nth(1).locator('td').nth(1).innerText(), '42 units');
});

await check('table cell: closing the tab right after typing keeps the text', async (page, ctx) => {
  await blankDeck(page);
  await addTable(page);
  await editCell(page, 1, 0);
  await page.keyboard.type('East', {delay: 30});
  const url = page.url();
  await page.close({runBeforeUnload: true});
  // Reopening within milliseconds can read storage before the closed tab's last write lands.
  await new Promise((r) => setTimeout(r, 1000));
  const again = await ctx.newPage();
  await openDeck(again, url);
  assert.equal(await again.locator('.stage .el-table tr').nth(1).locator('td').nth(0).innerText(), 'East');
});

await check('speaker notes: typed notes survive a reload', async (page) => {
  await blankDeck(page);
  await page.getByRole('button', {name: /Speaker notes/}).click();
  await page.locator('.notes-text').click();
  await page.keyboard.type('Mention the board', {delay: 30});
  await page.waitForTimeout(2000);
  await openDeck(page);
  await page.getByRole('button', {name: /Speaker notes/}).click().catch(() => {});
  assert.equal(await page.locator('.notes-text').inputValue(), 'Mention the board');
});

await browser.close();
console.log(results.join('\n'));
if (results.some((r) => r.startsWith('FAIL'))) process.exit(1);
