// Screenshot driver for the console. Uses the local Chrome through puppeteer-core.
// node scripts/shoot.mjs <jobsfile.json>
// Each job: { url, out, width, height, colorScheme?, now?, scroll?, wait?, keys?, fullPage? }
import { launch } from 'puppeteer-core';
import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const CHROME = process.env.CHROME_PATH
  || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const jobs = JSON.parse(readFileSync(process.argv[2], 'utf8'));

const browser = await launch({
  executablePath: CHROME,
  headless: 'shell',
  args: ['--force-color-profile=srgb', '--hide-scrollbars', '--disable-lcd-text'],
});

try {
  for (const job of jobs) {
    const page = await browser.newPage();
    await page.setViewport({
      width: job.width, height: job.height, deviceScaleFactor: job.dpr ?? 2,
    });
    if (job.now) {
      await page.evaluateOnNewDocument((iso) => {
        const NativeDate = Date;
        const fixed = NativeDate.parse(iso);
        class FixedDate extends NativeDate {
          constructor(...args) {
            super(...(args.length ? args : [fixed]));
          }
          static now() { return fixed; }
        }
        window.Date = FixedDate;
      }, job.now);
    }
    if (job.colorScheme) {
      await page.emulateMediaFeatures([
        { name: 'prefers-color-scheme', value: job.colorScheme },
      ]);
    }
    await page.goto(job.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (job.wait) await new Promise((r) => setTimeout(r, job.wait));
    for (const step of job.keys ?? []) {
      if (typeof step === 'string') await page.keyboard.press(step);
      else if (step.focus) await page.focus(step.focus);
      else if (step.type) await page.keyboard.type(step.type);
      else if (step.click) {
        await page.waitForSelector(step.click);
        await page.click(step.click);
      }
      else if (step.clickText) {
        const handle = await page.evaluateHandle((text) => {
          return [...document.querySelectorAll('button')].find(
            (el) => el.textContent?.trim() === text,
          );
        }, step.clickText);
        const element = handle.asElement();
        if (element) await element.click();
      }
      await new Promise((r) => setTimeout(r, step.after ?? 120));
    }
    if (job.scroll) {
      await page.evaluate(({ selector, top }) => {
        const el = selector ? document.querySelector(selector) : null;
        (el ?? document.scrollingElement).scrollTop = top;
      }, job.scroll);
      await new Promise((r) => setTimeout(r, 120));
    }
    const out = resolve(job.out);
    mkdirSync(dirname(out), { recursive: true });
    await page.screenshot({ path: out, fullPage: !!job.fullPage });
    console.log('shot', job.out);
    await page.close();
  }
} finally {
  await browser.close();
}
