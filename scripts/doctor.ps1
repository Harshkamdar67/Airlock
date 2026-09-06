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

function Resolve-Python3 {
  $names = if ($env:AIRLOCK_PYTHON) {
    @($env:AIRLOCK_PYTHON)
  } else {
    @('python3.exe', 'python3', 'python.exe', 'python')
  }
  foreach ($name in $names) {
    $candidate = Resolve-Application @($name)
    if (-not $candidate) { continue }
    & $candidate -c 'import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)' *> $null
    if ($LASTEXITCODE -eq 0) { return $candidate }
  }
  return $null
}

function Test-AirlockConsoleEndpoint {
  param([Parameter(Mandatory)][string]$Url)
  $response = $null
  $reader = $null
  try {
    $request = [Net.HttpWebRequest]::Create("$Url/healthz")
    $request.Method = 'GET'
    $request.Timeout = 500
    $request.ReadWriteTimeout = 500
    $request.Proxy = $null
    $response = [Net.HttpWebResponse]$request.GetResponse()
    if ([int]$response.StatusCode -ne 200) { return $false }
    $server = [string]$response.Headers['Server']
    if (-not $server.StartsWith('AirlockConsole')) { return $false }
    $reader = New-Object IO.StreamReader($response.GetResponseStream())
    $body = $reader.ReadToEnd()
    if ($body.Length -gt 4096) { return $false }
    $payload = $body | ConvertFrom-Json -ErrorAction Stop
    return $payload.ok -eq $true
  } catch {
    return $false
  } finally {
    if ($reader) { $reader.Dispose() }
    if ($response) { $response.Dispose() }
  }
}

$Claude = Resolve-Application @('claude.exe', 'claude.cmd', 'claude')
$Proxy = Resolve-Application @('claude-code-proxy.exe', 'claude-code-proxy')
$Bash = Resolve-Application @('bash.exe', 'bash')
$Launcher = Resolve-Application @('airlock.cmd', 'airlock.exe', 'airlock')
$Python = Resolve-Python3

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

# Grok is optional, so a missing Grok login is only a failure once the saved
# configuration actually enables Grok routes.
$ConfigTarget = if ($env:AIRLOCK_CONFIG_FILE) {
  $env:AIRLOCK_CONFIG_FILE
} else {
  Join-Path $HOME '.config\airlock\config'
}
$ConfigGrokModels = ''
$ConfigDefaultProfile = ''
if (Test-Path -LiteralPath $ConfigTarget) {
  foreach ($line in Get-Content -LiteralPath $ConfigTarget) {
    $pair = $line -split '=', 2
    if ($pair.Count -ne 2) { continue }
    switch ($pair[0].Trim()) {
      'AIRLOCK_GROK_MODELS' { $ConfigGrokModels = $pair[1].Trim() }
      'AIRLOCK_DEFAULT_PROFILE' { $ConfigDefaultProfile = $pair[1].Trim() }
    }
  }
}
$GrokModels = if ($env:AIRLOCK_GROK_MODELS) { $env:AIRLOCK_GROK_MODELS } else { $ConfigGrokModels }
$DefaultProfile = if ($env:AIRLOCK_DEFAULT_PROFILE) { $env:AIRLOCK_DEFAULT_PROFILE } else { $ConfigDefaultProfile }
if ($GrokModels -or $DefaultProfile -eq 'grok') {
  if ($Proxy) {
    if ($Launcher) {
      & $Launcher proxy grok auth status *> $null
    } else {
      & $Proxy grok auth status *> $null
    }
    if ($LASTEXITCODE -eq 0) {
      Pass 'Grok OAuth is configured'
      # The access token is short lived, but the proxy renews it from the stored
      # refresh token about five minutes before expiry. This is informational,
      # so do not advise a re-login just because the number looks small.
      $grokStatus = (& $Proxy grok auth status 2>$null | Out-String)
      if ($grokStatus -match 'Expires in (\d+)s') {
        $seconds = [int]$Matches[1]
        Info "Grok access token expires in $([int]($seconds / 3600))h $([int](($seconds % 3600) / 60))m; the proxy renews it automatically"
      }
    } else {
      Fail 'Grok OAuth is missing or expired'
      if ($Launcher) { Info 'Run: airlock proxy grok auth login' } else { Info 'Run: claude-code-proxy grok auth login' }
    }
  } else {
    Fail 'Grok routes are enabled but claude-code-proxy is not on PATH'
  }
} else {
  Info 'Grok workers are not enabled in the saved configuration'
}

