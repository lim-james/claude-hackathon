// Lets `node --test` import the app's browser-side sources without a build step.
// Node strips types from TypeScript on its own; this adds the two things it does not do:
//  - resolve the bundler-style extensionless imports used in src/;
//  - compile JSX in .tsx files, with the transform Vite already installs (rolldown).
// Only the JSX expressions are rewritten, each padded to the lines it occupied, so every
// other line stays where it is and coverage and stack traces point at the real source.
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { minifySync, parseSync, transformSync } from 'rolldown/utils';

const SRC = new URL('../../src/', import.meta.url).href;
// DOMPurify needs a real browser window, which Node does not have.
const STUBS = { dompurify: new URL('./dompurify-stub.mjs', import.meta.url).href };
const JSX_RUNTIME = 'import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";';
const lines = (s) => s.split('\n').length;

function outermostJsx(node, found = []) {
  if (!node || typeof node !== 'object') return found;
  if (node.type === 'JSXElement' || node.type === 'JSXFragment') found.push(node);
  else for (const value of Object.values(node)) outermostJsx(value, found);
  return found;
}

export function compileJsx(file, source) {
  const parsed = parseSync(file, source);
  if (parsed.errors.length) throw new Error(`${file}: ${parsed.errors[0].message}`);
  let out = source;
  for (const node of outermostJsx(parsed.program).sort((a, b) => b.start - a.start)) {
    const jsx = source.slice(node.start, node.end);
    const compiled = transformSync(file, `export default (${jsx});`, { jsx: { runtime: 'automatic' } });
    if (compiled.errors.length) throw new Error(`${file}: ${compiled.errors[0].message}`);
    const call = minifySync(file, compiled.code.replace(/^import [^\n]*react\/jsx-runtime[^\n]*\n/gm, ''), { compress: false, mangle: false }).code.trim().replace(/^export default\s*/, '').replace(/;$/, '');
    const pad = lines(jsx) - lines(call);
    if (pad < 0) throw new Error(`${file}: compiled JSX at offset ${node.start} needs more lines than its source`);
    out = out.slice(0, node.start) + `(${call})` + '\n'.repeat(pad) + out.slice(node.end);
  }
  return `${out}\n${JSX_RUNTIME}\n`;
}

registerHooks({
  resolve(specifier, context, next) {
    if (STUBS[specifier]) return { url: STUBS[specifier], shortCircuit: true };
    if (/^\.\.?\//.test(specifier) && context.parentURL?.startsWith(SRC) && !/\.[cm]?[jt]sx?$/.test(specifier)) {
      const base = fileURLToPath(new URL(specifier, context.parentURL));
      const hit = ['.ts', '.tsx', '/index.ts'].map((ext) => base + ext).find(existsSync);
      if (hit) return { url: pathToFileURL(hit).href, shortCircuit: true };
    }
    return next(specifier, context);
  },
  load(url, context, next) {
    if (!url.startsWith(SRC) || !url.endsWith('.tsx')) return next(url, context);
    const file = fileURLToPath(url);
    return { format: 'module-typescript', shortCircuit: true, source: compileJsx(file, readFileSync(file, 'utf8')) };
  },
});
