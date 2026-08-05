# Updating Airlock

Airlock updates are published as versioned GitHub Releases. Normal startup never checks GitHub, downloads an update, or changes installed files.

## Check manually

Show the installed version:

```bash
airlock version
```

Check whether a newer release is available:

```bash
airlock update --check
```

This is the manual update notice. It reads public GitHub Release metadata but does not download or install an asset. From inside an Airlock session, run:

```bash
! airlock update --check
```

A beta installation can move to a newer beta or a stable release. A stable installation sees stable releases only.

## Install a verified update

Finish active Airlock sessions, open a normal terminal, and run:

```bash
airlock update
```

Airlock will:

1. Find the newest release allowed by the current release channel.
2. Download the exact archive for the platform and `SHA256SUMS` from that release.
3. Verify the archive's SHA-256 checksum.
4. Verify GitHub build attestations when GitHub CLI is installed.
5. Reject unsafe archive paths, links, special files, or mismatched versions.
6. Show the current version, target version, release page, and verification results.
7. Ask before running the existing platform installer.
8. Run Airlock Doctor after installation.

If GitHub CLI is not installed, Airlock prints a provenance warning before installation. A checksum mismatch or failed attestation always stops the update.

For an explicitly approved noninteractive installation:

```bash
airlock update --yes
```

Without `--yes`, a noninteractive terminal is refused. Installation is also refused inside an active Airlock session because that session is still using the old managed files. Exit it and rerun the command from the terminal.

The updater preserves the existing Airlock configuration and the recognized optional `airlock-worker`. It delegates file replacement to the existing installers, which replace only recognized managed files. An unknown launcher, service file, symlink, or `airlock-worker.md` is refused instead of overwritten. The updater does not use `sudo`, modify native Claude or Codex settings, or update Claude Code or `claude-code-proxy`.

## Manual update

Use this path if the direct updater is unavailable or if you need to inspect every step.

1. Finish active Airlock sessions.
2. Open the [Airlock Releases](https://github.com/Harshkamdar67/Airlock/releases) page.
3. Choose a named release. Do not use a moving `main` branch archive.
4. Download the archive for the platform and `SHA256SUMS` from the same release.

Every official release is built by GitHub Actions from a tag whose commit is on `main`. Release assets are not built on a maintainer laptop.

### Verify on macOS or Linux

Set the exact archive name, select only its checksum entry, and verify it:

```bash
archive="airlock-<version>.tar.gz"
checksum_line="$(awk -v name="$archive" '$2 == name { print }' SHA256SUMS)"
[[ -n "$checksum_line" ]] || { printf 'Missing checksum for %s\n' "$archive" >&2; exit 1; }
printf '%s\n' "$checksum_line" | shasum -a 256 -c -   # macOS
# printf '%s\n' "$checksum_line" | sha256sum -c -     # Linux
```

### Verify on Windows

```powershell
$archive = 'airlock-<version>.zip'
$pattern = '^[0-9a-fA-F]{64}  {0}$' -f [regex]::Escape($archive)
$line = @(Get-Content .\SHA256SUMS | Where-Object { $_ -match $pattern })
if ($line.Count -ne 1) { throw "Expected one checksum for $archive" }
$expected = ($line[0] -split '\s+')[0]
$actual = (Get-FileHash ".\$archive" -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw "Checksum mismatch for $archive" }
```

If GitHub CLI is installed, verify the downloaded archive and checksum file:

```bash
gh attestation verify airlock-<version>.tar.gz --repo Harshkamdar67/Airlock
gh attestation verify SHA256SUMS --repo Harshkamdar67/Airlock
```

Use the ZIP name instead on Windows. Stop if a checksum or attestation does not match.

### Reinstall on macOS or Linux

Extract the verified archive, enter its directory, and run:

```bash
./scripts/install.sh --with-agent
./scripts/doctor.sh
```

Omit `--with-agent` if the optional generic `airlock-worker` is not installed.

### Reinstall on Windows

Extract the verified ZIP, open PowerShell in its directory, and run:

```powershell
powershell -NoProfile -File .\scripts\install.ps1
powershell -NoProfile -File .\scripts\doctor.ps1
```

Add `-WithAgent` only if the optional generic `airlock-worker` is installed.

## After updating

1. Read the release notes and check for configuration changes.
2. Confirm that Doctor reports the expected launcher, config, models, efforts, proxy state, and optional worker state.
3. Start a fresh Airlock session.

## Roll back

Download and verify the previous release, then run its installer manually. Do not replace assets under an existing tag. Security support before 1.0 covers only the latest tagged beta, so report a regression instead of staying on an older beta indefinitely.
