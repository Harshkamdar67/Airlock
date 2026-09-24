# Live tests

Live tests send requests to OpenAI or Anthropic and use subscription quota. Some tests can send repository files to a provider.

Do not run them in CI, setup, the doctor, or the normal offline suite.

## Permission checklist

Before each live test, record and get approval for:

- provider
- exact model ID
- exact repository files that may be read
- whether eligible untracked files are included
- public web state
- extra-usage state
- Fast state
- worker count

Approval for one test does not authorize the next test.

Never include OAuth files, tokens, raw env values, ignored files, outside-repository files, credentials, or private prompts in public web mode.

OpenRouter needs its own approval on top of the list above: the exact declared route name, the exact routable model ID and endpoint, and confirmation that extra-usage state covers it. OpenRouter is a separate account with its own billing, so approving an OpenAI or Anthropic live test does not also approve an OpenRouter one. Approving an OpenRouter route as a hybrid-session worker (test 13) does not also approve it as the exclusive `airlock opr` root (test 14); state exactly which one you are approving.

## Starting state

Record only safe output:

```bash
airlock bundle
airlock usage
airlock mode
git status --short
```

Do not print account IDs, email addresses, tokens, authorization headers, raw provider responses, or credential paths.

## Required native parity matrix

Run every check below before calling a release candidate ready.

### 1. Plain OpenAI root

Start an authorized exact OpenAI root:

```bash
airlock sol
```

Verify:

- the root uses the exact selected GPT model
- Claude Code tools work normally
- unpinned routine Explore is blocked from silently inheriting a different premium root and recommends the `haiku` family slot with its exact economical target
- Explore with `model="haiku"` uses the exact Luna target shown in the session guidance
- an exact Luna implementation Agent can edit only a disposable fixture in an isolated worktree
- no Anthropic route is available

### 2. Hybrid OpenAI root

Start:

```bash
airlock hybrid sol
```

Verify in the same session:

- an exact GPT Agent
- an exact Claude Agent
- cross-provider Explore with a schema-valid family alias that resolves to the expected exact Claude target
- native Agent cards name the exact models
- tools, background execution, cancellation, and native usage work

### 3. Hybrid Anthropic root

Start:

```bash
airlock hybrid opus
```

Verify in the same session:

- an exact Claude Agent
- an exact GPT Agent
- cross-provider Explore with the `haiku` family alias resolving to the expected exact GPT target
- native Agent cards name the exact models
- tools, background execution, cancellation, and native usage work

### 4. Native background overlap

Authorize several independent Luna workers and one stronger reviewer.

Start the useful Luna batch before waiting. Verify that Agent execution overlaps, each card names Luna, every result returns once, and the stronger model reviews all results.

Do not multiply Sol, Terra, Opus, Sonnet, Fable, or Haiku automatically.

### 5. Native cancellation

Start one harmless worker designed to wait long enough for cancellation. Cancel it through Claude Code.

Verify that:

- the Agent stops
- the router does not create a duplicate request
- no replacement worker starts on another route
- no worktree changes are lost silently

### 6. Native worktree isolation

Use a disposable repository fixture with:

- one dirty tracked file
- one harmless non-ignored untracked file
- one tracked env file with fake values
- one ignored fake file
- one fake credential-bearing JSON file

Verify that the Agent sees the tracked and safe untracked changes, sees env key names only, and cannot see ignored or credential-bearing files. Verify that the main checkout and index do not change.

### 7. Response longer than 120 seconds

Authorize one exact harmless task that is expected to run longer than 120 seconds.

Verify that Claude Code receives the final native Agent result without backgrounding a shell wrapper, losing output, or starting a duplicate request.

### 8. Router credential boundary

Use only synthetic test authorization with local fake upstreams for protocol checks. For the live route, inspect only sanitized router evidence.

Verify that:

