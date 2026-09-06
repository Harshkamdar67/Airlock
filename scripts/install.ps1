# Managed by https://github.com/Harshkamdar67/Airlock
[CmdletBinding()]
param(
  [switch]$WithAgent,
  [switch]$Login,
  [switch]$UpgradeProxy
)

$ErrorActionPreference = 'Stop'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
  throw 'install.ps1 supports native Windows only. Use scripts/setup.sh on macOS or Linux.'
}

$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$InstallDir = if ($env:AIRLOCK_INSTALL_DIR) { $env:AIRLOCK_INSTALL_DIR } else { Join-Path $HOME '.local\bin' }
$ConfigDir = if ($env:AIRLOCK_CONFIG_DIR) { $env:AIRLOCK_CONFIG_DIR } else { Join-Path $HOME '.config\airlock' }
$AgentDir = if ($env:AIRLOCK_AGENT_DIR) { $env:AIRLOCK_AGENT_DIR } else { Join-Path $HOME '.claude\agents' }
$ConfigTarget = Join-Path $ConfigDir 'config'
$BundleTarget = Join-Path $ConfigDir 'managed-bundle.json'
$PluginTarget = Join-Path $ConfigDir 'plugins\airlock'
$ManagedMarkers = @(
  'Managed by https://github.com/Harshkamdar67/Airlock',
  'Managed by Airlock'
)

function Resolve-Application {
  param([Parameter(Mandatory)][string[]]$Names)
  foreach ($name in $Names) {
    $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if ($command) { return [string]$command.Source }
  }
  return $null
}

function Resolve-Python3 {
  $names = if ($env:AIRLOCK_PYTHON) {
    @($env:AIRLOCK_PYTHON)
  } else {
    @('python3.exe', 'python3', 'python.exe', 'python')
  }
  foreach ($name in $names) {
    $candidate = Resolve-Application @($name)
    if (-not $candidate) { continue }
    try {
      & $candidate -c 'import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)' *> $null
      if ($LASTEXITCODE -eq 0) { return $candidate }
    } catch { }
  }
  return $null
}

function Assert-PlainDirectory {
  param([Parameter(Mandatory)][string]$Path)
  if (Test-Path -LiteralPath $Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw "install: refusing unsafe directory: $Path"
    }
    return
  }
  New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Test-ManagedTarget {
  param([Parameter(Mandatory)][string]$Path, [string]$Component)
  foreach ($marker in $ManagedMarkers) {
    if (Select-String -LiteralPath $Path -SimpleMatch $marker -Quiet -ErrorAction SilentlyContinue) {
      return $true
    }
  }
  # A file whose hash matches what the installed bundle recorded for the same
  # component was written by a previous Airlock release, even if it carries no
  # marker string. JSON catalogs never carried one.
  if ($Component -and (Test-Path -LiteralPath $BundleTarget -PathType Leaf)) {
    try {
      $recorded = (Get-Content -LiteralPath $BundleTarget -Raw | ConvertFrom-Json).components.$Component
      if ($recorded) {
        $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
        if ($actual -and $actual.ToLower() -eq ([string]$recorded).ToLower()) { return $true }
      }
    } catch { }
  }
  return $false
}

function Install-ManagedFile {
  param(
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$Target
  )
  if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
    throw "install: managed source is missing: $Source"
  }
  $parent = Split-Path -Parent $Target
  Assert-PlainDirectory $parent
  if (Test-Path -LiteralPath $Target) {
    $item = Get-Item -LiteralPath $Target -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw "install: refusing unsafe managed target: $Target"
    }
    $same = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash -eq
      (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash
    $component = $null
    if ($Source.StartsWith($RepoRoot)) {
      $component = $Source.Substring($RepoRoot.Length).TrimStart('\', '/').Replace('\', '/')
    }
    if (-not $same -and -not (Test-ManagedTarget -Path $Target -Component $component)) {
      throw "install: refusing to overwrite an unmanaged file: $Target"
    }
  }
  [IO.File]::Copy($Source, $Target, $true)
}

function Assert-PlainConsoleDirectory {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$UnsafeMessage
  )
  try {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw 'not a plain directory'
    }
    $unsafeChild = Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction Stop |
      Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
      Select-Object -First 1
    if ($unsafeChild) { throw 'contains a reparse point' }
  } catch {
    throw "${UnsafeMessage}: $Path"
  }
}

