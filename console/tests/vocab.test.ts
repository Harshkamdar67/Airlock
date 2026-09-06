// The state and status vocabulary. The rule under test is that colour is never
// the only carrier: every descriptor has a word and a shape as well as a tone.

import { describe, expect, it } from 'vitest';
import {
  blockedReasonWords,
  categoryLabel,
  contextFitLabel,
  kindGroups,
  routeState,
  sessionState,
  SESSION_GROUP_ORDER,
  SESSION_GROUP_TITLES,
  UNKNOWN,
} from '../src/lib/vocab';
import type { SessionState } from '../src/types';

describe('session states', () => {
  it('names each state in words', () => {
    expect(sessionState('running').label).toBe('Running');
    expect(sessionState('idle').label).toBe('Idle');
    expect(sessionState('ended').label).toBe('Ended');
    expect(sessionState('blocked').label).toBe('Blocked');
  });

  it('spells the blocked reason out', () => {
    expect(sessionState('blocked', 'rate_limit').label).toBe('Blocked, rate limited');
    expect(sessionState('blocked', 'provider_cooldown').label).toBe('Blocked, provider cooling');
    expect(sessionState('blocked', 'context_overflow').label).toBe('Blocked, conversation too large');
    expect(sessionState('blocked', 'chain_exhausted').label).toBe('Blocked, chain exhausted');
  });

  it('keeps an unfamiliar reason readable instead of hiding it', () => {
    expect(blockedReasonWords('some_new_reason')).toBe('some new reason');
    expect(blockedReasonWords(null)).toBeNull();
  });

  it('gives every state its own shape', () => {
    const shapes = (['running', 'blocked', 'idle', 'ended'] as SessionState[]).map(
      (s) => sessionState(s).glyph,
    );
    expect(new Set(shapes).size).toBe(4);
  });

  it('reserves the accent tone for the state that needs a decision', () => {
    expect(sessionState('blocked').tone).toBe('attn');
    expect(sessionState('running').tone).not.toBe('attn');
    expect(sessionState('idle').tone).not.toBe('attn');
    expect(sessionState('ended').tone).not.toBe('attn');
  });

  it('puts blocked first in the rail', () => {
    expect(SESSION_GROUP_ORDER[0]).toBe('blocked');
    expect(SESSION_GROUP_TITLES.blocked).toBe('Needs attention');
  });
});

describe('route states', () => {
  it('reads ready and unavailable as words', () => {
    expect(routeState('ready').label).toBe('Ready');
    expect(routeState('unavailable').label).toBe('Unavailable');
  });

  it('turns a cooldown into a countdown a person would say', () => {
    expect(routeState('cooling', 2890).label).toBe('48m left');
    expect(routeState('cooling', 45).label).toBe('under a minute left');
    expect(routeState('cooling', 7200).label).toBe('2h left');
  });

  it('never counts below zero, and never invents a number', () => {
    expect(routeState('cooling', 0).label).toBe('any moment');
    expect(routeState('cooling', -5).label).toBe('any moment');
    expect(routeState('cooling', null).label).toBe('unknown');
    expect(routeState('cooling', undefined).label).toBe('unknown');
  });

  it('gives every route state its own shape', () => {
    const shapes = [routeState('ready').glyph, routeState('cooling').glyph, routeState('unavailable').glyph];
    expect(new Set(shapes).size).toBe(3);
  });
});

describe('tier and unknown', () => {
  it('names the tiers', () => {
    expect(categoryLabel('included')).toBe('included');
    expect(categoryLabel('extra')).toBe('extra');
    expect(categoryLabel('metered')).toBe('metered');
  });

  it('says tier unknown rather than guessing', () => {
    expect(categoryLabel('unknown')).toBe('tier unknown');
    expect(categoryLabel('something-new')).toBe('tier unknown');
  });

  it('keeps fit unknown distinct from does not fit', () => {
    expect(contextFitLabel(true)).toBe('fits');
    expect(contextFitLabel(false)).toBe('does not fit');
    expect(contextFitLabel(null)).toBe('fit unknown');
    expect(contextFitLabel(undefined)).toBe('fit unknown');
  });

  it('has a single unknown descriptor with a word and a shape', () => {
    expect(UNKNOWN.label).toBe('unknown');
    expect(UNKNOWN.glyph).toBe('unknown');
  });
});

describe('timeline kind groups', () => {
  it('puts a plain request under requests', () => {
    expect(kindGroups(undefined)).toEqual(['all', 'requests']);
  });

  it('lets a kind answer to more than one chip', () => {
    expect(kindGroups('rate_limit_chain_exhausted')).toEqual(['all', 'handoffs', 'cooldowns']);
    expect(kindGroups('failover_overflow_attempted')).toEqual(['all', 'handoffs', 'overflow']);
  });

  it('classifies success diagnostics under their handoff and overflow filters', () => {
    expect(kindGroups('rate_limit_failover_succeeded')).toEqual(['all', 'handoffs']);
    expect(kindGroups('failover_overflow_succeeded')).toEqual(['all', 'handoffs', 'overflow']);
  });

  it('files pin and unpin as handoffs, and root selection as other', () => {
    expect(kindGroups('session_model_pinned')).toEqual(['all', 'handoffs']);
    expect(kindGroups('session_model_unpinned')).toEqual(['all', 'handoffs']);
    expect(kindGroups('session_root_selected')).toEqual(['all', 'other']);
  });

  it('files anything else under other, never nowhere', () => {
    expect(kindGroups('router_restarted')).toEqual(['all', 'other']);
    expect(kindGroups('router_reheated_the_kettle')).toEqual(['all', 'other']);
  });
});
