// Inline SVG only. One sprite in the document, referenced by <use>, so a
// status shape costs nine bytes wherever it appears.

import type { Descriptor, GlyphId, Tone } from '../lib/vocab';

export function Sprite() {
  return (
    <svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">
      <defs>
        <g id="g-run">
          <circle cx="5" cy="5" r="3.1" fill="currentColor" />
        </g>
        <g id="g-block">
          <circle cx="5" cy="5" r="3.6" fill="none" stroke="currentColor" stroke-width="1.4" />
          <path d="M3.1 5h3.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
        </g>
        <g id="g-idle">
          <circle cx="5" cy="5" r="3.1" fill="none" stroke="currentColor" stroke-width="1.4" />
        </g>
        <g id="g-end">
          <circle cx="5" cy="5" r="3.6" fill="none" stroke="currentColor" stroke-width="1.4" />
          <path d="M2.9 7.1 7.1 2.9" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
        </g>
        <g id="g-cool">
          <circle cx="5" cy="5" r="3.6" fill="none" stroke="currentColor" stroke-width="1.4" />
          <path d="M5 5V1.4A3.6 3.6 0 0 1 8.6 5Z" fill="currentColor" />
        </g>
        <g id="g-unav">
          <path
            d="M2.6 2.6 7.4 7.4M7.4 2.6 2.6 7.4"
            stroke="currentColor"
            stroke-width="1.4"
            stroke-linecap="round"
          />
        </g>
        <g id="g-unknown">
          <circle
            cx="5"
            cy="5"
            r="3.6"
            fill="none"
            stroke="currentColor"
            stroke-width="1.4"
            stroke-dasharray="1.7 1.7"
          />
        </g>
        <g id="g-arrow">
          <path
            d="M1 5h7M5.5 2.5 8 5l-2.5 2.5"
            fill="none"
            stroke="currentColor"
            stroke-width="1.3"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </g>
        <g id="g-caret">
          <path
            d="M3.5 2 6.5 5l-3 3"
            fill="none"
            stroke="currentColor"
            stroke-width="1.3"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </g>
        <g id="g-search">
          <circle cx="4.4" cy="4.4" r="2.6" fill="none" stroke="currentColor" stroke-width="1.3" />
          <path d="M6.4 6.4 8.6 8.6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
        </g>
      </defs>
    </svg>
  );
}

export function Glyph({ id }: { id: GlyphId }) {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" focusable="false">
      <use href={`#g-${id}`} />
    </svg>
  );
}

const TONE_CLASS: Record<Tone, string> = {
  ok: 'st-ok',
  attn: 'st-attn',
  muted: 'st-muted',
  faint: 'st-faint',
};

/** Word plus shape plus colour, in that order of importance. */
export function Status({ d, className = '' }: { d: Descriptor; className?: string }) {
  return (
    <span class={`st ${TONE_CLASS[d.tone]} ${className}`.trim()}>
      <Glyph id={d.glyph} />
      {d.label}
    </span>
  );
}

/** The shape alone, for rows where the word is already in the sentence. */
export function StatusMark({ d }: { d: Descriptor }) {
  return (
    <span class={`st ${TONE_CLASS[d.tone]}`}>
      <Glyph id={d.glyph} />
    </span>
  );
}

const PROVIDER_CLASS: Record<string, string> = {
  anthropic: 'pv-anthropic',
  openai: 'pv-openai',
  grok: 'pv-grok',
  openrouter: 'pv-openrouter',
  openmodel: 'pv-openmodel',
};

/** A small filled dot in the provider's colour. Never the only signal. */
export function ProviderDot({ provider }: { provider: string | null | undefined }) {
  const cls = PROVIDER_CLASS[(provider ?? '').toLowerCase()] ?? 'pv-unknown';
  return <span class={`pv ${cls}`} aria-hidden="true" />;
}

/** Model name with its provider dot, the one way a model is written on the page. */
export function ModelBadge({
  name,
  provider,
  strong = false,
  className = '',
}: {
  name: string;
  provider: string | null | undefined;
  strong?: boolean;
  className?: string;
}) {
  return (
    <span class={`mdl${strong ? ' mdl-strong' : ''} ${className}`.trim()}>
      <ProviderDot provider={provider} />
      <span class="mdl-name">{name}</span>
    </span>
  );
}

export function Bar({
  used,
  total,
  label,
}: {
  used: number | null | undefined;
  total: number | null | undefined;
  label: string;
}) {
  if (used == null || !total) {
    return <div class="un" role="img" aria-label={`${label}: unknown`} />;
  }
  const pct = Math.max(0, Math.min(100, Math.round((used / total) * 100)));
  return (
    <div class={`bar${pct >= 60 ? ' warn' : ''}`} role="img" aria-label={`${label}: ${pct} percent`}>
      <i style={`width:${pct}%`} />
    </div>
  );
}
