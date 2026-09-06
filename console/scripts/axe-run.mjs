// Runs axe-core against the built page in both themes and every fixture state.
// Fails on any serious or critical violation.
//
//   node scripts/axe-run.mjs http://localhost:4788
import { launch } from 'puppeteer-core';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const AXE = readFileSync(require.resolve('axe-core'), 'utf8');
const CHROME =
  process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const origin = process.argv[2] ?? 'http://localhost:4788';
const CASES = [
  ['blocked', ''],
  ['running', '?fixture=one'],
  ['loading', '?fixture=loading'],
  ['detail error', '?fixture=detail-error'],
  ['empty', '?fixture=empty'],
  ['twelve', '?fixture=twelve'],
  ['ended', '?fixture=ended'],
  ['ready', '?fixture=ready'],
  ['unknown', '?fixture=unknown'],
  ['all event kinds', '?fixture=allkinds'],
  ['reconnecting', '?fixture=reconnecting'],
  // Phase 2 surfaces.
  ['pending proposal', '?fixture=proposal&reset=1'],
  ['applied proposal', '?fixture=applied&reset=1'],
  ['conflicted proposal', '?fixture=conflict&reset=1'],
  ['every proposal status', '?fixture=states&reset=1'],
  ['chain editor', '?fixture=chains&reset=1'],
  ['pinned session', '?fixture=pinned&reset=1'],
  ['uncontrollable session', '?fixture=uncontrollable&reset=1'],
  ['metered guard', '?fixture=metered&reset=1'],
  ['router without metadata', '?fixture=bare&reset=1'],
  // History and usage pages, on synthesized transcripts.
  ['history', '?fixture=one#history'],
  ['usage', '?fixture=one#usage'],
];
const OVERLAYS = [
  ['command palette', 'Control+k'],
  ['shortcut sheet', '?'],
];

const browser = await launch({
  executablePath: CHROME,
  headless: 'shell',
  args: ['--force-color-profile=srgb'],
});

let serious = 0;
let total = 0;
const rows = [];

async function audit(page, name) {
  const result = await page.evaluate(async () => {
    // eslint-disable-next-line no-undef
    return await window.axe.run(document, {
      resultTypes: ['violations'],
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'] },
    });
  });
  const bad = result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const minor = result.violations.filter((v) => v.impact !== 'serious' && v.impact !== 'critical');
  serious += bad.length;
  total += result.violations.length;
  rows.push(
    `${name}: ${bad.length} serious or critical, ${minor.length} moderate or minor` +
      (result.violations.length
        ? `\n    ${result.violations
            .map((v) => `${v.impact} ${v.id} (${v.nodes.length}) ${v.help}`)
            .join('\n    ')}`
        : ''),
  );
}

try {
  for (const theme of ['light', 'dark']) {
    for (const [name, query] of CASES) {
      const page = await browser.newPage();
      await page.setViewport({ width: 1440, height: 900 });
      await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: theme }]);
      await page.goto(`${origin}/${query}`, { waitUntil: 'domcontentloaded' });
      await new Promise((r) =>
        setTimeout(r, name === 'reconnecting' ? 2600 : name === 'loading' ? 300 : 900),
      );
      await page.evaluate(AXE);
      await audit(page, `${theme} ${name}`);
      if (name === 'blocked') {
        for (const [overlay, key] of OVERLAYS) {
          await page.keyboard.press(key.includes('+') ? 'Escape' : 'Escape');
          if (key === 'Control+k') {
            await page.keyboard.down('Control');
            await page.keyboard.press('k');
            await page.keyboard.up('Control');
          } else {
            await page.keyboard.press('?');
          }
          await new Promise((r) => setTimeout(r, 250));
          await audit(page, `${theme} ${overlay}`);
          await page.keyboard.press('Escape');
          await new Promise((r) => setTimeout(r, 150));
        }
      }
      await page.close();
    }
  }
} finally {
  await browser.close();
}

console.log(rows.join('\n'));
console.log(`\naxe-core: ${serious} serious or critical violations across ${rows.length} audits.`);
console.log(`axe-core: ${total} violations of any impact.`);
process.exit(serious ? 1 : 0);
