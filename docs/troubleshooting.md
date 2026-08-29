# Troubleshooting

## `airlock` is not found

Add the install folder to PATH.

macOS and Linux:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Windows:

```text
%USERPROFILE%\.local\bin
```

Open a new terminal after changing PATH.

## Codex login is missing

If Airlock is already installed, run the upstream login through Airlock so the proxy receives the same configured directory selection:

```bash
airlock proxy auth status
airlock proxy auth login
```

If the first installation stopped before copying the launcher, rerun it with login explicitly allowed:

```bash
./scripts/install.sh --login
```

Then run the doctor again. Never paste a token into Airlock, an issue, or a support message.

## Grok workers are missing

Grok is off unless you enable it, so an empty Grok pool is usually the intended state.

Check the saved configuration and the login:

```bash
airlock config
airlock proxy grok auth status
```

Enable the routes by rerunning setup and answering the Grok question, or by passing the workers directly:

```bash
./scripts/setup.sh --yes --grok-workers grok,composer
airlock proxy grok auth login
```

A session that confirms the proxy is signed out of Grok disables the Grok routes on purpose, so that the main model is never offered a worker whose first request would fail. `airlock grok` and `airlock hybrid grok` enable the routes themselves, because naming a Grok root is an explicit request for that provider.

## Grok login shows `Missing or invalid client_id`

Stock Windows builds of `claude-code-proxy` open the login page through `cmd start` without quoting the address. cmd cuts the address at the first ampersand, so x.ai receives a login request without a client id and shows `Missing or invalid client_id`.

Check which build you have:

```powershell
claude-code-proxy --version
```

Airlock's carried build reports a version with `airlock` in it, such as `0.1.35-airlock.1`. If your version has no `airlock` marker, replace it with the fixed build:

```powershell
powershell -NoProfile -File .\scripts\install.ps1 -UpgradeProxy
powershell -NoProfile -File .\scripts\doctor.ps1
```

The upgrade keeps your previous binary next to the installed one as `claude-code-proxy.exe.<version>.bak`. Then log in again:

```powershell
airlock proxy grok auth login
```

macOS and Linux install upstream through Homebrew and never had this problem.

## Every Airlock session fails because of the OpenRouter registry

An expired or invalid OpenRouter registry entry does not just disable that one route. It makes the whole registry file invalid, which stops every Airlock session, including OpenAI-only and Grok-only ones. This is deliberate: a stale or broken declared route is treated as untrusted input rather than left running on unverified metadata.

Check what is declared:

```bash
airlock openrouter models list
```

A route's verified metadata expires after 30 days. Refresh it:

```bash
airlock openrouter models refresh --apply
```

Or remove the route if you no longer need it:

```bash
airlock openrouter models remove ROUTE
```

If you never ran `airlock openrouter models add` or `airlock openrouter models add-preset`, this is unrelated to OpenRouter. Listing presets does not create a registry. Run `airlock bundle` and check the doctor output instead.

## `airlock opr` refuses to start

`airlock opr` fails closed rather than guessing a route or falling back to another one:

