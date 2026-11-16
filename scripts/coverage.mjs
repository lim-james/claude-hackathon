// Measure line coverage of OpenPDF, OpenSlides and the launcher, and fail when an
// area is worse than its floor in scripts/coverage-floor.json.
//
//   node scripts/coverage.mjs            measure, print one line per area, check the floors
//   node scripts/coverage.mjs --update   measure and write the numbers as the new floors
//   --floors <file>                      use this floor file instead
//
// A floor is a measurement, {"covered": n, "lines": n}. An area fails when it has
// fewer covered lines than its floor, or more lines that no test runs (lines minus
// covered). So a change that adds lines no test runs fails until it adds tests or
// updates the floor file in the same change, where a reviewer sees it.
// OpenPDF has two floors, with and without Tesseract (see pdfAreaName).
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

const isCount = (value) => Number.isInteger(value) && value >= 0;

/** What is wrong with the floors as stored, before anything is measured. */
export function badFloors(floors) {
  if (!floors || typeof floors !== 'object' || Array.isArray(floors)) {
    return ['coverage-floor.json has no "floors" object'];
  }
  return Object.entries(floors)
    .filter(([, floor]) => !floor || !isCount(floor.covered) || !isCount(floor.lines) || floor.lines === 0 || floor.covered > floor.lines)
    .map(([name, floor]) => `${name}: the floor must be {"covered": n, "lines": n} in whole numbers, got ${JSON.stringify(floor)}`);
}

/** OpenPDF's two floors. One run measures one of them, whichever matches the machine. */
export const pdfAreas = ['OpenPDF', 'OpenPDF without Tesseract'];

/**
 * The areas that are worse than their floor, as messages. A floor is the measurement
 * it was set from, `{ "covered": n, "lines": n }`, and two counts are checked: covered
 * lines must not fall, and lines no test runs (lines minus covered) must not rise.
 * A percentage would let untested lines in whenever covered lines arrive with them.
 * Anything else in place of a floor fails, so a damaged file cannot pass, and so
 * does a floor whose area was not measured, so an area cannot be dropped unseen.
 */
export function belowFloor(areas, floors) {
  const failures = badFloors(floors);
  if (failures.length) return failures;
  for (const area of areas) {
    const floor = floors[area.name];
    if (floor === undefined) {
      failures.push(`${area.name}: no floor in coverage-floor.json`);
      continue;
    }
    const untested = area.lines - area.covered;
    const allowed = floor.lines - floor.covered;
    if (area.covered < floor.covered) {
      failures.push(`${area.name}: ${area.covered} lines are covered, below the floor of ${floor.covered}`);
    }
    if (untested > allowed) {
      failures.push(`${area.name}: ${untested} lines are run by no test, above the floor of ${allowed}`);
    }
  }
  const measured = new Set(areas.map((area) => area.name));
  for (const name of Object.keys(floors)) {
    const otherPdfState = pdfAreas.includes(name) && pdfAreas.some((other) => measured.has(other));
    if (!measured.has(name) && !otherPdfState) failures.push(`${name}: has a floor but was not measured`);
  }
  return failures;
}

/** Whether `command` is on PATH, the same test OpenPDF's OCR test uses to skip itself. */
export function onPath(command, env = process.env, platform = process.platform) {
  const extensions = platform === 'win32' ? (env.PATHEXT || '.EXE;.CMD;.BAT').split(';') : [''];
  return (env.PATH || env.Path || '').split(path.delimiter).filter(Boolean)
    .some((dir) => extensions.some((ext) => fs.existsSync(path.join(dir, command + ext))));
}

/**
 * OpenPDF's OCR test needs Tesseract and skips itself without it, which covers fewer
 * lines. CI must have it and is held to the full floor; a machine without it is
 * held to a floor of its own, so a local run neither fails nor goes unchecked.
 */
export function pdfAreaName(hasTesseract, inCi) {
  if (hasTesseract) return 'OpenPDF';
  if (inCi) throw new Error('Tesseract is not on PATH. CI must run the OCR test, or the OpenPDF floor would be checked against a run that skipped it.');
  return 'OpenPDF without Tesseract';
}

