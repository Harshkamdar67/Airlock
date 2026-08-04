## What changed

Describe the problem and the chosen fix in a few sentences.

## Security and compatibility

- [ ] The proxy and router remain bound to `127.0.0.1`.
- [ ] This change does not read, copy, print, or log credentials or tokens.
- [ ] Native Claude, native Codex, global hooks, plugins, and MCP settings remain unchanged.
- [ ] macOS, Linux, Windows, and existing configurations were considered.
- [ ] I reviewed the change for unsafe file handling and provider-boundary regressions.

## Verification

List the exact commands you ran and their results.

```text
command: result
```

- [ ] I added or updated regression tests where behavior changed.
- [ ] Normal tests made no live model request.
- [ ] Any live test is disclosed below with its approved scope.
- [ ] Documentation was updated when user-facing behavior changed.
- [ ] Every commit includes a Developer Certificate of Origin sign-off.

## Live model use

State `None` or list the provider, exact model, files shared, public web state, extra-usage state, and worker count.

## Remaining limits

List known limitations, follow-up work, or `None`.
