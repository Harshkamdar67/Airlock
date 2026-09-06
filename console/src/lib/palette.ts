// Command palette matching. Ranked so that what you meant is first after two
// or three characters, without pulling in a fuzzy search library.

export interface Command {
  id: string;
  label: string;
  group: string;
  hint?: string;
  /** Extra words that should match without being displayed. */
  keywords?: string[];
}

export interface CommandMatch<C extends Command = Command> {
  command: C;
  score: number;
  /** Character indices of the match inside `label`, for highlighting. */
  ranges: number[];
}

const BOUNDARY = /[\s\-_/:.]/;

function isBoundary(text: string, i: number): boolean {
  return i === 0 || BOUNDARY.test(text[i - 1]);
}

/**
 * Score one candidate. Returns null when the query does not match at all.
 *
 * Ranking, highest first:
 *   1000  the label starts with the query
 *    800  the query starts a word inside the label
 *    600  the query appears anywhere in the label
 *    300  the query's characters appear in order, bonus per word start
 */
export function scoreMatch(
  text: string,
  query: string,
): { score: number; ranges: number[] } | null {
  const hay = text.toLowerCase();
  const q = query.trim().toLowerCase();
  if (!q) return { score: 0, ranges: [] };

  const idx = hay.indexOf(q);
  if (idx >= 0) {
    const ranges = Array.from({ length: q.length }, (_, k) => idx + k);
    if (idx === 0) return { score: 1000 - q.length, ranges };
    if (isBoundary(hay, idx)) return { score: 800 - idx, ranges };
    return { score: 600 - idx, ranges };
  }

  const ranges: number[] = [];
  let boundaries = 0;
  let at = 0;
  for (const ch of q) {
    const found = hay.indexOf(ch, at);
    if (found < 0) return null;
    if (isBoundary(hay, found)) boundaries += 1;
    ranges.push(found);
    at = found + 1;
  }
  return { score: 300 + boundaries * 20 - ranges[0], ranges };
}

/** Filter and rank. An empty query keeps the given order. */
export function matchCommands<C extends Command>(commands: C[], query: string): CommandMatch<C>[] {
  const q = query.trim();
  if (!q) return commands.map((command) => ({ command, score: 0, ranges: [] }));

  const out: CommandMatch<C>[] = [];
  for (const command of commands) {
    const onLabel = scoreMatch(command.label, q);
    let best = onLabel ? { score: onLabel.score, ranges: onLabel.ranges } : null;
    for (const kw of command.keywords ?? []) {
      const onKeyword = scoreMatch(kw, q);
      // A keyword hit never outranks a label hit of the same strength.
      if (onKeyword && (!best || onKeyword.score - 50 > best.score)) {
        best = { score: onKeyword.score - 50, ranges: [] };
      }
    }
    if (best) out.push({ command, score: best.score, ranges: best.ranges });
  }

  out.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (a.command.label.length !== b.command.label.length) {
      return a.command.label.length - b.command.label.length;
    }
    return a.command.label.localeCompare(b.command.label);
  });
  return out;
}
