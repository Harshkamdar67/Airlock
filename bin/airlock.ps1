# Managed by https://github.com/Harshkamdar67/Airlock (Windows PowerShell port)
# Launches provider-scoped Claude Code sessions through native Anthropic or local OpenAI access.
$Arguments = @($args)

$ErrorActionPreference = 'Stop'

$ProxyUrl   = if ($env:AIRLOCK_PROXY_URL)   { $env:AIRLOCK_PROXY_URL }   else { 'http://127.0.0.1:18765' }
$MainEffort = if ($env:AIRLOCK_MAIN_EFFORT) { $env:AIRLOCK_MAIN_EFFORT } else { 'high' }
$BgEffort   = if ($env:AIRLOCK_BG_EFFORT)   { $env:AIRLOCK_BG_EFFORT }   else { 'medium' }
$SmallFast  = if ($env:AIRLOCK_SMALL_FAST_MODEL) { $env:AIRLOCK_SMALL_FAST_MODEL } else { 'gpt-5.6-sol[1m]' }
$ContextWin = if ($env:AIRLOCK_CONTEXT_WINDOW)   { $env:AIRLOCK_CONTEXT_WINDOW }   else { '272000' }
$ConfigFile = if ($env:AIRLOCK_CONFIG_FILE) { $env:AIRLOCK_CONFIG_FILE } else { Join-Path $HOME '.config\airlock\config' }
$ConfigValues = @{}
if (Test-Path -LiteralPath $ConfigFile -PathType Leaf) {
  foreach ($line in [IO.File]::ReadAllLines($ConfigFile)) {
    if ($line -match '^(AIRLOCK_[A-Z0-9_]+)=(.*)$') { $ConfigValues[$Matches[1]] = $Matches[2].TrimEnd("`r") }
  }
}
$MaxAgents = if ($env:AIRLOCK_MAX_CONCURRENT_SUBAGENTS) { $env:AIRLOCK_MAX_CONCURRENT_SUBAGENTS } elseif ($ConfigValues.ContainsKey('AIRLOCK_MAX_CONCURRENT_SUBAGENTS')) { $ConfigValues['AIRLOCK_MAX_CONCURRENT_SUBAGENTS'] } else { 'off' }
$GptEffortCapabilities = if ($env:AIRLOCK_GPT_EFFORT_CAPABILITIES) { $env:AIRLOCK_GPT_EFFORT_CAPABILITIES } elseif ($ConfigValues.ContainsKey('AIRLOCK_GPT_EFFORT_CAPABILITIES')) { $ConfigValues['AIRLOCK_GPT_EFFORT_CAPABILITIES'] } else { 'effort,xhigh_effort,max_effort' }
# The hybrid launcher rebuilds these declarations itself, so hand it the
# resolved value rather than letting it fall back to the built-in default.
$env:AIRLOCK_GPT_EFFORT_CAPABILITIES = $GptEffortCapabilities
$PluginDir = if ($env:AIRLOCK_PLUGIN_DIR) { $env:AIRLOCK_PLUGIN_DIR } else { Join-Path $HOME '.config\airlock\plugins\airlock' }
$OpenAIDirectAgentsFile = if ($env:AIRLOCK_OPENAI_DIRECT_AGENTS_FILE) { $env:AIRLOCK_OPENAI_DIRECT_AGENTS_FILE } else { Join-Path $HOME '.config\airlock\openai-direct-agents.json' }
$AnthropicDirectAgentsFile = if ($env:AIRLOCK_ANTHROPIC_DIRECT_AGENTS_FILE) { $env:AIRLOCK_ANTHROPIC_DIRECT_AGENTS_FILE } else { Join-Path $HOME '.config\airlock\anthropic-direct-agents.json' }
$OpenAIWrapperAgentsFile = if ($env:AIRLOCK_HYBRID_AGENTS_FILE) { $env:AIRLOCK_HYBRID_AGENTS_FILE } else { Join-Path $HOME '.config\airlock\hybrid-agents.json' }
$AnthropicWrapperAgentsFile = if ($env:AIRLOCK_CLAUDE_AGENTS_FILE) { $env:AIRLOCK_CLAUDE_AGENTS_FILE } else { Join-Path $HOME '.config\airlock\claude-agents.json' }
$AccessHelper = if ($env:AIRLOCK_ACCESS_HELPER) { $env:AIRLOCK_ACCESS_HELPER } else { Join-Path $PSScriptRoot 'airlock-access.py' }
$RouterHelper = if ($env:AIRLOCK_ROUTER_HELPER) { $env:AIRLOCK_ROUTER_HELPER } else { Join-Path $PSScriptRoot 'airlock-router.py' }
$ManagedBinDir = if ($env:AIRLOCK_MANAGED_BIN_DIR) { $env:AIRLOCK_MANAGED_BIN_DIR } else { $PSScriptRoot }
$ManagedBundleFile = if ($env:AIRLOCK_MANAGED_BUNDLE_FILE) { $env:AIRLOCK_MANAGED_BUNDLE_FILE } else { Join-Path $HOME '.config\airlock\managed-bundle.json' }