/** GitHub Actions sets both variables; either one alone still means CI. */
export const inCi = (env = process.env) => Boolean(env.CI || env.GITHUB_ACTIONS);

/** The OpenPDF floor this machine is held to. */
export function pdfArea(env = process.env, platform = process.platform) {
  return pdfAreaName(onPath('tesseract', env, platform), inCi(env));
}

/** Several runs' reports as one: a file loaded by more than one keeps its best run. */
export function mostCovered(...reports) {
  const files = new Map();
  for (const report of reports) {
    for (const [file, hit] of report) {
      if (!files.has(file) || hit[0] > files.get(file)[0]) files.set(file, hit);
    }
  }
  return files;
}

function sources(dir, extensions, recursive = false) {
  return fs.readdirSync(path.join(root, dir), { recursive })
    .map((entry) => path.join(root, dir, entry))
    .filter((file) => extensions.includes(path.extname(file)) && !/[\\/]tests[\\/]|\.test\./.test(file))
    .sort();
}

export function run(label, command, args, cwd, env = {}) {
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
  const pdfName = pdfArea();
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
      summarise(pdfName, sources('source-code/OpenPDF', ['.py']), pdf),
      summarise('OpenSlides', sources('source-code/OpenSlides/src', ['.ts', '.tsx'], true), slides),
      // Both Node suites load files from OpenSlides/desktop.
      summarise('OpenSlides desktop', sources('source-code/OpenSlides/desktop', ['.cjs']), mostCovered(slides, launcher)),
      summarise('Launcher', [...sources('source-code/main/desktop', ['.cjs']), ...sources('source-code/main/web', ['.js'])], launcher),
    ];
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

const show = (floor) => (floor && isCount(floor.covered) && isCount(floor.lines) ? `${floor.covered}/${floor.lines}` : 'none');

/**
 * Run the check and return the exit status. `--floors <file>` reads another floor
 * file, and `options.measure` replaces the test run; the tests use both.
 */
export function main(argv = process.argv.slice(2), options = {}) {
  const named = argv.indexOf('--floors');
  const file = named < 0 ? floorFile : path.resolve(argv[named + 1] ?? '');
  const config = JSON.parse(fs.readFileSync(file, 'utf8'));
  const floors = config?.floors;
  const update = argv.includes('--update');
  const damaged = update ? [] : badFloors(floors);
  if (damaged.length) {
    console.error(`${path.basename(file)} is damaged:\n  ${damaged.join('\n  ')}`);
    return 1;
  }
  const areas = (options.measure || measure)();
  for (const area of areas) {
    console.log(`${area.name.padEnd(26)}${area.percent.toFixed(1).padStart(5)}%  ${area.covered}/${area.lines} lines, `
      + `${area.loaded}/${area.files} files loaded by tests  (floor ${show(floors?.[area.name])})`);
  }
  if (update) {
    // Only the areas this run measured are rewritten: the OpenPDF floor for the other
    // Tesseract state is kept, and has to be updated from a machine in that state.
    const updated = { ...(floors && typeof floors === 'object' ? floors : {}) };
    for (const area of areas) updated[area.name] = { covered: area.covered, lines: area.lines };
    fs.writeFileSync(file, `${JSON.stringify({ floors: updated }, null, 2)}\n`);
    console.log(`Floors updated in ${path.basename(file)} for: ${areas.map((area) => area.name).join(', ')}`);
    return 0;
  }
  const failures = belowFloor(areas, floors);
  if (failures.length) {
    console.error(`Coverage is worse than its floor:\n  ${failures.join('\n  ')}\n`
      + 'Add tests for the lines this change adds or stops covering. If the change is meant to leave them\n'
      + 'untested, run `node scripts/coverage.mjs --update` and commit scripts/coverage-floor.json with it.');
    return 1;
  }
  console.log('Every area is at or better than its floor.');
  return 0;
}

/** main() as a process: its status becomes the exit status, and any error is exit 1. */
export function cli(argv, options) {
  try {
    process.exitCode = main(argv, options);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) cli();
