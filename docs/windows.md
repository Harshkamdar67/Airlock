# Windows guide

Airlock supports native Windows through PowerShell and CMD. Git for Windows Bash is also required for the Claude Code safety hooks.

Use WSL2 when you want stronger isolation for commands that run repository code.

## Requirements

Install these first:

1. [Claude Code](https://code.claude.com/docs/en/setup)
2. [Git for Windows](https://git-scm.com/download/win), including Git Bash
3. Python 3
4. The Windows release of [`claude-code-proxy`](https://github.com/raine/claude-code-proxy)
5. A ChatGPT plan with Codex access

Confirm the commands are available:

```powershell
claude --version
git --version
bash --version
python --version
claude-code-proxy --version
```

If `python` opens the Microsoft Store instead of Python, install Python from python.org and reopen the terminal. You can also set `AIRLOCK_PYTHON` to the full Python executable path for the current terminal.

## Complete Codex login

Use the proxy's official command:

```powershell
claude-code-proxy codex auth login
```

Do not copy tokens into this project or into a `.env` file.

## Install Airlock

From the repository folder:

```powershell
powershell -NoProfile -File .\scripts\install.ps1
```

Install the optional example worker:

```powershell
powershell -NoProfile -File .\scripts\install.ps1 -WithAgent
```

If OAuth is not ready and you want the installer to open the official login:

```powershell
powershell -NoProfile -File .\scripts\install.ps1 -Login
```

The installer copies files only to the Airlock folders. It installs both Windows command wrappers and the matching extensionless Git Bash commands, so `airlock` and managed helpers resolve to the same version in either shell. It refuses to replace a file that does not contain a recognized managed marker.

It does not change:

- your user PATH
- native Claude settings
- native Codex settings
- global hooks or plugins
- MCP settings
- unrelated agents

## Add the command to PATH

The default command folder is:

```text
%USERPROFILE%\.local\bin
```

Add that folder to your user PATH through Windows Settings, then open a new terminal.

Check:

```powershell
airlock models
```

## Run the doctor

```powershell
powershell -NoProfile -File .\scripts\doctor.ps1
```

The doctor does not make a model request. It checks versions, OAuth, local proxy health, installed router and helpers, configuration, and the session plugin.

## Start a session

```powershell
airlock                 # saved default, new installs use hybrid Claude Sonnet 5
airlock openai          # saved OpenAI-only root
airlock hybrid          # saved hybrid root
airlock hybrid choose   # interactive hybrid picker
airlock hybrid opus     # explicit hybrid root
```

The PowerShell launcher starts the local OpenAI proxy in the background when it is installed but not healthy. A hybrid session also starts a temporary loopback router owned by its launcher process.

The installed config saves both an OpenAI-only root and a hybrid root. Bare `airlock` starts the saved default profile. `airlock openai` and explicit OpenAI aliases connect directly to the OpenAI proxy. Hybrid uses the router for exact OpenAI and Anthropic Agent model IDs.

## Why Git Bash is required

The session plugin checks Agent calls, protects sensitive files, and creates filtered native worktrees. Claude Code starts those checks with Bash commands. Native Windows therefore needs the Bash that ships with Git for Windows.

The checks still run Python for the actual validation. The Bash wrapper only finds a working Python 3 executable and fails closed if it cannot find one.

## Native Windows and WSL2

Native Windows is supported for the launcher and managed workers. Windows does not provide the same Bash sandbox that Claude Code uses on macOS, Linux, and WSL2.

Use WSL2 when:

- a worker will run repository code
- checks touch package managers or build tools
- the repository contains untrusted scripts
- you want Linux file permissions and process isolation

## Change install folders

For one PowerShell session:

```powershell
$env:AIRLOCK_INSTALL_DIR = 'D:\Tools\Airlock'
$env:AIRLOCK_CONFIG_DIR = 'D:\Config\Airlock'
$env:AIRLOCK_AGENT_DIR = "$HOME\.claude\agents"
powershell -NoProfile -File .\scripts\install.ps1
```

The launcher also supports `AIRLOCK_CONFIG_FILE`, `AIRLOCK_PLUGIN_DIR`, and the helper path variables documented in the example config.

## Remove it

Review each managed path before deleting it. The main folders are:

```text
%USERPROFILE%\.local\bin\airlock*
%USERPROFILE%\.config\airlock
%USERPROFILE%\.claude\agents\airlock-worker.md
```

Removing these files does not remove Claude Code, Codex, `claude-code-proxy`, or provider login data.