$Models = @{
  'sol'      = @('gpt-5.6-sol[1m]',        'GPT-5.6 Sol')
  'sol-fast' = @('gpt-5.6-sol-fast[1m]',   'GPT-5.6 Sol Fast')
  'terra'    = @('gpt-5.6-terra[1m]',      'GPT-5.6 Terra')
  'luna'     = @('gpt-5.6-luna[1m]',       'GPT-5.6 Luna')
  '5.5'      = @('gpt-5.5[1m]',            'GPT-5.5')
  '5.4'      = @('gpt-5.4[1m]',            'GPT-5.4')
  'mini'     = @('gpt-5.4-mini[1m]',       'GPT-5.4 Mini')
  '5.3'      = @('gpt-5.3-codex[1m]',      'GPT-5.3 Codex')
  'spark'    = @('gpt-5.3-codex-spark',    'GPT-5.3 Codex Spark')
  '5.2'      = @('gpt-5.2[1m]',            'GPT-5.2')
}

$HybridRoots = @{
  'sol'    = @('gpt-5.6-sol[1m]', 'GPT-5.6 Sol', 'openai')
  'terra'  = @('gpt-5.6-terra[1m]', 'GPT-5.6 Terra', 'openai')
  'luna'   = @('gpt-5.6-luna[1m]', 'GPT-5.6 Luna', 'openai')
  'opus'   = @('claude-opus-5', 'Claude Opus 5', 'anthropic')
  'sonnet' = @('claude-sonnet-5', 'Claude Sonnet 5', 'anthropic')
  'fable'  = @('claude-fable-5', 'Claude Fable 5', 'anthropic')
  'haiku'  = @('claude-haiku-4-5-20251001', 'Claude Haiku 4.5', 'anthropic')
}

$ProxyVariables = @(
  'ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_MODEL',
  'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL',
  'ANTHROPIC_CUSTOM_MODEL_OPTION', 'ANTHROPIC_CUSTOM_MODEL_OPTION_NAME',
  'ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES',
  'ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES',
  'ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES',
  'CLAUDE_CODE_AUTO_COMPACT_WINDOW', 'CLAUDE_CODE_ALWAYS_ENABLE_EFFORT',
  'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
  'CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK', 'CLAUDE_CODE_SUBAGENT_MODEL'
)

