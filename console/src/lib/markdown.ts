// Markdown rendering rules for the copied report.
//
// Two kinds of value, two treatments:
//
//   identifier  a model id, path, digest, session id, or timestamp. It goes in
//               a code span, so `claude-fable-5-1[1m]` reads as itself rather
//               than as `claude\-fable\-5\-1\[1m\]`.
//   prose       a sentence. Only the characters that are actually Markdown
//               active are escaped, and the ones that matter only at the start
//               of a line are escaped only there.
//
// Both strip control characters and collapse newlines, so no single field can
// open a new block in a document someone else will read.

const CONTROLS = new RegExp('[\\u0000-\\u001f\\u007f-\\u009f]', 'g');

/** Active anywhere in a line. Hyphens, dots, and plus signs are not here. */
const INLINE_ACTIVE = /([\\`*_[\]()<>!|#])/g;

/** Active only where a line begins: list bullets, rules, and ordered markers. */
const LINE_LEADER = /^([-+.]|\d+\.)/;

function flatten(value: unknown): string {
  return String(value ?? 'unknown')
    .replace(/\r\n?|\n/g, ' ')
    .replace(CONTROLS, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/**
 * A code span for a value a person may need to copy exactly. Backticks are
 * removed rather than escaped, because a code span cannot contain the fence
 * that delimits it without a length dance that helps no one here.
 */
export function markdownCode(value: unknown): string {
  const text = flatten(value).replace(/`/g, '');
  return text ? `\`${text}\`` : '`unknown`';
}

/**
 * Prose. Escapes only what Markdown would otherwise interpret, so ordinary
 * sentences keep their hyphens, dots, and plus signs.
 */
export function markdownText(value: unknown): string {
  const text = flatten(value).replace(INLINE_ACTIVE, '\\$1');
  return text.replace(LINE_LEADER, '\\$1');
}

/**
 * A whole line the report authors, where a leading list or heading character
 * from an untrusted fragment must not turn into structure.
 */
export function markdownLine(value: unknown): string {
  return markdownText(value);
}