- Claude authorization goes only to Anthropic
- no Claude authorization or OAuth capability reaches the OpenAI proxy
- no credential, prompt, or body is logged
- every request uses one exact allowed model route
- redirects and unknown models fail closed

Do not print or inspect a real authorization header.

### 9. Luna implementation boundaries

Authorize several independent disposable implementation shards.

Every shard must have explicit file ownership, no-touch boundaries, and acceptance checks. Record the session effort or the configured Luna pin, verify every shard uses that level, verify shards do not touch each other's files, and have one stronger model review, integrate, and test the result.

### 10. UI and UX routing

Authorize Anthropic, Opus, exact harmless UI files, no public web unless separately needed, the extra-usage state, and one worker.

From an OpenAI-root hybrid session, request read-only visual direction without naming a worker. Verify that the main model chooses Opus when enabled, does not start Sonnet alongside it by default, and checks rendering and accessibility when tools permit.

Repeat with Sonnet only if Opus is disabled and Sonnet has separate authorization.

### 11. Luna Fast gate

Authorize OpenAI, exact Luna Fast, harmless task content, Fast state, extra-usage state, and exact worker count.

Verify that the sanitized plan is `prolite` or `pro`, proxy support is verified, the route is `luna-fast`, and the model is `gpt-5.6-luna-fast`.

An ineligible `on` request must fail without substitution. Do not test Sol Fast unless it has separate explicit root authorization.

### 12. Sol context above 300k

This check has a dedicated helper and needs its own approval. The fixed scope is OpenAI, exact `gpt-5.6-sol`, no repository files, no public web, no extra usage, Fast off, and zero workers.

After installing the candidate, run:

```bash
python scripts/test-sol-long-context.py \
  --authorized-scope 'provider=openai;model=gpt-5.6-sol;repository-files=none;public-web=off;extra-usage=off;fast=off;workers=0'
```

The helper runs from an empty temporary directory, disables tools and MCP, sends one generated prompt through standard input, and never writes or prints the prompt. It passes only when Claude Code identifies the Sol route, the response contains markers from both ends of the synthetic input, and provider-reported input plus cache usage exceeds 300,000 tokens.

Record only the helper's sanitized JSON result. If the provider rejects the request, the marker check fails, or observed usage is too small, record the failure and do not claim a 1M Sol path.

#### 2026-08-09 result

Approved scope: OpenAI, exact `gpt-5.6-sol`, no repository files, no public web, extra usage off, Fast off, and zero workers.

The first launcher attempt made no model request because the Windows batch path contained a space and the helper invoked it incorrectly. The helper was fixed and its no-request `--help` path passed. The authorized request then reached the installed launcher but exited with status 1 before producing a valid marker or usage result. The >300k proof failed. Airlock retained the conservative OpenAI context fallback and does not claim a usable 1M Sol path from this test.

### 13. OpenRouter route as a hybrid-session worker

This check needs its own approval, separate from the OpenAI and Anthropic scope above: the exact declared route name, the exact routable model ID and endpoint from `airlock openrouter models list`, repository files (if any), public web state, extra-usage state, and worker count.

Start:

```bash
airlock hybrid sol
```

Verify:

- the route appears as `airlock-or-ROUTE`, a named worker alongside the hybrid root, not as the session root
- the request reaches the exact endpoint declared in the registry, with no silent OpenRouter fallback to a different provider
- under the default `ask` extra-usage policy, the Agent needs `Extra usage authorized: yes` before it starts
- native Agent cards, tools, background execution, and cancellation work the same as any other named Agent
- `count_tokens` is refused for the route instead of returning an estimate

Do not run this check with a registry entry older than 30 days. Refresh it first with `airlock openrouter models refresh --apply`.

### 14. OpenRouter route as the exclusive `airlock opr` root

