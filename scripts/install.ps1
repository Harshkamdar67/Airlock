# Managed by https://github.com/Harshkamdar67/Airlock
[CmdletBinding()]
param(
  [switch]$WithAgent,
  [switch]$Login
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

$Git = Resolve-Application @('git.exe', 'git')
$Bash = Resolve-Application @('bash.exe', 'bash')
$Claude = Resolve-Application @('claude.exe', 'claude.cmd', 'claude')
$Proxy = Resolve-Application @('claude-code-proxy.exe', 'claude-code-proxy')
$Python = Resolve-Python3

foreach ($requirement in @(
  @('Git', $Git), @('Git Bash', $Bash), @('Claude Code', $Claude),
  @('claude-code-proxy', $Proxy), @('Python 3', $Python)
)) {
  if (-not $requirement[1]) {
    throw "install: $($requirement[0]) is required and was not found on PATH."
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
  'bin\airlock_openrouter_auth.py' = (Join-Path $InstallDir 'airlock_openrouter_auth.py')
  'bin\airlock_openrouter_presets.py' = (Join-Path $InstallDir 'airlock_openrouter_presets.py')
  'bin\airlock_openrouter_models.py' = (Join-Path $InstallDir 'airlock_openrouter_models.py')
  'bin\airlock-update.py' = (Join-Path $InstallDir 'airlock-update.py')
  'bin\airlock-router.py' = (Join-Path $InstallDir 'airlock-router.py')
  'bin\airlock-hybrid.py' = (Join-Path $InstallDir 'airlock-hybrid.py')
  'config\openai-direct-agents.json' = (Join-Path $ConfigDir 'openai-direct-agents.json')
  'config\anthropic-direct-agents.json' = (Join-Path $ConfigDir 'anthropic-direct-agents.json')
  'config\hybrid-agents.json' = (Join-Path $ConfigDir 'hybrid-agents.json')
  'config\claude-agents.json' = (Join-Path $ConfigDir 'claude-agents.json')
  'config\grok-agents.json' = (Join-Path $ConfigDir 'grok-agents.json')
  'plugins\airlock\.claude-plugin\plugin.json' = (Join-Path $PluginTarget '.claude-plugin\plugin.json')
  'plugins\airlock\hooks\hooks.json' = (Join-Path $PluginTarget 'hooks\hooks.json')
  'plugins\airlock\skills\usage\SKILL.md' = (Join-Path $PluginTarget 'skills\usage\SKILL.md')
  'plugins\airlock\skills\airlock-fast\SKILL.md' = (Join-Path $PluginTarget 'skills\airlock-fast\SKILL.md')
  'plugins\airlock\scripts\fast-session-end.sh' = (Join-Path $PluginTarget 'scripts\fast-session-end.sh')
  'plugins\airlock\scripts\fast-session-end.py' = (Join-Path $PluginTarget 'scripts\fast-session-end.py')
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
  # Keep this last so an interrupted install retains a stale or missing marker.
  'config\managed-bundle.json' = $BundleTarget
}

foreach ($entry in $managedFiles.GetEnumerator()) {
  Install-ManagedFile (Join-Path $RepoRoot $entry.Key) $entry.Value
}

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
