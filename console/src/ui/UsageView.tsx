// Usage over time and what carried it. Every number here is a count of
// something that happened, read from the session transcripts: prompts,
// replies, tool calls, output tokens, compactions, sessions active. Never a
// price. One measure per chart, one axis per chart; the only multi-series
// chart is replies by provider, which uses the provider colours in a fixed
// order and is always accompanied by a legend, a share strip, and a table.
//
// Each chart answers its question without reading the axes: the total for
// the range and how it compares with the previous range, the average as a
// dashed line, the busiest period labelled, weekends dimmed, today marked.

import { useEffect, useMemo, useState } from 'preact/hooks';
import { getUsage } from '../lib/api';
import { modelDisplayName, providerName } from '../lib/names';
import { compactTokens, relativeTime } from '../lib/time';
import type { UsagePeriod, UsageReport } from '../types';

type Range = '7d' | '30d' | '90d' | 'all';
type Group = 'day' | 'week' | 'month';
type Measure = 'prompts' | 'replies' | 'tool_calls' | 'output_tokens' | 'sessions' | 'compactions';

const RANGE_DAYS: Record<Range, number | null> = { '7d': 7, '30d': 30, '90d': 90, all: null };
const RANGE_WORDS: Record<Range, string> = { '7d': '7 days', '30d': '30 days', '90d': '90 days', all: 'all time' };
const MEASURES: { key: Measure; label: string; tokens?: boolean }[] = [
  { key: 'prompts', label: 'Prompts' },
  { key: 'tool_calls', label: 'Tool calls' },
  { key: 'output_tokens', label: 'Output tokens', tokens: true },
  { key: 'sessions', label: 'Sessions active' },
  { key: 'compactions', label: 'Compactions' },
];
const CALENDAR_MEASURES: { key: Measure; label: string; tokens?: boolean }[] = [
  { key: 'prompts', label: 'Prompts' },
  { key: 'tool_calls', label: 'Tool calls' },
  { key: 'output_tokens', label: 'Output tokens', tokens: true },
  { key: 'sessions', label: 'Sessions' },
];
/** Fixed categorical order, never cycled. Anything else folds into "other". */
const PROVIDER_ORDER = ['anthropic', 'openai', 'grok', 'openrouter'] as const;
const CALENDAR_WEEKS = 53;
const DAY_MS = 86400 * 1000;

function sinceFor(range: Range, now: number): string | undefined {
  const days = RANGE_DAYS[range];
  if (days == null) return undefined;
  return new Date(now - days * DAY_MS).toISOString();
}

function fmt(value: number, tokens = false): string {
  return tokens ? compactTokens(value) : value.toLocaleString();
}

function periodLabel(period: string, group: Group): string {
  if (group === 'month') {
    const [y, m] = period.split('-');
    return new Date(Number(y), Number(m) - 1, 1).toLocaleString(undefined, { month: 'short', year: '2-digit' });
  }
  if (group === 'week') return period.replace(/^\d{4}-/, '');
  const [y, m, d] = period.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleString(undefined, { month: 'short', day: 'numeric' });
}

function longDate(day: string): string {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
}

function isWeekend(period: string, group: Group): boolean {
  if (group !== 'day') return false;
  const [y, m, d] = period.split('-').map(Number);
  const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return dow === 0 || dow === 6;
}

function todayKey(): string {
  return new Date().toISOString().slice(0, 10);
}

function cumulative(values: number[]): number[] {
  let run = 0;
  return values.map((v) => (run += v));
}

/** Round, evenly spaced gridlines: 1, 2, or 5 times a power of ten. */
function niceTicks(max: number, count = 3, integer = true): number[] {
  if (max <= 0) return [0];
  const rough = max / count;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const candidates = [1, 2, 2.5, 5, 10].map((f) => f * mag);
  let step = candidates.find((c) => c >= rough) ?? 10 * mag;
  // Counts of things never need a half-step gridline.
  if (integer && step < 1) step = 1;
  const ticks: number[] = [];
  for (let v = 0; v <= max + step * 0.001 && ticks.length < 8; v += step) ticks.push(v);
  return ticks;
}

function pctChange(current: number, previous: number): number | null {
  if (!previous) return null;
  return Math.round(((current - previous) / previous) * 100);
}

// ---- hover ---------------------------------------------------------------
// One tooltip per chart, positioned inside the figure. The hit area is the
// whole period column, far larger than the mark, so a thin bar is easy to hit.

interface Tip {
  x: number;
  y: number;
  title: string;
  lines: string[];
}

function useTip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const show = (e: PointerEvent | MouseEvent, title: string, lines: string[]) => {
    const target = e.currentTarget as Element;
    const figure = target.closest('.chart') as HTMLElement | null;
    if (!figure) return;
    const box = figure.getBoundingClientRect();
    setTip({ x: e.clientX - box.left, y: e.clientY - box.top, title, lines });
  };
  const hide = () => setTip(null);
  return { tip, show, hide };
}

