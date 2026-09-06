// Proposal vocabulary, the primary button's promise, chain editing rules, and
// the WebMCP registration guard.

import { describe, expect, it } from 'vitest';
import {
  approvalBlock,
  EDITABLE_STATUSES,
  expiryWords,
  isActionable,
  isEditable,
  meteredWords,
  primaryLabel,
  proposalLead,
  proposalStatus,
  sortProposals,
} from '../src/lib/proposals';
import {
  addPeer,
  chainsEqual,
  diffChains,
  movePeer,
  prunedChains,
  removePeer,
  validateChains,
} from '../src/lib/chains';
import { isRegisterableTool, registerableTools, registerTools } from '../src/lib/webmcp';
import type { ProposalStatus, SessionHandoffProposal, ToolDefinition } from '../src/types';

const SHORT: Record<string, string> = {
  'claude-fable-5-1[1m]': 'fable',
  'claude-opus-5[1m]': 'opus',
  'stealth/ox-alpha': 'ox-alpha',
  'gpt-5.6-sol': 'sol',
};
const short = (model: string | null | undefined) => (model ? (SHORT[model] ?? model) : 'unknown');

function handoff(over: Partial<SessionHandoffProposal> = {}): SessionHandoffProposal {
  return {
    id: 'shp_1',
    kind: 'session_handoff',
    session_id: 'r-8f2c1a',
    operation: 'pin',
    target_model: 'claude-opus-5[1m]',
    reason: 'Opus is ready and fits.',
    allow_metered: false,
    created_by: 'agent',
    created_at: '2026-09-05T09:00:00Z',
    expires_at: '2026-09-05T09:15:00Z',
    revision: 1,
    status: 'pending',
    basis: null,
    application: null,
    last_error: null,
    ...over,
  };
}

describe('proposal status vocabulary', () => {
  const ALL: ProposalStatus[] = [
    'pending',
    'applying',
    'applied',
    'rejected',
    'expired',
    'superseded',
    'conflicted',
    'failed',
  ];

  it('gives every server status a word and a shape', () => {
    for (const status of ALL) {
      const d = proposalStatus(status);
      expect(d.label.length, status).toBeGreaterThan(2);
      expect(d.glyph, status).toBeTruthy();
    }
  });

  it('reserves the accent tone for the states that need a person', () => {
    expect(proposalStatus('pending').tone).toBe('attn');
    expect(proposalStatus('conflicted').tone).toBe('attn');
    expect(proposalStatus('failed').tone).toBe('attn');
    expect(proposalStatus('applied').tone).toBe('ok');
    expect(proposalStatus('rejected').tone).toBe('faint');
    expect(proposalStatus('expired').tone).toBe('faint');
  });

  it('renders an unknown status rather than hiding it', () => {
    expect(proposalStatus('something_new').label).toBe('something_new');
  });

  it('mirrors the server rule about what can be edited', () => {
    expect(EDITABLE_STATUSES).toEqual(['pending', 'failed', 'conflicted']);
    expect(isEditable('applied')).toBe(false);
    expect(isEditable('applying')).toBe(false);
    expect(isEditable('conflicted')).toBe(true);
    expect(isActionable('rejected')).toBe(false);
    expect(isActionable('failed')).toBe(true);
  });
});

describe('the primary button says exactly what it does', () => {
  it('names the target model for a pin', () => {
    expect(primaryLabel(handoff(), short, 'claude-fable-5-1[1m]')).toBe(
      'Approve and use Opus',
    );
  });

  it('names the root model for a restore', () => {
    expect(
      primaryLabel(
        handoff({ operation: 'restore_root', target_model: null }),
        short,
        'claude-fable-5-1[1m]',
      ),
    ).toBe('Approve and restore Fable');
  });

  it('never says approve without naming the effect', () => {
    for (const proposal of [handoff(), handoff({ operation: 'restore_root', target_model: null })]) {
      const label = primaryLabel(proposal, short, 'claude-fable-5-1[1m]');
      expect(label.startsWith('Approve and ')).toBe(true);
      expect(label.length).toBeGreaterThan('Approve and '.length + 3);
    }
  });

  it('says who proposed it and what it would do', () => {
    expect(proposalLead(handoff(), short, 'claude-fable-5-1[1m]')).toBe(
      'An agent proposed moving this session to Opus.',
    );
    expect(proposalLead(handoff({ created_by: 'human' }), short, 'claude-fable-5-1[1m]')).toBe(
      'You proposed moving this session to Opus.',
    );
  });
});