- **`an OpenRouter route is required outside an interactive terminal`**: you ran `airlock opr` with no route argument from a script, CI job, or other non-interactive shell. Pass the exact route: `airlock opr ROUTE`.
- **`unknown or disabled OpenRouter route`**: the name does not match a currently declared, enabled route. Check the exact spelling with `airlock openrouter models list`; a route you removed, disabled, or never added cannot be selected, and route names are matched exactly.
- **`no enabled OpenRouter routes are available`**: your registry has nothing declared yet, or every declared route is disabled. Add one with `airlock openrouter models add` or `add-preset` first.
- **`OpenRouter roots are selected by exact registry route; --model and -m cannot be forwarded`**: `airlock opr` picks the model through the route, not through `--model`. Drop that flag and pass Claude Code's other arguments normally.
- **A stale or invalid registry entry**: the same registry check that blocks other Airlock sessions also blocks `airlock opr`. See [Every Airlock session fails because of the OpenRouter registry](#every-airlock-session-fails-because-of-the-openrouter-registry) above.

These checks run before Airlock contacts OpenRouter, so a rejected `airlock opr` launch never sends a request.

## OpenRouter returns `The selected OpenRouter upstream rejected the request`

This message means the exact pinned OpenRouter endpoint returned an error. Airlock keeps the status but hides the upstream body because it may contain prompt or account data. It does not retry another provider.

Claude Code can put its custom-model notice in a `messages` entry with the non-standard `system` role. Older Airlock builds forwarded that shape unchanged, and strict endpoints such as the curated Qwen 3.6 27B Chutes route rejected it with HTTP 400. Current Airlock preserves the notice by moving it into the Anthropic Messages API's top-level `system` field.

Check the managed installation first:

```bash
airlock bundle
```

If the bundle is current and the error remains, inspect the declared route without changing it:

```bash
airlock openrouter models list
airlock openrouter models refresh
```

A provider can still reject a request for its own availability, account, limit, or compatibility reasons even while its public catalog entry is valid. Do not change the endpoint or enable fallback based only on the sanitized message.

## OpenRouter effort is clamped to the route ceiling

Claude Code can send `output_config.effort=max` for an unknown custom model after you select maximum effort. That value is specific to Anthropic and pinned OpenRouter endpoints may reject it. Airlock forwards instead the strongest level the target route accepts, called its ceiling: `high` for every OpenRouter route by default, raised by that route's registry metadata or by declaring the model in `models.json`. Only fields above the ceiling change, the rest of the request and your saved session setting stay untouched, and routes outside OpenRouter receive your level unchanged.

In a routed hybrid session, `airlock status` records the clamp as a timestamped action naming the requested value, the forwarded value, and the ceiling. This is expected behavior, not a silent model or endpoint change.

## Fast handoff does not resume

The direct `airlock fast -r` shortcut starts a new one-session `gpt-5.6-sol-fast` root and does not change saved `AIRLOCK_OPENAI_FAST`. It still requires an eligible OpenAI plan and verified proxy support, and never falls back.

For the in-session workflow, `/airlock-fast` must run in the current managed Airlock session. After it arms, exit normally so the owning launcher can resume the exact conversation once. A hard kill, crash, non-clean exit, SessionEnd hook failure, or expired marker intentionally prevents relaunch. This is not Claude Code Anthropic `/fast`.

Check that the managed plugin is installed and current:

```bash
airlock bundle
```

The SessionEnd hook is required and is authorized as part of the Airlock plugin, not as a global hook. The private marker is bound to the session, launcher, working directory, and nonce, so moving the directory or trying to reuse it is rejected.

## Claude login is missing

Run the official Claude Code login:

```bash
claude auth login
```

Airlock does not read Claude credential files. OpenAI-only sessions can still work without this login, but hybrid sessions and Claude routes need it. The doctor reports these states separately.

## The OpenAI proxy is not healthy

Check:

```bash
curl --fail http://127.0.0.1:18765/healthz
```

On macOS or Linux, rerun the installer to restore the registered service with the saved proxy directory choice:

```bash
./scripts/install.sh
./scripts/doctor.sh
```

On Windows:

```powershell
powershell -NoProfile -File .\scripts\doctor.ps1
```

Keep the proxy on `127.0.0.1`. Do not expose port `18765` to your network.

## The hybrid router does not start

Hybrid startup requires:

- a healthy loopback OpenAI proxy
- the installed `airlock-router.py` from the same managed bundle
- a saved Claude subscription login
- no explicit `ANTHROPIC_API_KEY`
- no non-placeholder `ANTHROPIC_AUTH_TOKEN`

Run:

```bash
airlock bundle
```

Then reinstall and start a fresh session. Do not set a gateway API token by hand. Hybrid mode must preserve the normal saved Claude login path.

The router chooses an unused `127.0.0.1` port and exits with its launcher. It is not expected to remain running between sessions.

## A model hits a rate limit

Sessions using Airlock's router handle this automatically when a failover peer is available. When an upstream answers 402, 429, or 529, the router retries the same request on another enabled model of the same cost category, at most three hops. A limited model also goes on a short cooldown, so later requests skip it instead of waiting on it. If every same-category peer is limited too, the request ends with an honest 429 and a fixed Airlock message without contacting a route that is still cooling.

One deliberate exception crosses categories: the session's declared OpenRouter root, such as Ox Alpha, is appended as the final peer of every other chain, so a fully limited premium or standard chain still has one last stop. OpenRouter requests themselves never receive it. Set `AIRLOCK_OPENROUTER_CHAIN_PEER=off` before launching to remove it. Remember that the route carries the data terms of its provider, which for Ox Alpha includes prompt retention by an anonymous preview provider.

Nothing about failover changes your saved models. The handoff lasts for one request and later requests return to the normal route once the cooldown expires.

To inspect recent router decisions inside an active session:

```bash
airlock status
```

The first line names the pinned profile and root. The remaining timestamped lines can show a failover attempt, successful handoff, exhausted chain, or cooldown skip. The list is bounded. It never includes prompts, headers, credentials, or provider error bodies, and a future event type is shown by its short kind rather than by arbitrary payload fields. The managed SessionEnd hook shows an even shorter version when the session closes and stays silent if the router is already gone.

For token totals and the full list of models that are currently cooling down, use:

```bash
airlock session-usage
```

To check that failover is enabled, and change it:

```bash
airlock mode
airlock mode failover ask
```

Under the default `ask` policy only included models act as failover targets, so healing never starts extra usage on its own.

## Explore, Plan, or general-purpose is blocked

Current sessions allow the exact built-in `Explore`, `Plan`, and `general-purpose` Agent types.

Plan and general-purpose inherit the orchestrator when the model field is omitted. Explore inherits only when the root is already the economical discovery route. If routine unpinned Explore would spend a different premium root, Airlock blocks the call and recommends `model="haiku"`, followed by the exact model behind that slot.

With a model field, use one of Claude Code's schema-valid `fable`, `opus`, `sonnet`, or `haiku` family aliases. Airlock resolves the alias to an exact enabled session model before the Agent starts. Bare `airlock` starts the profile saved by setup.

Unknown values, `inherit`, malformed or stale family maps, disabled routes, blocked extra-usage models, and ineligible Fast models fail closed. Use a named `airlock-*` Agent when exact model identity matters.

If a built-in is still blocked after a valid call, reinstall the managed files, close the old session, and start a new one. Existing sessions keep the guard and allowlists they started with.

## A named Agent uses the wrong model

A current named Agent definition contains an exact model ID. The native Agent card should show that model.

Run:

```bash
airlock bundle
```

If it fails, reinstall and restart. Do not add a caller `model` override to a named `airlock-*` Agent. The guard rejects it because the Agent name and model must remain consistent.

## WebFetch fails with an unrecognized model

The error looks like this, and it appears before anything is fetched:

```
[claude-code:unrecognized_model] {"model":"gpt-5.6-luna","query_source":"web_fetch_apply"}
```

WebFetch reads the fetched page with Claude Code's Haiku-class model, and Claude
Code refuses a model ID it does not recognize. Session titles use the same slot
and fail the same way. This is not a network, proxy, or router problem, and
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` has nothing to do with it.

A hybrid session seats a Claude model in that slot, so WebFetch works. It prefers
Claude Haiku and falls back to the other enabled Claude routes. Add `haiku` to
`AIRLOCK_ANTHROPIC_MODELS` in your Airlock config to get the cheapest seat;
without it the slot falls back to a larger Claude route and every page read
costs more.

A pure OpenAI, Grok, or OpenRouter session has no model Claude Code recognizes,
so WebFetch cannot work in those profiles. Use a hybrid session when a task needs
it, or give the URL to a worker that can read it another way.

## Web Search rejects `xhigh` or `max` effort

Web Search can use a smaller internal search model with thinking disabled. That helper may accept only `high` effort or below even when the main model or a named worker supports `xhigh` or `max`.

Do not retry the same failing search. Use one of these paths:

- change the session to `/effort high` before searching
- give search discovery to an eligible worker pinned at `high` or below
- give the design worker an exact public URL and use WebFetch instead

The error does not mean that Opus, the public website, or provider login failed. It is an effort mismatch at the Web Search helper boundary.

## Every tool call asks for manual approval

On a GPT, Grok, or OpenRouter root, almost every Bash, Edit, or Write call stops with a permission prompt even though auto mode should have approved it.

Claude Code runs a small permission classifier on calls that are not obviously safe. That classifier ignores your session's models and always asks for `claude-sonnet-5`. No route serves that exact ID on a non-Anthropic root, so every classified call fails closed into a prompt.

Airlock seats your session's cheapest served model in the classifier knob automatically, so new sessions do not hit this. If you still see it:

- confirm you launched through `airlock` or `airlock.ps1` and not raw `claude`
- start a new session; existing sessions keep the environment they started with
- pin a specific model yourself with `AIRLOCK_AUTO_MODE_MODEL=<exact-id>`
- set `AIRLOCK_AUTO_MODE_MODEL=off` to return to stock Claude Code behaviour

## WebSearch fails with `cannot run Anthropic server-side tools`

An OpenRouter-rooted session calls built-in WebSearch and the request dies with a 400 naming `web_search_20250305` or a similar server tool type.

Built-in WebSearch and WebFetch are features of Anthropic's API. The router forwards requests verbatim to OpenRouter, and an OpenRouter upstream rejects any request carrying those tool declarations before generating anything.

Airlock now strips those declarations and their history blocks before forwarding, so the rest of the request succeeds. This does not make WebSearch work: the router cannot execute searches or invent results, because server-side execution belongs to Anthropic's API, not to this machine. A non-Anthropic root should use the managed `airlock-web-tools` MCP server (`web_search` and `fetch_page`) instead. Set `AIRLOCK_OPENROUTER_KEEP_SERVER_TOOLS=1` if you need the old pass-through behaviour back.


## `/model` shows provider models under Claude family slots

Claude Code always presents Fable, Opus, Sonnet, and Haiku slots. In an OpenAI-only or Grok-only Airlock session, leaving those slots on native Claude IDs would send unsupported IDs to the subscription proxy. Airlock maps them to distinct enabled models from the active provider and labels each entry with its exact model ID. The launch command's exact root also stays available as the custom option.

If every slot shows the same root or Fable still shows a native Claude model, the installation is stale. Run `airlock bundle`, reinstall the current branch or release, and start a new session. Existing sessions keep the environment they started with.

A hybrid session behaves differently. Each family slot prefers the matching native Claude route when it is eligible, falls back only to another enabled session model, and labels the slot with the exact target. A GPT or Grok root appears as the custom hybrid option.

## A GPT model is missing from `/model`

Claude Code gateway discovery can ignore non-Claude model IDs. This is a discovery limit, not proof that the router cannot use the ID.

Use a reliable exact path:

```bash
airlock terra
airlock hybrid sol
```

Or use a named Agent such as `airlock-luna`. Built-in Explore, Plan, and general-purpose accept only Claude Code's schema-valid family aliases; Airlock resolves each alias to an exact allowed session model.

The router rejects IDs outside the active allowlist.

## Hybrid says `Model is not enabled for this session`

Claude Code removes the `[1m]` context suffix before sending a request for supported native Claude IDs. Legacy suffixed GPT IDs are normalized to bare IDs at launcher and setup boundaries, so a current installation registers only canonical bare OpenAI IDs plus any deterministic Claude wire form.

Run `airlock bundle`, reinstall Airlock if the bundle is stale, exit the failed session, and start a fresh `airlock hybrid MODEL` session.

## `/model` changes do not behave as expected in hybrid

Every hybrid model still uses one local router endpoint. The router can cross providers only when Claude Code sends an exact enabled model ID.

Not every GPT ID appears in the menu, and Anthropic does not officially support non-Claude models behind a Claude Code gateway. Starting a fresh session with `airlock hybrid MODEL` is the reliable way to select the main model.

Remote Control is unavailable behind a non-Anthropic base URL.

## The auto-compact setting in `/config` is greyed out

Claude Code disables that control whenever `CLAUDE_CODE_AUTO_COMPACT_WINDOW` is set. Native Anthropic roots leave it unset. OpenAI roots and Grok roots keep the saved conservative fallback because the authorized Sol proof above 300,000 tokens did not pass on 2026-08-09, and no shipped root declares a hard limit.

To take the control back for a session, start it with:

```bash
AIRLOCK_CONTEXT_WINDOW=auto airlock hybrid sol
```

If you explicitly export a numeric `AIRLOCK_CONTEXT_WINDOW` or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, that value wins even on a `[1m]` root and applies to every worker in the process.

To change the number instead of removing it, set `AIRLOCK_CONTEXT_WINDOW` to a whole number from 100000 to 1000000, in your config file or on the command line. Anthropic roots never set the variable, so the control stays available there. See [Context window and auto-compaction](models-and-usage.md#context-window-and-auto-compaction).

## The launcher says managed files are stale or incomplete

Run:

```bash
airlock bundle
```

Then reinstall and start a fresh session. The bundle marker is written last, so an interrupted installation fails closed instead of mixing launchers, hooks, Agent catalogs, or router versions.

Do not copy only one helper or catalog to work around the check.

## A worker cannot see an untracked file

Native worktree snapshots include only eligible non-ignored untracked regular files.

The file may be:

- ignored by Git
- a known credential path
- a credential-bearing JSON file
- a complete private-key block
- a link or Windows reparse point
- outside the repository
- too large or unstable while copied
- over the file-count or total-size limit

Ignored files are never projected or copied. A tracked env file can receive a key-only projection. An ignored untracked env file remains absent.

## An env file shows JSON instead of values

This is expected. An exact env read or an eligible env file in an isolated worktree receives a key-only projection:

```json
{"source_kind":"env","source_name":".env","keys":["DATABASE_URL"],"truncated":false,"values_exposed":false}
```

The model cannot see the value and should not guess it.

Use `.env.example`, `.env.sample`, or `.env.template` for safe examples.

## A broad Grep or Glob is blocked

The search could include a sensitive env or credential file. Narrow it to known safe files:

```text
path: src
file glob: *.py
```

The guard blocks broad content searches rather than trying to remove secret lines after the tool has already read them.

## An isolated worktree was preserved

The custom cleanup hook removes only an unchanged managed worktree. It preserves a worktree with uncommitted edits or additional commits.

This prevents silent loss when an Agent produced work. Review the worktree and remove it manually only when you are sure it is no longer needed.

Managed worktrees are under:

```text
.claude/worktrees/
```

Do not run `git clean` to remove them from a working repository.

## A worker tried to start another worker

Named Agents disallow Agent at the default Agent depth of 1, so fan-out must stay with the main model. Run `airlock mode depth 2` to let a worker spawn its own Agent type, then relaunch the session.

Show the saved cap:

```bash
airlock mode
```

Use several top-level native Agent calls when the work is truly independent. Do not build hidden descendant trees.

## A Grok session says `Unknown model "grok-4.6"`

Airlock pins the Grok flagship route to `grok-4.6`, which proxy 0.1.35 and later list. An older installed proxy does not carry that ID, so the router or proxy answers with an unknown-model error. Check what the installed build accepts with:

```bash
claude-code-proxy models
```

If `grok-4.6` is missing, upgrade the proxy. Composer is unaffected: `grok-composer-2.5-fast` exists in every current proxy catalog.

`grok-4.5` still works as a command-line alias and resolves to the same shipped flagship, because Airlock runs one Grok flagship route. To reach a different exact Grok ID, declare it in `models.json` as described in the models guide.

## Windows says the Claude Code launch command is too long

Current Airlock builds keep generated settings and routing guidance in private session files, so the complete managed worker catalog fits below the Windows process limit. Airlock checks the exact remaining command before starting the router. If it still refuses, a forwarded argument or the inline Agent catalog is unusually large. It reports the measured length without printing the command or prompt.

For a long noninteractive prompt, use standard input as shown below. If you still see raw `[WinError 206]` instead of Airlock's clear length message, run `airlock bundle`, reinstall the current managed bundle, and start a fresh session.

## A long noninteractive prompt says `Argument list too long`

Windows limits total command-line size. Supply the prompt through standard input:

```bash
airlock hybrid sol --output-format stream-json --verbose -p < prompt.txt
```

This avoids placing the prompt in the Windows process argument list.

## `airlock usage` shows an old value

Run:

```bash
airlock usage refresh
```

Bare `airlock usage` refreshes OpenAI readings older than 15 minutes. If refresh fails, it keeps the last valid reading and labels it as cached.

For Anthropic plan bars, use native Claude Code:

```text
/usage
```

## An OpenAI or Grok Agent card reports zero tokens

A native OpenAI or Grok worker can finish correctly and still show zero subagent tokens on its Agent card while an Anthropic worker in the same session reports real numbers.

The router forwards provider responses byte for byte. Airlock does not spoof Claude IDs or edit usage fields to influence Claude Code's closed-source card accounting. The work itself is unaffected.

Inside the active hybrid session, run:

```bash
airlock session-usage
airlock session-usage --json
```

The command reads only the active `127.0.0.1` router's cumulative sanitized summary. It reports how many requests contained upstream usage and shows per-provider/model input, cache-write, cache-read, and output totals. It never prints prompts, headers, bodies, or raw responses, and it labels the numbers as provider-reported session usage rather than a bill.

If `usage-observed` is lower than `requests`, some upstream responses carried no usage. If usage is nonzero here but the Agent card still shows zero, the display gap is inside Claude Code. A provider-pure session has no router, so `airlock session-usage` fails clearly rather than guessing.

## Shell scripts fail with `\r`

The repository includes `.gitattributes` rules that keep Bash files on LF line endings.

If an older checkout still has CRLF files, make sure `.gitattributes` is present and create a fresh checkout. Do not stage or reset a working tree that contains changes you need.

## The installer refuses to overwrite a file

This is a safety check. Airlock updates only files with a recognized managed marker.

Review the path. If it belongs to another tool or contains your changes, move it or choose another install folder. Do not remove the check just to make installation continue.

## Get more detail

Run the doctor and include only safe output in a report:

```bash
./scripts/doctor.sh
```

Windows:

```powershell
powershell -NoProfile -File .\scripts\doctor.ps1
```

Remove private paths, repository content, account details, and prompts before sharing logs.
