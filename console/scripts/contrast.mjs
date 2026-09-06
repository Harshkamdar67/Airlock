// The ratios quoted in design/DIRECTION.md. No package needed: this is the
// WCAG 2.1 sRGB relative-luminance formula.

const THEMES = {
  light: {
    bg: '#FBFBF9', surface: '#FFFFFF', sunken: '#F3F3F0', selected: '#EFEFEB',
    ink: '#17181A', muted: '#55575C', faint: '#6A6C71', accent: '#9A5405',
    accentInk: '#7A4204', accentBg: '#FDF5E9', ok: '#14664A', focus: '#1B5FBF',
    line: '#E4E4DF', lineStrong: '#B2B2AB', accentLine: '#E7C899', bar: '#8B8D91',
  },
  dark: {
    bg: '#0F1112', surface: '#16191A', sunken: '#101314', selected: '#1E2325',
    ink: '#E8EAE9', muted: '#A2A7A6', faint: '#8D9391', accent: '#E8AC55',
    accentInk: '#F0BE76', accentBg: '#241C10', ok: '#5FC79C', focus: '#79ADFF',
    line: '#262A2C', lineStrong: '#454B4D', accentLine: '#4C3B1E', bar: '#7C8382',
  },
};

const PAIRS = [
  ['ink', 'bg'], ['ink', 'surface'], ['muted', 'bg'], ['muted', 'surface'],
  ['faint', 'bg'], ['faint', 'surface'], ['faint', 'sunken'], ['faint', 'selected'],
  ['faint', 'accentBg'], ['accent', 'bg'], ['accent', 'surface'], ['accent', 'selected'],
  ['accent', 'accentBg'], ['accentInk', 'accentBg'], ['ok', 'bg'], ['ok', 'surface'],
  ['ok', 'accentBg'], ['focus', 'bg'], ['focus', 'surface'], ['line', 'surface'],
  ['lineStrong', 'surface'], ['accentLine', 'accentBg'], ['bar', 'bg'],
];

function channel(n) {
  const c = n / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(hex) {
  const s = hex.slice(1);
  return (
    0.2126 * channel(parseInt(s.slice(0, 2), 16)) +
    0.7152 * channel(parseInt(s.slice(2, 4), 16)) +
    0.0722 * channel(parseInt(s.slice(4, 6), 16))
  );
}

function ratio(a, b) {
  const x = luminance(a);
  const y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

let failed = false;
for (const [name, theme] of Object.entries(THEMES)) {
  console.log(`\n${name}`);
  for (const [fg, bg] of PAIRS) {
    const r = ratio(theme[fg], theme[bg]);
    const text = !['line', 'lineStrong', 'accentLine', 'bar'].includes(fg);
    const pass = !text || r >= 4.5;
    if (!pass) failed = true;
    console.log(
      `${fg.padEnd(11)} on ${bg.padEnd(10)} ${theme[fg]} on ${theme[bg]}  ${r.toFixed(2)}:1${text ? (pass ? ' pass' : ' FAIL') : ' decorative'}`,
    );
  }
}

process.exit(failed ? 1 : 0);
