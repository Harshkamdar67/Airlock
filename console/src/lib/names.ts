// Model and provider names as a person says them. The API supplies short_name
// for every route it knows; this fills the gaps for models that appear only in
// a chain or an event.

const PROVIDER_NAMES: Record<string, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  grok: 'Grok',
  openrouter: 'OpenRouter',
  openmodel: 'Open model',
};

export function providerName(provider: string | null | undefined): string {
  if (!provider) return 'an unknown provider';
  return PROVIDER_NAMES[provider] ?? provider;
}

/** Short name for a model id, used when the routes list does not carry one. */
/**
 * The provider a model id belongs to, when a summary only carries the root's
 * provider. Shipped ids are unambiguous by prefix; a slash means OpenRouter;
 * anything else keeps the fallback the router reported.
 */
export function inferProvider(model: string | null | undefined, fallback: string): string {
  const id = (model ?? '').toLowerCase();
  if (!id) return fallback;
  if (id.startsWith('openmodel/')) return 'openmodel';
  if (id.startsWith('claude-')) return 'anthropic';
  if (id.startsWith('gpt-') || id.startsWith('o1') || id.startsWith('o3') || id.startsWith('o4')) return 'openai';
  if (id.startsWith('grok-')) return 'grok';
  if (id.includes('/')) return 'openrouter';
  return fallback;
}

/**
 * True for a session Airlock is not routing: plain Claude Code, or an Airlock
 * profile that talks to the proxy directly and so has no router to control.
 */
export function isUnrouted(session: { source?: string; profile?: string }): boolean {
  return (
    session.source === 'claude-code' ||
    session.source === 'airlock-direct' ||
    session.profile === 'claude-code'
  );
}

/** A profile id as a person would say it: "Hybrid, OpenAI root", "OpenAI only". */
export function profileLabel(profile: string | null | undefined): string {
  const id = (profile ?? '').toLowerCase();
  if (!id) return 'unknown profile';
  if (id === 'claude-code') return 'Claude Code, not routed';
  const pure = id.match(/^(\w+)-pure$/);
  if (pure) return `${providerName(pure[1])} only`;
  const hybrid = id.match(/^hybrid-(\w+)-root$/);
  if (hybrid) return `Hybrid, ${providerName(hybrid[1])} root`;
  return id.replace(/-/g, ' ');
}

const FAMILY_WORDS: Record<string, string> = {
  fable: 'Fable',
  opus: 'Opus',
  sonnet: 'Sonnet',
  haiku: 'Haiku',
  sol: 'Sol',
  terra: 'Terra',
  luna: 'Luna',
  astra: 'Astra',
  composer: 'Composer',
  codex: 'Codex',
  spark: 'Spark',
  mini: 'Mini',
  fast: 'Fast',
};

function word(token: string): string {
  return FAMILY_WORDS[token] ?? token.charAt(0).toUpperCase() + token.slice(1);
}

/**
 * A model id as a person would name it, version and all: "Claude Fable 5.1",
 * "Claude Opus 5 (1M)", "GPT-5.6 Luna Fast", "Grok Composer 2.5 Fast",
 * "DeepSeek V4 Flash 0731". Short names collapse versions, which is right in a
 * rail badge and wrong in a ranking, so this is what rankings and filters use.
 */