function Show-Models {
  Write-Host 'Usage: airlock [model|bg] [claude arguments]'
  Write-Host '       airlock hybrid [sol|terra|luna|opus|sonnet|fable] [claude arguments]'
  Write-Host ''
  Write-Host 'Products:'
  Write-Host '  airlock          OpenAI root with OpenAI Sol/Terra/Luna workers only'
  Write-Host '  claude       Native Anthropic CLI, untouched by this launcher'
  Write-Host '  airlock hybrid   Choose an OpenAI or Anthropic root with all five mixed workers'
  Write-Host ''
  Write-Host 'OpenAI root aliases: sol (default), sol-fast, terra, luna, 5.5, 5.4, mini, 5.3, spark, 5.2'
  Write-Host 'Hybrid root aliases/menu: sol, terra, luna, opus, sonnet, fable'
  Write-Host 'Additional explicit Claude root: haiku'
  Write-Host 'Other modes: bg, mode, usage, access, bundle, config, models'
  Write-Host ''
  Write-Host 'Mode and usage policy:'
  Write-Host '  airlock mode                     Show current/default mode'
  Write-Host '  airlock mode balanced|economy|quality'
  Write-Host '  airlock mode budget              Set economy routing and deny extra usage'
  Write-Host '  airlock mode defaults            Restore tested policy defaults'
  Write-Host '  airlock mode extra-usage ask|never|allow'
  Write-Host '  airlock mode failover ask|never|allow'
  Write-Host '  airlock mode max-agents off|1..20'
  Write-Host '  airlock mode swarm-fast auto|on|off'
  Write-Host '  airlock mode nesting bounded|off'
  Write-Host '  airlock mode max-descendants 2'
  Write-Host '  airlock mode max-total-descendants 3'
  Write-Host '  airlock mode repair-rounds 2'
  Write-Host '  airlock mode set --routing economy --extra-usage never --failover ask --max-agents off --swarm-fast auto --descendants bounded --max-descendants 2 --max-total-descendants 3 --repair-rounds 2'
  Write-Host '  airlock usage [refresh]'
  Write-Host '  airlock usage set --claude-plan pro|max5x|max20x|unknown'
  Write-Host '  airlock usage set --openai-capacity auto|1x|5x|20x'
  Write-Host ''
  Write-Host 'Examples:'
  Write-Host '  airlock'
  Write-Host '  airlock terra'
  Write-Host '  airlock hybrid                 # interactive six-model root menu'
  Write-Host '  airlock hybrid opus'
  Write-Host '  airlock hybrid sol --effort high'
  Write-Host '  airlock access refresh'
  Write-Host ''
  Write-Host 'Inside a session:'
  Write-Host '  /effort changes root effort.'
  Write-Host '  /model switches only within the provider selected at launch.'
  Write-Host '  Exit and relaunch airlock hybrid to switch the root provider.'
  Write-Host '  The orchestrator may choose only from the user-enabled worker pool.'
  Write-Host '  /airlock:usage refreshes sanitized usage (one small root-model turn).'
  Write-Host '  Top-level workers use Claude Code''s native concurrency by default. Automatic high-volume swarms use Luna only. Real workers may use bounded same-model descendants; descendants cannot spawn again.'
}

$script:PythonResolutionAttempted = $false
$script:PythonBin = $null

