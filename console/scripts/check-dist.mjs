// Fails when the committed dist/ is not what a fresh build produces.
// Compares content hashes, not timestamps, so a rebuild that changes nothing
// passes.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DIST = join(ROOT, 'dist');
const TEMP = join(ROOT, '.dist-check');

function tree(dir) {
  const out = new Map();
  const walk = (d) => {
    for (const name of readdirSync(d)) {
      const full = join(d, name);
      if (statSync(full).isDirectory()) walk(full);
      else {
        out.set(
          relative(dir, full).replace(/\\/g, '/'),
          createHash('sha256').update(readFileSync(full)).digest('hex'),
        );
      }
    }
  };
  walk(dir);
  return out;
}

rmSync(TEMP, { recursive: true, force: true });
const build = spawnSync(
  process.execPath,
  [resolve(ROOT, 'node_modules', 'vite', 'bin', 'vite.js'), 'build', '--outDir', '.dist-check', '--emptyOutDir'],
  { cwd: ROOT, stdio: 'inherit' },
);
if (build.status !== 0) {
  console.error('check:dist: the build itself failed');
  process.exit(build.status ?? 1);
}

let committed;
try {
  committed = tree(DIST);
} catch {
  console.error('check:dist: dist/ is missing. Run npm run build and commit it.');
  rmSync(TEMP, { recursive: true, force: true });
  process.exit(1);
}
const fresh = tree(TEMP);
rmSync(TEMP, { recursive: true, force: true });

const problems = [];
for (const [file, hash] of fresh) {
  if (!committed.has(file)) problems.push(`missing from dist/: ${file}`);
  else if (committed.get(file) !== hash) problems.push(`differs from a fresh build: ${file}`);
}
for (const file of committed.keys()) {
  if (!fresh.has(file)) problems.push(`stale in dist/, no longer built: ${file}`);
}

if (problems.length) {
  console.error('check:dist failed:');
  for (const p of problems) console.error(`  ${p}`);
  console.error('\nRun npm run build and commit console/dist.');
  process.exit(1);
}
console.log(`check:dist: dist/ matches a fresh build, ${committed.size} files.`);
