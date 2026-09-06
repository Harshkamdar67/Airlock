// Time rendered the way a person says it out loud. Nothing here formats a date
// the machine's way when a duration is what the reader wants.

const MIN = 60;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** Parse an ISO timestamp to epoch ms, or null when it is missing or unusable. */
export function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

/**
 * "just now", "7s ago", "3m ago", "2h 21m ago", "yesterday", "3d ago".
 * Future instants read as "in ...". Unknown reads as "unknown".
 */
export function relativeTime(iso: string | null | undefined, now: number): string {
  const t = parseTime(iso);
  if (t === null) return 'unknown';
  const secs = Math.round((now - t) / 1000);
  if (secs >= 0 && secs < 5) return 'just now';
  const ago = secs >= 0;
  const s = Math.abs(secs);
  const body = shortDuration(s);
  return ago ? `${body} ago` : `in ${body}`;
}

/** Compact duration for tight columns: 7s, 3m, 2h 21m, 3d 4h. */
export function shortDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < MIN) return `${s}s`;
  if (s < HOUR) return `${Math.floor(s / MIN)}m`;
  if (s < DAY) {
    const h = Math.floor(s / HOUR);
    const m = Math.floor((s % HOUR) / MIN);
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  const d = Math.floor(s / DAY);
  const h = Math.floor((s % DAY) / HOUR);
  return h ? `${d}d ${h}h` : `${d}d`;
}

/** Spoken duration for sentences: "49 minutes", "1 hour 20 minutes", "40 seconds". */
export function spokenDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return 'an unknown time';
  }
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return s === 1 ? '1 second' : `${s} seconds`;
  const mins = Math.round(s / MIN);
  if (mins < 60) return mins === 1 ? '1 minute' : `${mins} minutes`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  const hp = h === 1 ? '1 hour' : `${h} hours`;
  if (!m) return hp;
  return `${hp} ${m === 1 ? '1 minute' : `${m} minutes`}`;
}

/** A countdown that never lies: below zero it becomes unknown. */
export function countdown(remainingSeconds: number | null | undefined): string {
  if (remainingSeconds === null || remainingSeconds === undefined) return 'unknown';
  if (!Number.isFinite(remainingSeconds)) return 'unknown';
  if (remainingSeconds <= 0) return 'any moment';
  if (remainingSeconds < 60) return 'under a minute left';
  return `${shortDuration(remainingSeconds)} left`;
}

/** Wall clock in the reader's own zone, to the second: "08:58:40". */
export function clockTime(iso: string, withSeconds = true): string {
  const t = parseTime(iso);
  if (t === null) return '--:--';
  const d = new Date(t);
  const p = (n: number) => String(n).padStart(2, '0');
  return withSeconds
    ? `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
    : `${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Minute bucket key in local time, so grouping matches what the clock shows. */
export function minuteKey(iso: string): string {
  const t = parseTime(iso);
  if (t === null) return 'unknown';
  const d = new Date(t);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Uptime for a session header. */
export function uptime(startedAt: string, now: number): string {
  const t = parseTime(startedAt);
  if (t === null) return 'unknown';
  return shortDuration(Math.max(0, (now - t) / 1000));
}

/** 612340 -> "612k", 1000000 -> "1M", 8100 -> "8.1k". */
export function compactTokens(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return 'unknown';
  if (n < 1000) return String(Math.round(n));
  if (n < 10_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  const m = n / 1_000_000;
  return `${(m < 10 ? m.toFixed(m % 1 ? 1 : 0) : Math.round(m))}M`;
}

/** Seconds a request took, spoken: "0.6 seconds", "14.0 seconds", "1m 2s". */
export function spokenMillis(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return null;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} seconds`;
  return shortDuration(ms / 1000);
}
