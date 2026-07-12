/**
 * Syntax/load gate over ALL non-vendor panel JS files.
 *
 * Every file under custom_components/heating_assistant/www (excluding the
 * vendored third-party bundles in www/vendor) is parsed with `node --check`.
 * Node 22 auto-detects ES-module syntax for .js files; if a direct check
 * still misfires the file is re-checked as an explicit ES module via stdin.
 * Nothing is executed — this is a pure parse gate so a stray syntax error in
 * any of the ~44 panel files fails CI even when no behaviour harness loads it.
 *
 * Run: node tests/panel_syntax.harness.mjs
 */
import { spawn } from 'node:child_process';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WWW = join(ROOT, 'custom_components/heating_assistant/www');
const EXCLUDE_DIRS = new Set(['vendor']);

function collectJsFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!EXCLUDE_DIRS.has(entry.name)) out.push(...collectJsFiles(full));
    } else if (entry.name.endsWith('.js')) {
      out.push(full);
    }
  }
  return out.sort();
}

function run(args, input) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, args, { stdio: ['pipe', 'ignore', 'pipe'] });
    let stderr = '';
    child.stderr.on('data', (d) => { stderr += d; });
    child.on('close', (code) => resolve({ code, stderr }));
    if (input !== undefined) child.stdin.write(input);
    child.stdin.end();
  });
}

async function checkFile(file) {
  const direct = await run(['--check', file]);
  if (direct.code === 0) return { file, ok: true };
  // Fallback: force ES-module parse goal (covers Node versions/configs where
  // .js is checked as CommonJS and `import`/`export` misfire).
  const asModule = await run(['--check', '--input-type=module'], readFileSync(file, 'utf8'));
  if (asModule.code === 0) return { file, ok: true };
  return { file, ok: false, error: direct.stderr.trim() || asModule.stderr.trim() };
}

const files = collectJsFiles(WWW);
if (files.length < 40) {
  console.error(`FAIL: expected at least 40 non-vendor panel JS files, found ${files.length}`);
  process.exit(1);
}

const POOL = 8;
const results = [];
let next = 0;
await Promise.all(
  Array.from({ length: POOL }, async () => {
    while (next < files.length) {
      const file = files[next++];
      results.push(await checkFile(file));
    }
  }),
);
results.sort((a, b) => a.file.localeCompare(b.file));

const failures = [];
for (const r of results) {
  const rel = relative(WWW, r.file);
  if (r.ok) {
    console.log(`OK   ${rel}`);
  } else {
    console.log(`FAIL ${rel}`);
    failures.push(r);
  }
}

if (failures.length > 0) {
  console.error(`\npanel syntax harness: ${failures.length}/${results.length} file(s) failed to parse:`);
  for (const f of failures) {
    console.error(`--- ${relative(WWW, f.file)}\n${f.error}\n`);
  }
  process.exit(1);
}
console.log(`panel syntax harness: ok (${results.length} files parsed)`);