export function modelDisplayName(model: string | null | undefined): string {
  if (!model) return 'unknown model';
  let id = model.trim();
  let suffix = '';
  const oneM = /\[1m\]$/i.exec(id);
  if (oneM) {
    id = id.slice(0, -4);
    suffix = ' (1M)';
  }
  const lower = id.toLowerCase();
  if (lower.startsWith('openmodel/')) return `${word(id.slice('openmodel/'.length))} (local)`;
  if (lower.startsWith('claude-')) {
    // claude-fable-5-1, claude-opus-4-7, claude-haiku-4-5-20251001, claude-sonnet-5
    const parts = lower.slice('claude-'.length).split('-');
    const family = parts.shift() ?? '';
    const numbers = parts.filter((p) => /^\d+$/.test(p) && p.length < 6);
    const version = numbers.length ? numbers.join('.') : '';
    return `Claude ${word(family)}${version ? ` ${version}` : ''}${suffix}`;
  }
  if (lower.startsWith('gpt-')) {
    // gpt-5.6-sol, gpt-5.6-luna-fast, gpt-6-astra, gpt-5.3-codex-spark, gpt-5.4-mini
    const parts = lower.slice('gpt-'.length).split('-');
    const version = parts.shift() ?? '';
    const rest = parts.map(word).join(' ');
    return `GPT-${version}${rest ? ` ${rest}` : ''}${suffix}`;
  }
  if (lower.startsWith('grok-')) {
    // grok-4.6, grok-composer-2.5-fast
    const parts = lower.slice('grok-'.length).split('-');
    return `Grok ${parts.map((p) => (/^\d/.test(p) ? p : word(p))).join(' ')}${suffix}`;
  }
  if (lower.includes('/')) {
    const tail = lower.slice(lower.lastIndexOf('/') + 1);
    const pretty = tail
      .split('-')
      .map((p) => (/^v\d/.test(p) ? p.toUpperCase() : /^\d/.test(p) ? p : word(p)))
      .join(' ')
      .replace(/^Deepseek/, 'DeepSeek')
      .replace(/^Moonshotai/, 'Moonshot');
    return `${pretty}${suffix}`;
  }
  return `${id}${suffix}`;
}

export function deriveShortName(model: string): string {
  if (!model) return 'unknown';
  let id = model.split('/').pop() ?? model;
  id = id.replace(/\[[^\]]*\]$/, '');
  const claude = /^claude-([a-z]+)/.exec(id);
  if (claude) return claude[1];
  const gpt = /^gpt-[\d.]+-(.+)$/.exec(id);
  if (gpt) return gpt[1];
  if (/^grok-composer/.test(id)) return 'composer';
  if (/^grok/.test(id)) return 'grok';
  return id;
}

export type ShortNames = (model: string | null | undefined) => string;

/** Build a lookup from the routes the session actually has. */
export function shortNameLookup(
  routes: { model: string; short_name?: string }[] | undefined,
): ShortNames {
  const map = new Map<string, string>();
  for (const r of routes ?? []) {
    if (r.short_name) map.set(r.model, r.short_name);
  }
  return (model) => {
    if (!model) return 'unknown';
    return map.get(model) ?? deriveShortName(model);
  };
}

/** Sentence case for a short name at the start of a sentence: sol -> Sol. */
export function titled(short: string): string {
  if (!short) return short;
  return short.charAt(0).toUpperCase() + short.slice(1);
}

/** "sol, terra and luna", with the Oxford-free join people actually speak. */
export function joinWords(items: string[]): string {
  if (items.length === 0) return '';
  if (items.length === 1) return items[0];
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/** Last path segment of a workdir, for machines that use either separator. */
export function projectFromWorkdir(workdir: string): string {
  const parts = workdir.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : workdir;
}

/**
 * A stable, honest name for a session whose router reported no workdir.
 * Every router installed before this release omits it, so the page must never
 * render an empty heading or an empty rail row.
 */
export function sessionTitle(session: {
  project?: string | null;
  workdir?: string | null;
  id: string;
}): string {
  const project = session.project?.trim();
  if (project) return project;
  const derived = session.workdir ? projectFromWorkdir(session.workdir).trim() : '';
  if (derived) return derived;
  return `Session ${session.id.replace(/^r-/, '').slice(0, 6)}`;
}

/** What sits where the working directory normally goes. */
export function sessionSubtitle(session: {
  workdir?: string | null;
  id: string;
}): string {
  const workdir = session.workdir?.trim();
  if (workdir) return workdir;
  return `No working directory reported. Session ${session.id}`;
}

/** True when the router told us nothing about where the session runs. */
export function hasWorkdir(session: { workdir?: string | null }): boolean {
  return !!session.workdir?.trim();
}
