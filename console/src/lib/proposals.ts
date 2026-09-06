// Proposal vocabulary and the wording of the one primary button.
//
// The rule from phase 1 holds: a status is a word plus a shape plus a colour,
// never a colour alone, and the button says exactly what pressing it will do.

import { titled } from './names';
import { spokenDuration } from './time';
import type { Descriptor } from './vocab';
import type { Proposal, ProposalStatus, SessionHandoffProposal } from '../types';

export const PROPOSAL_STATUS: Record<ProposalStatus, Descriptor> = {
  pending: { label: 'Waiting for you', glyph: 'block', tone: 'attn' },
  applying: { label: 'Applying', glyph: 'cool', tone: 'attn' },
  applied: { label: 'Applied', glyph: 'run', tone: 'ok' },
  rejected: { label: 'Rejected', glyph: 'end', tone: 'faint' },
  expired: { label: 'Expired', glyph: 'end', tone: 'faint' },
  superseded: { label: 'Superseded', glyph: 'end', tone: 'faint' },
  conflicted: { label: 'Conflicted', glyph: 'unav', tone: 'attn' },
  failed: { label: 'Failed', glyph: 'unav', tone: 'attn' },
};

export function proposalStatus(status: string): Descriptor {
  return (
    PROPOSAL_STATUS[status as ProposalStatus] ?? {
      label: status,
      glyph: 'unknown',
      tone: 'faint',
    }
  );
}

/** Only these can be edited, per CONTROL-SPEC. */
export const EDITABLE_STATUSES: ProposalStatus[] = ['pending', 'failed', 'conflicted'];

export function isEditable(status: string): boolean {
  return EDITABLE_STATUSES.includes(status as ProposalStatus);
}

/** A proposal a person can still decide on. */
export function isActionable(status: string): boolean {
  return status === 'pending' || status === 'failed' || status === 'conflicted';
}

/**
 * The primary button says exactly what will happen, naming the model in the
 * words the rest of the page uses.
 *   "Approve and use Opus"
 *   "Approve and restore Fable"
 */
export function primaryLabel(
  proposal: SessionHandoffProposal,
  shortName: (model: string | null | undefined) => string,
  rootModel: string,
): string {
  if (proposal.operation === 'restore_root') {
    return `Approve and restore ${titled(shortName(rootModel))}`;
  }
  return `Approve and use ${titled(shortName(proposal.target_model))}`;
}

/** The sentence above the button, in the same plain voice as the timeline. */
export function proposalLead(
  proposal: SessionHandoffProposal,
  shortName: (model: string | null | undefined) => string,
  rootModel: string,
): string {
  const who = proposal.created_by === 'human' ? 'You' : 'An agent';
  if (proposal.operation === 'restore_root') {
    return `${who} proposed restoring this session to ${titled(shortName(rootModel))}.`;
  }
  return `${who} proposed moving this session to ${titled(shortName(proposal.target_model))}.`;
}

/** "expires in 12 minutes", or an honest past-tense once it has lapsed. */
export function expiryWords(expiresAt: string | null | undefined, now: number): string {
  if (!expiresAt) return 'no expiry reported';
  const at = Date.parse(expiresAt);
  if (!Number.isFinite(at)) return 'no expiry reported';
  const seconds = Math.round((at - now) / 1000);
  if (seconds <= 0) return 'expired';
  return `expires in ${spokenDuration(seconds)}`;
}

/**
 * How the metered fact reads. A metered target is only proposable with
 * allow_metered true, so the honest wording names who accepted the extra usage
 * rather than stating a bare permission.
 */
export function meteredWords(
  proposal: SessionHandoffProposal,
  metered: boolean,
): string | null {
  if (!metered) return null;
  if (!proposal.allow_metered) return 'metered, extra usage not allowed';
  const who = proposal.created_by === 'human' ? 'you' : 'the agent';
  return `metered, extra usage accepted by ${who}`;
}

/**
 * The reason the primary button must not be offered, or null when approving is
 * safe to attempt.
 *
 * The server refuses to apply a metered target unless the proposal itself
 * allows the extra usage, and it refuses to create one that does not. Such a
 * proposal should therefore never arrive, but if it ever does, the button must
 * not promise something the router will reject.
 */
export function approvalBlock(
  proposal: SessionHandoffProposal,
  metered: boolean,
): string | null {
  if (proposal.operation !== 'pin') return null;
  if (metered && !proposal.allow_metered) {
    return 'This route bills extra usage, and this proposal does not allow it.';
  }
  return null;
}

/** Newest first, and anything a person can act on before anything finished. */
export function sortProposals<T extends Proposal>(proposals: T[]): T[] {
  return [...proposals].sort((a, b) => {
    const act = Number(isActionable(b.status)) - Number(isActionable(a.status));
    if (act !== 0) return act;
    return (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0);
  });
}