function Test-Python3 {
  param([string]$Candidate)
  if (-not $Candidate) { return $false }
  try {
    & $Candidate -c 'import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)' *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Resolve-PythonCommand {
  param([string]$Candidate)
  if (-not $Candidate) { return $null }
  $command = Get-Command $Candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($command) { return [string]$command.Source }
  return $null
}

function Resolve-Python {
  if ($script:PythonResolutionAttempted) { return $script:PythonBin }
  $script:PythonResolutionAttempted = $true

  if ($env:AIRLOCK_PYTHON) {
    $explicit = Resolve-PythonCommand $env:AIRLOCK_PYTHON
    if (-not $explicit -or -not (Test-Python3 $explicit)) {
      [Console]::Error.WriteLine("airlock: AIRLOCK_PYTHON is not a usable Python 3 interpreter: $($env:AIRLOCK_PYTHON)")
      exit 1
    }
    $script:PythonBin = $explicit
  } else {
    foreach ($candidate in @('python3.exe', 'python3', 'python.exe', 'python')) {
      $resolved = Resolve-PythonCommand $candidate
      if ($resolved -and (Test-Python3 $resolved)) {
        $script:PythonBin = $resolved
        break
      }
    }
  }

  if ($script:PythonBin) { $env:AIRLOCK_PYTHON = $script:PythonBin }
  return $script:PythonBin
}

function Invoke-AccessPolicy {
  param([string[]]$PolicyArguments = @('show'))
  if (-not (Test-Path -LiteralPath $AccessHelper -PathType Leaf)) {
    Write-Error "airlock: managed access helper is missing: $AccessHelper"
    return 1
  }
  $python = Resolve-Python
  if (-not $python) {
    Write-Error 'airlock: Python is required for access-policy handling.'
    return 1
  }
  $accessOutput = & $python $AccessHelper @PolicyArguments
  $accessExitCode = $LASTEXITCODE
  foreach ($line in $accessOutput) { Write-Host $line }
  return $accessExitCode
}

function Test-FastRootModel {
  param([string]$Model)
  $route = if ($Model -in @('gpt-5.6-sol-fast', 'gpt-5.6-sol-fast[1m]')) {
    'sol-fast'
  } elseif ($Model -in @('gpt-5.6-luna-fast', 'gpt-5.6-luna-fast[1m]')) {
    'luna-fast'
  } else {
    return
  }
  $exitCode = Invoke-AccessPolicy -PolicyArguments @('fast-check', '--route', $route, '--quiet')
  if ($exitCode -ne 0) { exit $exitCode }
}

function Test-ManagedBundle {
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python is required to validate the managed installation.')
    return 1
  }
  $bundleArguments = @(
    'bundle-check', '--quiet', '--platform', 'windows', '--bundle', $ManagedBundleFile,
    '--component', "bin/airlock=$(Join-Path $ManagedBinDir 'airlock')",
    '--component', "bin/airlock.cmd=$(Join-Path $PSScriptRoot 'airlock.cmd')",
    '--component', "bin/airlock.ps1=$(Join-Path $PSScriptRoot 'airlock.ps1')",
    '--component', "bin/airlock-access.py=$AccessHelper",
    '--component', "bin/airlock-router.py=$RouterHelper",
    '--component', "bin/airlock-hybrid.py=$(Join-Path $ManagedBinDir 'airlock-hybrid.py')",
    '--component', "config/openai-direct-agents.json=$OpenAIDirectAgentsFile",
    '--component', "config/anthropic-direct-agents.json=$AnthropicDirectAgentsFile",
    '--component', "config/hybrid-agents.json=$OpenAIWrapperAgentsFile",
    '--component', "config/claude-agents.json=$AnthropicWrapperAgentsFile",
    '--component', "plugins/airlock/.claude-plugin/plugin.json=$(Join-Path $PluginDir '.claude-plugin\plugin.json')",
    '--component', "plugins/airlock/hooks/hooks.json=$(Join-Path $PluginDir 'hooks\hooks.json')",
    '--component', "plugins/airlock/skills/usage/SKILL.md=$(Join-Path $PluginDir 'skills\usage\SKILL.md')",
    '--component', "plugins/airlock/scripts/agent-guard.sh=$(Join-Path $PluginDir 'scripts\agent-guard.sh')",
    '--component', "plugins/airlock/scripts/agent-guard.py=$(Join-Path $PluginDir 'scripts\agent-guard.py')",
    '--component', "plugins/airlock/scripts/secret-guard.sh=$(Join-Path $PluginDir 'scripts\secret-guard.sh')",
    '--component', "plugins/airlock/scripts/secret-guard.py=$(Join-Path $PluginDir 'scripts\secret-guard.py')",
    '--component', "plugins/airlock/scripts/file_safety.py=$(Join-Path $PluginDir 'scripts\file_safety.py')",
    '--component', "plugins/airlock/scripts/worktree.py=$(Join-Path $PluginDir 'scripts\worktree.py')",
    '--component', "plugins/airlock/scripts/worktree-create.sh=$(Join-Path $PluginDir 'scripts\worktree-create.sh')",
    '--component', "plugins/airlock/scripts/worktree-remove.sh=$(Join-Path $PluginDir 'scripts\worktree-remove.sh')"
  )
  & $python $AccessHelper @bundleArguments
  if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine('airlock: managed files are stale or incomplete. Reinstall Airlock, then start a fresh session.')
    return 1
  }
  return 0
}

function Test-ProxyHealth {
  try {
    Invoke-WebRequest -Uri "$ProxyUrl/healthz" -TimeoutSec 1 -UseBasicParsing | Out-Null
    return $true
  } catch { return $false }
}

