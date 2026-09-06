// The console server injects a per-process CSRF token into the root HTML only.
// It is never returned by a JSON endpoint, so this module is the single place
// that reads it, and nothing else may copy it anywhere.
//
// The value must never reach a URL, a log, a report, a state export, a tool
// call, or any rendered text. Callers get a header object, not the token.

const META_NAME = 'airlock-csrf';

export function csrfToken(): string | null {
  if (typeof document === 'undefined') return null;
  const meta = document.querySelector(`meta[name="${META_NAME}"]`);
  const value = meta?.getAttribute('content')?.trim();
  return value ? value : null;
}

/** True when this page was served by a console that accepts human mutations. */
export function humanApiAvailable(): boolean {
  return csrfToken() !== null;
}

/**
 * Headers for a human mutation. Returns null when there is no token, so a
 * caller fails closed instead of sending a request that will be rejected.
 */
export function mutationHeaders(): Record<string, string> | null {
  const token = csrfToken();
  if (!token) return null;
  return {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    'X-Airlock-CSRF': token,
  };
}
