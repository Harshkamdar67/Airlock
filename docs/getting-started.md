# Getting started

This guide takes you from a fresh checkout to your first Airlock session.

## Before you install

Airlock uses tools that you install separately:

1. Claude Code provides the terminal and tools.
2. `claude-code-proxy` connects Claude Code to Codex through your ChatGPT login.
3. Python 3 runs the local safety helpers.
4. Git creates isolated copies for workers.

Your normal `claude` and `codex` commands stay unchanged.

## macOS and Linux

Install Claude Code, Homebrew, Git, Python 3, and curl. Then run:

```bash
./scripts/setup.sh
```

The wizard asks about:

- the main model and effort
- the background model and effort
- the models that workers may use
- budget or quality preference
- extra usage
- parallel worker limits and Luna Fast swarm policy
- optional worker installation
- OAuth and service startup

It prints a full summary before writing files.

For the tested defaults, run:

```bash
./scripts/install.sh --with-agent
```

If Codex OAuth is missing, the installer stops. Complete the official login:

```bash
claude-code-proxy codex auth login
```

Then run setup again.

On a machine without a browser, use the upstream device flow:

```bash
claude-code-proxy codex auth device
```

## Windows

Follow [the Windows guide](windows.md). The short version is:

```powershell
claude-code-proxy codex auth login
powershell -NoProfile -File .\scripts\install.ps1
powershell -NoProfile -File .\scripts\doctor.ps1
```

Git for Windows Bash is required because Claude Code runs the session safety hooks through Bash.

## Check the installation

On macOS or Linux:

```bash
./scripts/doctor.sh
```

On Windows:

```powershell
powershell -NoProfile -File .\scripts\doctor.ps1
```

The doctor reports:

- Claude Code version
- proxy version
- OAuth status
- local health address
- launcher, router, and helper paths
- saved model choices
- plugin state
- optional worker state

It does not make a model request.

## Start a session

OpenAI only:

```bash
airlock
```

Choose a main model while keeping workers from both providers:

```bash
airlock hybrid
```

Direct choices:

```bash
airlock hybrid sol
airlock hybrid terra
airlock hybrid luna
airlock hybrid opus
airlock hybrid sonnet
airlock hybrid fable
```

Fable can use Anthropic extra usage. The saved extra-usage policy still applies.

Plain `airlock` uses native Claude Code Agents with enabled OpenAI model IDs. Hybrid starts one temporary loopback router so native Agents can use enabled OpenAI and Anthropic IDs in the same session.

Explore, Plan, and general-purpose inherit the main model when no model is supplied. The main model may give one of those built-ins an exact full model ID for one call when the active session policy allows it. Named `airlock-*` Agents already have a fixed model. Their effort follows the session unless the config pins it.

GPT IDs may not appear in Claude Code's `/model` discovery list behind a gateway. Use the launch commands above or an exact named Agent instead of relying on discovery.

## Pick a budget mode

Show the current settings:

```bash
airlock mode
```

Use fewer workers and block extra usage:

```bash
airlock mode budget
```

Return to the tested settings:

```bash
airlock mode defaults
```

These changes apply to new sessions. The default top-level worker setting is `off`, which uses Claude Code's native concurrency. Automatic armies start a native Luna or eligible Luna Fast batch before the main model waits, then collect every result. Workers follow the session effort, so `/effort` moves the main model and its workers together. Restart `airlock` after changing settings.

## Check plan usage

```bash
airlock usage
```

Airlock refreshes OpenAI data when the saved copy is older than 15 minutes. It falls back to the last valid copy if the refresh fails. This starts the local Codex app server but does not send a model request.

For Anthropic, open native Claude Code and run:

```text
/usage
```

## Where files are installed

Default macOS and Linux paths:

```text
~/.local/bin/airlock
~/.local/bin/airlock-*
~/.config/airlock/config
~/.config/airlock/plugins/airlock
```

Default Windows paths:

```text
%USERPROFILE%\.local\bin\airlock.cmd
%USERPROFILE%\.local\bin\airlock.ps1
%USERPROFILE%\.config\airlock\config
%USERPROFILE%\.config\airlock\plugins\airlock
```

## Next steps

- [Learn how the parts fit together](how-it-works.md)
- [Choose models and limits](models-and-usage.md)
- [Fix common problems](troubleshooting.md)