function Start-ProxyIfNeeded {
  if (Test-ProxyHealth) { return }
  $proxyExe = (Get-Command claude-code-proxy.exe -ErrorAction SilentlyContinue).Source
  if (-not $proxyExe) { $proxyExe = Join-Path $HOME '.local\bin\claude-code-proxy.exe' }
  if (Test-Path -LiteralPath $proxyExe -PathType Leaf) {
    $logDir = Join-Path $HOME '.local\state\claude-code-proxy'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Start-Process -FilePath $proxyExe -ArgumentList 'serve','--no-monitor' -WindowStyle Hidden `
      -RedirectStandardOutput (Join-Path $logDir 'service.out.log') `
      -RedirectStandardError  (Join-Path $logDir 'service.err.log')
  }
  for ($i = 0; $i -lt 50; $i++) {
    if (Test-ProxyHealth) { return }
    Start-Sleep -Milliseconds 200
  }
  Write-Error "airlock: proxy did not become healthy at $ProxyUrl/healthz"
  exit 1
}

# Claude Code decides whether a model supports effort by matching the model ID
# against known Anthropic patterns. A pinned GPT ID matches nothing, which would
# leave /effort unavailable, so declare the levels explicitly. Only do this for
# GPT IDs: declaring capabilities for a real Claude ID would disable every
# capability left off the list, and built-in detection already gets those right.
function Set-GptEffortCapabilities {
  param([string]$Variable, [string]$Model)
  if (-not $Model -or $Model.StartsWith('claude-')) { return }
  Set-Item -LiteralPath "Env:${Variable}_SUPPORTED_CAPABILITIES" -Value $GptEffortCapabilities
}

function Set-OpenAIEnvironment {
  param([string]$Model, [string]$ModelName)
  Start-ProxyIfNeeded
  $env:ANTHROPIC_BASE_URL   = $ProxyUrl
  $env:ANTHROPIC_AUTH_TOKEN = 'unused'
  $env:ANTHROPIC_MODEL      = $Model
  $env:ANTHROPIC_DEFAULT_OPUS_MODEL = $Model
  $env:ANTHROPIC_DEFAULT_SONNET_MODEL = $Model
  $env:ANTHROPIC_DEFAULT_HAIKU_MODEL = $SmallFast
  $env:ANTHROPIC_SMALL_FAST_MODEL = $SmallFast
  $env:ANTHROPIC_CUSTOM_MODEL_OPTION = $Model
  $env:ANTHROPIC_CUSTOM_MODEL_OPTION_NAME = "$ModelName (OpenAI subscription)"
  Set-GptEffortCapabilities 'ANTHROPIC_DEFAULT_OPUS_MODEL' $Model
  Set-GptEffortCapabilities 'ANTHROPIC_DEFAULT_SONNET_MODEL' $Model
  Set-GptEffortCapabilities 'ANTHROPIC_DEFAULT_HAIKU_MODEL' $SmallFast
  Set-GptEffortCapabilities 'ANTHROPIC_CUSTOM_MODEL_OPTION' $Model
  $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = $ContextWin
  if (-not $env:CLAUDE_CODE_ALWAYS_ENABLE_EFFORT) { $env:CLAUDE_CODE_ALWAYS_ENABLE_EFFORT = '1' }
  $env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = '1'
  $env:CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK = '1'
}

function Clear-ProxyEnvironment {
  foreach ($variable in $ProxyVariables) {
    Remove-Item -LiteralPath "Env:$variable" -ErrorAction SilentlyContinue
  }
}

function Get-ExplicitModel {
  param([string[]]$ChildArguments)
  for ($i = 0; $i -lt $ChildArguments.Count; $i++) {
    if ($ChildArguments[$i] -eq '--model') {
      if ($i + 1 -ge $ChildArguments.Count) {
        Write-Error 'airlock: --model requires a value.'
        exit 2
      }
      return $ChildArguments[$i + 1]
    }
    if ($ChildArguments[$i] -like '--model=*') {
      return $ChildArguments[$i].Substring('--model='.Length)
    }
  }
  return $null
}

