// A page reloads only on a confirmed newer bundle, never on a guess.

import { describe, expect, it } from 'vitest';
import { bundleNameFrom, siteHasNewerBuild } from '../src/lib/build';

function docWith(src: string): Document {
  return { scripts: [{ getAttribute: () => src }] } as unknown as Document;
}

function fetchReturning(status: number, body: string): typeof fetch {
  return (async () => ({ ok: status === 200, text: async () => body })) as unknown as typeof fetch;
}

describe('bundleNameFrom', () => {
  it('reads the hashed bundle out of the page', () => {
    expect(bundleNameFrom('<script type="module" src="/a/index-C6saf2-1.js"></script>')).toBe(
      'index-C6saf2-1.js',
    );
    expect(bundleNameFrom('<script src="/theme-init.js"></script>')).toBeNull();
  });
});

describe('siteHasNewerBuild', () => {
  const page = docWith('/a/index-old00000.js');

  it('is true only when the served bundle differs from the loaded one', async () => {
    expect(await siteHasNewerBuild(fetchReturning(200, '<script src="/a/index-new11111.js">'), page)).toBe(true);
    expect(await siteHasNewerBuild(fetchReturning(200, '<script src="/a/index-old00000.js">'), page)).toBe(false);
  });

  it('answers false on any trouble', async () => {
    expect(await siteHasNewerBuild(fetchReturning(500, ''), page)).toBe(false);
    expect(await siteHasNewerBuild(fetchReturning(200, 'no bundle here'), page)).toBe(false);
    const failing = (async () => { throw new Error('offline'); }) as unknown as typeof fetch;
    expect(await siteHasNewerBuild(failing, page)).toBe(false);
    expect(await siteHasNewerBuild(fetchReturning(200, '<script src="/a/index-x.js">'), docWith('/theme-init.js'))).toBe(false);
  });
});