function Assert-ManagedConsoleDirectory {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$MarkerText,
    [Parameter(Mandatory)][string]$UnsafeMessage,
    [Parameter(Mandatory)][string]$UnmanagedMessage
  )
  Assert-PlainConsoleDirectory -Path $Path -UnsafeMessage $UnsafeMessage
  $marker = Join-Path $Path '.airlock-managed'
  try {
    $markerItem = Get-Item -LiteralPath $marker -Force -ErrorAction Stop
    if ($markerItem.PSIsContainer -or
        ($markerItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw 'marker is not a plain file'
    }
    $markerValue = [IO.File]::ReadAllText($marker)
  } catch {
    throw "${UnmanagedMessage}: $Path"
  }
  if ($markerValue -cne "$MarkerText`n") {
    throw "${UnmanagedMessage}: $Path"
  }
}

function Assert-ConsoleBackupContainer {
  param([Parameter(Mandatory)]$Container)
  $unsafeMessage = 'install: refusing unsafe Airlock Console backup'
  if ($Container.Name -cnotmatch '^\.airlock-console-backup-[0-9a-f]{32}$') {
    throw "${unsafeMessage}: $($Container.FullName)"
  }
  Assert-PlainConsoleDirectory -Path $Container.FullName -UnsafeMessage $unsafeMessage
}

function Get-ConsoleBackupSite {
  param(
    [Parameter(Mandatory)]$Container,
    [Parameter(Mandatory)][string]$MarkerText
  )
  $unsafeMessage = 'install: refusing unsafe Airlock Console backup'
  $unmanagedMessage = 'install: refusing unmanaged Airlock Console backup'
  Assert-ConsoleBackupContainer -Container $Container
  try {
    $children = @(Get-ChildItem -LiteralPath $Container.FullName -Force -ErrorAction Stop)
  } catch {
    throw "${unsafeMessage}: $($Container.FullName)"
  }
  if ($children.Count -ne 1 -or $children[0].Name -cne 'site') {
    throw "${unsafeMessage}: $($Container.FullName)"
  }
  $site = Join-Path $Container.FullName 'site'
  Assert-ManagedConsoleDirectory `
    -Path $site `
    -MarkerText $MarkerText `
    -UnsafeMessage $unsafeMessage `
    -UnmanagedMessage $unmanagedMessage
  return $site
}

