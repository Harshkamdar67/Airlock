// The Markdown incident report, built from the same SessionDetail the page is
// showing, so what is copied is what was on screen. No prompts, no responses,
// no provider error bodies, no identifiers beyond the session id the router
// already prints.

import { buildTimeline, sentenceText, type SentenceContext } from './sentences';
import { resolveBlocked } from './resolution';
import { clockTime, compactTokens, countdown, uptime } from './time';
import { hasWorkdir, sessionTitle, shortNameLookup, titled } from './names';
import { sessionState } from './vocab';
import { markdownCode as code, markdownText as md } from './markdown';
import type { SessionDetail } from '../types';

export function buildReport(d: SessionDetail, now: number): string {
  const short = shortNameLookup(d.routes);
  const ctx: SentenceContext = {
    short,
    windowOf: (model) => d.routes?.find((r) => r.model === model)?.context_window ?? null,
  };

  const lines: string[] = [];
  const state = sessionState(d.state, d.blocked_reason);
  const activeProvider = d.routes?.find((r) => r.model === d.active_model)?.provider ?? 'unknown';
  lines.push(`# ${md(sessionTitle(d))}, Airlock session ${code(d.id)}`);
  lines.push('');
  lines.push(`- State: ${md(state.label)}`);
  lines.push(`- Active model: ${code(short(d.active_model))} on ${md(activeProvider)}`);
  lines.push(`- Profile: ${code(d.profile)}`);
  lines.push(
    hasWorkdir(d)
      ? `- Working directory: ${code(d.workdir)}`
      : '- Working directory: not reported by this router',
  );
  lines.push(`- Uptime: ${uptime(d.started_at, now)}`);
  if (d.context?.input_tokens != null) {
    const win = d.context.window ? ` of ${compactTokens(d.context.window)}` : '';
    lines.push(`- Context: ${compactTokens(d.context.input_tokens)}${win}`);
  }
  lines.push('');

  const res = resolveBlocked(d);
  if (res) {
    lines.push('## What unblocks this');
    lines.push('');
    lines.push(md(res.lead));
    lines.push('');
    for (const o of res.options) {
      lines.push(`- ${md(o.text)}${o.detail ? ` ${md(`(${o.detail})`)}` : ''}`);
    }
    lines.push('');
  }

  lines.push('## Routes');
  lines.push('');
  for (const r of d.routes ?? []) {
    const status =
      r.status === 'cooling' ? `cooling, ${countdown(r.cooldown_remaining_seconds)}` : r.status;
    const fits =
      r.fits_context === true
        ? ', fits'
        : r.fits_context === false
          ? ', does not fit'
          : ', fit unknown';
    const win = r.context_window ? `${compactTokens(r.context_window)} window` : 'window unknown';
    lines.push(
      `- ${code(r.short_name)} (${code(r.model)}, ${md(r.provider)}, ${md(r.category)}): ` +
        `${md(status)}, ${md(win)}${md(fits)}`,
    );
  }
  lines.push('');

  const chain = d.chains?.[d.active_model];
  if (chain?.length) {
    lines.push(`## Chain for ${code(short(d.active_model))}`);
    lines.push('');
    lines.push([d.active_model, ...chain].map((mm) => code(short(mm))).join(' -> '));
    lines.push('');
  }

  lines.push('## Timeline, newest first');
  lines.push('');
  for (const group of buildTimeline(d.events ?? [], ctx)) {
    lines.push(`### ${group.label}`);
    lines.push('');
    for (const row of group.rows) {
      if (row.type === 'quiet') {
        lines.push(`- ${md(row.summary)}`);
      } else {
        const s = row.sentence;
        const hop = s.hop ? ` (${code(s.hop.from)} -> ${code(s.hop.to)})` : '';
        lines.push(`- ${code(clockTime(s.timestamp))} ${md(sentenceText(s))}${hop}`);
        if (s.note) lines.push(`  - ${md(s.note)}`);
      }
    }
    lines.push('');
  }

  if (d.workers?.length) {
    lines.push('## Workers');
    lines.push('');
    for (const w of d.workers) {
      lines.push(`- ${code(titled(short(w.model)))}: ${w.requests} requests`);
    }
    lines.push('');
  }

  return lines.join('\n');
}

/** Clipboard write with a textarea fallback for a page served without TLS. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the manual path.
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
