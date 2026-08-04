# Managed by https://github.com/Harshkamdar67/Airlock
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$Failures = 0
$ClaudeSignedIn = $false

function Pass([string]$Message) { Write-Host "PASS  $Message" }
function Fail([string]$Message) { Write-Host "FAIL  $Message"; $script:Failures++ }
function Info([string]$Message) { Write-Host "INFO  $Message" }

function Resolve-Application {
  param([Parameter(Mandatory)][string[]]$Names)
  foreach ($name in $Names) {
    $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if ($command) { return [string]$command.Source }
  }
  return $null
}

$Claude = Resolve-Application @('claude.exe', 'claude.cmd', 'claude')
$Proxy = Resolve-Application @('claude-code-proxy.exe', 'claude-code-proxy')
$Bash = Resolve-Application @('bash.exe', 'bash')
$Launcher = Resolve-Application @('airlock.cmd', 'airlock.exe', 'airlock')

if ($Claude) {
  $version = (& $Claude --version 2>$null | Select-Object -First 1)
  Pass "Claude Code: $version"
  & $Claude auth status *> $null
  if ($LASTEXITCODE -eq 0) {
    Pass 'Claude login is configured'
    $ClaudeSignedIn = $true
  } else {
    Info 'Claude login was not detected; run: claude auth login'
    Info 'OpenAI-only sessions can still work, but hybrid and Claude routes need this login.'
  }
} else { Fail 'Claude Code is not on PATH' }

if ($Proxy) {
  $version = (& $Proxy --version 2>$null | Select-Object -First 1)
  Pass "Proxy: $version"
} else { Fail 'claude-code-proxy is not on PATH' }

if ($Bash) { Pass "Git Bash: $Bash" } else { Fail 'Git for Windows Bash is not on PATH' }

if ($Proxy) {
  if ($Launcher) {
    & $Launcher proxy auth status *> $null
  } else {
    & $Proxy codex auth status *> $null
  }
  if ($LASTEXITCODE -eq 0) {
    Pass 'Codex OAuth is configured'
  } else {
    Fail 'Codex OAuth is missing or expired'
    if ($Launcher) { Info 'Run: airlock proxy auth login' } else { Info 'Run: claude-code-proxy codex auth login' }
  }
}

$ProxyUrl = if ($env:AIRLOCK_PROXY_URL) { $env:AIRLOCK_PROXY_URL } else { 'http://127.0.0.1:18765' }
try {
  Invoke-WebRequest -Uri "$ProxyUrl/healthz" -TimeoutSec 2 -UseBasicParsing | Out-Null
  Pass "Proxy health: $ProxyUrl/healthz"
} catch {
  Fail "Proxy health: $ProxyUrl/healthz"
}

if ($Launcher) {
  Pass "Launcher: $Launcher"
  $configOutput = & $Launcher config 2>$null
  foreach ($line in $configOutput) { Info $line }
  & $Launcher bundle *> $null
  if ($LASTEXITCODE -eq 0) {
    Pass 'Managed bundle is current and complete'
  } else {
    Fail 'Managed bundle is stale or incomplete; reinstall and start a fresh session'
  }
} else {
  Fail 'airlock is not on PATH'
}

$InstallDir = if ($env:AIRLOCK_INSTALL_DIR) { $env:AIRLOCK_INSTALL_DIR } else { Join-Path $HOME '.local\bin' }
$Router = if ($env:AIRLOCK_ROUTER_HELPER) { $env:AIRLOCK_ROUTER_HELPER } else { Join-Path $InstallDir 'airlock-router.py' }
if (Test-Path -LiteralPath $Router -PathType Leaf) {
  $routerItem = Get-Item -LiteralPath $Router -Force
  if ($routerItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Fail "Hybrid router is unsafe: $Router"
  } else {
    Pass "Hybrid router: $Router (session-scoped loopback)"
  }
} else {
  Fail "Hybrid router is missing: $Router"
}
$ConfigDir = if ($env:AIRLOCK_CONFIG_DIR) { $env:AIRLOCK_CONFIG_DIR } else { Join-Path $HOME '.config\airlock' }
$PluginDir = if ($env:AIRLOCK_PLUGIN_DIR) { $env:AIRLOCK_PLUGIN_DIR } else { Join-Path $ConfigDir 'plugins\airlock' }
$pluginFiles = @(
  '.claude-plugin\plugin.json', 'hooks\hooks.json',
  'scripts\agent-guard.py', 'scripts\secret-guard.py',
  'scripts\agent-guard.sh', 'scripts\secret-guard.sh',
  'scripts\file_safety.py', 'scripts\worktree.py',
  'scripts\worktree-create.sh', 'scripts\worktree-remove.sh'
)
$pluginSafe = Test-Path -LiteralPath $PluginDir -PathType Container
if ($pluginSafe) {
  $item = Get-Item -LiteralPath $PluginDir -Force
  $pluginSafe = -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}
foreach ($relative in $pluginFiles) {
  if (-not (Test-Path -LiteralPath (Join-Path $PluginDir $relative) -PathType Leaf)) {
    $pluginSafe = $false
  }
}
if ($pluginSafe) { Pass "Session plugin: $PluginDir" } else { Fail "Session plugin is missing or unsafe: $PluginDir" }

$AgentDir = if ($env:AIRLOCK_AGENT_DIR) { $env:AIRLOCK_AGENT_DIR } else { Join-Path $HOME '.claude\agents' }
$AgentFile = Join-Path $AgentDir 'airlock-worker.md'
if (Test-Path -LiteralPath $AgentFile -PathType Leaf) {
  $effort = Select-String -LiteralPath $AgentFile -Pattern '^effort: (.+)$' |
    Select-Object -First 1
  $label = if ($effort) { $effort.Matches[0].Groups[1].Value } else { 'inherits the session level' }
  Pass "Optional worker effort: $label"
} else {
  Info 'Optional airlock-worker is not installed'
}

if ($Failures -gt 0) {
  Write-Host ''
  Write-Error "Doctor found $Failures problem(s)."
  exit 1
}

Write-Host ''
if ($ClaudeSignedIn) {
  Write-Host 'Airlock is ready. No live model request was made.'
} else {
  Write-Host 'Airlock OpenAI-only sessions are ready. Sign in to Claude before using hybrid or Claude routes. No live model request was made.'
}