describe('a metered target', () => {
  it('reads as accepted by whoever proposed it, because the server demands consent', () => {
    expect(meteredWords(handoff({ allow_metered: true }), true)).toBe(
      'metered, extra usage accepted by the agent',
    );
    expect(
      meteredWords(handoff({ allow_metered: true, created_by: 'human' }), true),
    ).toBe('metered, extra usage accepted by you');
  });

  it('says nothing at all when the route is not metered', () => {
    expect(meteredWords(handoff({ allow_metered: true }), false)).toBeNull();
    expect(meteredWords(handoff({ allow_metered: false }), false)).toBeNull();
  });

  it('offers approval when the proposal already allows the extra usage', () => {
    expect(approvalBlock(handoff({ allow_metered: true }), true)).toBeNull();
  });

  it('blocks approval when a metered target has no consent, and says why', () => {
    // The server refuses to apply this, so the button must not promise it.
    const blocked = approvalBlock(handoff({ allow_metered: false }), true);
    expect(blocked).toBe(
      'This route bills extra usage, and this proposal does not allow it.',
    );
  });

  it('never blocks a route that is not metered, or a restore', () => {
    expect(approvalBlock(handoff({ allow_metered: false }), false)).toBeNull();
    expect(
      approvalBlock(
        handoff({ operation: 'restore_root', target_model: null, allow_metered: false }),
        true,
      ),
    ).toBeNull();
  });

  it('keeps the same promise once consent is given', () => {
    // Ticking the consent edits the proposal; the label does not change.
    const before = handoff({ allow_metered: false });
    const after = handoff({ allow_metered: true, revision: 2 });
    expect(primaryLabel(before, short, 'claude-fable-5-1[1m]')).toBe(
      primaryLabel(after, short, 'claude-fable-5-1[1m]'),
    );
    expect(approvalBlock(before, true)).not.toBeNull();
    expect(approvalBlock(after, true)).toBeNull();
  });
});

describe('expiry and ordering', () => {
  const now = Date.parse('2026-09-05T09:02:00Z');

  it('counts down in words, and admits when it has lapsed', () => {
    expect(expiryWords('2026-09-05T09:15:00Z', now)).toBe('expires in 13 minutes');
    expect(expiryWords('2026-09-05T09:01:00Z', now)).toBe('expired');
    expect(expiryWords(null, now)).toBe('no expiry reported');
  });

  it('puts what a person can act on above what is already finished', () => {
    const ordered = sortProposals([
      handoff({ id: 'a', status: 'applied', created_at: '2026-09-05T09:01:00Z' }),
      handoff({ id: 'b', status: 'pending', created_at: '2026-09-05T08:00:00Z' }),
      handoff({ id: 'c', status: 'rejected', created_at: '2026-09-05T09:02:00Z' }),
    ]);
    expect(ordered.map((p) => p.id)).toEqual(['b', 'c', 'a']);
  });
});

describe('chain editing', () => {
  const peers = ['claude-opus-5[1m]', 'gpt-5.6-sol', 'stealth/ox-alpha'];

  it('moves a peer up and down without losing anyone', () => {
    expect(movePeer(peers, 2, -1)).toEqual([
      'claude-opus-5[1m]',
      'stealth/ox-alpha',
      'gpt-5.6-sol',
    ]);
    expect(movePeer(peers, 0, 1)).toEqual([
      'gpt-5.6-sol',
      'claude-opus-5[1m]',
      'stealth/ox-alpha',
    ]);
  });

  it('refuses to move past either end', () => {
    expect(movePeer(peers, 0, -1)).toEqual(peers);
    expect(movePeer(peers, 2, 1)).toEqual(peers);
  });

  it('removes by position', () => {
    expect(removePeer(peers, 1)).toEqual(['claude-opus-5[1m]', 'stealth/ox-alpha']);
    expect(removePeer(peers, 9)).toEqual(peers);
  });

  it('never adds a duplicate or the source itself', () => {
    expect(addPeer(peers, 'gpt-5.6-sol')).toEqual(peers);
    expect(addPeer(peers, 'fable', 'fable')).toEqual(peers);
    expect(addPeer(peers, 'grok-4.6')).toEqual([...peers, 'grok-4.6']);
  });

  it('explains what it will not save', () => {
    const enabled = ['claude-opus-5[1m]', 'gpt-5.6-sol'];
    expect(validateChains({ 'gpt-5.6-sol': ['gpt-5.6-sol'] }, enabled)[0].message).toBe(
      'A model cannot hand off to itself.',
    );
    expect(
      validateChains({ 'gpt-5.6-sol': ['claude-opus-5[1m]', 'claude-opus-5[1m]'] }, enabled)[0]
        .message,
    ).toMatch(/listed twice/);
    expect(
      validateChains({ 'gpt-5.6-sol': ['grok-4.6'] }, enabled)[0].message,
    ).toMatch(/not an enabled route/);
    expect(validateChains({ 'gpt-5.6-sol': ['claude-opus-5[1m]'] }, enabled)).toEqual([]);
  });

  it('drops an empty peer list, which the server treats as no chain', () => {
    expect(prunedChains({ a: [], b: ['c'] })).toEqual({ b: ['c'] });
    expect(chainsEqual({ a: [], b: ['c'] }, { b: ['c'] })).toBe(true);
    expect(chainsEqual({ b: ['c'] }, { b: ['d'] })).toBe(false);
    expect(chainsEqual({ b: ['c', 'd'] }, { b: ['d', 'c'] })).toBe(false);
  });

  it('describes what a proposal would change', () => {
    const diff = diffChains(
      { 'gpt-5.6-sol': ['claude-opus-5[1m]'], gone: ['x'] },
      { 'gpt-5.6-sol': ['claude-opus-5[1m]', 'grok-4.6'], added: ['y'] },
    );
    const bySource = Object.fromEntries(diff.map((line) => [line.source, line.change]));
    expect(bySource['gpt-5.6-sol']).toBe('reordered');
    expect(bySource.gone).toBe('removed');
    expect(bySource.added).toBe('added');
  });
});

