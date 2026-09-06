// Runs before first paint so the page never flashes the wrong theme.
// A separate file rather than an inline script: the console serves the page
// under a strict CSP with no unsafe-inline, which blocks inline execution.
(function () {
  try {
    var t = localStorage.getItem('airlock-console-theme');
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  } catch (e) {
    // A locked down profile still gets the system preference from CSS.
  }
})();