# Catalog discovery is informational only: newer gateway IDs are surfaced so a
# person can declare them in models.json; nothing is ever enabled implicitly.
if ($Proxy -and ($GrokModels -or $DefaultProfile -eq 'grok') -and $Python) {
  $ModelsFile = if ($env:AIRLOCK_MODELS_FILE) {
    $env:AIRLOCK_MODELS_FILE
  } else {
    Join-Path (Split-Path -Parent $ConfigTarget) 'models.json'
  }
  $DiscoveryCode = @'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
models = data.get("models") if isinstance(data, dict) else None
if isinstance(models, list):
    for entry in models:
        try:
            if entry.get("provider") == "grok" and entry.get("enabled") is True:
                print(entry["id"])
        except Exception:
            pass
'@
  $DeclaredGrok = @()
  if (Test-Path -LiteralPath $ModelsFile -PathType Leaf) {
    try {
      foreach ($item in ($DiscoveryCode | & $Python - $ModelsFile 2>$null)) {
        if ($item) { $DeclaredGrok += [string]$item }
      }
    } catch { $DeclaredGrok = @() }
  }
  $EnabledGrok = @('grok-4.6', 'grok-composer-2.5-fast') + $DeclaredGrok
  foreach ($catalogLine in (& $Proxy models 2>$null)) {
    $text = [string]$catalogLine
    if (-not $text.StartsWith('grok:')) { continue }
    # The proxy prints the catalog comma separated, so split on commas as well
    # as spaces; otherwise every entry but the last keeps a trailing comma and
    # looks unrecognized.
    foreach ($catalogId in ($text.Substring(5) -split '[,\s]+')) {
      if ($catalogId -and $EnabledGrok -notcontains $catalogId) {
        Info "Available but not enabled (Grok): $catalogId; declare it in models.json to use it"
      }
    }
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
$ConfigDir = if ($env:AIRLOCK_CONFIG_DIR) { $env:AIRLOCK_CONFIG_DIR } else { Join-Path $HOME '.config\airlock' }
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
$Updater = if ($env:AIRLOCK_UPDATE_HELPER) { $env:AIRLOCK_UPDATE_HELPER } else { Join-Path $InstallDir 'airlock-update.py' }
if (Test-Path -LiteralPath $Updater -PathType Leaf) {
  $updaterItem = Get-Item -LiteralPath $Updater -Force
  if ($updaterItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Fail "Release updater is unsafe: $Updater"
  } else {
    Pass "Release updater: $Updater (manual checks only)"
  }
} else {
  Fail "Release updater is missing: $Updater"
}

$PolicyHelper = if ($env:AIRLOCK_POLICY_HELPER) { $env:AIRLOCK_POLICY_HELPER } else { Join-Path $InstallDir 'airlock_policy.py' }
$OpenModelHelper = if ($env:AIRLOCK_OPENMODEL_HELPER) { $env:AIRLOCK_OPENMODEL_HELPER } else { Join-Path $InstallDir 'airlock_openmodel.py' }
$OpenModelAdapterHelper = if ($env:AIRLOCK_OPENMODEL_ADAPTER_HELPER) { $env:AIRLOCK_OPENMODEL_ADAPTER_HELPER } else { Join-Path $InstallDir 'airlock_openmodel_adapter.py' }
$OpenModelRegistry = if ($env:AIRLOCK_OPENMODEL_REGISTRY_FILE) { $env:AIRLOCK_OPENMODEL_REGISTRY_FILE } else { Join-Path $ConfigDir 'openmodel-registry.json' }
$OpenRouterAuthHelper = if ($env:AIRLOCK_OPENROUTER_AUTH_HELPER) { $env:AIRLOCK_OPENROUTER_AUTH_HELPER } else { Join-Path $InstallDir 'airlock_openrouter_auth.py' }
$OpenRouterPresetsHelper = if ($env:AIRLOCK_OPENROUTER_PRESETS_HELPER) { $env:AIRLOCK_OPENROUTER_PRESETS_HELPER } else { Join-Path $InstallDir 'airlock_openrouter_presets.py' }
$OpenRouterModelsHelper = if ($env:AIRLOCK_OPENROUTER_MODELS_HELPER) { $env:AIRLOCK_OPENROUTER_MODELS_HELPER } else { Join-Path $InstallDir 'airlock_openrouter_models.py' }
$OpenRouterRegistry = if ($env:AIRLOCK_OPENROUTER_REGISTRY_FILE) { $env:AIRLOCK_OPENROUTER_REGISTRY_FILE } else { Join-Path $ConfigDir 'openrouter-registry.json' }
$OpenRouterHelpersSafe = $true
foreach ($helper in @($PolicyHelper, $OpenRouterAuthHelper, $OpenRouterPresetsHelper, $OpenRouterModelsHelper)) {
  if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
    Fail "OpenRouter helper is missing or unsafe: $helper"
    $OpenRouterHelpersSafe = $false
    continue
  }
  $helperItem = Get-Item -LiteralPath $helper -Force
  if ($helperItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Fail "OpenRouter helper is missing or unsafe: $helper"
    $OpenRouterHelpersSafe = $false
  }
}
if (-not $Python) {
  Fail 'Python 3 is required for OpenRouter checks'
  $OpenRouterHelpersSafe = $false
}

$OpenRouterRegistryState = 'unknown'
$OpenRouterRegistryCount = 0
if ($OpenRouterHelpersSafe) {
  $registryOutput = @(& $Python $OpenRouterModelsHelper --registry $OpenRouterRegistry _doctor-status 2>&1)
  $registryExitCode = $LASTEXITCODE
  foreach ($lineValue in $registryOutput) {
    $line = [string]$lineValue
    if ($line -match '^STATE=(.*)$') { $OpenRouterRegistryState = $Matches[1]; continue }
    if ($line -match '^COUNT=([0-9]+)$') { $OpenRouterRegistryCount = [int]$Matches[1]; continue }
    if ($line -match '^MODEL=(.*)$') {
      $parts = $Matches[1] -split "`t", 4
      if ($parts.Count -eq 4) {
        Info "OpenRouter route: airlock-or-$($parts[0]) -> $($parts[1]) via $($parts[2]) ($($parts[3]))"
      }
      continue
    }
    if ($line -match '^DETAIL=(.*)$') { Info "OpenRouter registry detail: $($Matches[1])" }
  }
  switch ($OpenRouterRegistryState) {
    'absent' { Info "OpenRouter registry is not configured: $OpenRouterRegistry" }
    'valid' { Pass "OpenRouter registry is valid and fresh ($OpenRouterRegistryCount model(s)): $OpenRouterRegistry" }
    'stale' { Fail 'OpenRouter registry metadata is stale; refresh it before starting a new session' }
    'invalid' { Fail "OpenRouter registry is invalid: $OpenRouterRegistry" }
    default {
      Fail "OpenRouter registry status could not be determined: $OpenRouterRegistry"
      if ($registryExitCode -ne 0) { Info 'The registry helper returned an error.' }
    }
  }

  $backendOutput = @(& $Python $OpenRouterAuthHelper _backend-status 2>&1)
  $backendExitCode = $LASTEXITCODE
  $OpenRouterBackend = 'unknown'
  $OpenRouterBackendState = 'unknown'
  foreach ($lineValue in $backendOutput) {
    $line = [string]$lineValue
    if ($line -match '^BACKEND=(.*)$') { $OpenRouterBackend = $Matches[1]; continue }
    if ($line -match '^STATE=(.*)$') { $OpenRouterBackendState = $Matches[1] }
  }
  if ($OpenRouterBackendState -eq 'available') {
    Pass "OpenRouter credential backend is available: $OpenRouterBackend"
  } elseif ($OpenRouterRegistryCount -gt 0) {
    Fail "OpenRouter credential backend is unavailable: $OpenRouterBackend"
  } else {
    Info "OpenRouter credential backend is unavailable: $OpenRouterBackend"
  }
  Info 'Doctor does not read the OpenRouter credential; run airlock openrouter auth status to check it.'
  if ($backendExitCode -ne 0 -and $OpenRouterBackendState -ne 'unavailable') {
    Fail 'OpenRouter credential backend status could not be determined'
  }
}

$OpenModelHelpersSafe = $true
foreach ($helper in @($PolicyHelper, $OpenModelHelper, $OpenModelAdapterHelper)) {
  if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
    Fail "Open-model helper is missing or unsafe: $helper"
    $OpenModelHelpersSafe = $false
    continue
  }
  $helperItem = Get-Item -LiteralPath $helper -Force
  if ($helperItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Fail "Open-model helper is missing or unsafe: $helper"
    $OpenModelHelpersSafe = $false
  }
}
if (-not $Python) {
  Fail 'Python 3 is required for open-model checks'
  $OpenModelHelpersSafe = $false
}

