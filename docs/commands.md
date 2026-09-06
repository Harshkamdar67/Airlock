# Capabilities and commands

Every command and setting Airlock exposes, in one place. Commands that start a
session are listed first, then the settings that change how a session behaves,
then the maintenance commands.

Anything not listed here is not a command. In particular there is no
`airlock doctor`: the health check is a script, `./scripts/doctor.sh` on macOS
and Linux and `powershell -NoProfile -File .\scripts\doctor.ps1` on Windows.

## What Airlock does

| Capability | Where it is explained |
|---|---|
| Run GPT, Grok, and OpenRouter models inside Claude Code on subscriptions or a separate declared key | [How it works](how-it-works.md) |
| Run a user-hosted Chat Completions model through an explicit loopback-only route | [Open models](#open-models) below |
| Mix providers in one session, each as a named worker with its own model | [Models and usage](models-and-usage.md) |
| Hand a rate-limited or oversized request to another enabled model automatically | [Handoff](#handoff) below |
| Keep the model that answered visible and honest | [Handoff](#handoff) below |
| Declare extra provider models without waiting for a release | [Declaring your own models](models-and-usage.md#declaring-your-own-models) |
| Block direct reads of credential and env files during a session | [Threat model](threat-model.md) |
| Give a worker an isolated git worktree built from a safe snapshot | [Security and file access](../README.md#security-and-file-access) |
| See every session's state, why one is blocked, and which routes have room | [Console](console.md) |

## Starting a session

```bash
airlock                          # the saved default profile and root
airlock openai                   # OpenAI-only session on the saved OpenAI root
airlock astra                    # OpenAI-only session on an exact root
airlock terra
airlock luna
airlock 5.5
airlock 5.4
airlock mini
airlock spark
airlock fast -r                  # one session on a Fast root, not saved
airlock grok                     # Grok-only session
airlock grok composer
airlock hybrid                   # mixed-provider session on the saved root
airlock hybrid choose            # interactive root picker
airlock hybrid auto              # resolve the root at launch
airlock hybrid opus              # exact hybrid root
airlock hybrid sonnet
airlock hybrid fable
airlock hybrid haiku
airlock hybrid astra
airlock hybrid sol
airlock hybrid grok
airlock opr                      # OpenRouter-only session, interactive picker
airlock opr kimi-k3              # OpenRouter-only session on an exact route
airlock om local-coder           # open-model-only session on an exact route
airlock hybrid om:local-coder    # open-model root with mixed-provider workers
airlock bg                       # background convenience command
```

`airlock hybrid ROUTE` can also take a declared OpenRouter route as the root. An open model always uses the reserved `om:` prefix in a hybrid command, so Airlock never guesses between private registries.

## Handoff

When an upstream answers 402, 429, or 529, Airlock retries the same request on
another enabled model in the same usage category, up to three hops. It does the
same when a peer rejects a conversation for being too large, and it can condense
history so a smaller peer can still serve the request. User-declared open models
never appear as a handoff source, peer, or compaction model.

```bash
airlock mode failover ask        # default: chains use included models only
airlock mode failover never      # no chains at all
airlock mode failover allow      # extra-usage models may serve as peers
airlock mode anthropic-rate-limit native   # default: Claude Code waits/resumes
airlock mode anthropic-rate-limit handoff  # cross-provider continuity
airlock mode overflow auto       # default: condense, then trim, then fail honestly
airlock mode overflow summarize
airlock mode overflow truncate
airlock mode overflow off
```

Handoff is never silent:

- At the end of any turn where the router switched models, a short notice names
  the model that was unavailable and the model that actually answered.
- The replacement is told it is the replacement, so it does not answer in the
  other model's name or misreport its identity.
- `airlock status` lists the hops, cooldown skips, and exhausted chains.

Two things worth knowing. An Anthropic 429 is forwarded to Claude Code
untouched by default because Claude Code already understands its own provider's
limits. Save `airlock mode anthropic-rate-limit handoff` to switch models
instead; an `AIRLOCK_ANTHROPIC_RATE_LIMIT` environment value overrides the saved
choice for one launch. And `/model` cannot show a
handed-off model: that is Claude Code's own session state, and a handoff is
decided per request rather than per session, so the turn notice and
`airlock status` are the accurate record.

Claude Code also runs some work of its own, compaction most visibly, by asking
for a Haiku model directly instead of using the slot Airlock seats for it. When
your access policy does not include Haiku there is no such route, so those
requests are served by the background seat Claude Code was already given.
`airlock status` records each substitution. If that seat is on an exhausted
Anthropic plan, compaction is itself another rate-limited model request:
`anthropic-rate-limit handoff` lets it continue down the declared chain, while
`native` preserves Claude Code's reset time and resume behavior. If every
enabled provider in the chain is exhausted, no model-based compaction can run;
start a fresh session or wait for a provider reset.

### Choosing the order yourself

```bash
airlock handoff                     # show the tree
airlock handoff set sol opus grok   # sol tries opus, then grok
airlock handoff off sol             # sol never hands off
airlock handoff clear sol           # back to the default order
airlock handoff recommended         # use the suggested order
airlock handoff reset               # clear every choice
```

`recommended` applies an order that follows capability rather than price:
each frontier route falls to another frontier route on a different provider,
and the smaller tiers fall to the nearest capable neighbour. The derived
default instead keeps a handoff inside one usage category, so it can never
spend more than the model it replaces but leaves a category with a single
member, such as the metered route, with nowhere to go. Routes you have not
connected are dropped, so the shape follows your own providers.

Names are the short route names shown in the tree, not exact model IDs, so
you never have to type a suffix like `[1m]`. A name that does not exist is
refused with the list of names that would have worked. Add `--profile NAME`
to work on a profile other than the mixed-provider one.

These commands write `failover.json` for you; the file format is described
in [Declaring your own chains](models-and-usage.md#declaring-your-own-chains)
if you would rather edit it directly.

## Routing and limits

```bash
airlock mode                     # show current routing and worker limits
airlock mode economy
airlock mode balanced
airlock mode quality
airlock mode budget              # economy, no extra usage, no failover, Fast off
airlock mode defaults            # restore tested settings
airlock mode max-agents off      # use Claude Code's native worker limit
airlock mode max-agents 3
airlock mode depth 1             # named workers cannot spawn workers
airlock mode depth 2             # a worker may spawn only its own type
```

`airlock mode set` accepts the same values as flags, so several can change at
once: `--routing`, `--extra-usage`, `--max-agents`, `--openai-fast`,
`--anthropic-fast`, `--swarm-fast`, `--failover`,
`--anthropic-rate-limit`, `--overflow-shrink`.

## Usage and status

```bash
airlock usage                    # refresh stale OpenAI usage and show it
airlock session-usage            # router totals for the active hybrid session
airlock status                   # this session's root and recent router actions
airlock models                   # list the models this configuration enables
airlock config                   # show saved roots and advanced values
```

`airlock usage` covers OpenAI only. Anthropic has no documented personal
subscription API that Airlock can read safely, so use Claude Code's `/usage`.
Grok has no readable plan window either.

## OpenRouter

```bash
airlock openrouter auth set-key    # store a key in OS credential storage
airlock openrouter auth status
airlock openrouter auth logout
airlock openrouter models list
airlock openrouter models presets   # curated opt-in starting points
airlock openrouter models add-preset NAME
```

Declared routes appear as `airlock-or-ROUTE` workers in a hybrid session and can
lead an `airlock opr` session as the root.

## Open models

Airlock does not create any open-model route during installation. First declare
an already-running loopback Chat Completions endpoint:

```bash
airlock open-model endpoint add local-server --max-concurrency 1
# Hidden prompt: Loopback base URL
airlock open-model endpoint list
```

Then declare the route and every capability you intend Airlock to permit:

```bash
airlock open-model add local-coder local-server \
  --context-window 32768 \
  --max-output-tokens 4096 \
  --streaming \
  --tools single \
  --tool-choice auto \
  --worker
# Hidden prompts: upstream model identity and accepted response identities
airlock open-model list
airlock open-model check local-coder
airlock open-model remove local-coder
airlock open-model endpoint remove local-server
```

Private endpoint and model identities are never command arguments. By default,
`add` reads them through bounded hidden prompts and requires an interactive terminal.
Each prompt retains at most 1,024 Unicode characters and 4,096 UTF-8 bytes. For
automation, pass `--stdin` and send one bounded UTF-8 JSON object to standard
input. Endpoint input has exactly `{"base_url":"..."}`. Route input has exactly
`{"upstream_model":"...","accepted_response_models":["..."]}`. For example,
a protected producer can pipe route JSON into:

```bash
generate-private-route-json | airlock open-model add local-coder local-server \
  --stdin --context-window 32768 --max-output-tokens 4096 \
  --streaming --tools single --tool-choice auto --worker
```

List every exact identity the same configured model can return in
`accepted_response_models`. Repeat `--tool-choice` for each mode that the
server actually supports: `auto`, `named`, `none`, or `required`. A route with
`--tools none` declares no tool-choice mode. Choose exactly one of `--streaming`
or `--no-streaming`, and exactly one of `--worker` or `--no-worker`. Add
`--disabled` to create an inactive endpoint or route. Removal asks for
confirmation unless `--yes` is present.

`check` is the only management command here that contacts the inference server.
It makes one bounded, credential-free `GET /v1/models` request, follows no
redirect, and compares the exact private identity without printing it. It does
not send a generation request. Doctor never performs this check.

A route can lead a pure `airlock om ROUTE` session. If it declares `--worker`,
it also appears as the exact `airlock-om-ROUTE` Agent in eligible open-model and
hybrid sessions. Use `airlock hybrid om:ROUTE` when the open model should lead
while normal subscription workers remain available. Open models are explicit
only and never become a saved root, `auto` choice, discovery seat, handoff peer,
compactor, or automatic swarm worker.

## Console

```bash
airlock console               # start the console, print its address, open a browser
airlock console --port 4900   # use a different port
airlock console --no-open     # start the server without opening a browser
airlock console --scan        # also probe for routers with no registry file yet
airlock console --once        # print one JSON overview to stdout and exit
```

The console is a local page that shows every Airlock session, why one is blocked, what the router already tried, and which routes still have room. The default address is `http://127.0.0.1:4783`. See the [console guide](console.md).

## Provider sign-in

```bash
airlock proxy auth status          # Codex OAuth state
airlock proxy auth login           # sign in for GPT routes
airlock proxy auth device          # device-code sign-in
airlock proxy grok auth login      # the same three for Grok
```

## Maintenance

```bash
airlock bundle                     # verify managed files
airlock version
airlock update --check             # check for a newer release
airlock update                     # download, verify, and confirm an update
airlock access show
airlock access refresh
```

Health check, which is a script rather than a subcommand:

```bash
./scripts/doctor.sh                                   # macOS and Linux
powershell -NoProfile -File .\scripts\doctor.ps1      # Windows
```

## Settings files

| File | Purpose |
|---|---|
| `$AIRLOCK_CONFIG_DIR/config` | saved roots, efforts, and policies |
| `$AIRLOCK_CONFIG_DIR/models.json` | extra provider models you declare |
| `$AIRLOCK_CONFIG_DIR/failover.json` | your own handoff order per model |
| `$AIRLOCK_CONFIG_DIR/openrouter-registry.json` | declared OpenRouter routes |
| `$AIRLOCK_CONFIG_DIR/openmodel-registry.json` | private loopback endpoints and declared open-model routes |

Every running router also writes one small registry file describing itself, so the console can find it. On Windows this lives under `%LOCALAPPDATA%\Airlock\sessions\`, elsewhere under `$XDG_STATE_HOME/airlock/sessions/` (default `~/.local/state/airlock/sessions/`). The router writes this file when it is ready and removes it when it shuts down; it holds no credentials and is not meant to be edited by hand. See [How it works](how-it-works.md#the-session-registry) for what it contains.

While the console serves, on port 4783 or one supplied with `--port`, it atomically writes its private, non-secret address marker at `<console runtime root>/console-address.json`. The marker records a schema version, its loopback URL, process ID, and random instance identity, never a CSRF token or router control token. `airlock-console-tools` rereads it on every call, so a custom port works automatically; an absent or stale marker falls back to `http://127.0.0.1:4783`, while an explicit MCP `--console-url` bypasses discovery.

## Environment settings

Environment values override saved mode settings for one launch. The remaining
variables in this table have no `airlock mode` equivalent.

| Variable | Effect |
|---|---|
| `AIRLOCK_ANTHROPIC_RATE_LIMIT` | one-launch override for `airlock mode anthropic-rate-limit` (`native` or `handoff`) |
| `AIRLOCK_OPENROUTER_CHAIN_PEER` | `off` removes the declared OpenRouter route as the last-resort peer |
| `AIRLOCK_OPENMODEL_REGISTRY_FILE` | path to the private open-model endpoint and route registry |
| `AIRLOCK_FAILOVER_FILE` | path to your handoff file |
| `AIRLOCK_CONSOLE_TOOLS` | `off` skips registering the `airlock-console-tools` MCP server for this session |
| `AIRLOCK_CONSOLE_SITE` | path to the built console page, overriding the one installed beside `bin/` |
| `AIRLOCK_MODELS_FILE` | path to your declared models file |
| `AIRLOCK_PYTHON` | exact Python 3 interpreter to use |
| `AIRLOCK_EXTRA_USAGE_POLICY` | `ask`, `never`, or `allow` for this session |
