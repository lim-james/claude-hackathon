import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';

// The store imports its siblings without a file extension (the bundler resolves
// them); teach node the same so the real store can be loaded here.
registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context);
    } catch (err) {
      if (!specifier.startsWith('.')) throw err;
      return nextResolve(`${specifier}.ts`, context);
    }
  },
});

const { changeLayout, editElements } = await import('../src/store/actions.ts');
const { openDeck, undo, useEditor } = await import('../src/store/editor.ts');
const { createFromTemplate } = await import('../src/model/templates.ts');

const deckNow = () => useEditor.getState().deck;
const slideNow = () => deckNow().slides[0];
const layoutNow = () => deckNow().layouts.find((l) => l.id === slideNow().layoutId);
const kindOf = (el) => layoutNow().elements.find((l) => l.id === el.ph?.ref)?.placeholder;
const byKind = (kind) => slideNow().elements.find((e) => kindOf(e) === kind);
const setText = (kind, html) => editElements([byKind(kind).id], (el) => { el.html = html; });

/** Opens the "Blank deck" template: one slide on the Title layout. */
function blankDeck() {
  openDeck(createFromTemplate('blank'));
  assert.equal(slideNow().layoutId, 'title');
  return slideNow().id;
}

test('changeLayout switches a new slide to every layout', () => {
  const id = blankDeck();
  const layouts = deckNow().layouts;
  assert.ok(layouts.some((l) => l.id === 'blank') && layouts.length > 5);
  for (const layout of [...layouts, layouts[0]]) {
    changeLayout(id, layout.id);
    assert.equal(slideNow().layoutId, layout.id, layout.name);
    const placeholders = layout.elements.filter((l) => l.placeholder && (l.type === 'text' || l.type === 'image'));
    assert.deepEqual(slideNow().elements.map((e) => e.ph?.ref), placeholders.map((l) => l.id), layout.name);
  }
});

test('changeLayout keeps the title and carries body text across', () => {
  const id = blankDeck();
  setText('title', '<p>Quarterly review</p>');
  changeLayout(id, 'title-content');
  assert.equal(byKind('title').html, '<p>Quarterly review</p>');
  setText('body', '<p>Revenue is up</p>');
  changeLayout(id, 'two-content');
  assert.equal(byKind('title').html, '<p>Quarterly review</p>');
  assert.equal(byKind('body').html, '<p>Revenue is up</p>');
});

test('changeLayout keeps text that has no matching placeholder as a free text box', () => {
  const id = blankDeck();
  setText('title', '<p>Quarterly review</p>');
  setText('subtitle', '<p>Finance team</p>');
  changeLayout(id, 'title-only');
  assert.equal(byKind('title').html, '<p>Quarterly review</p>');
  const loose = slideNow().elements.filter((e) => !e.ph);
  assert.deepEqual(loose.map((e) => e.html), ['<p>Finance team</p>']);
  changeLayout(id, 'blank');
  assert.deepEqual(slideNow().elements.map((e) => e.html).sort(), ['<p>Finance team</p>', '<p>Quarterly review</p>']);
  assert.ok(slideNow().elements.every((e) => !e.ph));
});

test('one undo restores the old layout and its text', () => {
  const id = blankDeck();
  setText('title', '<p>Quarterly review</p>');
  const before = deckNow();
  changeLayout(id, 'two-content');
  assert.equal(slideNow().layoutId, 'two-content');
  undo();
  assert.equal(deckNow(), before);
  assert.equal(slideNow().layoutId, 'title');
  assert.equal(byKind('title').html, '<p>Quarterly review</p>');
});

test('changeLayout works on every slide of a template deck', () => {
  openDeck(createFromTemplate('pitch'));
  for (const slide of deckNow().slides) {
    const count = slide.elements.filter((e) => !e.ph).length;
    changeLayout(slide.id, 'title-content');
    const after = deckNow().slides.find((s) => s.id === slide.id);
    assert.equal(after.layoutId, 'title-content');
    assert.ok(after.elements.filter((e) => !e.ph).length >= count, 'free elements are kept');
  }
});
