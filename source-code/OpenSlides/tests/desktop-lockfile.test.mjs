// The desktop packaging tools (Electron, electron-builder) must install from a
// committed lockfile, so two builds of one commit package with the same tools.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));

test('desktop:install installs from the lockfile', () => {
  assert.equal(read('../package.json').scripts['desktop:install'], 'npm ci --prefix desktop');
});

test('desktop lockfile matches desktop/package.json', () => {
  const manifest = read('../desktop/package.json');
  const lock = read('../desktop/package-lock.json');
  assert.deepEqual(lock.packages[''].devDependencies, manifest.devDependencies);
  for (const [name, version] of Object.entries(manifest.devDependencies)) {
    assert.equal(lock.packages[`node_modules/${name}`].version, version, name);
  }
});

test('every locked desktop package carries an integrity hash or a pinned commit', () => {
  const lock = read('../desktop/package-lock.json');
  const loose = Object.entries(lock.packages)
    .filter(([path, entry]) => path && !entry.integrity && !/#[0-9a-f]{40}$/.test(entry.resolved ?? ''))
    .map(([path]) => path);
  assert.deepEqual(loose, []);
});
