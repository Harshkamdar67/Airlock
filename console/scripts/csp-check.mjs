// Serves the built dist behind the console's exact Content-Security-Policy and
// fails on any blocked resource or page error.
//
// The console sends no unsafe-inline, so an inline <script> in dist/index.html
// would silently never run. This catches that before an install does.
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync } from 'node:fs';
import { extname, join, normalize, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch } from 'puppeteer-core';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DIST = join(ROOT, 'dist');
const CHROME =
  process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

// Exactly the header from bin/airlock_console.py.
const CSP =
  "default-src 'self'; base-uri 'none'; object-src 'none'; " +
  "frame-ancestors 'none'; form-action 'self'; connect-src 'self'";

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.json': 'application/json; charset=utf-8',
};

const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', 'http://127.0.0.1');
  let file = url.pathname === '/' ? '/index.html' : url.pathname;
  const path = join(DIST, normalize(file).replace(/^(\.\.[/\\])+/, ''));
  res.setHeader('content-security-policy', CSP);
  res.setHeader('x-content-type-options', 'nosniff');
  res.setHeader('cache-control', 'no-store');

  if (url.pathname === '/api/stream') {
    res.statusCode = 200;
    res.setHeader('content-type', 'text/event-stream');
    res.write(`event: heartbeat\ndata: {}\n\n`);
    return;
  }

  if (url.pathname.startsWith('/api/')) {
    res.statusCode = 200;
    res.setHeader('content-type', 'application/json; charset=utf-8');
    // Enough for the page to render its empty state without a router.
    res.end(
      JSON.stringify({
        generated_at: new Date().toISOString(),
        console_version: '0.1.0',
        sessions: [],
        routes: [],
        headroom: [],
        attention: [],
        proposals: [],
        chain_digest: null,
      }),
    );
    return;
  }

  if (!existsSync(path) || !statSync(path).isFile()) {
    res.statusCode = 404;
    res.end('not found');
    return;
  }
  res.statusCode = 200;
  res.setHeader('content-type', TYPES[extname(path)] ?? 'application/octet-stream');
  // The real server injects the CSRF meta into the root HTML only.
  const body = readFileSync(path);
  if (path.endsWith('index.html')) {
    res.end(
      body
        .toString('utf8')
        .replace(/<\/head>/i, '<meta name="airlock-csrf" content="csp-check-token"></head>'),
    );
    return;
  }
  res.end(body);
});

await new Promise((r) => server.listen(4791, '127.0.0.1', r));

const browser = await launch({ executablePath: CHROME, headless: 'shell' });
const page = await browser.newPage();
const problems = [];
page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
page.on('console', (message) => {
  const text = message.text();
  if (message.type() === 'error' || /Content Security Policy|violates/i.test(text)) {
    problems.push(`console ${message.type()}: ${text}`);
  }
});
page.on('requestfailed', (request) => {
  problems.push(`request failed: ${request.url()} ${request.failure()?.errorText}`);
});
page.on('response', (response) => {
  if (response.status() === 404) problems.push(`404: ${response.url()}`);
});

try {
  await page.goto('http://127.0.0.1:4791/', { waitUntil: 'domcontentloaded' });
  await new Promise((r) => setTimeout(r, 900));

  // The theme preflight must have run: it is the whole reason the file exists.
  const themeRan = await page.evaluate(() => {
    localStorage.setItem('airlock-console-theme', 'dark');
    return true;
  });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await new Promise((r) => setTimeout(r, 600));
  const applied = await page.evaluate(() => document.documentElement.dataset.theme);
  if (!themeRan || applied !== 'dark') {
    problems.push(`theme preflight did not run under CSP (data-theme=${applied})`);
  }

  const rendered = await page.evaluate(() => !!document.querySelector('#root')?.children.length);
  if (!rendered) problems.push('the page rendered nothing under the console CSP');

  const favicon = await page.evaluate(async () => {
    const res = await fetch('/favicon.svg');
    return res.status;
  });
  if (favicon !== 200) problems.push(`favicon.svg answered ${favicon}`);
} finally {
  await page.close();
  await browser.close();
  server.close();
}

if (problems.length) {
  console.error('CSP check failed:');
  for (const problem of problems) console.error(`  ${problem}`);
  process.exit(1);
}
console.log('csp-check: the built page runs clean under the console CSP, no inline script, favicon served.');
