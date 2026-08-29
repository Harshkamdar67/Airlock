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
| Run GPT, Grok, and OpenRouter models inside Claude Code on subscriptions rather than API keys | [How it works](how-it-works.md) |
| Mix providers in one session, each as a named worker with its own model | [Models and usage](models-and-usage.md) |
| Hand a rate-limited or oversized request to another enabled model automatically | [Handoff](#handoff) below |
| Keep the model that answered visible and honest | [Handoff](#handoff) below |
| Declare extra provider models without waiting for a release | [Declaring your own models](models-and-usage.md#declaring-your-own-models) |
| Block direct reads of credential and env files during a session | [Threat model](threat-model.md) |
| Give a worker an isolated git worktree built from a safe snapshot | [Security and file access](../README.md#security-and-file-access) |

## Starting a session

```bash
airlock                          # the saved default profile and root
airlock openai                   # OpenAI-only session on the saved OpenAI root
airlock terra                    # OpenAI-only session on an exact root
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
airlock hybrid sol
airlock hybrid grok
airlock opr                      # OpenRouter-only session, interactive picker
airlock opr kimi-k3              # OpenRouter-only session on an exact route
airlock bg                       # background convenience command
```

`airlock hybrid ROUTE` can also take a declared OpenRouter route as the root.

## Handoff

When an upstream answers 402, 429, or 529, Airlock retries the same request on
another enabled model in the same usage category, up to three hops. It does the
same when a peer rejects a conversation for being too large, and it can condense
history so a smaller peer can still serve the request.

```bash
airlock mode failover ask        # default: chains use included models only
airlock mode failover never      # no chains at all
airlock mode failover allow      # extra-usage models may serve as peers
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
untouched rather than handed to another provider, because Claude Code already
understands its own provider's limits; set `AIRLOCK_ANTHROPIC_RATE_LIMIT=handoff`
before launching to switch models instead. And `/model` cannot show a
handed-off model: that is Claude Code's own session state, and a handoff is
decided per request rather than per session, so the turn notice and
`airlock status` are the accurate record.

Claude Code also runs some work of its own, compaction most visibly, by asking
for a Haiku model directly instead of using the slot Airlock seats for it. When
your access policy does not include Haiku there is no such route, so those
requests are served by the background seat Claude Code was already given.
`airlock status` records each substitution.

### Choosing the order yourself

```bash
airlock handoff                     # show the tree
airlock handoff set sol opus grok   # sol tries opus, then grok
airlock handoff off sol             # sol never hands off
airlock handoff clear sol           # back to the default order
airlock handoff reset               # clear every choice
```

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
`--anthropic-fast`, `--swarm-fast`, `--failover`, `--overflow-shrink`.

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

## Environment settings

These have no `airlock mode` equivalent and are read at launch.

| Variable | Effect |
|---|---|
| `AIRLOCK_ANTHROPIC_RATE_LIMIT` | `native` (default) forwards an Anthropic 429 to Claude Code; `handoff` switches models instead |
| `AIRLOCK_OPENROUTER_CHAIN_PEER` | `off` removes the declared OpenRouter route as the last-resort peer |
| `AIRLOCK_FAILOVER_FILE` | path to your handoff file |
| `AIRLOCK_MODELS_FILE` | path to your declared models file |
| `AIRLOCK_PYTHON` | exact Python 3 interpreter to use |
| `AIRLOCK_EXTRA_USAGE_POLICY` | `ask`, `never`, or `allow` for this session |
