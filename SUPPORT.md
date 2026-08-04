# Support

Airlock is an independent community project in beta. Support is provided on a best-effort basis.

## Start with the documentation

- [Getting started](docs/getting-started.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Windows guide](docs/windows.md)
- [Known limits](README.md#known-limits)

Run the matching doctor before reporting a problem. The doctor does not make a model request.

```bash
./scripts/doctor.sh
```

Windows:

```powershell
powershell -NoProfile -File .\scripts\doctor.ps1
```

## Ask for help or report a bug

Open a GitHub issue with:

- the Airlock version
- operating system and shell
- the exact command that failed
- expected and actual behavior
- safe reproduction steps
- the relevant doctor result with private details removed

Do not post tokens, account details, credential paths, raw environment values, private prompts, or private repository content.

Questions about `claude-code-proxy`, Codex OAuth, or translation behavior may belong in the [upstream proxy repository](https://github.com/raine/claude-code-proxy).

## Security and private reports

Do not report a vulnerability or private conduct concern in a public issue. Follow [Security](SECURITY.md) or [Code of Conduct](CODE_OF_CONDUCT.md) for a private report.