function Get-ModelProvider {
  param([string]$Model)
  if ($Model -like 'gpt-*') { return 'openai' }
  if ($Model -like 'claude-*') { return 'anthropic' }
  return $null
}

function Add-DefaultEffort {
  param([string[]]$ChildArguments, [string]$Effort)
  foreach ($argument in $ChildArguments) {
    if ($argument -eq '--effort' -or $argument -like '--effort=*') { return $ChildArguments }
  }
  return @('--effort', $Effort) + $ChildArguments
}

function Invoke-AirlockSession {
  param(
    [string]$Profile,
    [string]$RootModel,
    [string]$RootName,
    [string[]]$ChildArguments
  )

  foreach ($argument in $ChildArguments) {
    if ($argument -eq '--safe-mode' -or $argument -eq '--bare' -or $argument -eq '--agent' -or $argument -eq '--agents' -or $argument -eq '--plugin-dir' -or $argument -eq '--plugin-url' -or $argument -eq '--settings' -or
        $argument -like '--safe-mode=*' -or $argument -like '--bare=*' -or $argument -like '--agent=*' -or $argument -like '--agents=*' -or $argument -like '--plugin-dir=*' -or $argument -like '--plugin-url=*' -or $argument -like '--settings=*') {
      [Console]::Error.WriteLine("airlock: $argument is managed or incompatible with the Airlock worker guard and cannot be forwarded.")
      exit 2
    }
  }
  if ($env:CLAUDE_CODE_SAFE_MODE -eq '1' -or $env:CLAUDE_CODE_SIMPLE -eq '1') {
    [Console]::Error.WriteLine('airlock: CLAUDE_CODE_SAFE_MODE/CLAUDE_CODE_SIMPLE would disable managed session protection; unset it before launching.')
    exit 2
  }
  if ((Test-ManagedBundle) -ne 0) { exit 1 }
  if (-not (Test-Path -LiteralPath $PluginDir -PathType Container) -or -not (Test-Path -LiteralPath (Join-Path $PluginDir '.claude-plugin\plugin.json') -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed session plugin is missing or unsafe: $PluginDir")
    exit 1
  }
  Remove-Item -LiteralPath 'Env:CLAUDE_CODE_SUBAGENT_MODEL' -ErrorAction SilentlyContinue
  if ($env:AIRLOCK_ALLOW_CLAUDE_API_SKILL -ne '1') {
    $ChildArguments = @('--disallowedTools', 'Skill(claude-api)', 'Skill(claude-api *)') + $ChildArguments
  }

  $claudeBin = if ($env:AIRLOCK_REAL_CLAUDE) { $env:AIRLOCK_REAL_CLAUDE } else { (Get-Command claude -ErrorAction SilentlyContinue).Source }
  if (-not $claudeBin) {
    Write-Error 'airlock: Claude Code (claude) not found on PATH. Set AIRLOCK_REAL_CLAUDE.'
    exit 1
  }
  $launcher = Join-Path $PSScriptRoot 'airlock-hybrid.py'
  if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    Write-Error "airlock: session launcher is missing: $launcher"
    exit 1
  }
  $pythonBin = Resolve-Python
  if (-not $pythonBin) {
    Write-Error 'airlock: Python is required for safe argument handling.'
    exit 1
  }

  $catalogFiles = [ordered]@{
    openai_direct = [IO.Path]::GetFullPath($OpenAIDirectAgentsFile)
    anthropic_direct = [IO.Path]::GetFullPath($AnthropicDirectAgentsFile)
    openai_wrappers = [IO.Path]::GetFullPath($OpenAIWrapperAgentsFile)
    anthropic_wrappers = [IO.Path]::GetFullPath($AnthropicWrapperAgentsFile)
  }
  $launchDirectory = Join-Path $env:LOCALAPPDATA 'Airlock\launch'
  New-Item -ItemType Directory -Force -Path $launchDirectory | Out-Null
  $launchRequestPath = Join-Path $launchDirectory ("{0}.json" -f [Guid]::NewGuid().ToString('N'))
  $launchRequest = [ordered]@{
    profile = $Profile
    claude = [string]$claudeBin
    catalog_files = $catalogFiles
    plugin_dir = [IO.Path]::GetFullPath($PluginDir)
    router_helper = [IO.Path]::GetFullPath($RouterHelper)
    proxy_url = [string]$ProxyUrl
    root_model = [string]$RootModel
    root_name = [string]$RootName
    context_window = [string]$ContextWin
    max_agents = [string]$MaxAgents
    args = [string[]]$ChildArguments
  }
  $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
  [IO.File]::WriteAllText(
    $launchRequestPath,
    (ConvertTo-Json -InputObject $launchRequest -Depth 4 -Compress),
    $utf8WithoutBom
  )

  try {
    & $pythonBin $launcher --request-file $launchRequestPath
    $exitCode = $LASTEXITCODE
  } finally {
    Remove-Item -LiteralPath $launchRequestPath -Force -ErrorAction SilentlyContinue
  }
  exit $exitCode
}

