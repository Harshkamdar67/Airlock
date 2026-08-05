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

The guided terminal setup supports Up and Down arrow selection with Enter. Number and name entry still work. It has six short sections:

1. **Session and orchestrator:** choose hybrid or OpenAI-only, then choose from full model names and exact IDs. New installs recommend the hybrid profile with GPT-5.6 Sol.
2. **Worker pool:** choose the Balanced pool, the Economical pool, the Balanced pool plus Claude Fable 5, or individual exact-model workers.
3. **Effort:** choose the starting level and whether workers follow the session `/effort` setting, share one fixed level, or use selected pins.
4. **Safety and budget:** choose extra-usage behavior, provider Fast startup, routing preference, and a parallel-worker ceiling.
5. **Installation:** decide whether setup may start Codex OAuth for the local proxy when the proxy reports that it is signed out, and whether to start the proxy service. Claude Code is a separate prerequisite, and Airlock never changes or reads its sign-in.
6. **Review:** check the grouped settings, the numbered list of actions, and the config path. Nothing on the machine changes until you accept this screen, and you can start the questions over from here.

Advanced settings are optional. They contain the separate `airlock bg` convenience command, the utility model used for lightweight Claude Code requests, Luna Fast selection, failover, capacity hints, the optional generic worker, and per-model controls. None of those concepts are required to complete the normal setup path.

## Missing tools and sign-ins

Airlock treats the Claude and OpenAI paths separately:

| Machine state | macOS and Linux | Windows |
| --- | --- | --- |
| Claude Code is missing | Installation stops with the official setup link before managed files are copied. | Installation stops and asks you to install Claude Code. |
| Claude Code is installed but signed out | Installation can finish. Doctor says OpenAI-only sessions can work and tells you to run `claude auth login` before hybrid or Claude routes. | Same behavior. |
| `claude-code-proxy` is missing | The installer installs it with Homebrew, then checks Codex OAuth. | Installation stops. Install the Windows proxy build, then rerun the installer. |
| The proxy is installed but Codex OAuth is missing | Setup opens the official proxy login only if you approved that action. Otherwise installation stops and tells you to rerun the installer with `--login`, which preserves the selected writable proxy directory. | Run `claude-code-proxy codex auth login`, or explicitly run the installer with `-Login`. |
| Both sign-ins are healthy | Installation finishes and Doctor checks the local service and managed files. | Same behavior. |

The native `codex` command is not an Airlock installation dependency. Codex OAuth here means the login managed by `claude-code-proxy`.

`--config-only` is different by design. It writes only the noncredential Airlock configuration and does not install tools, check login, or start a service.

On macOS and Linux, Airlock normally uses `~/.config/airlock`. If that default location cannot be written and neither `AIRLOCK_CONFIG_DIR` nor `XDG_CONFIG_HOME` selected a location, setup automatically uses `~/.airlock` instead. The launcher, installer, and Doctor all resolve the same existing fallback. Airlock does not ask for `sudo` or change ownership of another application directory.

The proxy keeps OAuth data separately from the Airlock config. If the upstream proxy's normal config or state parent cannot be written, setup selects private directories under the writable Airlock location and passes the proxy's documented environment overrides only to proxy commands. It uses the same selection for login, background startup, later launches, and Doctor. Existing healthy proxy storage is not moved.

For the tested defaults, run:

```bash
./scripts/install.sh --with-agent
```

If Codex OAuth is missing, rerun the installer and explicitly allow the official login:

```bash
./scripts/install.sh --with-agent --login
```

After Airlock is installed, use its wrapper for later status checks or reauthentication. The wrapper applies the same proxy directory choice automatically:

```bash
airlock proxy auth status
airlock proxy auth login
airlock proxy auth device
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

## Check for updates

Airlock does not check GitHub during normal startup. Check manually with:

```bash
airlock update --check
```

Inside a session, run `! airlock update --check`. To install, exit every Airlock session and run `airlock update` from the terminal. Airlock downloads the platform archive, requires its SHA-256 checksum, verifies GitHub provenance when GitHub CLI is available, shows the target release, and asks before installation. Read [Updating Airlock](updating.md) for the noninteractive command and manual fallback.

## Start a session

Start the profile and orchestrator saved by setup:

```bash
airlock
```

New setup runs recommend the hybrid profile with GPT-5.6 Sol. Existing configs created before saved profiles were added keep their OpenAI-only bare command.

Start the saved OpenAI-only root:

```bash
airlock openai
```

Start the saved hybrid root or open the full picker:

```bash
airlock hybrid
airlock hybrid choose
```

Direct choices:

```bash
airlock terra
airlock hybrid sol
airlock hybrid terra
airlock hybrid luna
airlock hybrid opus
airlock hybrid sonnet
airlock hybrid fable
airlock hybrid haiku
```

An OpenAI alias such as `airlock terra` always means an explicit OpenAI-only launch. Fable can use Anthropic extra usage. The saved extra-usage policy still applies.

The OpenAI-only profile uses native Claude Code Agents with enabled OpenAI model IDs. Hybrid starts one temporary loopback router so native Agents can use enabled OpenAI and Anthropic IDs in the same session.

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

## Choose Fast startup

Control both providers at once or change one without changing the other:

```bash
airlock mode fast all
airlock mode fast openai
airlock mode fast anthropic
airlock mode fast off
airlock mode openai-fast on
airlock mode anthropic-fast off
```

OpenAI Fast still requires an eligible sanitized plan and verified proxy support. Anthropic Fast starts only an exact Claude Opus 5 root in native Fast mode and does not switch another Claude root to Opus. It uses paid Anthropic usage credits from the first token, so `ask` requires confirmation, `never` refuses, and `allow` starts it directly. Unsupported models stay at standard speed. See [Models, limits, and usage](models-and-usage.md) for the advanced Luna swarm control.

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

If the normal config parent is not writable, the last two paths move together to `~/.airlock/config` and `~/.airlock/plugins/airlock`.

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
