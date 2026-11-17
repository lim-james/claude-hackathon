import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// The DejaVu and Latin Modern faces load as separate files, so the stylesheet that blocks first paint stays small.
const cssUrl = new URL('../src/styles/extra-fonts.css', import.meta.url);
const css = readFileSync(cssUrl, 'utf8');

test('extra fonts are separate woff files, not data URLs', () => {
  assert.doesNotMatch(css, /url\(data:/);
  const faces = css.match(/@font-face\{[^}]*\}/g) ?? [];
  assert.equal(faces.length, 10);
  for (const face of faces) {
    const [, file] = face.match(/src:url\((\.\/fonts\/[a-z-]+-\d00\.woff)\) format\("woff"\)/) ?? [];
    assert.ok(file, face);
    const bytes = readFileSync(new URL(file, cssUrl));
    assert.equal(bytes.subarray(0, 4).toString('latin1'), 'wOFF', file);
  }
});

test('the offline web app precaches the extra font files', () => {
  const config = readFileSync(new URL('../vite.config.ts', import.meta.url), 'utf8');
  assert.match(config, /'assets\/\{dejavu,latin-modern\}-\*\.woff'/);
  const files = css.match(/\.\/fonts\/[a-z0-9-]+\.woff/g) ?? [];
  assert.equal(files.length, 10);
  for (const file of files) assert.match(file, /^\.\/fonts\/(dejavu|latin-modern)-/);
});

test('the desktop build gives font files root-absolute URLs, so exports can embed them', async () => {
  // html-to-image resolves relative stylesheet URLs through <base>, which the desktop CSP (base-uri 'none') blocks.
  process.env.DECKFORGE_DESKTOP = '1';
  const { default: config } = await import('../vite.config.ts');
  const render = config.experimental.renderBuiltUrl;
  assert.equal(render('assets/dejavu-serif-400-abc.woff', { hostType: 'css' }), '/assets/dejavu-serif-400-abc.woff');
  assert.equal(render('assets/lora-latin-400-normal-abc.woff2', { hostType: 'css' }), '/assets/lora-latin-400-normal-abc.woff2');
  assert.deepEqual(render('assets/index-abc.js', { hostType: 'html' }), { relative: true });
});