function Install-ConsoleSite {
  $source = Join-Path $RepoRoot 'console\dist'
  $shareDir = Join-Path $InstallDir 'share'
  $airlockShareDir = Join-Path $shareDir 'airlock'
  $target = Join-Path $airlockShareDir 'console'
  $markerText = 'Managed by https://github.com/Harshkamdar67/Airlock (console site)'
  $targetUnsafe = 'install: refusing unsafe Airlock Console site directory'
  $targetUnmanaged = 'install: refusing to replace unmanaged Airlock Console site'

  $sourceAvailable = Test-Path -LiteralPath $source
  if ($sourceAvailable) {
    Assert-PlainConsoleDirectory `
      -Path $source `
      -UnsafeMessage 'install: refusing unsafe Airlock Console site source'
  } elseif (-not (Test-Path -LiteralPath $airlockShareDir)) {
    Write-Host 'Skipping Airlock Console site: console/dist is absent.'
    return
  }

  foreach ($directory in @($shareDir, $airlockShareDir)) {
    Assert-PlainDirectory $directory
  }

  # An interrupted same-parent swap has one of two recoverable shapes. Before
  # staging another site, either restore the only saved site when the target is
  # absent, or remove it after confirming that both old and active sites are
  # still exact managed trees. Anything ambiguous stays untouched.
  $backupCandidates = @(
    Get-ChildItem -LiteralPath $airlockShareDir -Force -ErrorAction Stop |
      Where-Object {
        $_.Name.StartsWith(
          '.airlock-console-backup-',
          [StringComparison]::OrdinalIgnoreCase
        )
      }
  )
  if ($backupCandidates.Count -gt 1) {
    throw "install: refusing multiple Airlock Console backups under: $airlockShareDir"
  }
  if ($backupCandidates.Count -eq 1) {
    $recoveryItem = $backupCandidates[0]
    $recoveryContainer = $recoveryItem.FullName
    Assert-ConsoleBackupContainer -Container $recoveryItem
    try {
      $recoveryChildren = @(
        Get-ChildItem -LiteralPath $recoveryContainer -Force -ErrorAction Stop
      )
    } catch {
      throw "install: refusing unsafe Airlock Console backup: $recoveryContainer"
    }

    # A crash can leave the exclusive container empty immediately before the
    # old target is moved into it, or immediately after a successful restore.
    # It is safe to remove only when a fully validated managed target exists.
    if ($recoveryChildren.Count -eq 0) {
      if (-not (Test-Path -LiteralPath $target)) {
        throw "install: refusing ambiguous empty Airlock Console backup while the target is absent: $recoveryContainer"
      }
      Assert-ManagedConsoleDirectory `
        -Path $target `
        -MarkerText $markerText `
        -UnsafeMessage $targetUnsafe `
        -UnmanagedMessage $targetUnmanaged
      $recoveryItem = Get-Item -LiteralPath $recoveryContainer -Force -ErrorAction Stop
      Assert-ConsoleBackupContainer -Container $recoveryItem
      if (@(Get-ChildItem -LiteralPath $recoveryContainer -Force -ErrorAction Stop).Count -ne 0) {
        throw "install: refusing unsafe Airlock Console backup: $recoveryContainer"
      }
      Remove-Item -LiteralPath $recoveryContainer -Force
      Write-Host "Removed empty Airlock Console backup: $recoveryContainer"
    } else {
      $recoverySite = Get-ConsoleBackupSite `
        -Container $recoveryItem `
        -MarkerText $markerText
      if (Test-Path -LiteralPath $target) {
        Assert-ManagedConsoleDirectory `
          -Path $target `
          -MarkerText $markerText `
          -UnsafeMessage $targetUnsafe `
          -UnmanagedMessage $targetUnmanaged
        # Revalidate both sides immediately before deleting the stale backup.
        $recoveryItem = Get-Item -LiteralPath $recoveryContainer -Force -ErrorAction Stop
        $recoverySite = Get-ConsoleBackupSite `
          -Container $recoveryItem `
          -MarkerText $markerText
        Assert-ManagedConsoleDirectory `
          -Path $target `
          -MarkerText $markerText `
          -UnsafeMessage $targetUnsafe `
          -UnmanagedMessage $targetUnmanaged
        Remove-Item -LiteralPath $recoveryContainer -Recurse -Force
        Write-Host "Removed stale Airlock Console backup: $recoveryContainer"
      } else {
        # Revalidate immediately before moving the only recovery candidate.
        $recoveryItem = Get-Item -LiteralPath $recoveryContainer -Force -ErrorAction Stop
        $recoverySite = Get-ConsoleBackupSite `
          -Container $recoveryItem `
          -MarkerText $markerText
        try {
          Move-Item -LiteralPath $recoverySite -Destination $target
        } catch {
          throw "install: could not recover the previous Airlock Console site; backup retained at '$recoveryContainer'. $($_.Exception.Message)"
        }
        Assert-ManagedConsoleDirectory `
          -Path $target `
          -MarkerText $markerText `
          -UnsafeMessage $targetUnsafe `
          -UnmanagedMessage $targetUnmanaged
        $recoveryItem = Get-Item -LiteralPath $recoveryContainer -Force -ErrorAction Stop
        Assert-ConsoleBackupContainer -Container $recoveryItem
        if (@(Get-ChildItem -LiteralPath $recoveryContainer -Force -ErrorAction Stop).Count -ne 0) {
          throw "install: refusing unsafe Airlock Console backup: $recoveryContainer"
        }
        Remove-Item -LiteralPath $recoveryContainer -Force
        Write-Host "Recovered previous Airlock Console site: $target"
      }
    }
  }

  if (-not $sourceAvailable) {
    Write-Host 'Skipping Airlock Console site: console/dist is absent.'
    return
  }

  if (Test-Path -LiteralPath $target) {
    Assert-ManagedConsoleDirectory `
      -Path $target `
      -MarkerText $markerText `
      -UnsafeMessage $targetUnsafe `
      -UnmanagedMessage $targetUnmanaged
  }

  $staging = Join-Path $airlockShareDir (
    '.airlock-console-staging-' + [Guid]::NewGuid().ToString('N')
  )
  New-Item -ItemType Directory -Path $staging | Out-Null
  try {
    Get-ChildItem -LiteralPath $source -Force |
      Copy-Item -Destination $staging -Recurse -Force
    [IO.File]::WriteAllText(
      (Join-Path $staging '.airlock-managed'),
      "$markerText`n",
      (New-Object Text.UTF8Encoding($false))
    )
    Assert-ManagedConsoleDirectory `
      -Path $staging `
      -MarkerText $markerText `
      -UnsafeMessage 'install: refusing unsafe staged Airlock Console site' `
      -UnmanagedMessage 'install: staged Airlock Console site has an invalid managed marker'

    if (-not (Test-Path -LiteralPath $target)) {
      Move-Item -LiteralPath $staging -Destination $target
      Assert-ManagedConsoleDirectory `
        -Path $target `
        -MarkerText $markerText `
        -UnsafeMessage $targetUnsafe `
        -UnmanagedMessage $targetUnmanaged
      Write-Host "Installed Airlock Console site: $target"
      return
    }

    $backupContainer = Join-Path $airlockShareDir (
      '.airlock-console-backup-' + [Guid]::NewGuid().ToString('N')
    )
    New-Item -ItemType Directory -Path $backupContainer | Out-Null
    Assert-PlainConsoleDirectory `
      -Path $backupContainer `
      -UnsafeMessage 'install: refusing unsafe Airlock Console backup'
    $backupSite = Join-Path $backupContainer 'site'
    # Revalidate the managed target and exclusive empty container immediately
    # before the first same-parent rename.
    Assert-ManagedConsoleDirectory `
      -Path $target `
      -MarkerText $markerText `
      -UnsafeMessage $targetUnsafe `
      -UnmanagedMessage $targetUnmanaged
    Assert-PlainConsoleDirectory `
      -Path $backupContainer `
      -UnsafeMessage 'install: refusing unsafe Airlock Console backup'
    if (@(Get-ChildItem -LiteralPath $backupContainer -Force -ErrorAction Stop).Count -ne 0) {
      throw "install: refusing unsafe Airlock Console backup: $backupContainer"
    }
    try {
      Move-Item -LiteralPath $target -Destination $backupSite
    } catch {
      Remove-Item -LiteralPath $backupContainer -Force -ErrorAction SilentlyContinue
      throw "install: could not back up the existing Airlock Console site; target was not replaced. $($_.Exception.Message)"
    }

    try {
      $backupItem = Get-Item -LiteralPath $backupContainer -Force -ErrorAction Stop
      $backupSite = Get-ConsoleBackupSite `
        -Container $backupItem `
        -MarkerText $markerText
      Assert-ManagedConsoleDirectory `
        -Path $staging `
        -MarkerText $markerText `
        -UnsafeMessage 'install: refusing unsafe staged Airlock Console site' `
        -UnmanagedMessage 'install: staged Airlock Console site has an invalid managed marker'
      Move-Item -LiteralPath $staging -Destination $target
      Assert-ManagedConsoleDirectory `
        -Path $target `
        -MarkerText $markerText `
        -UnsafeMessage $targetUnsafe `
        -UnmanagedMessage $targetUnmanaged
    } catch {
      $activationFailure = $_.Exception.Message
      $rollbackFailure = $null
      try {
        if (Test-Path -LiteralPath $target) {
          Assert-PlainConsoleDirectory `
            -Path $target `
            -UnsafeMessage 'install: refusing unsafe failed Airlock Console activation'
          Remove-Item -LiteralPath $target -Recurse -Force
        }
        $backupItem = Get-Item -LiteralPath $backupContainer -Force -ErrorAction Stop
        $backupSite = Get-ConsoleBackupSite `
          -Container $backupItem `
          -MarkerText $markerText
        Move-Item -LiteralPath $backupSite -Destination $target
      } catch {
        $rollbackFailure = $_.Exception.Message
      }
      if ($rollbackFailure) {
        throw "install: Airlock Console activation failed and rollback failed; previous site retained at '$backupContainer'. Activation error: $activationFailure Rollback error: $rollbackFailure"
      }
      try {
        Remove-Item -LiteralPath $backupContainer -Force
      } catch {
        throw "install: Airlock Console activation failed; restored previous Airlock Console site, but could not remove the empty backup container '$backupContainer'. Activation error: $activationFailure"
      }
      throw "install: Airlock Console activation failed; restored previous Airlock Console site. $activationFailure"
    }

    # Once activation is committed, revalidate the new site and complete old
    # backup immediately before the only recursive backup removal. A cleanup
    # failure leaves the valid new target in place for conservative recovery.
    Assert-ManagedConsoleDirectory `
      -Path $target `
      -MarkerText $markerText `
      -UnsafeMessage $targetUnsafe `
      -UnmanagedMessage $targetUnmanaged
    $backupItem = Get-Item -LiteralPath $backupContainer -Force -ErrorAction Stop
    $backupSite = Get-ConsoleBackupSite `
      -Container $backupItem `
      -MarkerText $markerText
    Remove-Item -LiteralPath $backupContainer -Recurse -Force
    Write-Host "Installed Airlock Console site: $target"
  } finally {
    if (Test-Path -LiteralPath $staging) {
      Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
}
$Git = Resolve-Application @('git.exe', 'git')
$Bash = Resolve-Application @('bash.exe', 'bash')
$Claude = Resolve-Application @('claude.exe', 'claude.cmd', 'claude')
$Python = Resolve-Python3

foreach ($requirement in @(
  @('Git', $Git), @('Git Bash', $Bash), @('Claude Code', $Claude),
  @('Python 3', $Python)
)) {
  if (-not $requirement[1]) {
    throw "install: $($requirement[0]) is required and was not found on PATH."
  }
}

# Airlock carries a Windows claude-code-proxy build with one patch. Stock
# releases open OAuth login URLs through cmd start without quoting, cmd cuts
# the URL at the first ampersand, and x.ai rejects the sign-in with "Missing
# or invalid client_id". macOS and Linux install upstream through Homebrew
# and were never affected. Each release below is upstream plus that patch;
# refresh the version, archives, and hashes together when adopting a newer
# carried build.
$ProxyReleaseVersion = '0.1.35-airlock.3'
$ProxyReleaseAssets = [ordered]@{
  'AMD64' = @{ Archive = 'claude-code-proxy-windows-amd64.zip'; Sha256 = '7fe1aae0300fe9c2be2fe8868f31569c30cebee8a2f341433eda98c23feab18f' }
  'ARM64' = @{ Archive = 'claude-code-proxy-windows-arm64.zip'; Sha256 = '1575a35f79dedb2ee2cbbdc2fc2ee4875c6edd25c0818e176d23040c68fc39f3' }
}

function Get-ProxyVersionLine {
  param([Parameter(Mandatory)][string]$Path)
  try {
    return ((& $Path --version 2>$null) -join ' ').Trim()
  } catch {
    return ''
  }
}

function Install-CarriedProxy {
  param([Parameter(Mandatory)][string]$DestinationDir)
  $arch = "$env:PROCESSOR_ARCHITECTURE"
  if (-not $ProxyReleaseAssets.Contains($arch)) {
    throw "install: no carried claude-code-proxy release supports processor architecture '$arch'."
  }
  $asset = $ProxyReleaseAssets[$arch]
  if ($asset.Sha256 -notmatch '^[0-9a-fA-F]{64}$') {
    throw 'install: the carried claude-code-proxy release hash is not recorded; refusing to download an unverified binary.'
  }
  $tag = "v$ProxyReleaseVersion"
  $baseUrl = "https://github.com/Harshkamdar67/claude-code-proxy/releases/download/$tag"
  $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("airlock-proxy-" + [IO.Path]::GetRandomFileName())
  New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
  try {
    try {
      [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch { }
    Write-Host "Downloading claude-code-proxy $ProxyReleaseVersion ($arch)..."
    $archivePath = Join-Path $tempRoot $asset.Archive
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$($asset.Archive)" -OutFile $archivePath
    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLower()
    if ($actualHash -ne $asset.Sha256.ToLower()) {
      throw "install: the downloaded claude-code-proxy archive failed its SHA-256 check."
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $tempRoot
    Assert-PlainDirectory $DestinationDir
    $exeTarget = Join-Path $DestinationDir 'claude-code-proxy.exe'
    Copy-Item -LiteralPath (Join-Path $tempRoot 'claude-code-proxy.exe') -Destination $exeTarget -Force
    Write-Host "Installed claude-code-proxy $ProxyReleaseVersion to $exeTarget"
    return $exeTarget
  } finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Repair-CarriedProxy {
  param([Parameter(Mandatory)][string]$ExistingProxy)
  $item = Get-Item -LiteralPath $ExistingProxy -Force
  if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "install: refusing to replace an unsafe claude-code-proxy target: $ExistingProxy"
  }
  $directory = Split-Path -Parent $ExistingProxy
  # Name the backup after the build being replaced so two upgrades in a row
  # never overwrite each other's copy.
  $foundVersion = ((Get-ProxyVersionLine $ExistingProxy) -replace '^claude-code-proxy\s+', '') -replace '[^0-9A-Za-z.\-]', ''
  if (-not $foundVersion) { $foundVersion = 'previous' }
  $backup = Join-Path $directory ("claude-code-proxy.exe." + $foundVersion + ".bak")
  Copy-Item -LiteralPath $ExistingProxy -Destination $backup -Force
  # Windows refuses to overwrite a running executable. The launcher starts the
  # service again on the next launch when it is not healthy, so stop any
  # instance of this exact binary before replacing it.
  $running = @(Get-CimInstance Win32_Process -Filter "name='claude-code-proxy.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -ieq [IO.Path]::GetFullPath($ExistingProxy)) })
  foreach ($process in $running) {
    Write-Host "Stopping the running claude-code-proxy service (PID $($process.ProcessId)) so it can be replaced."
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  }
  if ($running.Count -gt 0) { Start-Sleep -Seconds 2 }
  $installed = Install-CarriedProxy -DestinationDir $directory
  if (-not $installed) { throw 'install: carried claude-code-proxy installation did not produce a binary.' }
  Write-Host "Previous binary kept at $backup"
}

$Proxy = Resolve-Application @('claude-code-proxy.exe', 'claude-code-proxy')
if ($env:AIRLOCK_PROXY_DOWNLOAD -eq 'off') {
  if (-not $Proxy) {
    throw 'install: claude-code-proxy is required and was not found on PATH.'
  }
} else {
  if ($Proxy) {
    $proxyVersionLine = Get-ProxyVersionLine $Proxy
    if ($proxyVersionLine -and $proxyVersionLine -notlike '*airlock*') {
      Write-Host ''
      Write-Host "The found claude-code-proxy is a stock build ($proxyVersionLine). Stock"
      Write-Host 'Windows builds open browser login through cmd start, which truncates'
      Write-Host 'OAuth URLs at the first ampersand; grok auth login then fails with'
      Write-Host '"Missing or invalid client_id". Codex login and serving still work.'
      Write-Host 'Replace it with Airlock''s fixed build by rerunning:'
      Write-Host ''
      Write-Host '  powershell -NoProfile -File .\scripts\install.ps1 -UpgradeProxy'
      Write-Host ''
      if ($UpgradeProxy) {
        Repair-CarriedProxy -ExistingProxy $Proxy
        $Proxy = Resolve-Application @('claude-code-proxy.exe', 'claude-code-proxy')
      }
    } elseif ($UpgradeProxy) {
      if ($proxyVersionLine -and $proxyVersionLine -notlike "*$ProxyReleaseVersion*") {
        Write-Host "The found claude-code-proxy is an older carried build ($proxyVersionLine)."
        Write-Host "Replacing it with $ProxyReleaseVersion."
        Repair-CarriedProxy -ExistingProxy $Proxy
        $Proxy = Resolve-Application @('claude-code-proxy.exe', 'claude-code-proxy')
      } else {
        Write-Host "The found claude-code-proxy is already the carried build $ProxyReleaseVersion; nothing to upgrade."
      }
    }
  } else {
    $Proxy = Install-CarriedProxy -DestinationDir $InstallDir
  }
}

& $Proxy codex auth status *> $null
if ($LASTEXITCODE -ne 0) {
  if ($Login) {
    & $Proxy codex auth login
    if ($LASTEXITCODE -ne 0) { throw 'install: Codex OAuth login did not complete.' }
  } else {
    Write-Host ''
    Write-Host 'Codex OAuth needs interactive approval. Run:'
    Write-Host ''
    Write-Host '  claude-code-proxy codex auth login'
    Write-Host ''
    Write-Host 'Then run this installer again. No token needs to be copied.'
    exit 2
  }
}

Assert-PlainDirectory $InstallDir
Assert-PlainDirectory $ConfigDir

$managedFiles = [ordered]@{
  'bin\airlock' = (Join-Path $InstallDir 'airlock')
  'bin\airlock.cmd' = (Join-Path $InstallDir 'airlock.cmd')
  'bin\airlock.ps1' = (Join-Path $InstallDir 'airlock.ps1')
  'bin\airlock-access.py' = (Join-Path $InstallDir 'airlock-access.py')
  'bin\airlock_policy.py' = (Join-Path $InstallDir 'airlock_policy.py')
  'bin\airlock_openmodel.py' = (Join-Path $InstallDir 'airlock_openmodel.py')
  'bin\airlock_openmodel_adapter.py' = (Join-Path $InstallDir 'airlock_openmodel_adapter.py')
  'bin\airlock_openrouter_auth.py' = (Join-Path $InstallDir 'airlock_openrouter_auth.py')
  'bin\airlock_openrouter_presets.py' = (Join-Path $InstallDir 'airlock_openrouter_presets.py')
  'bin\airlock_openrouter_models.py' = (Join-Path $InstallDir 'airlock_openrouter_models.py')
  'bin\airlock-update.py' = (Join-Path $InstallDir 'airlock-update.py')
  'bin\airlock-router.py' = (Join-Path $InstallDir 'airlock-router.py')
  'bin\airlock-hybrid.py' = (Join-Path $InstallDir 'airlock-hybrid.py')
  'bin\airlock_console.py' = (Join-Path $InstallDir 'airlock_console.py')
  'bin\airlock_console_tools.py' = (Join-Path $InstallDir 'airlock_console_tools.py')
  'bin\airlock_console_history.py' = (Join-Path $InstallDir 'airlock_console_history.py')
  'config\openai-direct-agents.json' = (Join-Path $ConfigDir 'openai-direct-agents.json')
  'config\anthropic-direct-agents.json' = (Join-Path $ConfigDir 'anthropic-direct-agents.json')
  'config\hybrid-agents.json' = (Join-Path $ConfigDir 'hybrid-agents.json')
  'config\claude-agents.json' = (Join-Path $ConfigDir 'claude-agents.json')
  'config\grok-agents.json' = (Join-Path $ConfigDir 'grok-agents.json')
  'plugins\airlock\.claude-plugin\plugin.json' = (Join-Path $PluginTarget '.claude-plugin\plugin.json')
  'plugins\airlock\hooks\hooks.json' = (Join-Path $PluginTarget 'hooks\hooks.json')
  'plugins\airlock\skills\usage\SKILL.md' = (Join-Path $PluginTarget 'skills\usage\SKILL.md')
  'plugins\airlock\skills\airlock-fast\SKILL.md' = (Join-Path $PluginTarget 'skills\airlock-fast\SKILL.md')
  'plugins\airlock\mcp-server\airlock_web_tools.py' = (Join-Path $PluginTarget 'mcp-server\airlock_web_tools.py')
  'plugins\airlock\mcp-server\airlock_console_mcp.py' = (Join-Path $PluginTarget 'mcp-server\airlock_console_mcp.py')
  'plugins\airlock\scripts\fast-session-end.sh' = (Join-Path $PluginTarget 'scripts\fast-session-end.sh')
  'plugins\airlock\scripts\fast-session-end.py' = (Join-Path $PluginTarget 'scripts\fast-session-end.py')
  'plugins\airlock\scripts\router-session-end.sh' = (Join-Path $PluginTarget 'scripts\router-session-end.sh')
  'plugins\airlock\scripts\router-session-end.py' = (Join-Path $PluginTarget 'scripts\router-session-end.py')
  'plugins\airlock\scripts\router-turn-notice.sh' = (Join-Path $PluginTarget 'scripts\router-turn-notice.sh')
  'plugins\airlock\scripts\router-turn-notice.py' = (Join-Path $PluginTarget 'scripts\router-turn-notice.py')
  'plugins\airlock\scripts\agent-guard.sh' = (Join-Path $PluginTarget 'scripts\agent-guard.sh')
  'plugins\airlock\scripts\agent-guard.py' = (Join-Path $PluginTarget 'scripts\agent-guard.py')
  'plugins\airlock\scripts\secret-guard.sh' = (Join-Path $PluginTarget 'scripts\secret-guard.sh')
  'plugins\airlock\scripts\secret-guard.py' = (Join-Path $PluginTarget 'scripts\secret-guard.py')
  'plugins\airlock\scripts\update-notice.sh' = (Join-Path $PluginTarget 'scripts\update-notice.sh')
  'plugins\airlock\scripts\update-notice.py' = (Join-Path $PluginTarget 'scripts\update-notice.py')
  'plugins\airlock\scripts\file_safety.py' = (Join-Path $PluginTarget 'scripts\file_safety.py')
  'plugins\airlock\scripts\worktree.py' = (Join-Path $PluginTarget 'scripts\worktree.py')
  'plugins\airlock\scripts\worktree-create.sh' = (Join-Path $PluginTarget 'scripts\worktree-create.sh')
  'plugins\airlock\scripts\worktree-remove.sh' = (Join-Path $PluginTarget 'scripts\worktree-remove.sh')
}

foreach ($entry in $managedFiles.GetEnumerator()) {
  Install-ManagedFile (Join-Path $RepoRoot $entry.Key) $entry.Value
}

Install-ConsoleSite

# Write the bundle marker last so interrupted installs fail closed as incomplete.
Install-ManagedFile (Join-Path $RepoRoot 'config\managed-bundle.json') $BundleTarget

if (-not (Test-Path -LiteralPath $ConfigTarget)) {
  [IO.File]::Copy((Join-Path $RepoRoot 'config\airlock.conf.example'), $ConfigTarget, $false)
} else {
  $configItem = Get-Item -LiteralPath $ConfigTarget -Force
  if ($configItem.PSIsContainer -or ($configItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "install: refusing unsafe config target: $ConfigTarget"
  }
  Write-Host "Preserving existing config: $ConfigTarget"
}

if ($WithAgent) {
  $effort = $env:AIRLOCK_SUBAGENT_EFFORT
  if (-not $effort -and (Test-Path -LiteralPath $ConfigTarget)) {
    foreach ($line in [IO.File]::ReadAllLines($ConfigTarget)) {
      if ($line -match '^AIRLOCK_SUBAGENT_EFFORT=(.*)$') { $effort = $Matches[1].Trim() }
    }
  }
  if (-not $effort) { $effort = 'inherit' }
  if ($effort -notin @('inherit', 'low', 'medium', 'high', 'xhigh', 'max')) {
    throw "install: unsupported sub-agent effort: $effort"
  }
  $template = [IO.File]::ReadAllText((Join-Path $RepoRoot 'examples\agents\airlock-worker.md'))
  # An agent with no effort of its own follows the session level, so /effort
  # moves it mid-session. A named level pins it instead.
  if ($effort -eq 'inherit') {
    $rendered = [Text.RegularExpressions.Regex]::Replace(
      $template, '(?m)^effort: .*\r?\n', ''
    )
  } else {
    $rendered = [Text.RegularExpressions.Regex]::Replace(
      $template, '(?m)^effort: .*$', "effort: $effort"
    )
  }
  Assert-PlainDirectory $AgentDir
  $temporary = Join-Path ([IO.Path]::GetTempPath()) ("airlock-agent-{0}.md" -f [Guid]::NewGuid().ToString('N'))
  try {
    [IO.File]::WriteAllText($temporary, $rendered, (New-Object Text.UTF8Encoding($false)))
    Install-ManagedFile $temporary (Join-Path $AgentDir 'airlock-worker.md')
  } finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
  }
}

Write-Host ''
Write-Host 'Airlock installed.'
Write-Host "  Launcher: $(Join-Path $InstallDir 'airlock.cmd')"
Write-Host "  Config:   $ConfigTarget"
Write-Host "  Bundle:   $BundleTarget"
Write-Host "  Plugin:   $PluginTarget"
if ($WithAgent) { Write-Host "  Agent:    $(Join-Path $AgentDir 'airlock-worker.md')" }
Write-Host ''
Write-Host "Run: powershell -NoProfile -File `"$(Join-Path $PSScriptRoot 'doctor.ps1')`""
if (($env:PATH -split ';') -notcontains $InstallDir) {
  Write-Host "Add $InstallDir to your user PATH before running airlock from a new terminal."
}