$OpenModelRegistryState = 'unknown'
$OpenModelEndpointCount = 0
$OpenModelActiveEndpointCount = 0
$OpenModelRouteCount = 0
$OpenModelActiveRouteCount = 0
if ($OpenModelHelpersSafe) {
  $openModelOutput = @(& $Python $OpenModelHelper --registry $OpenModelRegistry _doctor-status 2>&1)
  $openModelExitCode = $LASTEXITCODE
  foreach ($lineValue in $openModelOutput) {
    $line = [string]$lineValue
    if ($line -match '^STATE=(.*)$') { $OpenModelRegistryState = $Matches[1]; continue }
    if ($line -match '^ENDPOINT_COUNT=([0-9]+)$') { $OpenModelEndpointCount = [int]$Matches[1]; continue }
    if ($line -match '^ACTIVE_ENDPOINT_COUNT=([0-9]+)$') { $OpenModelActiveEndpointCount = [int]$Matches[1]; continue }
    if ($line -match '^ROUTE_COUNT=([0-9]+)$') { $OpenModelRouteCount = [int]$Matches[1]; continue }
    if ($line -match '^ACTIVE_ROUTE_COUNT=([0-9]+)$') { $OpenModelActiveRouteCount = [int]$Matches[1]; continue }
    if ($line -match '^ENDPOINT=(.*)$') {
      $parts = $Matches[1] -split "`t", 3
      if ($parts.Count -eq 3) {
        Info "Open-model endpoint: $($parts[0]) ($($parts[1]), $($parts[2]))"
      }
      continue
    }
    if ($line -match '^ROUTE=(.*)$') {
      $parts = $Matches[1] -split "`t", 3
      if ($parts.Count -eq 3) {
        Info "Open-model route: airlock-om-$($parts[0]) ($($parts[1]), $($parts[2]))"
      }
      continue
    }
    if ($line -match '^DETAIL=(.*)$') { Info "Open-model registry detail: $($Matches[1])" }
  }
  switch ($OpenModelRegistryState) {
    'absent' { Info "Open-model registry is not configured: $OpenModelRegistry" }
    'valid' {
      Pass "Open-model registry is valid ($OpenModelActiveEndpointCount/$OpenModelEndpointCount endpoint(s) active, $OpenModelActiveRouteCount/$OpenModelRouteCount route(s) active): $OpenModelRegistry"
    }
    'invalid' { Fail "Open-model registry is invalid: $OpenModelRegistry" }
    default {
      Fail "Open-model registry status could not be determined: $OpenModelRegistry"
      if ($openModelExitCode -ne 0) { Info 'The open-model registry helper returned an error.' }
    }
  }
  Info 'Doctor did not contact a local inference server; run airlock open-model check ROUTE explicitly.'
}

