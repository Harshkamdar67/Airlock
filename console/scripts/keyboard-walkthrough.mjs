// A keyboard-only walkthrough against the built page. This is not a unit test:
// it launches local Chrome, uses the keys a person uses, and checks what the
// rendered page did after each key.
//
//   node scripts/keyboard-walkthrough.mjs http://localhost:4788
import { launch } from 'puppeteer-core';

const CHROME =
  process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const origin = process.argv[2] ?? 'http://localhost:4788';

const browser = await launch({
  executablePath: CHROME,
  headless: 'shell',
  args: ['--force-color-profile=srgb'],
});

function fail(message) {
  throw new Error(`Keyboard walkthrough failed: ${message}`);
}

const page = await browser.newPage();
// The dev server keeps one SSE stream per visited fixture for a while, and a
// long run can wait on Chrome's per-host connection limit before a navigation
// starts. That is a harness delay, not a page fault, so give it room.
page.setDefaultNavigationTimeout(90000);
try {
  await browser.defaultBrowserContext().overridePermissions(origin, [
    'clipboard-read',
    'clipboard-write',
  ]);
  await page.setViewport({ width: 1440, height: 900 });
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'light' }]);
  await page.goto(`${origin}/?fixture=twelve`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.srow[aria-selected="true"]');
  await new Promise((r) => setTimeout(r, 350));

  const selectedProject = () =>
    page.$eval('.srow[aria-selected="true"] .name', (el) => el.textContent?.trim());
  const active = () =>
    page.evaluate(() => {
      const el = document.activeElement;
      if (!el) return 'none';
      const text = el.textContent?.trim().replace(/\s+/g, ' ').slice(0, 50);
      return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${text ? ` “${text}”` : ''}`;
    });

  const start = await selectedProject();
  if (start !== 'claudex') fail(`expected claudex to be selected first, got ${start}`);
  console.log('Initial: blocked session claudex is selected.');

  await page.keyboard.press('/');
  if ((await active()) !== 'input#rail-filter') fail('/ did not focus the session filter');
  console.log('/: focus moved to the session filter.');

  await page.keyboard.type('relay');
  await page.waitForFunction(() => document.querySelectorAll('.srow').length === 1);
  const only = await page.$eval('.srow .name', (el) => el.textContent?.trim());
  if (only !== 'airlock-relay') fail(`filter left ${only}`);
  await page.waitForFunction(() =>
    document.querySelector('.srow[aria-selected="true"] .name')?.textContent === 'airlock-relay',
  );
  console.log('typed “relay”: the rail narrowed to and selected airlock-relay; no letter shortcut fired.');

  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelectorAll('.srow').length === 12);
  await page.keyboard.press('Escape');
  if (!(await active()).startsWith('div')) fail('Escape did not return focus to the rail listbox');
  console.log('Escape, Escape: the first cleared the filter; the second returned focus to the rail.');

  const visibleRing = await page.$eval('.srow[aria-selected="true"]', (el) => {
    const s = getComputedStyle(el);
    return s.outlineStyle !== 'none' && s.outlineWidth !== '0px';
  });
  if (!visibleRing) fail('the active rail row has no visible focus ring');
  console.log('Focus check: the selected rail row has a visible focus outline.');

  await page.keyboard.press('ArrowDown');
  if ((await selectedProject()) !== 'agentquant') fail('ArrowDown did not cross into the running group');
  console.log('ArrowDown: selection moved from airlock-relay to agentquant across a group boundary.');

  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.activeElement?.tagName === 'H1');
  const open = await page.$eval('h1', (el) => el.textContent?.trim());
  if (open !== 'agentquant') fail(`Enter opened ${open}`);
  console.log('Enter: agentquant opened and focus moved to its session heading.');

  await page.keyboard.press('j');
  await page.waitForFunction(() => document.querySelector('.srow[aria-selected="true"] .name')?.textContent === 'ornith-serve');
  console.log('j: selection moved to the next running session, ornith-serve.');

  await page.keyboard.press('g');
  await page.waitForFunction(() => document.activeElement?.tagName === 'H1');
  console.log('g: focus moved to the current session heading.');

  await page.keyboard.press('r');
  if ((await active()).split(' ')[0] !== 'aside#inspector-panel') fail('r did not focus the inspector');
  console.log('r: focus moved to the scrollable routes inspector.');

  await page.keyboard.down('Control');
  await page.keyboard.press('k');
  await page.keyboard.up('Control');
  await page.waitForSelector('[role="dialog"] .pinput');
  await page.waitForFunction(() => document.activeElement?.classList.contains('pinput'));
  if ((await active()) !== 'input') fail('Ctrl K did not put focus in the palette input');
  console.log('Ctrl+K: the command palette opened with focus in its input.');

  await page.keyboard.type('dark');
  await page.waitForFunction(() => document.querySelector('.pitem')?.textContent?.includes('Use dark theme'));
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
  const remembered = await page.evaluate(() => localStorage.getItem('airlock-console-theme'));
  if (remembered !== 'dark') fail('dark theme was not persisted');
  console.log('typed “dark”, Enter: dark theme applied and persisted.');

  await page.keyboard.down('Control');
  await page.keyboard.press('k');
  await page.keyboard.up('Control');
  await page.waitForFunction(() => document.activeElement?.classList.contains('pinput'));
  await page.keyboard.type('airlock-relay');
  await page.waitForFunction(() =>
    [...document.querySelectorAll('.pitem')].some((el) => el.textContent?.includes('Go to airlock-relay')),
  );
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('h1')?.textContent === 'airlock-relay');
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'));
  await page.keyboard.press('g');
  await page.waitForFunction(() => document.activeElement?.tagName === 'H1');
  console.log('Ctrl+K, typed “airlock-relay”, Enter: jumped to the session and focused its heading.');

  await page.keyboard.press('?');
  await page.waitForSelector('[role="dialog"][aria-label="Keyboard shortcuts"]');
  await page.waitForFunction(() => !!document.activeElement?.closest('[role="dialog"]'));
  const inDialog = await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'));
  if (!inDialog) fail('shortcut sheet opened without moving focus into it');
  console.log('?: the shortcut sheet opened and moved focus inside the dialog.');

  await page.keyboard.press('Escape');
  await new Promise((r) => setTimeout(r, 150));
  if (await page.$('[role="dialog"]')) {
    await page.focus('[data-dialog-close]');
    await page.keyboard.press('Enter');
  }
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'));
  if (!(await active()).startsWith('h1')) fail('Escape did not restore focus to the opener');
  console.log('Escape: the sheet closed and focus returned to the session heading.');

  // Copy directly, immediately after a dialog closes. This caught a stale
  // overlay-state race during implementation.
  await page.keyboard.press('c');
  await new Promise((r) => setTimeout(r, 500));
  const copyState = await page.evaluate(() => ({
    active: document.activeElement?.tagName,
    status: [...document.querySelectorAll('[role="status"]')].map((el) => el.textContent?.trim()),
    buttons: [...document.querySelectorAll('button')].map((el) => el.textContent?.trim()).filter((x) => /Copy/.test(x ?? '')),
  }));
  if (!copyState.status.includes('Report copied') && !copyState.buttons.includes('Copied')) fail(`copy state was ${JSON.stringify(copyState)}`);
  console.log('c: copied the selected session’s Markdown report and announced it.');

  await new Promise((r) => setTimeout(r, 2100));
  // Copy through the command palette too, proving the action is exposed there.
  await page.keyboard.down('Control');
  await page.keyboard.press('k');
  await page.keyboard.up('Control');
  await page.waitForFunction(() => document.activeElement?.classList.contains('pinput'));
  await page.keyboard.type('copy report');
  await page.keyboard.press('Enter');
  await new Promise((r) => setTimeout(r, 500));
  const paletteCopy = await page.evaluate(() =>
    [...document.querySelectorAll('[role="status"]')].some((el) => el.textContent?.trim() === 'Report copied'),
  );
  if (!paletteCopy) fail('the palette copy command did not run');
  console.log('Ctrl+K, typed “copy report”, Enter: ran the same action from the palette.');

  await page.keyboard.press('g');
  await page.keyboard.press('Tab');
  const radio = await active();
  if (!radio.includes('All')) fail(`Tab from the heading reached ${radio}, not the filter group`);
  await page.keyboard.press('ArrowRight');
  const checked = await page.$eval('[role="radio"][aria-checked="true"]', (el) => el.textContent?.trim());
  if (checked !== 'Handoffs') fail(`ArrowRight selected ${checked}`);
  console.log('Tab, ArrowRight: focus entered the timeline filters and selected Handoffs.');

  const focusVisible = await page.evaluate(() => document.activeElement?.matches(':focus-visible'));
  if (!focusVisible) fail('the final keyboard focus is not visibly styled');
  console.log('Final focus check: the active filter has :focus-visible.');

  // An overlay replaces the other one. It can never stack a second modal.
  await page.keyboard.press('?');
  await page.waitForSelector('[role="dialog"][aria-label="Keyboard shortcuts"]');
  await page.keyboard.down('Control');
  await page.keyboard.press('k');
  await page.keyboard.up('Control');
  await page.waitForSelector('[role="dialog"][aria-label="Command palette"]');
  const dialogCount = await page.$$eval('[role="dialog"]', (nodes) => nodes.length);
  if (dialogCount !== 1) fail(`Ctrl K stacked ${dialogCount} dialogs`);
  console.log('?, Ctrl+K: the command palette replaced the shortcut sheet; one dialog remained.');
  await page.keyboard.press('Escape');

  // A historical model absent from current routes remains a valid event filter
  // after the next full SSE overview and detail refresh.
  await page.goto(`${origin}/?fixture=allkinds`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('h1')?.textContent === 'every-event');
  await page.keyboard.down('Control');
  await page.keyboard.press('k');
  await page.keyboard.up('Control');
  await page.waitForFunction(() => document.activeElement?.classList.contains('pinput'));
  await page.keyboard.type('filter timeline to haiku');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() =>
    document.querySelector('#model-filter')?.value === 'claude-haiku-4-5-20251001',
  );
  await new Promise((r) => setTimeout(r, 3300));
  const historical = await page.$eval('#model-filter', (el) => el.value);
  if (historical !== 'claude-haiku-4-5-20251001') fail('detail refresh cleared a historical model filter');
  console.log('Haiku filter: the event-only historical model survived an SSE detail refresh.');

  // Scenario summaries remain authoritative even when a rich default detail
  // fixture for the same id exists.
  await page.goto(`${origin}/?fixture=ended#session=r-8f2c1a`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('h1')?.textContent === 'claudex');
  await page.waitForFunction(() => document.querySelector('.head')?.textContent?.includes('Ended'));
  if (await page.$('.resolution')) fail('ended detail contradicted its overview with a blocked verdict');
  console.log('ended fixture: rail and session view both rendered Ended.');

  await page.goto(`${origin}/?fixture=unknown`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.body.textContent?.includes('fit unknown'));
  const unknownText = await page.$$eval('.route', (nodes) =>
    nodes.find((el) => el.textContent?.includes('local-coder'))?.textContent ?? '',
  );
  if (!unknownText.includes('fit unknown') || unknownText.includes('does not fit')) {
    fail(`unknown fit rendered as ${unknownText}`);
  }
  console.log('unknown fixture: null fit rendered as “fit unknown”, never false.');

  // A failed detail has a recoverable state. Re-activating the same row retries,
  // and another failure returns to the error rather than hanging on a skeleton.
  let detailResponses = 0;
  page.on('response', (response) => {
    if (response.url().includes('/api/sessions/r-2b77e0')) detailResponses += 1;
  });
  await page.goto(`${origin}/?fixture=detail-error`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('h1')?.textContent === 'This session did not answer');
  const beforeRetry = detailResponses;
  await page.keyboard.press('/');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Enter');
  await new Promise((r) => setTimeout(r, 450));
  if (detailResponses <= beforeRetry) fail('activating the failed row did not retry its detail');
  const retryHeading = await page.$eval('h1', (el) => el.textContent);
  if (retryHeading !== 'This session did not answer') fail(`repeated detail failure left ${retryHeading}`);
  if (await page.$('main[aria-busy="true"]')) fail('repeated detail failure hung on the skeleton');
  console.log('detail error: same-row Enter retried; repeated failure returned to the explicit error.');

  // ---- phase 2 -----------------------------------------------------------

  // A pending proposal is approved entirely by keyboard, and the button says
  // exactly what it will do before it is pressed.
  await page.goto(`${origin}/?fixture=proposal&reset=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.proposal-pending');
  const primary = await page.$eval('.btn-primary', (el) => el.textContent?.trim());
  if (primary !== 'Approve and use Ox-alpha') fail(`primary button said ${primary}`);
  console.log(`proposal: the primary button reads "${primary}".`);

  const reached = await page.evaluate(() => {
    const button = [...document.querySelectorAll('button')].find(
      (el) => el.textContent?.trim() === 'Approve and use Ox-alpha',
    );
    button?.focus();
    return document.activeElement === button && button.matches(':focus-visible');
  });
  if (!reached) fail('the approve button could not take visible keyboard focus');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((el) =>
      el.textContent?.includes('Applied.'),
    ),
  );
  await page.waitForFunction(() => !document.querySelector('.proposal-pending'));
  console.log('Enter on the primary button: approved, applied, and announced in a live region.');

  const csrfLeak = await page.evaluate(() => document.body.innerText.includes('dev-csrf-token'));
  if (csrfLeak) fail('the CSRF token was rendered on the page');
  console.log('CSRF check: the token appears nowhere in the rendered page.');

  // A metered target without consent must not promise an apply the server
  // would refuse; ticking the consent edits the proposal and unblocks it.
  await page.goto(`${origin}/?fixture=metered&reset=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.proposal-consent');
  const beforeConsent = await page.evaluate(() => {
    const button = [...document.querySelectorAll('button')].find((el) =>
      el.textContent?.startsWith('Approve and use'),
    );
    return {
      label: button?.textContent?.trim(),
      disabled: button?.disabled,
      fact: [...document.querySelectorAll('.proposal-facts span')]
        .map((el) => el.textContent?.trim())
        .find((text) => text?.startsWith('metered')),
    };
  });
  if (!beforeConsent.disabled) fail('a metered target without consent offered approval');
  if (beforeConsent.fact !== 'metered, extra usage not allowed') {
    fail(`metered fact read ${beforeConsent.fact}`);
  }
  console.log(`metered guard: "${beforeConsent.label}" is disabled, and the reason is shown.`);

  await page.evaluate(() => {
    document.querySelector('.proposal-consent input[type="checkbox"]')?.focus();
  });
  await page.keyboard.press('Space');
  await page.waitForFunction(() => !document.querySelector('.proposal-consent'));
  const afterConsent = await page.evaluate(() => {
    const button = [...document.querySelectorAll('button')].find((el) =>
      el.textContent?.startsWith('Approve and use'),
    );
    return {
      label: button?.textContent?.trim(),
      disabled: button?.disabled,
      revision: document.querySelector('.rev')?.textContent?.trim(),
      fact: [...document.querySelectorAll('.proposal-facts span')]
        .map((el) => el.textContent?.trim())
        .find((text) => text?.startsWith('metered')),
    };
  });
  if (afterConsent.disabled) fail('approval stayed blocked after consent');
  if (afterConsent.label !== beforeConsent.label) {
    fail(`the promise changed from ${beforeConsent.label} to ${afterConsent.label}`);
  }
  if (afterConsent.revision !== 'revision 2') fail(`revision was ${afterConsent.revision}`);
  if (afterConsent.fact !== 'metered, extra usage accepted by the agent') {
    fail(`consented fact read ${afterConsent.fact}`);
  }
  console.log(
    `metered consent: Space sent the revision-bound edit, revision 2, and "${afterConsent.label}" is now offered.`,
  );

  // A session with no control channel offers no controls, and says why.
  await page.goto(`${origin}/?fixture=uncontrollable&reset=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.proposal');
  const blocked = await page.evaluate(() => ({
    explained: !!document.querySelector('.proposal-blocked'),
    approve: [...document.querySelectorAll('button')].some((el) =>
      el.textContent?.startsWith('Approve and'),
    ),
    use: [...document.querySelectorAll('button')].some((el) =>
      el.textContent?.includes('for this session'),
    ),
  }));
  if (!blocked.explained || blocked.approve || blocked.use) {
    fail(`uncontrollable session state was ${JSON.stringify(blocked)}`);
  }
  console.log('uncontrollable session: no approve or handoff controls, with an explanation.');

  // The chain editor reorders by keyboard and refuses an unsaveable state.
  await page.goto(`${origin}/?fixture=chains&reset=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#chain-editor');
  const notice = await page.$eval('.chain-notice', (el) => el.textContent?.trim());
  if (notice !== 'Applies to sessions started after this change. Running sessions keep their frozen chains.') {
    fail(`chain notice said ${notice}`);
  }
  console.log('chain editor: the new-sessions-only notice is present and permanent.');

  const orderBefore = await page.$$eval('.chain-peer', (nodes) =>
    nodes.map((el) => el.children[1].textContent?.trim()),
  );
  await page.evaluate(() => {
    document.querySelector('[aria-label="Move sol earlier"]')?.focus();
  });
  await page.keyboard.press('Enter');
  await new Promise((r) => setTimeout(r, 150));
  const orderAfter = await page.$$eval('.chain-peer', (nodes) =>
    nodes.map((el) => el.children[1].textContent?.trim()),
  );
  if (orderAfter[0] !== orderBefore[1] || orderAfter[1] !== orderBefore[0]) {
    fail(`reorder produced ${orderAfter.join(',')} from ${orderBefore.join(',')}`);
  }
  console.log(`chain editor: Enter on "Move sol earlier" reordered ${orderBefore.join(' → ')} to ${orderAfter.join(' → ')}.`);

  await page.evaluate(() => {
    [...document.querySelectorAll('button')]
      .find((el) => el.textContent?.trim() === 'Save chains')
      ?.focus();
  });
  await page.keyboard.press('Enter');
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[role="status"]')].some((el) =>
      /Chains saved|already this/.test(el.textContent ?? ''),
    ),
  );
  console.log('chain editor: Enter on Save chains saved through the digest compare-and-swap.');

  // A stale digest must not overwrite: the server refuses and the page reloads.
  const conflict = await page.evaluate(async () => {
    const token = document
      .querySelector('meta[name="airlock-csrf"]')
      ?.getAttribute('content');
    const res = await fetch('/api/human/chains?fixture=chains', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-Airlock-CSRF': token ?? '' },
      body: JSON.stringify({ chains: {}, expected_digest: 'a'.repeat(64) }),
    });
    return { status: res.status, body: await res.json() };
  });
  if (conflict.status !== 409 || conflict.body?.error?.type !== 'chain_conflict') {
    fail(`stale digest returned ${JSON.stringify(conflict)}`);
  }
  if (!conflict.body?.current?.digest || !conflict.body?.current?.chains) {
    fail('chain_conflict did not carry the current snapshot');
  }
  console.log('chain editor: a stale expected_digest is refused with chain_conflict and carries the current snapshot.');

  // Command palette reaches the chain editor without a pointer.
  await page.goto(`${origin}/?fixture=proposal&reset=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.proposal');
  await page.keyboard.down('Control');
  await page.keyboard.press('k');
  await page.keyboard.up('Control');
  await page.waitForFunction(() => document.activeElement?.classList.contains('pinput'));
  await page.keyboard.type('global failover');
  await page.waitForFunction(() =>
    [...document.querySelectorAll('.pitem')].some((el) =>
      el.textContent?.includes('Edit global failover chains'),
    ),
  );
  await page.keyboard.press('Enter');
  await page.waitForSelector('#chain-editor');
  console.log('Ctrl+K, "global failover", Enter: the chain editor opened from the palette.');

  console.log('Keyboard walkthrough: PASS');
} finally {
  await page.close();
  await browser.close();
}
