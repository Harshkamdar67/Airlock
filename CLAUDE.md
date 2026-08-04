# Agent installation protocol

Your task in this repository is to install and verify Airlock without exposing credentials or breaking the user's existing Claude Code or Codex setup.

1. Read `README.md` and `SECURITY.md` completely before changing the machine.
2. Confirm the operating system. On macOS or Linux, check for `git`, `brew`, `claude`, `curl`, Bash, and Python 3. On Windows, check for Git for Windows Bash, PowerShell, `git`, `claude`, `claude-code-proxy`, and Python 3.
3. On macOS or Linux, ask for missing model, effort, worker, OAuth, and service choices, or invite the user to run `./scripts/setup.sh`.
4. Apply explicit macOS or Linux choices with `./scripts/setup.sh --yes` and matching flags. The tested default installer is `./scripts/install.sh --with-agent`.
5. On Windows, use `powershell -NoProfile -File .\scripts\install.ps1`. Add `-WithAgent` only when requested.
6. If Codex OAuth is not healthy, stop for interactive approval. On macOS or Linux, use `./scripts/install.sh --login`, or `airlock proxy auth login` after installation, so the proxy receives Airlock's saved writable directory choice. On Windows, ask the user to run `claude-code-proxy codex auth login` in an interactive terminal. Never read, print, copy, summarize, or commit OAuth credentials or token files.
7. Run the matching doctor script. Use `./scripts/doctor.sh` on macOS or Linux and `powershell -NoProfile -File .\scripts\doctor.ps1` on Windows.
8. Run the normal stub tests. They do not make a model request. Include `bash tests/test-airlock.sh`, `bash tests/test-setup.sh`, and the platform tests listed in `docs/testing.md`.
9. Do not run a live model test without permission that names the provider, model, repository files, public web access, extra usage, and worker count.
10. Report the proxy version, OAuth status, service state, health address, launcher path, config path, selected models, selected efforts, and optional worker state. Do not report account IDs or tokens.

Safety constraints:

- Keep the proxy bound to `127.0.0.1`.
- Do not overwrite an unknown `airlock` launcher or `airlock-worker.md`. The installer may update only files carrying a recognized managed marker and must stop on every other conflict.
- Do not alter native Claude, native Codex, global settings, global hooks, registered plugins, MCP configuration, or unrelated agents.
- Do not set `ENABLE_TOOL_SEARCH=false`, `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`, `CLAUDE_CODE_SUBAGENT_MODEL`, or `CLAUDE_CODE_EFFORT_LEVEL` globally.
- Use only the upstream proxy's documented OAuth commands.
- Preserve user files and unrelated repository changes.
