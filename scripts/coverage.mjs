// Measure line coverage of OpenPDF, OpenSlides and the launcher, and fail when an
// area falls below its floor in scripts/coverage-floor.json.
//
//   node scripts/coverage.mjs            measure, print one line per area, check the floors
//   node scripts/coverage.mjs --update   measure and write the numbers as the new floors
//
// A source file that no test loads counts as 0% covered. Node's own report leaves
// such files out, which would let untested code raise the percentage.
// Needs coverage.py for OpenPDF (`python -m pip install coverage`); it is a
// development tool only and no app depends on it.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const floorFile = path.join(root, 'scripts', 'coverage-floor.json');

/** lcov text -> Map of absolute file path -> [lines hit, lines found]. */
export function parseLcov(text, baseDir) {
  const files = new Map();
  let current = null;
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith('SF:')) {
      current = [0, 0];
      files.set(path.resolve(baseDir, line.slice(3)), current);
    } else if (current && line.startsWith('LH:')) current[0] = Number(line.slice(3));
    else if (current && line.startsWith('LF:')) current[1] = Number(line.slice(3));
  }
  return files;
}

/** coverage.py JSON report -> the same Map. */
export function parseCoveragePy(report, baseDir) {
  const files = new Map();
  for (const [file, data] of Object.entries(report.files)) {
    files.set(path.resolve(baseDir, file), [data.summary.covered_lines, data.summary.num_statements]);
  }
  return files;
}

/**
 * Totals for one area. `files` are the area's source files; `measured` is what the
 * test run reported. A file missing from `measured` was never loaded: all of its
 * non-blank lines count as uncovered.
 */
export function summarise(name, files, measured) {
  let covered = 0, lines = 0, loaded = 0;
  for (const file of files) {
    const hit = measured.get(path.resolve(file));
    if (hit) {
      covered += hit[0];
      lines += hit[1];
      loaded += 1;
    } else {
      lines += fs.readFileSync(file, 'utf8').split(/\r?\n/).filter((line) => line.trim()).length;
    }
  }
  const percent = lines ? Math.round((1000 * covered) / lines) / 10 : 0;
  return { name, covered, lines, percent, files: files.length, loaded };
}

/**
 * The areas that are below their floor, as messages. Floors are stored rounded to
 * one decimal, so the exact percentage may sit up to `tolerance` points under one.
 */
export function belowFloor(areas, floors, tolerance) {
  const failures = [];
  for (const area of areas) {
    const floor = floors[area.name];
    const exact = area.lines ? (100 * area.covered) / area.lines : 0;
    if (floor === undefined) failures.push(`${area.name}: no floor in coverage-floor.json`);
    else if (exact < floor - tolerance) {
      failures.push(`${area.name}: ${exact.toFixed(2)}% is below the floor of ${floor}% (tolerance ${tolerance})`);
    }
  }
  return failures;
}

function sources(dir, extensions, recursive = false) {
  return fs.readdirSync(path.join(root, dir), { recursive })
    .map((entry) => path.join(root, dir, entry))
    .filter((file) => extensions.includes(path.extname(file)) && !/[\\/]tests[\\/]|\.test\./.test(file))
    .sort();
}

function run(label, command, args, cwd, env = {}) {
  const result = spawnSync(command, args, { cwd, env: { ...process.env, ...env }, encoding: 'utf8' });
  if (result.status !== 0) {
    const output = `${result.stdout || ''}${result.stderr || ''}`.trim().split('\n').slice(-30).join('\n');
    throw new Error(`${label} failed (${result.error?.message || `exit ${result.status}`}):\n${output}`);
  }
}

function nodeSuite(label, cwd, pattern, lcovFile) {
  run(label, process.execPath, ['--test', '--experimental-test-coverage', '--test-reporter=lcov',
    `--test-reporter-destination=${lcovFile}`, pattern], cwd);
  return parseLcov(fs.readFileSync(lcovFile, 'utf8'), cwd);
}

function measure() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'opensuite-coverage-'));
  try {
    const pdfDir = path.join(root, 'source-code', 'OpenPDF');
    const slidesDir = path.join(root, 'source-code', 'OpenSlides');
    const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
    const env = { COVERAGE_FILE: path.join(tmp, 'pdf.coverage') };
    const pdfJson = path.join(tmp, 'pdf.json');
    run('OpenPDF tests', python, ['-m', 'coverage', 'run', '--source=.', '--omit=tests/*',
      '-m', 'unittest', 'discover', '-s', 'tests'], pdfDir, env);
    run('OpenPDF coverage report', python, ['-m', 'coverage', 'json', '-q', '-o', pdfJson], pdfDir, env);
    const pdf = parseCoveragePy(JSON.parse(fs.readFileSync(pdfJson, 'utf8')), pdfDir);
    const slides = nodeSuite('OpenSlides tests', slidesDir, 'tests/*.test.mjs', path.join(tmp, 'slides.lcov'));
    const launcher = nodeSuite('Launcher tests', root, 'tests/*.test.cjs', path.join(tmp, 'root.lcov'));
    return [
      summarise('OpenPDF', sources('source-code/OpenPDF', ['.py']), pdf),
      summarise('OpenSlides', sources('source-code/OpenSlides/src', ['.ts', '.tsx'], true), slides),
      summarise('Launcher', [...sources('source-code/main/desktop', ['.cjs']), ...sources('source-code/main/web', ['.js'])], launcher),
    ];
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

function main() {
  const config = JSON.parse(fs.readFileSync(floorFile, 'utf8'));
  const areas = measure();
  for (const area of areas) {
    console.log(`${area.name.padEnd(11)}${area.percent.toFixed(1).padStart(5)}%  ${area.covered}/${area.lines} lines, `
      + `${area.loaded}/${area.files} files loaded by tests  (floor ${config.floors[area.name] ?? 'none'}%)`);
  }
  if (process.argv.includes('--update')) {
    config.floors = Object.fromEntries(areas.map((area) => [area.name, area.percent]));
    fs.writeFileSync(floorFile, `${JSON.stringify(config, null, 2)}\n`);
    console.log('Floors updated in scripts/coverage-floor.json');
    return;
  }
  const failures = belowFloor(areas, config.floors, config.tolerance);
  if (failures.length) {
    console.error(`Coverage fell below its floor:\n  ${failures.join('\n  ')}\nAdd tests for the code this change adds.`);
    process.exit(1);
  }
  console.log(`Coverage is at or above every floor (tolerance ${config.tolerance} points).`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    main();
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}
