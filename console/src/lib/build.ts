// Detects that the console site on disk is newer than the code this page is
// running. The page holds a live stream, so after a reinstall or a Console
// restart it happily reconnects and keeps showing the old interface, which
// reads as "my change did not land". Comparing the hashed bundle name in a
// fresh copy of the page against our own is enough to know.

const BUNDLE_RE = /\/a\/(index-[A-Za-z0-9_-]+\.js)/;

/** The hashed bundle name from a page's HTML, or null when none is found. */
export function bundleNameFrom(html: string): string | null {
  const m = BUNDLE_RE.exec(html);
  return m ? m[1] : null;
}

/** The hashed bundle name this document loaded. */
export function currentBundleName(doc: Document = document): string | null {
  for (const script of Array.from(doc.scripts)) {
    const name = bundleNameFrom(script.getAttribute('src') ?? '');
    if (name) return name;
  }
  return null;
}

/**
 * True when the site on disk carries a different bundle than this page.
 * Network or parse trouble answers false: the page must never reload on a
 * guess, only on a confirmed newer build.
 */
export async function siteHasNewerBuild(
  fetchImpl: typeof fetch = fetch,
  doc: Document = document,
): Promise<boolean> {
  const mine = currentBundleName(doc);
  if (!mine) return false;
  try {
    const res = await fetchImpl('/', { cache: 'no-store', credentials: 'same-origin' });
    if (!res.ok) return false;
    const theirs = bundleNameFrom(await res.text());
    return theirs !== null && theirs !== mine;
  } catch {
    return false;
  }
}