This check needs its own approval, separate from every other scope on this page: explicit confirmation that this is an `airlock opr` **root** proof (not the worker case in test 13), the exact declared route name, the exact routable model ID and endpoint from `airlock openrouter models list`, repository files (if any), public web state, extra-usage state for any other declared route that might also be enabled, and worker count. Approval for test 13 or for an OpenAI or Anthropic live test does not cover this one, because `airlock opr` uses a separate account with its own billing and starts a different session profile.

This section documents the required approval and the verification checklist only. No `airlock opr` root proof has been run, and none should run without that separate, explicit approval recorded first.

Once approved, start:

```bash
airlock opr ROUTE
```

Verify:

- the named Agent for the selected route is the session root, not an extra worker, and needs no `Extra usage authorized: yes` confirmation to run even under the default `ask` extra-usage policy
- the request reaches the exact endpoint declared in the registry, with no silent OpenRouter fallback to a different provider or model
- the session has no Claude, Codex, or Grok credential dependency and does not start the OpenAI subscription proxy
- if any other route is declared and enabled by the approved extra-usage scope, it appears only as a separate `airlock-or-ROUTE` worker and still needs its own extra-usage confirmation under `ask`
- native Agent cards, tools, background execution, and cancellation work the same as any other root
- `count_tokens` is refused for the route instead of returning an estimate
- an unknown, disabled, or misspelled route name fails closed before any request is sent

Do not run this check with a registry entry older than 30 days. Refresh it first with `airlock openrouter models refresh --apply`.

### 15. `/airlock-research` with a Luna army

Approval for this check names the root provider and exact model, the worker route (`airlock-luna`, `airlock-luna-fast`, or `airlock-composer`) and its exact model, public web state (on: the check searches the public web), extra-usage state, and the worker count for the chosen depth: `quick` up to 3 workers per wave, `standard` up to 8, plus up to 2 verification workers. It reads no repository files. Run it once in a non-Anthropic root, where workers use `airlock-web-tools`, and once in a hybrid Anthropic root, where workers get built-in WebSearch, because those two web paths differ.

Start the approved profile, then:

```text
/airlock-research Which transport mechanisms does the current Model Context Protocol specification define, and which earlier transport was deprecated, in which revision? --depth quick
```

Verify:

- the skill loads and the depth is read from the arguments
- every worker is a named swarm route, runs in the background, and its task carries `Public web research authorized: yes` and no local path or repository content
- workers return the claim-ledger format, with a quote and a URL on every claim
- in the non-Anthropic root, workers use `web_search` (with `queries` or `timelimit` where useful) and verification uses `fetch_page` with `find`
- in the hybrid Anthropic root, record whether built-in WebSearch works for a Luna worker at the session effort; if it fails, record the exact error, because the skill then depends on workers being given URLs to read
- the report opens with a direct answer (stdio and Streamable HTTP; HTTP+SSE deprecated in the 2025-03-26 revision), cites every factual sentence, and every cited URL resolves
- `--depth deep` shows the brief and lane plan and starts no worker until you approve

The orchestration alone, without the launcher, Luna, or the router, is covered by `claude plugin eval plugins/airlock --tag research` (see `plugins/airlock/evals/README.md`). That suite also makes real model requests and needs the same kind of approval.

## Gateway limits to record

During live testing, record these product limits honestly:

- Anthropic does not officially support non-Claude models behind a Claude Code gateway.
- GPT IDs may not appear in `/model` discovery.
- Remote Control is unavailable behind a non-Anthropic base URL.
- Claude Code controls which tools are available to subagents.

A successful local test does not remove those support boundaries.

## Finish

Record safe final state:

```bash
airlock usage
git status --short
```

Report:

- which tests ran
- provider and exact model for each
- repository files authorized
- public web, extra-usage, and Fast state
- maximum concurrent workers
- whether cancellation worked
- whether worktrees or main files changed
- whether any test was skipped or failed

Do not call native parity complete when exact model cards, complete Agent results, cancellation, worktree isolation, long-response behavior, or credential separation is missing.
