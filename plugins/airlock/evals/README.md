# Airlock plugin evals

Cases for `claude plugin eval`. They make real model requests on your own
Claude Code login and use public web search, so run them only when you
mean to:

```bash
claude plugin eval plugins/airlock --tag research --runs 1 \
  --allow-tools WebSearch WebFetch --max-cost-usd 10
```

These cases run a plain Claude Code session with the plugin loaded. They do
not start the Airlock launcher, router, proxy, or named airlock-* workers,
so they test the research skill's orchestration, not the Sol and Luna path.
See docs/live-tests.md for the full launcher test.