function Select-HybridRoot {
  $interactive = [Environment]::UserInteractive
  try { $interactive = $interactive -and -not [Console]::IsInputRedirected } catch { }
  if (-not $interactive) {
    [Console]::Error.WriteLine('airlock: hybrid root is required in noninteractive use: airlock hybrid sol|terra|luna|opus|sonnet|fable')
    exit 2
  }
  Write-Host 'Choose the airlock hybrid orchestrator:'
  Write-Host '  1) GPT-5.6 Sol'
  Write-Host '  2) GPT-5.6 Terra'
  Write-Host '  3) GPT-5.6 Luna'
  Write-Host '  4) Claude Opus 5'
  Write-Host '  5) Claude Sonnet 5'
  Write-Host '  6) Claude Fable 5 (may use Anthropic extra usage)'
  $selection = Read-Host 'Selection [1-6]'
  $choices = @{ '1' = 'sol'; '2' = 'terra'; '3' = 'luna'; '4' = 'opus'; '5' = 'sonnet'; '6' = 'fable' }
  if (-not $choices.ContainsKey($selection)) {
    [Console]::Error.WriteLine('airlock: invalid hybrid root selection.')
    exit 2
  }
  return $choices[$selection]
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'mode') {
  $modeArguments = @('mode')
  if ($Arguments.Count -gt 1) {
    $modeArguments += @($Arguments[1..($Arguments.Count - 1)])
  }
  exit (Invoke-AccessPolicy -PolicyArguments $modeArguments)
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'usage') {
  $usageArguments = @('usage')
  if ($Arguments.Count -gt 1) {
    $usageArguments += @($Arguments[1..($Arguments.Count - 1)])
  }
  exit (Invoke-AccessPolicy -PolicyArguments $usageArguments)
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'bundle') {
  if ($Arguments.Count -ne 1) {
    [Console]::Error.WriteLine('airlock: usage: airlock bundle')
    exit 2
  }
  if ((Test-ManagedBundle) -ne 0) { exit 1 }
  Write-Host 'Managed bundle is current and complete.'
  exit 0
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'access') {
  $accessCommand = if ($Arguments.Count -gt 1) { $Arguments[1] } else { 'show' }
  if ($accessCommand -notin @('show', 'refresh')) {
    Write-Error 'airlock: usage: airlock access [show|refresh]'
    exit 2
  }
  exit (Invoke-AccessPolicy -PolicyArguments @($accessCommand))
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'hybrid') {
  $hybridArgs = @()
  if ($Arguments.Count -gt 1) { $hybridArgs = @($Arguments[1..($Arguments.Count - 1)]) }

  $rootAlias = $null
  if ($hybridArgs.Count -gt 0 -and $HybridRoots.ContainsKey($hybridArgs[0])) {
    $rootAlias = $hybridArgs[0]
    if ($hybridArgs.Count -gt 1) { $hybridArgs = @($hybridArgs[1..($hybridArgs.Count - 1)]) } else { $hybridArgs = @() }
  }

  $explicitModel = Get-ExplicitModel $hybridArgs
  if (-not $rootAlias -and -not $explicitModel) { $rootAlias = Select-HybridRoot }

  if ($rootAlias) {
    $rootModel = $HybridRoots[$rootAlias][0]
    $rootName = $HybridRoots[$rootAlias][1]
    $rootProvider = $HybridRoots[$rootAlias][2]
    if ($explicitModel) {
      $explicitProvider = Get-ModelProvider $explicitModel
      if (-not $explicitProvider -or $explicitProvider -ne $rootProvider) {
        [Console]::Error.WriteLine('airlock: hybrid root alias and --model must select the same provider.')
        exit 2
      }
      $rootModel = $explicitModel
      $rootName = $explicitModel
    } else {
      $hybridArgs = @('--model', $rootModel) + $hybridArgs
    }
  } else {
    $rootModel = $explicitModel
    $rootName = $explicitModel
    $rootProvider = Get-ModelProvider $explicitModel
    if (-not $rootProvider) {
      [Console]::Error.WriteLine('airlock: cannot determine the hybrid root provider from --model; use a gpt-* or claude-* model ID.')
      exit 2
    }
  }
  $hybridArgs = Add-DefaultEffort $hybridArgs $MainEffort

  Start-ProxyIfNeeded
  if ($rootProvider -eq 'openai') {
    Test-FastRootModel $rootModel
    Remove-Item -LiteralPath 'Env:AIRLOCK_HYBRID' -ErrorAction SilentlyContinue
    $env:AIRLOCK_GPT_HYBRID = '1'
    Invoke-AirlockSession 'hybrid-openai-root' $rootModel $rootName $hybridArgs
  }

  Remove-Item -LiteralPath 'Env:AIRLOCK_GPT_HYBRID' -ErrorAction SilentlyContinue
  $env:AIRLOCK_HYBRID = '1'
  Invoke-AirlockSession 'hybrid-anthropic-root' $rootModel $rootName $hybridArgs
}

