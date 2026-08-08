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

## Explore, Plan, or general-purpose is blocked

Current sessions allow the exact built-in `Explore`, `Plan`, and `general-purpose` Agent types.

Without a model field, each one inherits the orchestrator model.

With a model field, the value must be one exact full model ID enabled for the active session. The OpenAI-only profile allows OpenAI IDs only. Hybrid can allow both providers. Bare `airlock` starts the profile saved by setup.

Aliases, `inherit`, malformed IDs, disabled models, blocked extra-usage models, and ineligible Fast models fail closed.

If a built-in is still blocked after a valid call, reinstall the managed files, close the old session, and start a new one. Existing sessions keep the guard and allowlists they started with.

## A named Agent uses the wrong model

A current named Agent definition contains an exact model ID. The native Agent card should show that model.

Run:

```bash
airlock bundle
```

If it fails, reinstall and restart. Do not add a caller `model` override to a named `airlock-*` Agent. The guard rejects it because the Agent name and model must remain consistent.

## Web Search rejects `xhigh` or `max` effort

Web Search can use a smaller internal search model with thinking disabled. That helper may accept only `high` effort or below even when the main model or a named worker supports `xhigh` or `max`.

Do not retry the same failing search. Use one of these paths:

- change the session to `/effort high` before searching
- give search discovery to an eligible worker pinned at `high` or below
- give the design worker an exact public URL and use WebFetch instead

The error does not mean that Opus, the public website, or provider login failed. It is an effort mismatch at the Web Search helper boundary.

## A GPT model is missing from `/model`

Claude Code gateway discovery can ignore non-Claude model IDs. This is a discovery limit, not proof that the router cannot use the ID.

Use a reliable exact path:

```bash
airlock terra
airlock hybrid sol
```

Or use a named Agent such as `airlock-luna`. Explore, Plan, and general-purpose may receive an exact allowed GPT ID for one call.

The router rejects IDs outside the active allowlist.

## Hybrid says `Model is not enabled for this session`

Claude Code removes the `[1m]` context suffix before sending a GPT request. A current installation registers both the exact enabled full ID and that deterministic wire form. It does not register other aliases.

Run `airlock bundle`, reinstall Airlock if the bundle is stale, exit the failed session, and start a fresh `airlock hybrid MODEL` session.

## `/model` changes do not behave as expected in hybrid

Every hybrid model still uses one local router endpoint. The router can cross providers only when Claude Code sends an exact enabled model ID.

Not every GPT ID appears in the menu, and Anthropic does not officially support non-Claude models behind a Claude Code gateway. Starting a fresh session with `airlock hybrid MODEL` is the reliable way to select the main model.

Remote Control is unavailable behind a non-Anthropic base URL.

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

Named Agents disallow Agent and top-level spawn depth is one. Fan-out must stay with the main model.

Show the saved cap:

```bash
airlock mode
```

Use several top-level native Agent calls when the work is truly independent. Do not build hidden descendant trees.

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

## An OpenAI Agent card reports zero tokens

A native OpenAI worker can finish correctly and still show zero subagent tokens on its Agent card, while an Anthropic worker in the same session reports real numbers.

Claude Code reads that number from usage fields associated with the response. The router forwards responses byte for byte and never edits them, so it cannot repair a missing or ignored count. Airlock records the integer token fields that were present in the response so you can tell where the gap begins.

The work itself is not affected. Only the reported count is.

To see what actually arrived, read the router diagnostics for the session:

```bash
curl -s "$ANTHROPIC_BASE_URL/diagnostics"
```

Each event carries a `usage` object when the response contained token counts. If an OpenAI event has no `usage` object, the counts were missing from the response produced by the translation step. If the event has nonzero usage but the Agent card still shows zero, the display gap is inside Claude Code's accounting. The router reports what arrived and does not invent a number to fill either gap.

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
