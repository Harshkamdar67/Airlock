// The whole-session numbers a person asks for after the fact: how big the
// context got, how often it was compacted, how many turns and tool calls, how
// many subagents, and when the session ran through Airlock rather than plain
// Claude Code. All of it comes from the history index over the transcript
// and the router launch log, so it is the same for a live and a past session.

import { Bar } from './Glyph';
import { clockTime, compactTokens, relativeTime } from '../lib/time';
import { profileLabel, shortNameLookup } from '../lib/names';
import type { AirlockPeriod, HistoryFacts, HistoryItem } from '../types';

type Facts = Pick<
  HistoryFacts,
  | 'prompts'
  | 'replies'
  | 'tool_calls'
  | 'compactions'
  | 'peak_context'
  | 'window'
  | 'subagents'
  | 'output_tokens'
> & { periods?: AirlockPeriod[]; airlock_inferred?: boolean; started_at?: string | null };

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div class="stat">
      <dt class="micro">{label}</dt>
      <dd class="tnum">
        {value}
        {note ? <span class="faint"> {note}</span> : null}
      </dd>
    </div>
  );
}

export function PeriodStrip({
  periods,
  inferred,
  startedAt,
  now,
}: {
  periods: AirlockPeriod[];
  inferred: boolean;
  startedAt: string | null | undefined;
  now: number;
}) {
  const short = shortNameLookup(undefined);
  if (!periods.length) {
    return (
      <p class="periods-note faint">
        {inferred
          ? 'Ran through Airlock at some point, since it answered on a non-Claude model, but no router launch was recorded for its directory. Launch records start with this Airlock version.'
          : 'No Airlock router was recorded for this directory, so as far as the record goes this session ran as plain Claude Code.'}
      </p>
    );
  }
  return (
    <ol class="periods" aria-label="When this session ran through Airlock">
      {periods.map((p) => (
        <li key={`${p.from}-${p.root_model}`} class={`period${p.open ? ' period-open' : ''}`}>
          <span class="period-when tnum">
            {clockTime(p.from, false)}
            {' to '}
            {p.open ? 'now' : clockTime(p.to, false)}
          </span>
          <span class="period-what">
            {`Airlock, ${profileLabel(p.profile)}`}
            {p.root_model ? `, root ${short(p.root_model)}` : ''}
          </span>
          <span class="period-ago faint">{p.open ? 'still running' : relativeTime(p.to, now)}</span>
        </li>
      ))}
      {startedAt && periods[0] && startedAt < periods[0].from ? (
        <li class="period period-plain">
          <span class="period-when tnum">{`${clockTime(startedAt, false)} to ${clockTime(periods[0].from, false)}`}</span>
          <span class="period-what">Plain Claude Code before the first router</span>
          <span class="period-ago" />
        </li>
      ) : null}
    </ol>
  );
}

export function SessionStats({ facts, now }: { facts: Facts | HistoryItem; now: number }) {
  const peak = facts.peak_context ?? null;
  const window = facts.window ?? null;
  const pct = peak != null && window ? Math.round((peak / window) * 100) : null;
  return (
    <section class="stats" aria-label="Session totals">
      <dl class="statgrid">
        <div class="stat stat-peak">
          <dt class="micro">Peak context</dt>
          <dd class="tnum">
            {peak == null ? 'unknown' : compactTokens(peak)}
            {window && peak != null ? <span class="faint">{` of ${compactTokens(window)}, ${pct}%`}</span> : null}
            {peak != null && window ? <Bar used={peak} total={window} label="Peak context" /> : null}
          </dd>
        </div>
        <Stat label="Compactions" value={String(facts.compactions ?? 0)} />
        <Stat label="Prompts" value={String(facts.prompts ?? 0)} note={`${facts.replies ?? 0} replies`} />
        <Stat label="Tool calls" value={String(facts.tool_calls ?? 0)} />
        <Stat label="Subagents" value={String(facts.subagents ?? 0)} />
        <Stat label="Output" value={compactTokens(facts.output_tokens ?? 0)} note="tokens" />
      </dl>
      {'periods' in facts && facts.periods ? (
        <PeriodStrip
          periods={facts.periods}
          inferred={facts.airlock_inferred === true}
          startedAt={facts.started_at}
          now={now}
        />
      ) : null}
    </section>
  );
}
