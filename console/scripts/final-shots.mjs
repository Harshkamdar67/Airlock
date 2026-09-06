// Final, deterministic screenshot set for the evidence folder. The fixed clock
// matches the fixtures, so relative times say 3m, 7s, and 31m every run.
import { spawnSync } from 'node:child_process';
import { writeFileSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const origin = process.argv[2] ?? 'http://localhost:4788';
const now = '2026-09-05T09:02:12Z';
const states = [
  ['blocked', '', 900],
  ['running', '?fixture=one', 900],
  ['empty', '?fixture=empty', 700],
  ['reconnecting', '?fixture=reconnecting', 1700],
  // Phase 2.
  ['proposal-pending', '?fixture=proposal&reset=1', 1200],
  ['proposal-applied', '?fixture=applied&reset=1', 1200],
  ['proposal-conflict', '?fixture=conflict&reset=1', 1200],
  ['proposal-states', '?fixture=states&reset=1', 1200],
  ['proposal-metered-guard', '?fixture=metered&reset=1', 1200],
  ['router-without-metadata', '?fixture=bare&reset=1', 1200],
];
const jobs = [];
for (const [state, query, wait] of states) {
  for (const theme of ['light', 'dark']) {
    jobs.push({
      url: `${origin}/${query}`,
      out: `design/screenshots/final/${state}-${theme}-1440.png`,
      width: 1440,
      height: 900,
      colorScheme: theme,
      now,
      wait,
    });
  }
}
// The chain editor lives in the inspector, so it is captured scrolled to it.
for (const theme of ['light', 'dark']) {
  jobs.push({
    url: `${origin}/?fixture=chains&reset=1`,
    out: `design/screenshots/final/chain-editor-${theme}-1440.png`,
    width: 1440,
    height: 900,
    colorScheme: theme,
    now,
    wait: 1500,
    scroll: { selector: '.inspector', top: 620 },
  });
}

for (const theme of ['light', 'dark']) {
  jobs.push({
    url: `${origin}/`,
    out: `design/screenshots/final/blocked-${theme}-1024.png`,
    width: 1024,
    height: 800,
    colorScheme: theme,
    now,
    wait: 900,
  });
}

const file = resolve(ROOT, 'scripts', '.final-shots.json');
writeFileSync(file, JSON.stringify(jobs, null, 2));
const run = spawnSync(process.execPath, ['scripts/shoot.mjs', file], {
  cwd: ROOT,
  stdio: 'inherit',
});
rmSync(file, { force: true });
process.exit(run.status ?? 1);