describe('WebMCP registration', () => {
  const manifest: ToolDefinition[] = [
    { name: 'airlock_list_sessions', description: '', inputSchema: {} },
    { name: 'airlock_get_session', description: '', inputSchema: {} },
    { name: 'airlock_propose_session_handoff', description: '', inputSchema: {} },
    { name: 'airlock_propose_chain_change', description: '', inputSchema: {} },
  ];

  it('registers nothing when the browser has no WebMCP', () => {
    delete (globalThis as any).navigator;
    expect(registerTools(manifest).registered).toBe(0);
  });

  it('ignores a modelContext that is not shaped like one', () => {
    (globalThis as any).navigator = { modelContext: 'yes please' };
    expect(registerTools(manifest).registered).toBe(0);
    (globalThis as any).navigator = { modelContext: {} };
    expect(registerTools(manifest).registered).toBe(0);
    delete (globalThis as any).navigator;
  });

  it('registers read and propose tools when WebMCP is present', () => {
    const seen: unknown[] = [];
    (globalThis as any).navigator = {
      modelContext: { registerTool: (d: unknown) => seen.push(d) },
    };
    expect(registerTools(manifest).registered).toBe(4);
    expect(seen).toHaveLength(4);
    delete (globalThis as any).navigator;
  });

  it('never registers a tool that could approve, apply, or control', () => {
    expect(isRegisterableTool('airlock_approve_proposal')).toBe(false);
    expect(isRegisterableTool('airlock_reject_proposal')).toBe(false);
    expect(isRegisterableTool('airlock_apply_proposal')).toBe(false);
    expect(isRegisterableTool('airlock_edit_proposal')).toBe(false);
    expect(isRegisterableTool('airlock_pin_session')).toBe(false);
    expect(isRegisterableTool('airlock_unpin_session')).toBe(false);
    expect(isRegisterableTool('airlock_execute_anything')).toBe(false);
    expect(isRegisterableTool('airlock_control_router')).toBe(false);
    expect(isRegisterableTool('airlock_list_sessions')).toBe(true);
    expect(isRegisterableTool('airlock_propose_session_handoff')).toBe(true);
  });

  it('drops a forbidden tool even when the server offers it', () => {
    const hostile = [...manifest, { name: 'airlock_approve_proposal', description: '', inputSchema: {} }];
    const allowed = registerableTools(hostile).map((t) => t.name);
    expect(allowed).not.toContain('airlock_approve_proposal');
    const registered: string[] = [];
    (globalThis as any).navigator = {
      modelContext: { registerTool: (d: any) => registered.push(d.name) },
    };
    const result = registerTools(hostile);
    expect(result.registered).toBe(4);
    expect(result.skipped).toEqual(['airlock_approve_proposal']);
    expect(registered).not.toContain('airlock_approve_proposal');
    delete (globalThis as any).navigator;
  });

  it('treats a browser that throws on registration as absent', () => {
    (globalThis as any).navigator = {
      modelContext: {
        registerTool: () => {
          throw new Error('refused');
        },
      },
    };
    expect(registerTools(manifest).registered).toBe(0);
    delete (globalThis as any).navigator;
  });
});