$PluginDir = if ($env:AIRLOCK_PLUGIN_DIR) { $env:AIRLOCK_PLUGIN_DIR } else { Join-Path $ConfigDir 'plugins\airlock' }
$ConsoleBinDir = if ($Launcher) { Split-Path -Parent $Launcher } else { $InstallDir }
$ConsoleHelper = if ($env:AIRLOCK_CONSOLE_HELPER) { $env:AIRLOCK_CONSOLE_HELPER } else { Join-Path $ConsoleBinDir 'airlock_console.py' }
$ConsoleToolsHelper = Join-Path $ConsoleBinDir 'airlock_console_tools.py'
$ConsoleMcpHelper = Join-Path $PluginDir 'mcp-server\airlock_console_mcp.py'
$ConsoleSite = Join-Path $ConsoleBinDir 'share\airlock\console'
foreach ($consoleCheck in @(
  @('Console helper', $ConsoleHelper),
  @('Console tools helper', $ConsoleToolsHelper),
  @('Console MCP wrapper', $ConsoleMcpHelper)
)) {
  $consolePath = [string]$consoleCheck[1]
  $consoleSafe = Test-Path -LiteralPath $consolePath -PathType Leaf
  if ($consoleSafe) {
    $consoleItem = Get-Item -LiteralPath $consolePath -Force
    $consoleSafe = -not ($consoleItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
  }
  if ($consoleSafe) {
    Pass "Airlock $($consoleCheck[0]): $consolePath"
  } else {
    Info "Airlock $($consoleCheck[0]) is not installed or is unsafe: $consolePath"
  }
}
$ConsoleSiteSafe = Test-Path -LiteralPath $ConsoleSite -PathType Container
if ($ConsoleSiteSafe) {
  $consoleSiteItem = Get-Item -LiteralPath $ConsoleSite -Force
  $ConsoleSiteSafe = -not ($consoleSiteItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
}
if ($ConsoleSiteSafe) {
  Pass "Airlock Console site: $ConsoleSite"
} else {
  Info "Airlock Console site is not installed or is unsafe: $ConsoleSite"
}
$ConsoleUrl = 'http://127.0.0.1:4783'
if (Test-AirlockConsoleEndpoint -Url $ConsoleUrl) {
  Pass "Airlock Console health: $ConsoleUrl/healthz"
} else {
  Info 'Airlock Console is not running at 127.0.0.1:4783'
}

$pluginFiles = @(
  '.claude-plugin\plugin.json', 'hooks\hooks.json',
  'skills\airlock-fast\SKILL.md',
  'scripts\fast-session-end.sh', 'scripts\fast-session-end.py',
  'scripts\agent-guard.py', 'scripts\secret-guard.py',
  'scripts\agent-guard.sh', 'scripts\secret-guard.sh',
  'scripts\update-notice.sh', 'scripts\update-notice.py',
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
