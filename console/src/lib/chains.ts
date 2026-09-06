// Chain editing rules, kept out of the component so the ordering and the
// validation have direct tests.

import type { ChainSnapshot } from '../types';

export type ChainMap = Record<string, string[]>;

export function cloneChains(chains: ChainMap | null | undefined): ChainMap {
  const out: ChainMap = {};
  for (const [source, peers] of Object.entries(chains ?? {})) {
    out[source] = [...peers];
  }
  return out;
}

/** Plain wording when GET /api/chains cannot be told apart from an empty map. */
export function unavailableChainExplanation(
  snapshot: ChainSnapshot | null | undefined,
): string | null {
  if (snapshot == null || snapshot.unavailable !== true) return null;
  if (snapshot.reason === 'helper_unavailable') {
    return 'Chain storage is unavailable, so the editor cannot read or save chains.';
  }
  return 'The handoff chain file could not be read.';
}

/** Save stays off when chains are unknown. An empty digest is not a real snapshot. */
export function chainSnapshotSaveAllowed(
  snapshot: ChainSnapshot | null | undefined,
): boolean {
  if (unavailableChainExplanation(snapshot) != null) return false;
  return typeof snapshot?.digest === 'string' && snapshot.digest.length > 0;
}

export function movePeer(peers: string[], index: number, delta: number): string[] {
  const next = [...peers];
  const target = index + delta;
  if (index < 0 || index >= next.length || target < 0 || target >= next.length) {
    return next;
  }
  const [moved] = next.splice(index, 1);
  next.splice(target, 0, moved);
  return next;
}

export function removePeer(peers: string[], index: number): string[] {
  if (index < 0 || index >= peers.length) return [...peers];
  const next = [...peers];
  next.splice(index, 1);
  return next;
}

/** Appending is a no-op when the peer is already there, or is the source. */
export function addPeer(peers: string[], model: string, source?: string): string[] {
  if (!model || model === source || peers.includes(model)) return [...peers];
  return [...peers, model];
}

export interface ChainProblem {
  source: string;
  message: string;
}

/**
 * What the page refuses to save. The server also validates, but a person
 * should see the reason before pressing Save, not after.
 */
export function validateChains(chains: ChainMap, enabled: string[]): ChainProblem[] {
  const problems: ChainProblem[] = [];
  const known = new Set(enabled);
  for (const [source, peers] of Object.entries(chains)) {
    if (peers.includes(source)) {
      problems.push({ source, message: 'A model cannot hand off to itself.' });
    }
    const seen = new Set<string>();
    for (const peer of peers) {
      if (seen.has(peer)) {
        problems.push({ source, message: `${peer} is listed twice.` });
        break;
      }
      seen.add(peer);
    }
    for (const peer of peers) {
      if (!known.has(peer)) {
        problems.push({ source, message: `${peer} is not an enabled route.` });
        break;
      }
    }
  }
  return problems;
}

/** Empty peer lists are dropped: the server treats them as no chain at all. */
export function prunedChains(chains: ChainMap): ChainMap {
  const out: ChainMap = {};
  for (const [source, peers] of Object.entries(chains)) {
    if (peers.length) out[source] = [...peers];
  }
  return out;
}

export function chainsEqual(a: ChainMap, b: ChainMap): boolean {
  const left = prunedChains(a);
  const right = prunedChains(b);
  const keys = Object.keys(left);
  if (keys.length !== Object.keys(right).length) return false;
  return keys.every((key) => {
    const other = right[key];
    return other && other.length === left[key].length && other.every((m, i) => m === left[key][i]);
  });
}

export interface ChainDiffLine {
  source: string;
  before: string[];
  after: string[];
  change: 'added' | 'removed' | 'reordered' | 'same';
}

/** What a pending chain proposal would change, per source model. */
export function diffChains(current: ChainMap, proposed: ChainMap): ChainDiffLine[] {
  const sources = [...new Set([...Object.keys(current), ...Object.keys(proposed)])].sort();
  return sources.map((source) => {
    const before = current[source] ?? [];
    const after = proposed[source] ?? [];
    let change: ChainDiffLine['change'] = 'same';
    if (!before.length && after.length) change = 'added';
    else if (before.length && !after.length) change = 'removed';
    else if (before.length !== after.length || before.some((m, i) => m !== after[i])) {
      change = 'reordered';
    }
    return { source, before, after, change };
  });
}

export const EMPTY_SNAPSHOT: ChainSnapshot = {
  chains: {},
  digest: '',
  notice: '',
};
