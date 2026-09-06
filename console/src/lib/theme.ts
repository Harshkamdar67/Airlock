// Theme: the system decides, an explicit choice overrides, the choice persists.

export type ThemePref = 'system' | 'light' | 'dark';

const KEY = 'airlock-console-theme';

export function readTheme(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    if (v === 'light' || v === 'dark') return v;
  } catch {
    // Private mode or a locked down profile. System preference still works.
  }
  return 'system';
}

export function systemTheme(): 'light' | 'dark' {
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';
}

export function effectiveTheme(pref: ThemePref): 'light' | 'dark' {
  return pref === 'system' ? systemTheme() : pref;
}

export function applyTheme(pref: ThemePref): void {
  const root = document.documentElement;
  if (pref === 'system') delete root.dataset.theme;
  else root.dataset.theme = pref;
  try {
    if (pref === 'system') localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    // Not being able to remember the choice is not a reason to refuse it.
  }
}

/** system to light to dark and back, so every state is reachable from one key. */
export function nextTheme(pref: ThemePref): ThemePref {
  return pref === 'system' ? 'light' : pref === 'light' ? 'dark' : 'system';
}

export const THEME_LABEL: Record<ThemePref, string> = {
  system: 'System theme',
  light: 'Light theme',
  dark: 'Dark theme',
};
