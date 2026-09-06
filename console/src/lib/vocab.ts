// The state and status vocabulary. One place decides the word, the shape and
// the tone for every state, so no component invents its own.
//
// Colour is never the only carrier: every descriptor has a `label` that is
// rendered as text and a `glyph` that is rendered as a shape.

import { countdown } from './time';
import type { BlockedReason, RouteState, SessionState } from '../types';

export type Tone = 'ok' | 'attn' | 'muted' | 'faint';
export type GlyphId =
  | 'run'
  | 'block'
  | 'idle'
  | 'end'
  | 'cool'
  | 'unav'
  | 'unknown'
  | 'arrow'
  | 'caret'
  | 'search';

export interface Descriptor {
  label: string;
  glyph: GlyphId;
  tone: Tone;
}

const BLOCKED_REASONS: Record<BlockedReason, string> = {
  rate_limit: 'rate limited',
  provider_cooldown: 'provider cooling',
  context_overflow: 'conversation too large',
  chain_exhausted: 'chain exhausted',
};

/** "rate_limit" -> "rate limited"; anything unrecognised keeps its raw name. */
export function blockedReasonWords(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return BLOCKED_REASONS[reason as BlockedReason] ?? reason.replace(/_/g, ' ');
}

export function sessionState(
  state: SessionState,
  blockedReason?: string | null,
): Descriptor {
  switch (state) {
    case 'running':
      return { label: 'Running', glyph: 'run', tone: 'ok' };
    case 'blocked': {
      const why = blockedReasonWords(blockedReason);
      return { label: why ? `Blocked, ${why}` : 'Blocked', glyph: 'block', tone: 'attn' };
    }
    case 'idle':
      return { label: 'Idle', glyph: 'idle', tone: 'muted' };
    case 'ended':
      return { label: 'Ended', glyph: 'end', tone: 'faint' };
    default:
      return { label: String(state), glyph: 'unknown', tone: 'faint' };
  }
}

/** Order the rail groups appear in. Blocked always first. */
export const SESSION_GROUP_ORDER: SessionState[] = ['blocked', 'running', 'idle', 'ended'];

export const SESSION_GROUP_TITLES: Record<SessionState, string> = {
  blocked: 'Needs attention',
  running: 'Running',
  idle: 'Idle',
  ended: 'Ended',
};

export function routeState(
  status: RouteState,
  cooldownRemainingSeconds?: number | null,
): Descriptor {
  switch (status) {
    case 'ready':
      return { label: 'Ready', glyph: 'run', tone: 'ok' };
    case 'cooling':
      return { label: countdown(cooldownRemainingSeconds), glyph: 'cool', tone: 'attn' };
    case 'unavailable':
      return { label: 'Unavailable', glyph: 'unav', tone: 'faint' };
    default:
      return { label: String(status), glyph: 'unknown', tone: 'faint' };
  }
}

export const UNKNOWN: Descriptor = { label: 'unknown', glyph: 'unknown', tone: 'faint' };

/** A route's fit is three-valued. Null and absence must never look like false. */
export function contextFitLabel(fits: boolean | null | undefined): string {
  return fits === true ? 'fits' : fits === false ? 'does not fit' : 'fit unknown';
}

/** The tier tag beside a model. `metered` gets its own dashed mark. */
export function categoryLabel(category: string): string {
  switch (category) {
    case 'included':
      return 'included';
    case 'extra':
      return 'extra';
    case 'metered':
      return 'metered';
    default:
      return 'tier unknown';
  }
}

/** Timeline filter groups, in the order the chips appear. */
export const KIND_GROUPS = ['all', 'handoffs', 'overflow', 'cooldowns', 'requests', 'other'] as const;
export type KindGroup = (typeof KIND_GROUPS)[number];

export const KIND_GROUP_TITLES: Record<KindGroup, string> = {
  all: 'All',
  handoffs: 'Handoffs',
  overflow: 'Overflow',
  cooldowns: 'Cooldowns',
  requests: 'Requests',
  other: 'Other',
};

const HANDOFF_KINDS = new Set([
  'rate_limit_failover_attempted',
  'rate_limit_failover_succeeded',
  'rate_limit_chain_exhausted',
  'failover_overflow_attempted',
  'failover_overflow_succeeded',
  'session_model_pinned',
  'session_model_unpinned',
  'background_model_substituted',
]);
const OVERFLOW_KINDS = new Set([
  'failover_overflow_attempted',
  'failover_overflow_succeeded',
  'failover_overflow_skipped',
  'failover_shrink_compacted',
  'failover_shrink_truncated',
  'failover_shrink_failed',
  'overflow_chain_exhausted',
  'upstream_context_overflow',
]);
const COOLDOWN_KINDS = new Set([
  'rate_limit_cooldown_skipped',
  'rate_limit_provider_cooldown',
  'rate_limit_chain_exhausted',
  'anthropic_rate_limit_passthrough',
]);

/** Which chips a given event kind answers to. A kind may belong to several. */
export function kindGroups(kind: string | undefined): KindGroup[] {
  const groups: KindGroup[] = ['all'];
  if (!kind) {
    groups.push('requests');
    return groups;
  }
  if (HANDOFF_KINDS.has(kind)) groups.push('handoffs');
  if (OVERFLOW_KINDS.has(kind)) groups.push('overflow');
  if (COOLDOWN_KINDS.has(kind)) groups.push('cooldowns');
  if (groups.length === 1) groups.push('other');
  return groups;
}

/**
 * Which route facts are missing from every route in a group.
 *
 * Repeating "tier unknown, window unknown, fit unknown" on fifteen rows buries
 * the two facts that are known. When a field is missing everywhere, the page
 * says so once for the whole section instead.
 */
export interface UnknownFields {
  tier: boolean;
  window: boolean;
  fit: boolean;
}

export function sharedUnknowns(
  routes: { category?: string; context_window?: number | null; fits_context?: boolean | null }[],
): UnknownFields {
  if (!routes.length) return { tier: false, window: false, fit: false };
  return {
    tier: routes.every((r) => !r.category || r.category === 'unknown'),
    window: routes.every((r) => r.context_window == null),
    fit: routes.every((r) => r.fits_context == null),
  };
}

/**
 * One sentence naming exactly the fields that are unknown for every route.
 *
 * `current` means the session's router already speaks the console contract,
 * so window and fit are only waiting on the first foreground request, and
 * tier is simply not exposed for these routes. Without it the missing fields
 * point at an older router that has to be updated.
 */
export function sharedUnknownNotice(unknowns: UnknownFields, current = false): string | null {
  const names = [
    unknowns.tier && !current ? 'Tier' : null,
    unknowns.window ? 'window' : null,
    unknowns.fit ? 'fit' : null,
  ].filter((x): x is string => x !== null);
  if (!names.length) {
    return current && unknowns.tier ? 'Tier is unknown for these routes.' : null;
  }
  const list =
    names.length === 1
      ? names[0]
      : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
  const verb = names.length === 1 ? 'is' : 'are';
  const sentence = current
    ? `${list} ${verb} unknown until this session sends its first request.`
    : `${list} ${verb} unknown until this session's router is updated.`;
  return sentence.replace(/^./, (c) => c.toUpperCase());
}