function TipBox({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  const flip = tip.x > 360;
  return (
    <div class={`tip${flip ? ' tip-left' : ''}`} style={`left:${tip.x}px;top:${tip.y}px`} role="status">
      <div class="tip-title">{tip.title}</div>
      {tip.lines.map((line) => (
        <div class="tip-line tnum" key={line}>{line}</div>
      ))}
    </div>
  );
}

/** "+12% vs previous 30 days", or nothing when there is no basis. */
function Delta({ current, previous, range, invert = false }: { current: number; previous: number | null; range: Range; invert?: boolean }) {
  if (previous == null || range === 'all') return null;
  const change = pctChange(current, previous);
  if (change == null) return <span class="delta delta-flat">no previous {RANGE_WORDS[range]}</span>;
  const up = change > 0;
  const tone = change === 0 ? 'flat' : (up && !invert) || (!up && invert) ? 'up' : 'down';
  // Past a tripling, a percentage stops being readable; say the multiple.
  const amount = change > 200 ? `${(current / previous).toFixed(current / previous >= 10 ? 0 : 1)}×` : `${Math.abs(change)}%`;
  return (
    <span class={`delta delta-${tone}`} title={`${fmt(previous)} in the previous ${RANGE_WORDS[range]}`}>
      {change === 0 ? 'same as' : `${up ? '▲' : '▼'} ${amount} vs`} previous {RANGE_WORDS[range]}
    </span>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const w = 96;
  const h = 26;
  const max = Math.max(1, ...values);
  const step = w / (values.length - 1);
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * (h - 3) - 1).toFixed(1)}`);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} class="spark" aria-hidden="true">
      <path d={`M${pts.join(' L')}`} class="spark-line" />
      <path d={`M0,${h} L${pts.join(' L')} L${w},${h} Z`} class="spark-fill" />
    </svg>
  );
}

// ---- charts ---------------------------------------------------------------

/** A single-series bar chart, or a step line when cumulative. */
function Bars({
  series,
  measure,
  label,
  tokens,
  group,
  cumulate,
  range,
  total: totalOverride,
  previous,
  invert,
}: {
  series: UsagePeriod[];
  measure: Measure;
  label: string;
  tokens?: boolean;
  group: Group;
  cumulate: boolean;
  range: Range;
  /** A whole-range total that is not the sum of the periods, such as unique sessions. */
  total?: number;
  /** The same total for the previous range, for the comparison line. */
  previous?: number | null;
  /** True when less is better, so the comparison colour flips. */
  invert?: boolean;
}) {
  const { tip, show, hide } = useTip();
  const raw = series.map((row) => row[measure]);
  const values = cumulate ? cumulative(raw) : raw;
  const max = Math.max(1, ...values);
  const width = 640;
  const height = 168;
  const padL = 48;
  const padB = 22;
  const padT = 20;
  const innerW = width - padL - 8;
  const innerH = height - padB - padT;
  const n = values.length;
  const slot = n ? innerW / n : innerW;
  const barW = Math.max(2, Math.min(28, slot - 2));
  const y = (v: number) => padT + innerH - (v / max) * innerH;
  const ticks = niceTicks(max, 3, !tokens);
  const total = totalOverride ?? raw.reduce((a, b) => a + b, 0);
  const active_periods = raw.filter((v) => v > 0).length;
  const average = n ? raw.reduce((a, b) => a + b, 0) / n : 0;
  const peakIndex = raw.length ? raw.indexOf(Math.max(...raw)) : -1;
  const points = values.map((v, i) => [padL + i * slot + slot / 2, y(v)] as const);
  const path = points.map(([px, py], i) => `${i === 0 ? 'M' : 'L'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ');
  const area = points.length
    ? `M${points[0][0].toFixed(1)},${(padT + innerH).toFixed(1)} L${points.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(' L')} L${points[points.length - 1][0].toFixed(1)},${(padT + innerH).toFixed(1)} Z`
    : '';
  const labelEvery = Math.max(1, Math.ceil(n / 8));
  const today = todayKey();
  // The average label sits at whichever end the busiest bar is not.
  const avgAtRight = peakIndex < n * 0.8;
  const [active, setActive] = useState<number | null>(null);
  const hover = (e: PointerEvent, i: number) => {
    setActive(i);
    const lines = cumulate
      ? [`${fmt(values[i], tokens)} cumulative`, `${fmt(raw[i], tokens)} this ${group}`]
      : [`${fmt(raw[i], tokens)} ${label.toLowerCase()}`, `${series[i].sessions} session${series[i].sessions === 1 ? '' : 's'} active`];
    if (!cumulate && average) {
      const diff = Math.round(((raw[i] - average) / average) * 100);
      lines.push(diff === 0 ? 'on the average' : `${Math.abs(diff)}% ${diff > 0 ? 'above' : 'below'} the average`);
    }
    show(e, periodLabel(series[i].period, group), lines);
  };
  const leave = () => {
    setActive(null);
    hide();
  };
  return (
    <figure class="chart">
      <figcaption>
        <span class="chart-title">{label}</span>
        <span class="chart-head tnum">
          <strong>{fmt(total, tokens)}</strong>
          <Delta current={total} previous={previous ?? null} range={range} invert={invert} />
        </span>
        <span class="chart-sub faint tnum">
          {n
            ? `${fmt(Math.round(average), tokens)} per ${group} on average · ${active_periods} of ${n} ${group}s active`
            : ''}
        </span>
      </figcaption>
      {n === 0 ? (
        <p class="section-unknown">Nothing in this range.</p>
      ) : (
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label} per ${group}`} class="chart-svg" onPointerLeave={leave}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={width - 8} y1={y(t)} y2={y(t)} class="chart-grid" />
              <text x={padL - 6} y={y(t) + 3} class="chart-tick" text-anchor="end">
                {fmt(t, tokens)}
              </text>
            </g>
          ))}
          {cumulate ? (
            <>
              <path d={area} class="chart-area" />
              <path d={path} class="chart-line" />
              {active != null ? <line x1={points[active][0]} x2={points[active][0]} y1={padT} y2={padT + innerH} class="chart-cross" /> : null}
              {points.map(([px, py], i) => (
                <circle key={i} cx={px} cy={py} r={active === i ? 5 : 3} class="chart-dot" />
              ))}
              {points.length ? (
                <text x={points[points.length - 1][0]} y={points[points.length - 1][1] - 8} class="chart-label" text-anchor="end">
                  {fmt(values[values.length - 1], tokens)}
                </text>
              ) : null}
            </>
          ) : (
            <>
              {values.map((v, i) => {
                const x = padL + i * slot + (slot - barW) / 2;
                const top = y(v);
                const cls = [
                  'chart-bar',
                  isWeekend(series[i].period, group) ? 'chart-bar-weekend' : '',
                  series[i].period === today ? 'chart-bar-today' : '',
                  i === peakIndex && v > 0 ? 'chart-bar-peak' : '',
                  active === i ? 'chart-bar-on' : '',
                ]
                  .filter(Boolean)
                  .join(' ');
                return <rect key={i} x={x} y={top} width={barW} height={Math.max(0, padT + innerH - top)} rx="2" class={cls} />;
              })}
              {average > 0 ? (
                <>
                  <line x1={padL} x2={width - 8} y1={y(average)} y2={y(average)} class="chart-avg" />
                  <text x={avgAtRight ? width - 8 : padL + 4} y={y(average) - 4} class="chart-label chart-label-avg" text-anchor={avgAtRight ? 'end' : 'start'}>
                    {`avg ${fmt(Math.round(average), tokens)}`}
                  </text>
                </>
              ) : null}
              {peakIndex >= 0 && raw[peakIndex] > 0 ? (
                <text
                  x={padL + peakIndex * slot + slot / 2}
                  y={y(raw[peakIndex]) - 5}
                  class="chart-label"
                  text-anchor={peakIndex > n * 0.8 ? 'end' : peakIndex < n * 0.2 ? 'start' : 'middle'}
                >
                  {fmt(raw[peakIndex], tokens)}
                </text>
              ) : null}
            </>
          )}
          {series.map((row, i) =>
            i % labelEvery === 0 || i === n - 1 ? (
              <text key={row.period} x={padL + i * slot + slot / 2} y={height - 6} class={`chart-tick${row.period === today ? ' chart-tick-today' : ''}`} text-anchor="middle">
                {row.period === today ? 'today' : periodLabel(row.period, group)}
              </text>
            ) : null,
          )}
          {series.map((row, i) => (
            <rect
              key={`hit-${row.period}`}
              x={padL + i * slot}
              y={padT}
              width={slot}
              height={innerH}
              class="chart-hit"
              onPointerEnter={(e) => hover(e, i)}
              onPointerMove={(e) => hover(e, i)}
            />
          ))}
        </svg>
      )}
      <TipBox tip={tip} />
    </figure>
  );
}

/** Replies by provider: a share strip for the whole range, then the stack per period. */
function ProviderStack({ series, group }: { series: UsagePeriod[]; group: Group }) {
  const { tip, show, hide } = useTip();
  const [active, setActive] = useState<number | null>(null);
  const providers = PROVIDER_ORDER.filter((p) => series.some((row) => (row.by_provider[p] ?? 0) > 0));
  const other = series.some((row) => Object.keys(row.by_provider).some((p) => !(PROVIDER_ORDER as readonly string[]).includes(p)));
  const keys = other ? [...providers, 'other'] : providers;
  const valueOf = (row: UsagePeriod, key: string) =>
    key === 'other'
      ? Object.entries(row.by_provider).reduce((n, [p, v]) => ((PROVIDER_ORDER as readonly string[]).includes(p) ? n : n + v), 0)
      : (row.by_provider[key] ?? 0);
  const totals = series.map((row) => keys.reduce((n, k) => n + valueOf(row, k), 0));
  const max = Math.max(1, ...totals);
  const width = 1320;
  const height = 180;
  const padL = 48;
  const padB = 22;
  const padT = 14;
  const innerW = width - padL - 8;
  const innerH = height - padB - padT;
  const n = series.length;
  const slot = n ? innerW / n : innerW;
  const barW = Math.max(2, Math.min(28, slot - 2));
  const y = (v: number) => padT + innerH - (v / max) * innerH;
  const labelEvery = Math.max(1, Math.ceil(n / 12));
  const grand = keys.map((k) => ({ key: k, total: series.reduce((s, row) => s + valueOf(row, k), 0) }));
  const grandTotal = grand.reduce((s, g) => s + g.total, 0);
  const share = (v: number) => (grandTotal ? Math.round((v / grandTotal) * 100) : 0);
  const name = (k: string) => (k === 'other' ? 'Other' : providerName(k));
  const hover = (e: PointerEvent, i: number) => {
    setActive(i);
    const row = series[i];
    const lines = keys
      .map((k) => [k, valueOf(row, k)] as const)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${name(k)}: ${v.toLocaleString()} (${totals[i] ? Math.round((v / totals[i]) * 100) : 0}%)`);
    show(e, periodLabel(row.period, group), [`${totals[i].toLocaleString()} replies`, ...lines]);
  };
  const leave = () => {
    setActive(null);
    hide();
  };
  return (
    <figure class="chart chart-wide">
      <figcaption>
        <span class="chart-title">Replies by provider</span>
        <span class="chart-head tnum">
          <strong>{grandTotal.toLocaleString()}</strong>
          <span class="faint"> replies in range</span>
        </span>
        <span class="legend" aria-label="Providers">
          {grand.map(({ key, total }) => (
            <span key={key} class="legend-item">
              <i class={`swatch swatch-${key}`} aria-hidden="true" />
              {name(key)}
              <span class="faint tnum">{` ${share(total)}%`}</span>
            </span>
          ))}
        </span>
      </figcaption>
      {grandTotal > 0 ? (
        <div class="share" role="img" aria-label={grand.map(({ key, total }) => `${name(key)} ${share(total)} percent`).join(', ')}>
          {grand
            .filter(({ total }) => total > 0)
            .map(({ key, total }) => (
              <span key={key} class={`share-seg seg-${key}`} style={`flex:${total}`} title={`${name(key)}: ${total.toLocaleString()} replies, ${share(total)}%`} />
            ))}
        </div>
      ) : null}
      {n === 0 ? (
        <p class="section-unknown">Nothing in this range.</p>
      ) : (
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Replies by provider per ${group}`} class="chart-svg" onPointerLeave={leave}>
          {niceTicks(max).map((t) => (
            <g key={t}>
              <line x1={padL} x2={width - 8} y1={y(t)} y2={y(t)} class="chart-grid" />
              <text x={padL - 6} y={y(t) + 3} class="chart-tick" text-anchor="end">{t.toLocaleString()}</text>
            </g>
          ))}
          {series.map((row, i) => {
            let acc = 0;
            const x = padL + i * slot + (slot - barW) / 2;
            return keys.map((key) => {
              const v = valueOf(row, key);
              if (!v) return null;
              const y1 = y(acc + v);
              const h = Math.max(0, y(acc) - y1 - (acc > 0 ? 2 : 0));
              acc += v;
              return <rect key={key} x={x} y={y1} width={barW} height={h} rx="2" class={`chart-seg seg-${key}${active === i ? ' chart-seg-on' : ''}`} />;
            });
          })}
          {series.map((row, i) =>
            i % labelEvery === 0 || i === n - 1 ? (
              <text key={row.period} x={padL + i * slot + slot / 2} y={height - 6} class="chart-tick" text-anchor="middle">
                {periodLabel(row.period, group)}
              </text>
            ) : null,
          )}
          {series.map((row, i) => (
            <rect
              key={`hit-${row.period}`}
              x={padL + i * slot}
              y={padT}
              width={slot}
              height={innerH}
              class="chart-hit"
              onPointerEnter={(e) => hover(e, i)}
              onPointerMove={(e) => hover(e, i)}
            />
          ))}
        </svg>
      )}
      <TipBox tip={tip} />
    </figure>
  );
}

/** How close sessions came to their context window: the compaction question. */
function PeakDistribution({ buckets, sessions }: { buckets: UsageReport['peak_buckets']; sessions: number }) {
  const rows: { key: keyof UsageReport['peak_buckets']; label: string; level: number }[] = [
    { key: 'under_25', label: 'Under 25%', level: 2 },
    { key: '25_to_50', label: '25% to 50%', level: 3 },
    { key: '50_to_75', label: '50% to 75%', level: 4 },
    { key: 'over_75', label: 'Over 75%', level: 5 },
  ];
  const known = rows.reduce((n, r) => n + buckets[r.key], 0);
  const max = Math.max(1, ...rows.map((r) => buckets[r.key]));
  const heavy = buckets['50_to_75'] + buckets.over_75;
  return (
    <figure class="chart chart-dist">
      <figcaption>
        <span class="chart-title">Peak context per session</span>
        <span class="chart-head tnum">
          <strong>{known ? `${Math.round((heavy / known) * 100)}%` : '–'}</strong>
          <span class="faint"> of sessions passed half the window</span>
        </span>
        <span class="chart-sub faint tnum">
          {`${known} of ${sessions} sessions with a known window${buckets.unknown ? `, ${buckets.unknown} unknown` : ''}`}
        </span>
      </figcaption>
      <ol class="dist">
        {rows.map((r) => (
          <li key={r.key} class="dist-row" title={`${r.label}: ${buckets[r.key]} sessions`}>
            <span class="dist-label">{r.label}</span>
            <span class="dist-bar" aria-hidden="true">
              <i class={`cal-${r.level}`} style={`width:${Math.max(1, Math.round((buckets[r.key] / max) * 100))}%`} />
            </span>
            <span class="dist-value tnum">
              {buckets[r.key]}
              <span class="faint">{known ? ` · ${Math.round((buckets[r.key] / known) * 100)}%` : ''}</span>
            </span>
          </li>
        ))}
      </ol>
    </figure>
  );
}

/**
 * A year of days as a contribution calendar: one cell per day, one hue
 * stepped by magnitude, weeks as columns. The shape people already read.
 */
function Calendar({ days, now }: { days: UsagePeriod[]; now: number }) {
  const { tip, show, hide } = useTip();
  const [measure, setMeasure] = useState<Measure>('prompts');
  const spec = CALENDAR_MEASURES.find((m) => m.key === measure) ?? CALENDAR_MEASURES[0];
  const byDay = useMemo(() => new Map(days.map((row) => [row.period, row])), [days]);
  // Columns end on the current week; the first column starts on a Sunday.
  const today = new Date(now);
  const end = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
  const endDow = end.getUTCDay();
  const start = new Date(end.getTime() - (CALENDAR_WEEKS * 7 - 1 - (6 - endDow)) * DAY_MS);
  const cells: { day: string; col: number; row: number; value: number; rowData?: UsagePeriod }[] = [];
  for (let i = 0; ; i += 1) {
    const at = new Date(start.getTime() + i * DAY_MS);
    if (at.getTime() > end.getTime()) break;
    const key = at.toISOString().slice(0, 10);
    const rowData = byDay.get(key);
    cells.push({ day: key, col: Math.floor(i / 7), row: at.getUTCDay(), value: rowData ? rowData[measure] : 0, rowData });
  }
  const values = cells.map((c) => c.value).filter((v) => v > 0).sort((a, b) => a - b);
  const q = (f: number) => (values.length ? values[Math.min(values.length - 1, Math.floor(f * values.length))] : 0);
  const steps = [q(0.25), q(0.5), q(0.75), q(0.9)];
  const level = (v: number) => (v <= 0 ? 0 : v <= steps[0] ? 1 : v <= steps[1] ? 2 : v <= steps[2] ? 3 : v <= steps[3] ? 4 : 5);
  const cell = 12;
  const gap = 3;
  const padT = 18;
  const padL = 28;
  const cols = Math.ceil(cells.length / 7);
  const width = padL + cols * (cell + gap);
  const height = padT + 7 * (cell + gap);
  const months: { x: number; label: string }[] = [];
  let lastMonth = -1;
  cells.forEach((c) => {
    const m = Number(c.day.slice(5, 7));
    if (c.row === 0 && m !== lastMonth) {
      lastMonth = m;
      months.push({ x: padL + c.col * (cell + gap), label: new Date(c.day + 'T00:00:00Z').toLocaleString(undefined, { month: 'short', timeZone: 'UTC' }) });
    }
  });
  const total = cells.reduce((n, c) => n + c.value, 0);
  const activeDays = cells.filter((c) => c.value > 0).length;
  // Streaks of consecutive active days, and the busiest day.
  let longest = 0;
  let run = 0;
  for (const c of cells) {
    run = c.value > 0 ? run + 1 : 0;
    longest = Math.max(longest, run);
  }
  let current = 0;
  for (let i = cells.length - 1; i >= 0; i -= 1) {
    if (cells[i].value > 0) current += 1;
    else if (i === cells.length - 1) continue; // today may not have started
    else break;
  }
  const busiest = cells.reduce((best, c) => (c.value > (best?.value ?? 0) ? c : best), null as (typeof cells)[number] | null);
  return (
    <figure class="chart chart-wide chart-calendar">
      <figcaption>
        <span class="chart-title">Activity calendar</span>
        <span class="chart-head tnum">
          <strong>{fmt(total, spec.tokens)}</strong>
          <span class="faint">{` ${spec.label.toLowerCase()} on ${activeDays} active days in the last year`}</span>
        </span>
        <span class="legend" role="group" aria-label="Calendar measure">
          {CALENDAR_MEASURES.map((m) => (
            <button key={m.key} type="button" class="chip" aria-pressed={measure === m.key} onClick={() => setMeasure(m.key)}>
              {m.label}
            </button>
          ))}
        </span>
      </figcaption>
      <div class="calendar-scroll">
        <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label={`${spec.label} per day over the last year`} class="calendar-svg" onPointerLeave={hide}>
          {months.map((m) => (
            <text key={`${m.label}-${m.x}`} x={m.x} y={11} class="chart-tick">{m.label}</text>
          ))}
          {['Mon', 'Wed', 'Fri'].map((d, i) => (
            <text key={d} x={0} y={padT + (1 + i * 2) * (cell + gap) + cell - 2} class="chart-tick">{d}</text>
          ))}
          {cells.map((c) => (
            <rect
              key={c.day}
              x={padL + c.col * (cell + gap)}
              y={padT + c.row * (cell + gap)}
              width={cell}
              height={cell}
              rx="2"
              class={`cal-cell cal-${level(c.value)}${c.day === todayKey() ? ' cal-today' : ''}`}
              onPointerEnter={(e) =>
                show(e, longDate(c.day), c.rowData
                  ? [
                      `${c.rowData.prompts} prompts, ${c.rowData.tool_calls} tool calls`,
                      `${c.rowData.sessions} session${c.rowData.sessions === 1 ? '' : 's'}, ${compactTokens(c.rowData.output_tokens)} output`,
                      `${c.rowData.compactions} compaction${c.rowData.compactions === 1 ? '' : 's'}`,
                    ]
                  : ['Nothing recorded'])
              }
            />
          ))}
        </svg>
      </div>
      <div class="cal-foot">
        <span class="cal-facts faint tnum">
          {`Current streak ${current} day${current === 1 ? '' : 's'} · longest ${longest} · busiest ${busiest ? `${longDate(busiest.day)} with ${fmt(busiest.value, spec.tokens)}` : 'none yet'}`}
        </span>
        <span class="cal-legend faint">
          <span>Less</span>
          {[0, 1, 2, 3, 4, 5].map((l) => (
            <i key={l} class={`cal-swatch cal-${l}`} aria-hidden="true" />
          ))}
          <span>More</span>
        </span>
      </div>
      <TipBox tip={tip} />
    </figure>
  );
}

function Ranked({
  title,
  rows,
  tokens,
  unit,
  wide,
}: {
  title: string;
  rows: { name: string; value: number; note?: string; provider?: string }[];
  tokens?: boolean;
  unit?: string;
  /** Spans two columns, for lists whose names and notes both matter. */
  wide?: boolean;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  const sum = rows.reduce((n, r) => n + r.value, 0);
  return (
    <section class={`rank${wide ? ' rank-wide' : ''}`} aria-label={title}>
      <h2 class="micro">{title}</h2>
      {rows.length === 0 ? (
        <p class="section-unknown">Nothing yet.</p>
      ) : (
        <ol class="ranklist">
          {rows.slice(0, 8).map((row) => (
            <li
              key={row.name}
              class="rankrow"
              title={`${row.name}${row.provider ? ` (${providerName(row.provider)})` : ''}: ${fmt(row.value, tokens)}${unit ? ` ${unit}` : ''}${sum && !tokens ? `, ${Math.round((row.value / sum) * 100)}% of the top eight` : ''}`}
            >
              <span class="rank-name">
                {row.provider ? <i class={`swatch swatch-${row.provider}`} aria-hidden="true" /> : null}
                <span class="rank-name-text">{row.name}</span>
                {row.note ? <span class="rank-note faint">{row.note}</span> : null}
              </span>
              <span class="rank-bar" aria-hidden="true">
                <i style={`width:${Math.max(2, Math.round((row.value / max) * 100))}%`} />
              </span>
              <span class="rank-value tnum">
                {fmt(row.value, tokens)}
                {sum && !tokens ? <span class="faint">{` ${Math.round((row.value / sum) * 100)}%`}</span> : null}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function UsageView({ now }: { now: number }) {
  const [report, setReport] = useState<UsageReport | null>(null);
  const [previous, setPrevious] = useState<UsageReport | null>(null);
  const [year, setYear] = useState<UsagePeriod[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<Range>('30d');
  const [group, setGroup] = useState<Group>('day');
  const [project, setProject] = useState('');
  const [model, setModel] = useState('');
  const [cumulate, setCumulate] = useState(false);
  const [projects, setProjects] = useState<{ name: string; sessions: number }[]>([]);
  const [models, setModels] = useState<{ name: string; replies: number }[]>([]);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const since = sinceFor(range, Date.now());
    getUsage({ group, project, model, since }, controller.signal)
      .then((r) => {
        setReport(r);
        setError(null);
        // The option lists come from the unfiltered report so a filter never
        // hides the choice that would undo it.
        if (!project && !model) {
          setProjects(
            r.projects
              .filter((p) => p.name)
              .map((p) => ({ name: p.name, sessions: p.sessions }))
              .sort((a, b) => b.sessions - a.sessions || a.name.localeCompare(b.name)),
          );
          setModels(
            r.models
              .map((m) => ({ name: m.name, replies: m.replies }))
              .sort((a, b) => b.replies - a.replies || a.name.localeCompare(b.name)),
          );
        }
      })
      .catch((e: Error) => setError(e.message));
    // The previous range of the same length, for the comparison lines.
    const days = RANGE_DAYS[range];
    if (days != null) {
      getUsage(
        { group, project, model, since: new Date(Date.now() - 2 * days * DAY_MS).toISOString(), until: since },
        controller.signal,
      )
        .then(setPrevious)
        .catch(() => setPrevious(null));
    } else {
      setPrevious(null);
    }
    return () => controller.abort();
  }, [group, project, model, range, tick]);

  // The calendar always shows the last year by day, under the same project
  // and model filters, whatever the range and grouping above say.
  useEffect(() => {
    const controller = new AbortController();
    getUsage({ group: 'day', project, model, since: new Date(Date.now() - CALENDAR_WEEKS * 7 * DAY_MS).toISOString() }, controller.signal)
      .then((r) => setYear(r.series))
      .catch(() => setYear([]));
    return () => controller.abort();
  }, [project, model, tick]);

  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 60_000);
    return () => clearInterval(timer);
  }, []);

  const series = report?.series ?? [];
  const totals = report?.totals;
  const prevTotals = previous?.totals ?? null;
  const spark = (measure: Measure) => series.slice(-14).map((row) => row[measure]);
  const kpis = useMemo(() => {
    if (!totals) return [];
    const perPrompt = totals.prompts ? totals.tool_calls / totals.prompts : 0;
    const prevPerPrompt = prevTotals?.prompts ? prevTotals.tool_calls / prevTotals.prompts : null;
    return [
      { label: 'Sessions', value: fmt(totals.sessions), current: totals.sessions, previous: prevTotals?.sessions ?? null, spark: spark('sessions') },
      { label: 'Prompts', value: fmt(totals.prompts), current: totals.prompts, previous: prevTotals?.prompts ?? null, spark: spark('prompts') },
      { label: 'Tool calls', value: fmt(totals.tool_calls), current: totals.tool_calls, previous: prevTotals?.tool_calls ?? null, spark: spark('tool_calls') },
      { label: 'Tool calls per prompt', value: perPrompt.toFixed(1), current: Math.round(perPrompt * 10), previous: prevPerPrompt == null ? null : Math.round(prevPerPrompt * 10), spark: series.slice(-14).map((row) => (row.prompts ? row.tool_calls / row.prompts : 0)) },
      { label: 'Output tokens', value: compactTokens(totals.output_tokens), current: totals.output_tokens, previous: prevTotals?.output_tokens ?? null, spark: spark('output_tokens') },
      { label: 'Compactions', value: fmt(totals.compactions), current: totals.compactions, previous: prevTotals?.compactions ?? null, spark: spark('compactions'), invert: true },
      { label: 'Subagents', value: fmt(totals.subagents), current: totals.subagents, previous: prevTotals?.subagents ?? null, spark: [] as number[] },
      { label: 'Largest context', value: compactTokens(totals.peak_context_max), current: totals.peak_context_max, previous: prevTotals?.peak_context_max ?? null, spark: [] as number[] },
    ];
  }, [totals, prevTotals, series]);

  return (
    <main class="usage" id="session-view" aria-label="Usage over time">
      <h1 class="sr-only">Usage over time</h1>
      <section class="hfilters" aria-label="Usage filters">
        <div class="seg" role="radiogroup" aria-label="Range">
          {(
            [
              ['7d', '7 days'],
              ['30d', '30 days'],
              ['90d', '90 days'],
              ['all', 'All time'],
            ] as const
          ).map(([key, label]) => (
            <button key={key} type="button" role="radio" class="chip" aria-checked={range === key} onClick={() => setRange(key)}>
              {label}
            </button>
          ))}
        </div>
        <div class="seg" role="radiogroup" aria-label="Group by">
          {(
            [
              ['day', 'By day'],
              ['week', 'By week'],
              ['month', 'By month'],
            ] as const
          ).map(([key, label]) => (
            <button key={key} type="button" role="radio" class="chip" aria-checked={group === key} onClick={() => setGroup(key)}>
              {label}
            </button>
          ))}
        </div>
        <button type="button" class="chip" aria-pressed={cumulate} onClick={() => setCumulate((v) => !v)}>
          Cumulative
        </button>
        <label class="hselect">
          <span class="sr-only">Project</span>
          <select value={project} onChange={(e) => setProject((e.target as HTMLSelectElement).value)} aria-label="Project">
            <option value="">All projects</option>
            {projects.map((p) => (
              <option key={p.name} value={p.name}>{`${p.name} · ${p.sessions} session${p.sessions === 1 ? '' : 's'}`}</option>
            ))}
          </select>
        </label>
        <label class="hselect">
          <span class="sr-only">Model</span>
          <select value={model} onChange={(e) => setModel((e.target as HTMLSelectElement).value)} aria-label="Model">
            <option value="">All models</option>
            {models.map((m) => (
              <option key={m.name} value={m.name}>{`${modelDisplayName(m.name)} · ${m.replies.toLocaleString()} replies`}</option>
            ))}
          </select>
        </label>
        {project || model || range !== '30d' || group !== 'day' || cumulate ? (
          <button
            type="button"
            class="disc"
            onClick={() => {
              setProject('');
              setModel('');
              setRange('30d');
              setGroup('day');
              setCumulate(false);
            }}
          >
            Reset
          </button>
        ) : null}
        <span class="hsummary faint tnum">
          {report
            ? `${project ? `${project} · ` : ''}${model ? `${modelDisplayName(model)} · ` : ''}counted ${relativeTime(report.generated_at, now)}`
            : 'Reading the index.'}
        </span>
      </section>

      {error ? <p class="section-unknown hpad">{`Usage could not be read: ${error}`}</p> : null}

      <dl class="kpis" aria-label="Totals in range">
        {kpis.map((k) => (
          <div class="kpi" key={k.label}>
            <dt class="micro">{k.label}</dt>
            <dd class="tnum">
              <span class="kpi-value">{k.value}</span>
              {k.spark.length ? <Sparkline values={k.spark} /> : null}
            </dd>
            <dd class="kpi-delta">
              <Delta current={k.current} previous={k.previous} range={range} invert={k.invert} />
            </dd>
          </div>
        ))}
      </dl>

      <div class="charts">
        <Calendar days={year} now={now} />
        {MEASURES.map((m) => (
          <Bars
            key={m.key}
            series={series}
            measure={m.key}
            label={m.label}
            tokens={m.tokens}
            group={group}
            cumulate={cumulate}
            range={range}
            total={m.key === 'sessions' && !cumulate ? report?.totals.sessions : undefined}
            previous={prevTotals ? (m.key === 'sessions' ? prevTotals.sessions : prevTotals[m.key]) : null}
            invert={m.key === 'compactions'}
          />
        ))}
        {report ? <PeakDistribution buckets={report.peak_buckets} sessions={report.totals.sessions} /> : null}
        <ProviderStack series={series} group={group} />
      </div>

      <div class="ranks">
        <Ranked title="Subagent types most used" rows={(report?.agent_types ?? []).map((r) => ({ name: r.name, value: r.count }))} unit="launches" />
        <Ranked title="Tools most used" rows={(report?.tools ?? []).map((r) => ({ name: r.name, value: r.count }))} unit="calls" />
        <Ranked
          title="Models by replies"
          rows={(report?.models ?? []).map((r) => ({ name: modelDisplayName(r.name), value: r.replies, provider: r.provider }))}
          unit="replies"
        />
        <Ranked
          title="Projects by prompts"
          rows={(report?.projects ?? []).map((r) => ({ name: r.name || 'unknown', value: r.prompts, note: `${r.sessions} sessions` }))}
          unit="prompts"
        />
        <Ranked
          title="Largest contexts"
          rows={(report?.peaks ?? []).map((r) => ({
            name: r.title || r.project,
            value: r.peak_context,
            note: r.window ? `${Math.round((r.peak_context / r.window) * 100)}% of ${compactTokens(r.window)}` : r.project,
          }))}
          tokens
          wide
        />
      </div>

      <details class="usage-table">
        <summary>Table view of the series</summary>
        <table class="htable">
          <thead>
            <tr>
              <th>Period</th>
              <th class="tnum">Sessions</th>
              <th class="tnum">Prompts</th>
              <th class="tnum">Replies</th>
              <th class="tnum">Tool calls</th>
              <th class="tnum">Output tokens</th>
              <th class="tnum">Compactions</th>
              {PROVIDER_ORDER.map((p) => (
                <th key={p} class="tnum">{providerName(p)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {series.map((row) => (
              <tr key={row.period}>
                <td>{row.period}</td>
                <td class="tnum">{row.sessions}</td>
                <td class="tnum">{row.prompts}</td>
                <td class="tnum">{row.replies}</td>
                <td class="tnum">{row.tool_calls}</td>
                <td class="tnum">{compactTokens(row.output_tokens)}</td>
                <td class="tnum">{row.compactions}</td>
                {PROVIDER_ORDER.map((p) => (
                  <td key={p} class="tnum">{row.by_provider[p] ?? 0}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      <p class="act-note faint hpad">
        Counts come from the session transcripts on this machine. They are not a bill; Airlock does not
        know provider prices, and plan usage is shown on the live page when a provider reports it.
      </p>
    </main>
  );
}
