import { describe, expect, it } from 'vitest';
import {
  chainSnapshotSaveAllowed,
  unavailableChainExplanation,
  type ChainMap,
} from '../src/lib/chains';
import { CHAIN_NOTICE, type ChainSnapshot } from '../src/types';

const empty: ChainSnapshot = {
  chains: {},
  digest: '',
  notice: CHAIN_NOTICE,
};

const readable: ChainSnapshot = {
  chains: { 'gpt-5.6-sol': ['claude-opus-5[1m]'] } as ChainMap,
  digest: 'a'.repeat(64),
  notice: CHAIN_NOTICE,
};

describe('unavailable chain snapshot', () => {
  it('explains an unreadable file without looking like an empty map', () => {
    const snapshot: ChainSnapshot = {
      chains: null,
      digest: null,
      notice: CHAIN_NOTICE,
      unavailable: true,
      reason: 'unreadable',
    };
    expect(unavailableChainExplanation(snapshot)).toBe(
      'The handoff chain file could not be read.',
    );
    expect(chainSnapshotSaveAllowed(snapshot)).toBe(false);
  });

  it('explains missing chain storage and keeps save off', () => {
    const snapshot: ChainSnapshot = {
      chains: null,
      digest: null,
      notice: CHAIN_NOTICE,
      unavailable: true,
      reason: 'helper_unavailable',
    };
    expect(unavailableChainExplanation(snapshot)).toBe(
      'Chain storage is unavailable, so the editor cannot read or save chains.',
    );
    expect(chainSnapshotSaveAllowed(snapshot)).toBe(false);
  });

  it('does not treat a real empty snapshot as unavailable', () => {
    expect(unavailableChainExplanation(empty)).toBeNull();
    expect(unavailableChainExplanation(readable)).toBeNull();
    expect(chainSnapshotSaveAllowed(empty)).toBe(false);
    expect(chainSnapshotSaveAllowed(readable)).toBe(true);
    expect(chainSnapshotSaveAllowed(null)).toBe(false);
  });
});
