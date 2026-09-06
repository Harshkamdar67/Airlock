// "What unblocks this": the one derived thing on the page.
//
// It answers, from data the API already sends, the question a person asks at
// 2 AM: why is this stuck, when does it stop being stuck by itself, and what
// could move it right now. It invents no fields; everything here comes from
// routes, cooldowns, chains and the session's own context size.

import { compactTokens, spokenDuration } from './time';
import { joinWords, providerName, titled } from './names';
import type { RouteStatus, SessionDetail } from '../types';

export interface ResolutionOption {
  tone: 'ok' | 'blocked' | 'unknown';
  text: string;
  detail?: string;
}

export interface Resolution {
  lead: string;
  options: ResolutionOption[];
}

function shortOf(routes: RouteStatus[], model: string): string {
  return routes.find((r) => r.model === model)?.short_name ?? model;
}

/**
 * Routes the selected session's frozen active-model chain can try.
 *
 * A router that declares no chain for the active model derives one at need, so
 * the console must not pretend the candidate set is empty. It falls back to
 * every enabled route, and `declared` tells the caller to word it honestly.
 */
export function routesInActiveChain(d: SessionDetail): {
  routes: RouteStatus[];
  declared: boolean;
} {
  const chain = d.chains?.[d.active_model];
  if (!chain?.length) return { routes: d.routes ?? [], declared: false };
  const models = new Set([d.active_model, ...chain]);
  return { routes: (d.routes ?? []).filter((r) => models.has(r.model)), declared: true };
}

/** Soonest relevant cooldown that will expire on its own, in seconds. */
function earliestRelease(
  d: SessionDetail,
  routes: RouteStatus[],
): { seconds: number; provider: string } | null {
  const models = new Set(routes.map((r) => r.model));
  const providers = new Set(routes.map((r) => r.provider));
  let best: { seconds: number; provider: string } | null = null;
  for (const c of d.cooldowns ?? []) {
    const relevant =
      c.scope === 'provider' ? providers.has(c.provider) : !!c.model && models.has(c.model);
    if (!relevant) continue;
    if (typeof c.remaining_seconds !== 'number' || c.remaining_seconds <= 0) continue;
    if (!best || c.remaining_seconds < best.seconds) {
      best = { seconds: c.remaining_seconds, provider: c.provider };
    }
  }
  if (best) return best;
  for (const r of routes) {
    if (r.status !== 'cooling') continue;
    const s = r.cooldown_remaining_seconds;
    if (typeof s !== 'number' || s <= 0) continue;
    if (!best || s < best.seconds) best = { seconds: s, provider: r.provider };
  }
  return best;
}

function leadSentence(d: SessionDetail, routes: RouteStatus[], declared: boolean): string {
  const active = titled(shortOf(d.routes ?? [], d.active_model));
  const release = earliestRelease(d, routes);
  const frees = release
    ? ` ${providerName(release.provider)} frees up in ${spokenDuration(release.seconds)}.`
    : '';

  switch (d.blocked_reason) {
    case 'chain_exhausted':
      return declared
        ? `${active}’s handoff chain had nothing left to try.${frees}`
        : `${active} had nothing left to try.${frees}`;
    case 'rate_limit':
      return `${active} is rate limited.${frees}`;
    case 'provider_cooldown': {
      const activeProvider =
        d.routes?.find((r) => r.model === d.active_model)?.provider ?? d.root_provider;
      const activeCooldown = d.cooldowns?.find(
        (c) => c.scope === 'provider' && c.provider === activeProvider,
      );
      const provider = providerName(activeProvider);
      const activeSeconds = activeCooldown?.remaining_seconds;
      return typeof activeSeconds === 'number' && activeSeconds > 0
        ? `${provider} is cooling. It frees up in ${spokenDuration(activeSeconds)}.`
        : `${active} is blocked, but the router has not reported a cooldown for ${provider}.`;
    }
    case 'context_overflow': {
      const size = compactTokens(d.context?.input_tokens ?? null);
      const fits = routes.filter((r) => r.fits_context === true);
      if (fits.length) {
        // Something does fit, so the honest sentence is that the router did
        // not reach it, not that nothing is large enough.
        return `The conversation is ${size} tokens and the route it was on could not take it.`;
      }
      return declared
        ? `The conversation is ${size} tokens and no route in ${active}’s chain has a window that large.`
        : `The conversation is ${size} tokens and no enabled route has a window that large.`;
    }
    default:
      return `${active} is blocked.${frees}`;
  }
}

/** Free routes first, cheaper tiers before metered ones, larger windows first. */
function rankReady(a: RouteStatus, b: RouteStatus): number {
  const tier = (r: RouteStatus) => (r.category === 'included' ? 0 : r.metered ? 2 : 1);
  const d = tier(a) - tier(b);
  if (d !== 0) return d;
  return (b.context_window ?? 0) - (a.context_window ?? 0);
}

export function resolveBlocked(d: SessionDetail): Resolution | null {
  if (d.state !== 'blocked') return null;

  const { routes, declared } = routesInActiveChain(d);
  const ready = routes.filter((r) => r.status === 'ready');
  const fits = ready.filter((r) => r.fits_context === true).sort(rankReady);
  const unknownFit = ready.filter((r) => r.fits_context == null).sort(rankReady);
  const tooSmall = ready.filter((r) => r.fits_context === false);

  const options: ResolutionOption[] = [];

  if (fits.length) {
    const pick = fits.slice(0, 3);
    const names = pick.map((r) => r.short_name);
    const one = pick.length === 1;
    options.push({
      tone: 'ok',
      text: `${joinWords(names)} ${one ? 'is' : 'are'} ready now and ${one ? 'fits' : 'fit'} this conversation.`,
      detail: one
        ? `${pick[0].provider}${pick[0].metered ? ', extra usage' : ''}`
        : undefined,
    });
  }

  if (unknownFit.length) {
    const pick = unknownFit.slice(0, 3);
    const names = joinWords(pick.map((r) => r.short_name));
    const one = pick.length === 1;
    options.push({
      tone: 'unknown',
      text: `${names} ${one ? 'is' : 'are'} ready, but the router cannot tell whether this conversation fits ${one ? 'its' : 'their'} window.`,
      detail: one
        ? `${pick[0].provider}${pick[0].metered ? ', extra usage' : ''}`
        : undefined,
    });
  }

  if (tooSmall.length) {
    const byWindow = new Map<number | null, RouteStatus[]>();
    for (const r of tooSmall) {
      const w = r.context_window ?? null;
      if (!byWindow.has(w)) byWindow.set(w, []);
      byWindow.get(w)!.push(r);
    }
    const size = compactTokens(d.context?.input_tokens ?? null);
    for (const [w, group] of [...byWindow.entries()].slice(0, 2)) {
      const names = joinWords(group.map((r) => r.short_name));
      const one = group.length === 1;
      options.push({
        tone: 'blocked',
        text: w
          ? `${names} ${one ? 'has' : 'have'} room, but ${size} does not fit ${one ? 'its' : 'their'} ${compactTokens(w)} window.`
          : `${names} ${one ? 'has' : 'have'} room, but the conversation does not fit ${one ? 'its' : 'their'} window.`,
      });
    }
  }

  if (!options.length) {
    const release = earliestRelease(d, routes);
    options.push({
      tone: 'blocked',
      text: release
        ? `Nothing is ready. The first route opens in ${spokenDuration(release.seconds)}.`
        : 'Nothing is ready, and no route has told the router when it will be.',
    });
  }

  return { lead: leadSentence(d, routes, declared), options: options.slice(0, 3) };
}