$requested = 'sol'
$effort = $MainEffort
$rest = @()

if ($Arguments.Count -gt 0) {
  switch ($Arguments[0]) {
    'models'   { Show-Models; return }
    '--models' { Show-Models; return }
    'config'   {
      Write-Host "Main model: $($Models[$requested][0])"
      Write-Host "Main effort: $MainEffort"
      Write-Host "Background: $($Models['sol'][0]) / $BgEffort"
      Write-Host "Utility model: $SmallFast"
      Write-Host "Context window: $ContextWin"
      Write-Host "Proxy URL: $ProxyUrl"
      Write-Host "OpenAI direct agents: $OpenAIDirectAgentsFile"
      Write-Host "Anthropic direct agents: $AnthropicDirectAgentsFile"
      Write-Host "OpenAI bridge agents: $OpenAIWrapperAgentsFile"
      Write-Host "Anthropic bridge agents: $AnthropicWrapperAgentsFile"
      Write-Host "Managed plugin: $PluginDir"
      Write-Host "Max concurrent top-level subagents: $MaxAgents"
      $accessExitCode = Invoke-AccessPolicy -PolicyArguments @('show')
      if ($accessExitCode -ne 0) { exit $accessExitCode }
      return
    }
    'bg' {
      $requested = 'sol'
      $effort = $BgEffort
      if ($Arguments.Count -gt 1) { $rest = @($Arguments[1..($Arguments.Count - 1)]) }
    }
    default {
      if ($Models.ContainsKey($Arguments[0])) {
        $requested = $Arguments[0]
        if ($Arguments.Count -gt 1) { $rest = @($Arguments[1..($Arguments.Count - 1)]) }
      } else {
        $rest = $Arguments
      }
    }
  }
}

$selected = $Models[$requested][0]
$modelName = $Models[$requested][1]
Test-FastRootModel $selected
Set-OpenAIEnvironment $selected $modelName
Remove-Item -LiteralPath 'Env:AIRLOCK_HYBRID' -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'Env:AIRLOCK_GPT_HYBRID' -ErrorAction SilentlyContinue

$cmdArgs = @('--model', $selected)
$cmdArgs += Add-DefaultEffort $rest $effort
Invoke-AirlockSession 'openai-pure' $selected $modelName $cmdArgs
