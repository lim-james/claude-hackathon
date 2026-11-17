// #136: focusing a field or pressing a slider without changing anything keeps undo and redo.
// Run against a dev server: OPENSLIDES_URL=<url> PLAYWRIGHT_PACKAGE_JSON=<playwright>/package.json node scripts/history-focus-smoke.mjs
import {createRequire} from 'node:module';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
const {chromium} = createRequire(resolve(process.env.PLAYWRIGHT_PACKAGE_JSON || '.qa/package.json'))('playwright');
const URL = process.env.OPENSLIDES_URL || 'http://127.0.0.1:5173';

const browser = await chromium.launch({headless: true});
const results = [];
async function check(name, fn) {
  const ctx = await browser.newContext({viewport: {width: 1500, height: 960}});
  const page = await ctx.newPage();
  try {
    await fn(page);
    results.push(`PASS ${name}`);
  } catch (e) {
    results.push(`FAIL ${name}: ${e.message.split('\n')[0]}`);
  }
  await ctx.close();
}

const state = (page) => page.evaluate(async () => {
  const s = (await import('/src/store/editor.ts')).useEditor.getState();
  return {
    hearts: s.deck.slides[0].elements.filter((e) => e.name === 'Heart').length,
    notes: s.deck.slides.map((x) => x.notes),
    opacity: s.deck.slides[0].elements[0]?.opacity,
    past: s.past.length,
    future: s.future.length,
  };
});
const away = (page) => page.locator('.stage').click({position: {x: 5, y: 5}});
async function heartThenUndo(page) {
  await page.goto(URL);
  await page.getByRole('button', {name: 'New blank deck', exact: true}).click();
  await page.getByRole('button', {name: 'Add Heart', exact: true}).click();
  await away(page);
  await page.keyboard.press('Control+z');
  assert.deepEqual([(await state(page)).hearts, (await state(page)).future], [0, 1]);
}
async function openNotes(page) {
  await page.getByRole('button', {name: /Speaker notes/}).click();
  return page.locator('.notes-text');
}

await check('issue steps: click into notes, type nothing, Redo brings the heart back', async (page) => {
  await heartThenUndo(page);
  const notes = await openNotes(page);
  await notes.click();
  await away(page);
  const mid = await state(page);
  assert.deepEqual([mid.past, mid.future], [0, 1], `click added an undo step or cleared redo: ${JSON.stringify(mid)}`);
  await page.keyboard.press('Control+Shift+z');
  assert.equal((await state(page)).hearts, 1);
});

await check('typing notes then one Ctrl+Z removes the typing, as before', async (page) => {
  await heartThenUndo(page);
  const notes = await openNotes(page);
  await notes.click();
  await page.keyboard.type('Say hi', {delay: 20});
  await away(page);
  const s = await state(page);
  assert.deepEqual([s.notes[0], s.past, s.future], ['Say hi', 1, 0]);
  await page.keyboard.press('Control+z');
  assert.equal((await state(page)).notes[0], '');
});

await check('notes on two slides are two undo steps', async (page) => {
  await page.goto(URL);
  await page.getByRole('button', {name: 'New blank deck', exact: true}).click();
  await page.evaluate(async () => {
    const a = await import('/src/store/actions.ts');
    const {useEditor} = await import('/src/store/editor.ts');
    a.addSlide(useEditor.getState().deck.slides[0].layoutId);
    a.goToSlide(useEditor.getState().deck.slides[0].id);
  });
  const p0 = (await state(page)).past;
  const notes = await openNotes(page);
  await notes.click();
  await page.keyboard.type('one', {delay: 20});
  await page.getByRole('button', {name: 'Slide 2', exact: true}).click();
  await notes.click();
  await page.keyboard.type('two', {delay: 20});
  await away(page);
  assert.deepEqual((await state(page)).notes, ['one', 'two']);
  assert.equal((await state(page)).past, p0 + 2);
  await page.keyboard.press('Control+z');
  assert.deepEqual((await state(page)).notes, ['one', '']);
});

await check('Design panel: focusing a brand colour without changing it keeps redo', async (page) => {
  await heartThenUndo(page);
  await page.evaluate(async () => (await import('/src/store/editor.ts')).useEditor.setState({inspectorOpen: true, panel: 'design'}));
  await page.locator('.brand-color input[type=color]').first().focus();
  await page.locator('.brand-color input[type=color]').first().blur();
  const s = await state(page);
  assert.deepEqual([s.past, s.future], [0, 1]);
});

await check('opacity slider: a press without moving adds no undo step; a drag is one', async (page) => {
  await page.goto(URL);
  await page.getByRole('button', {name: 'New blank deck', exact: true}).click();
  await page.getByRole('button', {name: 'Add Heart', exact: true}).click();
  await page.evaluate(async () => {
    const {useEditor} = await import('/src/store/editor.ts');
    const s = useEditor.getState();
    useEditor.setState({selection: [s.deck.slides[0].elements[0].id], inspectorOpen: true, panel: 'selection'});
  });
  const slider = page.locator('.field', {hasText: 'Opacity'}).locator('input[type=range]');
  const before = await state(page);
  await slider.dispatchEvent('pointerdown');
  await slider.dispatchEvent('pointerup');
  const pressed = await state(page);
  assert.deepEqual([pressed.past, pressed.future], [before.past, before.future], 'press changed history');
  const box = await slider.boundingBox();
  await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height / 2, {steps: 5});
  await page.mouse.up();
  const dragged = await state(page);
  assert.ok(dragged.opacity < 0.9, `opacity ${dragged.opacity}`);
  assert.deepEqual([dragged.past, dragged.future], [before.past + 1, 0]);
});

await browser.close();
console.log(results.join('\n'));
if (results.some((r) => r.startsWith('FAIL'))) process.exit(1);
