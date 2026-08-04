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
- inherited Explore uses the root model
- Explore with an exact allowed OpenAI model ID uses that ID
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
- cross-provider Explore with an allowed exact Claude ID
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
- cross-provider Explore with an allowed exact GPT ID
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

Every shard must have explicit file ownership, no-touch boundaries, and acceptance checks. Verify that Luna uses max effort, shards do not touch each other's files, and one stronger model reviews, integrates, and tests the result.

### 10. UI and UX routing

Authorize Anthropic, Opus, exact harmless UI files, no public web unless separately needed, the extra-usage state, and one worker.

From an OpenAI-root hybrid session, request read-only visual direction without naming a worker. Verify that the main model chooses Opus when enabled, does not start Sonnet alongside it by default, and checks rendering and accessibility when tools permit.

Repeat with Sonnet only if Opus is disabled and Sonnet has separate authorization.

### 11. Luna Fast gate

Authorize OpenAI, exact Luna Fast, harmless task content, Fast state, extra-usage state, and exact worker count.

Verify that the sanitized plan is `prolite` or `pro`, proxy support is verified, the route is `luna-fast`, and the model is `gpt-5.6-luna-fast[1m]`.

An ineligible `on` request must fail without substitution. Do not test Sol Fast unless it has separate explicit root authorization.

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
