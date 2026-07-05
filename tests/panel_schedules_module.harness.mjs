/**
 * Regression harness: panel schedule modules must load without ES-module
 * binding shadow errors (e.g. importing and re-exporting patchStateSchedule).
 *
 * Run: node tests/panel_schedules_module.harness.mjs
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const JS_ROOT = join(ROOT, 'custom_components/heating_assistant/www/js');
const SCHEDULES_PAGE = join(JS_ROOT, 'pages/schedules.js');
const SCHEDULES_DETAIL = join(JS_ROOT, 'schedules/schedules-detail.js');
const SCHEDULES_SHARED = join(JS_ROOT, 'schedules/schedules-shared.js');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function walkJsFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === 'vendor') continue;
      files.push(...walkJsFiles(full));
    } else if (entry.endsWith('.js') && entry !== 'panel-cache-bust.js') {
      files.push(full);
    }
  }
  return files;
}

function parseNamedImports(source) {
  const imports = new Set();
  const importRe = /import\s*(?:type\s*)?\{([^}]+)\}\s*from\s*['"][^'"]+['"]/g;
  let match;
  while ((match = importRe.exec(source)) !== null) {
    for (const part of match[1].split(',')) {
      const trimmed = part.trim();
      if (!trimmed) continue;
      const name = trimmed.split(/\s+as\s+/).pop().trim();
      if (name) imports.add(name);
    }
  }
  return imports;
}

function parseExportedBindings(source) {
  const names = new Set();
  const patterns = [
    /export\s+function\s+(\w+)/g,
    /export\s+const\s+(\w+)/g,
    /export\s+class\s+(\w+)/g,
    /export\s+async\s+function\s+(\w+)/g,
    /export\s*\{\s*([^}]+)\s*\}/g,
  ];
  for (const re of patterns) {
    let match;
    while ((match = re.exec(source)) !== null) {
      if (re.source.startsWith('export\\s*\\{')) {
        for (const part of match[1].split(',')) {
          const trimmed = part.trim();
          if (!trimmed) continue;
          const name = trimmed.split(/\s+as\s+/)[0].trim();
          if (name) names.add(name);
        }
      } else {
        names.add(match[1]);
      }
    }
  }
  return names;
}

function scanForImportExportShadowing() {
  const offenders = [];
  for (const file of walkJsFiles(JS_ROOT)) {
    const rel = file.slice(ROOT.length + 1);
    const source = readFileSync(file, 'utf8');
    const imports = parseNamedImports(source);
    const exports = parseExportedBindings(source);
    for (const name of imports) {
      if (exports.has(name)) {
        offenders.push(`${rel}: '${name}' is both imported and exported`);
      }
    }
  }
  assert(
    offenders.length === 0,
    `Import/export shadowing detected:\n${offenders.join('\n')}`,
  );
}

function scanSchedulesDetailDoesNotExportPatchStateSchedule() {
  const source = readFileSync(SCHEDULES_DETAIL, 'utf8');
  assert(
    !/export\s+function\s+patchStateSchedule/.test(source),
    'schedules-detail.js must not export patchStateSchedule (use schedules-shared.js)',
  );
  assert(
    /import\s*\{[^}]*\bpatchStateSchedule\b[^}]*\}\s*from\s*['"]\.\/schedules-shared\.js/.test(source),
    'schedules-detail.js must import patchStateSchedule from schedules-shared.js',
  );
  const shared = readFileSync(SCHEDULES_SHARED, 'utf8');
  assert(
    /export\s+function\s+patchStateSchedule/.test(shared),
    'schedules-shared.js must export patchStateSchedule',
  );
}

async function loadSchedulesModuleChain() {
  const pageUrl = `${pathToFileURL(SCHEDULES_PAGE).href}?v=94`;
  const mod = await import(pageUrl);
  assert(typeof mod.renderSchedules === 'function', 'pages/schedules.js must export renderSchedules');
  const detailUrl = `${pathToFileURL(SCHEDULES_DETAIL).href}?v=94`;
  const detailMod = await import(detailUrl);
  assert(
    typeof detailMod.renderScheduleDetail === 'function',
    'schedules-detail.js must export renderScheduleDetail',
  );
  assert(
    detailMod.patchStateSchedule === undefined,
    'schedules-detail.js must not export patchStateSchedule',
  );
}

scanForImportExportShadowing();
scanSchedulesDetailDoesNotExportPatchStateSchedule();
await loadSchedulesModuleChain();
console.log('panel schedules module harness: ok');
