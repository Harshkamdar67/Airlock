# Managed by https://github.com/Harshkamdar67/Airlock (Windows PowerShell port)
# Launches provider-scoped Claude Code sessions through native Anthropic or local OpenAI access.
$Arguments = @($args)

$ErrorActionPreference = 'Stop'

# Every managed path hangs off one resolved config root. install.ps1
# honours AIRLOCK_CONFIG_DIR, so the launcher has to honour it too, or an
# install into a custom directory validates another installation's files.
$ConfigDir = if ($env:AIRLOCK_CONFIG_DIR) { $env:AIRLOCK_CONFIG_DIR } else { Join-Path $HOME '.config\airlock' }
$ConfigFile = if ($env:AIRLOCK_CONFIG_FILE) { $env:AIRLOCK_CONFIG_FILE } else { Join-Path $ConfigDir 'config' }
$ConfigValues = @{}
if (Test-Path -LiteralPath $ConfigFile -PathType Leaf) {
  foreach ($line in [IO.File]::ReadAllLines($ConfigFile)) {
    if ($line -match '^(AIRLOCK_[A-Z0-9_]+)=(.*)$') { $ConfigValues[$Matches[1]] = $Matches[2].TrimEnd("`r") }
  }
}
$ProxyUrl = if ($env:AIRLOCK_PROXY_URL) { $env:AIRLOCK_PROXY_URL } elseif ($ConfigValues.ContainsKey('AIRLOCK_PROXY_URL')) { $ConfigValues['AIRLOCK_PROXY_URL'] } else { 'http://127.0.0.1:18765' }
$ProxyConfigDir = if ($env:CCP_CONFIG_DIR) { $env:CCP_CONFIG_DIR } elseif ($env:AIRLOCK_PROXY_CONFIG_DIR) { $env:AIRLOCK_PROXY_CONFIG_DIR } elseif ($ConfigValues.ContainsKey('AIRLOCK_PROXY_CONFIG_DIR')) { $ConfigValues['AIRLOCK_PROXY_CONFIG_DIR'] } else { '' }
$ProxyStateHome = if ($env:XDG_STATE_HOME) { $env:XDG_STATE_HOME } elseif ($env:AIRLOCK_PROXY_STATE_HOME) { $env:AIRLOCK_PROXY_STATE_HOME } elseif ($ConfigValues.ContainsKey('AIRLOCK_PROXY_STATE_HOME')) { $ConfigValues['AIRLOCK_PROXY_STATE_HOME'] } else { '' }
$MainEffort = if ($env:AIRLOCK_MAIN_EFFORT) { $env:AIRLOCK_MAIN_EFFORT } elseif ($ConfigValues.ContainsKey('AIRLOCK_MAIN_EFFORT')) { $ConfigValues['AIRLOCK_MAIN_EFFORT'] } else { 'high' }
$BgEffort = if ($env:AIRLOCK_BG_EFFORT) { $env:AIRLOCK_BG_EFFORT } elseif ($ConfigValues.ContainsKey('AIRLOCK_BG_EFFORT')) { $ConfigValues['AIRLOCK_BG_EFFORT'] } else { 'medium' }
$SmallFast = if ($env:AIRLOCK_SMALL_FAST_MODEL) { $env:AIRLOCK_SMALL_FAST_MODEL } elseif ($ConfigValues.ContainsKey('AIRLOCK_SMALL_FAST_MODEL')) { $ConfigValues['AIRLOCK_SMALL_FAST_MODEL'] } else { 'gpt-5.6-sol' }
$ExplicitContextWin = Test-Path Env:\AIRLOCK_CONTEXT_WINDOW
$ContextWin = if ($ExplicitContextWin) { [string]$env:AIRLOCK_CONTEXT_WINDOW } elseif ($ConfigValues.ContainsKey('AIRLOCK_CONTEXT_WINDOW')) { $ConfigValues['AIRLOCK_CONTEXT_WINDOW'] } else { '272000' }
# A window the user set themselves outranks Airlock's default. Record it before
# any proxy cleanup removes it.
$UserContextWin = if ($env:CLAUDE_CODE_AUTO_COMPACT_WINDOW) { $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW } else { '' }
# Claude Code accepts 100000 to 1000000 and silently ignores everything else, so
# an unchecked value here would look applied while doing nothing at all.
# The shape check has to match the POSIX launcher exactly. TryParse alone would
# accept a leading plus, surrounding whitespace, and leading zeros, all of which
# are exported verbatim and then discarded by Claude Code.
if ($ContextWin -ne 'auto') {
  if ($ContextWin -notmatch '^[1-9][0-9]{5,6}$' -or
      [int]$ContextWin -lt 100000 -or [int]$ContextWin -gt 1000000) {
    Write-Error "airlock: AIRLOCK_CONTEXT_WINDOW must be 'auto' or a whole number from 100000 to 1000000."
    exit 2
  }
}
$DefaultProfile = if ($env:AIRLOCK_DEFAULT_PROFILE) { $env:AIRLOCK_DEFAULT_PROFILE } elseif ($ConfigValues.ContainsKey('AIRLOCK_DEFAULT_PROFILE')) { $ConfigValues['AIRLOCK_DEFAULT_PROFILE'] } else { 'openai' }
$DefaultHybridModel = if ($env:AIRLOCK_HYBRID_MODEL) { $env:AIRLOCK_HYBRID_MODEL } elseif ($ConfigValues.ContainsKey('AIRLOCK_HYBRID_MODEL')) { $ConfigValues['AIRLOCK_HYBRID_MODEL'] } else { 'sonnet' }
$DefaultGrokModel = if ($env:AIRLOCK_GROK_MODEL) { $env:AIRLOCK_GROK_MODEL } elseif ($ConfigValues.ContainsKey('AIRLOCK_GROK_MODEL')) { $ConfigValues['AIRLOCK_GROK_MODEL'] } else { 'grok' }
$DefaultOpenAIModel = if ($env:AIRLOCK_MODEL) { $env:AIRLOCK_MODEL } elseif ($ConfigValues.ContainsKey('AIRLOCK_MODEL')) { $ConfigValues['AIRLOCK_MODEL'] } else { 'sol' }
$DefaultBgModel = if ($env:AIRLOCK_BG_MODEL) { $env:AIRLOCK_BG_MODEL } elseif ($ConfigValues.ContainsKey('AIRLOCK_BG_MODEL')) { $ConfigValues['AIRLOCK_BG_MODEL'] } else { 'sol' }
$MaxAgents = if ($env:AIRLOCK_MAX_CONCURRENT_SUBAGENTS) { $env:AIRLOCK_MAX_CONCURRENT_SUBAGENTS } elseif ($ConfigValues.ContainsKey('AIRLOCK_MAX_CONCURRENT_SUBAGENTS')) { $ConfigValues['AIRLOCK_MAX_CONCURRENT_SUBAGENTS'] } else { 'off' }
$OpenAIFast = if ($env:AIRLOCK_OPENAI_FAST) { $env:AIRLOCK_OPENAI_FAST } elseif ($ConfigValues.ContainsKey('AIRLOCK_OPENAI_FAST')) { $ConfigValues['AIRLOCK_OPENAI_FAST'] } else { 'off' }
$AnthropicFast = if ($env:AIRLOCK_ANTHROPIC_FAST) { $env:AIRLOCK_ANTHROPIC_FAST } elseif ($ConfigValues.ContainsKey('AIRLOCK_ANTHROPIC_FAST')) { $ConfigValues['AIRLOCK_ANTHROPIC_FAST'] } else { 'off' }
$ExtraUsagePolicy = if ($env:AIRLOCK_EXTRA_USAGE_POLICY) { $env:AIRLOCK_EXTRA_USAGE_POLICY } elseif ($ConfigValues.ContainsKey('AIRLOCK_EXTRA_USAGE_POLICY')) { $ConfigValues['AIRLOCK_EXTRA_USAGE_POLICY'] } else { 'ask' }
$GptEffortCapabilities = if ($env:AIRLOCK_GPT_EFFORT_CAPABILITIES) { $env:AIRLOCK_GPT_EFFORT_CAPABILITIES } elseif ($ConfigValues.ContainsKey('AIRLOCK_GPT_EFFORT_CAPABILITIES')) { $ConfigValues['AIRLOCK_GPT_EFFORT_CAPABILITIES'] } else { 'effort,xhigh_effort,max_effort' }

function Normalize-OpenAIModelId {
  param([string]$Model)
  if ($Model -and $Model.StartsWith('gpt-') -and $Model.EndsWith('[1m]')) {
    return $Model.Substring(0, $Model.Length - 4)
  }
  return $Model
}

$SmallFast = Normalize-OpenAIModelId $SmallFast
$DefaultHybridModel = Normalize-OpenAIModelId $DefaultHybridModel
$DefaultOpenAIModel = Normalize-OpenAIModelId $DefaultOpenAIModel
$DefaultBgModel = Normalize-OpenAIModelId $DefaultBgModel
# Legacy GPT [1m] values remain accepted at launcher boundaries, but all
# resolved OpenAI IDs are bare before they reach the session policy.
# The hybrid launcher rebuilds these declarations itself, so hand it the
# resolved value rather than letting it fall back to the built-in default.
$env:AIRLOCK_GPT_EFFORT_CAPABILITIES = $GptEffortCapabilities
$AgentDepth = if ($env:AIRLOCK_AGENT_DEPTH) { $env:AIRLOCK_AGENT_DEPTH } elseif ($ConfigValues.ContainsKey('AIRLOCK_AGENT_DEPTH')) { $ConfigValues['AIRLOCK_AGENT_DEPTH'] } else { '1' }
if ($AgentDepth -ne '1' -and $AgentDepth -ne '2') {
  Write-Error "airlock: invalid agent depth '$AgentDepth' (expected 1 or 2)"
  exit 2
}
# Claude Code enforces the cap, and it decides whether a worker gets the
# Agent tool, so the session launchers must see the configured depth.
$env:AIRLOCK_AGENT_DEPTH = $AgentDepth
$PluginDir = if ($env:AIRLOCK_PLUGIN_DIR) { $env:AIRLOCK_PLUGIN_DIR } else { Join-Path $ConfigDir 'plugins\airlock' }
$OpenAIDirectAgentsFile = if ($env:AIRLOCK_OPENAI_DIRECT_AGENTS_FILE) { $env:AIRLOCK_OPENAI_DIRECT_AGENTS_FILE } else { Join-Path $ConfigDir 'openai-direct-agents.json' }
$AnthropicDirectAgentsFile = if ($env:AIRLOCK_ANTHROPIC_DIRECT_AGENTS_FILE) { $env:AIRLOCK_ANTHROPIC_DIRECT_AGENTS_FILE } else { Join-Path $ConfigDir 'anthropic-direct-agents.json' }
$OpenAIWrapperAgentsFile = if ($env:AIRLOCK_HYBRID_AGENTS_FILE) { $env:AIRLOCK_HYBRID_AGENTS_FILE } else { Join-Path $ConfigDir 'hybrid-agents.json' }
$AnthropicWrapperAgentsFile = if ($env:AIRLOCK_CLAUDE_AGENTS_FILE) { $env:AIRLOCK_CLAUDE_AGENTS_FILE } else { Join-Path $ConfigDir 'claude-agents.json' }
$GrokAgentsFile = if ($env:AIRLOCK_GROK_DIRECT_AGENTS_FILE) { $env:AIRLOCK_GROK_DIRECT_AGENTS_FILE } elseif ($env:AIRLOCK_GROK_AGENTS_FILE) { $env:AIRLOCK_GROK_AGENTS_FILE } else { Join-Path $ConfigDir 'grok-agents.json' }
$AccessHelper = if ($env:AIRLOCK_ACCESS_HELPER) { $env:AIRLOCK_ACCESS_HELPER } else { Join-Path $PSScriptRoot 'airlock-access.py' }
$PolicyHelper = if ($env:AIRLOCK_POLICY_HELPER_PATH) { $env:AIRLOCK_POLICY_HELPER_PATH } else { Join-Path $PSScriptRoot 'airlock_policy.py' }
$OpenRouterAuthHelper = if ($env:AIRLOCK_OPENROUTER_AUTH_HELPER) { $env:AIRLOCK_OPENROUTER_AUTH_HELPER } else { Join-Path $PSScriptRoot 'airlock_openrouter_auth.py' }
$OpenRouterPresetsHelper = if ($env:AIRLOCK_OPENROUTER_PRESETS_HELPER) { $env:AIRLOCK_OPENROUTER_PRESETS_HELPER } else { Join-Path $PSScriptRoot 'airlock_openrouter_presets.py' }
$OpenRouterModelsHelper = if ($env:AIRLOCK_OPENROUTER_MODELS_HELPER) { $env:AIRLOCK_OPENROUTER_MODELS_HELPER } else { Join-Path $PSScriptRoot 'airlock_openrouter_models.py' }
$OpenRouterRegistryFile = if ($env:AIRLOCK_OPENROUTER_REGISTRY_FILE) { $env:AIRLOCK_OPENROUTER_REGISTRY_FILE } else { Join-Path $ConfigDir 'openrouter-registry.json' }
$RouterHelper = if ($env:AIRLOCK_ROUTER_HELPER) { $env:AIRLOCK_ROUTER_HELPER } else { Join-Path $PSScriptRoot 'airlock-router.py' }
$UpdateHelper = if ($env:AIRLOCK_UPDATE_HELPER) { $env:AIRLOCK_UPDATE_HELPER } else { Join-Path $PSScriptRoot 'airlock-update.py' }
$ManagedBinDir = if ($env:AIRLOCK_MANAGED_BIN_DIR) { $env:AIRLOCK_MANAGED_BIN_DIR } else { $PSScriptRoot }
$ManagedBundleFile = if ($env:AIRLOCK_MANAGED_BUNDLE_FILE) { $env:AIRLOCK_MANAGED_BUNDLE_FILE } else { Join-Path $ConfigDir 'managed-bundle.json' }
$UpdateNoticeFile = Join-Path $ConfigDir 'update-notice.json'

$Models = @{
  'sol'      = @('gpt-5.6-sol',        'GPT-5.6 Sol')
  'sol-fast' = @('gpt-5.6-sol-fast',   'GPT-5.6 Sol Fast')
  'terra'    = @('gpt-5.6-terra',      'GPT-5.6 Terra')
  'luna'     = @('gpt-5.6-luna',       'GPT-5.6 Luna')
  '5.5'      = @('gpt-5.5',            'GPT-5.5')
  '5.4'      = @('gpt-5.4',            'GPT-5.4')
  'mini'     = @('gpt-5.4-mini',       'GPT-5.4 Mini')
  '5.3'      = @('gpt-5.3-codex',      'GPT-5.3 Codex')
  'spark'    = @('gpt-5.3-codex-spark',    'GPT-5.3 Codex Spark')
  '5.2'      = @('gpt-5.2',            'GPT-5.2')
}

$GrokModels = @{
  'grok'     = @('grok-4.5', 'Grok 4.5')
  'composer' = @('grok-composer-2.5-fast', 'Grok Composer 2.5 Fast')
}

$HybridRoots = @{
  'sol'    = @('gpt-5.6-sol', 'GPT-5.6 Sol', 'openai')
  'terra'  = @('gpt-5.6-terra', 'GPT-5.6 Terra', 'openai')
  'luna'   = @('gpt-5.6-luna', 'GPT-5.6 Luna', 'openai')
  # Claude Code only grants these models their native 1M window when
  # ANTHROPIC_BASE_URL is unset or points at api.anthropic.com, and Airlock
  # always points it at the session router. The [1m] suffix is the one lever
  # that survives that. Haiku 4.5 is a genuine 200000 model, so it stays bare.
  'opus'   = @('claude-opus-5[1m]', 'Claude Opus 5', 'anthropic')
  'sonnet' = @('claude-sonnet-5[1m]', 'Claude Sonnet 5', 'anthropic')
  'fable'  = @('claude-fable-5[1m]', 'Claude Fable 5', 'anthropic')
  'haiku'  = @('claude-haiku-4-5-20251001', 'Claude Haiku 4.5', 'anthropic')
  'grok'     = @('grok-4.5', 'Grok 4.5', 'grok')
  'composer' = @('grok-composer-2.5-fast', 'Grok Composer 2.5 Fast', 'grok')
}

$ProxyVariables = @(
  'ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_MODEL',
  'ANTHROPIC_DEFAULT_FABLE_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'ANTHROPIC_SMALL_FAST_MODEL', 'ANTHROPIC_CUSTOM_MODEL_OPTION',
  'ANTHROPIC_DEFAULT_FABLE_MODEL_NAME', 'ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION',
  'ANTHROPIC_DEFAULT_OPUS_MODEL_NAME', 'ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION',
  'ANTHROPIC_DEFAULT_SONNET_MODEL_NAME', 'ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME', 'ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION',
  'ANTHROPIC_CUSTOM_MODEL_OPTION_NAME', 'ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION',
  'ANTHROPIC_DEFAULT_FABLE_MODEL_SUPPORTED_CAPABILITIES',
  'ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES',
  'ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES',
  'ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES',
  'CLAUDE_CODE_AUTO_COMPACT_WINDOW', 'CLAUDE_CODE_ALWAYS_ENABLE_EFFORT',
  'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
  'CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK', 'CLAUDE_CODE_SUBAGENT_MODEL'
)

function Show-Models {
  Write-Host 'Usage: airlock [claude arguments]'
  Write-Host '       airlock openai [model] [claude arguments]'
  Write-Host '       airlock grok [model] [claude arguments]'
  Write-Host '       airlock hybrid [model|choose] [claude arguments]'
  Write-Host '       airlock opr [route] [claude arguments]'
  Write-Host '       airlock [sol|terra|luna|...] [claude arguments]'
  Write-Host ''
  Write-Host 'Profiles:'
  Write-Host '  airlock          Start the saved default profile and orchestrator'
  Write-Host '  airlock openai   Start the saved OpenAI-only orchestrator'
  Write-Host '  airlock grok     Start the saved Grok-only orchestrator (subscription proxy)'
  Write-Host '  airlock hybrid   Start the saved hybrid orchestrator'
  Write-Host '  airlock fast     Start one session with OpenAI Fast without saving the mode'
  Write-Host '  airlock opr      Start an OpenRouter-only session on an exact registry route'
  Write-Host '  claude           Start the native Anthropic CLI without Airlock'
  Write-Host ''
  Write-Host 'OpenAI root aliases: sol, sol-fast, terra, luna, 5.5, 5.4, mini, 5.3, spark, 5.2'
  Write-Host 'Grok root aliases: grok, composer'
  Write-Host 'Hybrid root aliases: sonnet, sol, terra, luna, opus, fable, haiku, grok, composer'
  Write-Host 'Other commands: fast, bg, mode, usage, session-usage, access, bundle, config, models, openrouter auth/models, proxy auth, version, update'
  Write-Host ''
  Write-Host 'OpenRouter credential commands:'
  Write-Host '  airlock openrouter auth set-key     Store a key using a hidden prompt'
  Write-Host '  airlock openrouter auth set-key --stdin'
  Write-Host '  airlock openrouter auth status      Show only local credential state'
  Write-Host '  airlock openrouter auth logout      Delete the local credential after confirmation'
  Write-Host ''
  Write-Host 'OpenRouter model registry commands:'
  Write-Host '  airlock openrouter models list'
  Write-Host '  airlock openrouter models presets'
  Write-Host '  airlock openrouter models add-preset NAME [--yes]'
  Write-Host '  airlock openrouter models add ROUTE MODEL ENDPOINT [--yes]'
  Write-Host '  airlock openrouter models remove ROUTE [--yes]'
  Write-Host '  airlock openrouter models refresh [ROUTE] [--apply] [--yes]'
  Write-Host ''
  Write-Host 'Proxy login commands:'
  Write-Host '  airlock proxy auth status          Check Codex OAuth in Airlock''s selected proxy directory'
  Write-Host '  airlock proxy auth login           Start the upstream Codex browser login'
  Write-Host '  airlock proxy auth device          Start the upstream Codex device-code login'
  Write-Host '  airlock proxy grok auth status     Check Grok OAuth in Airlock''s selected proxy directory'
  Write-Host '  airlock proxy grok auth login      Start the upstream Grok browser login'
  Write-Host '  airlock proxy grok auth device     Start the upstream Grok device-code login'
  Write-Host ''
  Write-Host 'Policy commands:'
  Write-Host '  airlock mode                     Show the saved routing and usage policy'
  Write-Host '  airlock mode balanced|economy|quality'
  Write-Host '  airlock mode budget              Prefer economy routes and block extra usage'
  Write-Host '  airlock mode defaults            Restore tested policy defaults'
  Write-Host '  airlock mode extra-usage ask|never|allow'
  Write-Host '  airlock mode failover ask|never|allow'
  Write-Host '  airlock mode max-agents off|1..20'
  Write-Host '  airlock mode fast all|openai|anthropic|off'
  Write-Host '  airlock mode openai-fast on|off'
  Write-Host '  airlock mode anthropic-fast on|off'
  Write-Host '  airlock mode swarm-fast auto|on|off'
  Write-Host '  airlock mode set --routing economy --extra-usage never --failover ask --max-agents off --openai-fast off --anthropic-fast off --swarm-fast auto'
  Write-Host '  airlock usage                    Show cached sanitized subscription usage'
  Write-Host '  airlock usage refresh            Refresh OpenAI quota windows without a model call'
  Write-Host '  airlock usage set --claude-plan pro|max5x|max20x|unknown'
  Write-Host '  airlock usage set --openai-capacity auto|1x|5x|20x'
  Write-Host '  airlock usage defaults           Clear capacity overrides'
  Write-Host '  airlock session-usage [--json]   Show router-observed token counts for this hybrid session'
  Write-Host ''
  Write-Host 'Update commands:'
  Write-Host '  airlock version                  Show the installed Airlock version'
  Write-Host '  airlock update                   Download, verify, and confirm an update'
  Write-Host '  airlock update --check           Check without downloading or installing'
  Write-Host '  airlock update --yes             Install without an interactive confirmation'
  Write-Host ''
  Write-Host 'Examples:'
  Write-Host '  airlock                          # saved default profile and orchestrator'
  Write-Host '  airlock openai                   # saved OpenAI-only orchestrator'
  Write-Host '  airlock terra                    # explicit OpenAI-only root'
  Write-Host '  airlock hybrid                   # saved hybrid orchestrator'
  Write-Host '  airlock hybrid choose            # interactive seven-model picker'
  Write-Host '  airlock hybrid opus              # explicit hybrid root'
  Write-Host '  airlock opr kimi-k3 -r           # exact OpenRouter-only root and resume'
  Write-Host '  airlock opr                      # interactive OpenRouter route picker'
  Write-Host '  airlock access refresh'
  Write-Host ''
  Write-Host 'Inside a session:'
  Write-Host '  /effort changes the root and workers that are configured to follow it.'
  Write-Host '  Named airlock-* workers use fixed exact models and cannot start more workers.'
  Write-Host '  /model accepts only exact model IDs enabled for the active profile. Some GPT IDs may not appear in its menu.'
  Write-Host '  Exit and relaunch with another explicit root when switching through /model is unavailable.'
  Write-Host '  The orchestrator may choose only from the user-enabled worker pool.'
  Write-Host '  /airlock:usage refreshes sanitized usage with one small root-model turn.'
  Write-Host '  Top-level workers use Claude Code''s native concurrency unless an Airlock ceiling is saved.'
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

function Invoke-AccessJson {
  param([string[]]$PolicyArguments)
  if (-not (Test-Path -LiteralPath $AccessHelper -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed access helper is missing: $AccessHelper")
    exit 1
  }
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python is required for access-policy handling.')
    exit 1
  }
  $raw = @(& $python $AccessHelper @PolicyArguments)
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  try {
    return (($raw -join "`n") | ConvertFrom-Json -ErrorAction Stop)
  } catch {
    [Console]::Error.WriteLine('airlock: OpenRouter access helper returned invalid JSON.')
    exit 1
  }
}

function Test-OpenRouterRouteRecord {
  param([object]$Value, [string]$ExpectedRoute = '')
  if ($null -eq $Value) { return $false }
  foreach ($field in @('route', 'model', 'endpoint_provider', 'provider_slug', 'quantization')) {
    $property = $Value.PSObject.Properties[$field]
    if ($null -eq $property -or -not ($property.Value -is [string]) -or -not $property.Value) {
      return $false
    }
  }
  return (-not $ExpectedRoute -or [string]$Value.route -ceq $ExpectedRoute)
}

function Resolve-OpenRouterRootRoute {
  param([string]$Route)
  $resolved = Invoke-AccessJson -PolicyArguments @('openrouter-resolve', $Route)
  if (-not (Test-OpenRouterRouteRecord -Value $resolved -ExpectedRoute $Route)) {
    [Console]::Error.WriteLine('airlock: OpenRouter route resolver returned an invalid result.')
    exit 1
  }
  return $resolved
}

function Select-OpenRouterRootRoute {
  $interactive = [Environment]::UserInteractive
  try {
    $interactive = $interactive -and -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected
  } catch { }
  if (-not $interactive) {
    [Console]::Error.WriteLine('airlock: an OpenRouter route is required outside an interactive terminal; use airlock opr ROUTE.')
    exit 2
  }
  $routes = @(Invoke-AccessJson -PolicyArguments @('openrouter-routes'))
  foreach ($route in $routes) {
    if (-not (Test-OpenRouterRouteRecord -Value $route)) {
      [Console]::Error.WriteLine('airlock: OpenRouter route list returned an invalid result.')
      exit 1
    }
  }
  if ($routes.Count -eq 0) {
    [Console]::Error.WriteLine('airlock: no enabled OpenRouter routes are available; add one with airlock openrouter models add or add-preset.')
    exit 2
  }
  Write-Host 'Choose the exact OpenRouter root route:'
  for ($index = 0; $index -lt $routes.Count; $index++) {
    $route = $routes[$index]
    Write-Host ("  {0}) {1} ({2}; {3}; {4}/{5})" -f ($index + 1), $route.route, $route.model, $route.endpoint_provider, $route.provider_slug, $route.quantization)
  }
  $selection = Read-Host "Selection [1-$($routes.Count)]"
  $selectedIndex = 0
  if (-not [int]::TryParse($selection, [ref]$selectedIndex) -or $selectedIndex -lt 1 -or $selectedIndex -gt $routes.Count) {
    [Console]::Error.WriteLine('airlock: invalid OpenRouter route selection.')
    exit 2
  }
  return [string]$routes[$selectedIndex - 1].route
}

function Invoke-OpenRouterAuth {
  param([string[]]$AuthArguments, [ref]$Result)
  $Result.Value = 1
  if (-not (Test-Path -LiteralPath $OpenRouterAuthHelper -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed OpenRouter credential helper is missing: $OpenRouterAuthHelper")
    return
  }
  $helperItem = Get-Item -LiteralPath $OpenRouterAuthHelper
  if (($helperItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    [Console]::Error.WriteLine("airlock: managed OpenRouter credential helper is unsafe: $OpenRouterAuthHelper")
    return
  }
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python is required for OpenRouter credential storage.')
    return
  }
  & $python $OpenRouterAuthHelper @AuthArguments
  $Result.Value = $LASTEXITCODE
}

function Invoke-OpenRouterModels {
  param([string[]]$ModelArguments, [ref]$Result)
  $Result.Value = 1
  if (-not (Test-Path -LiteralPath $OpenRouterModelsHelper -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed OpenRouter model helper is missing: $OpenRouterModelsHelper")
    return
  }
  $helperItem = Get-Item -LiteralPath $OpenRouterModelsHelper
  if (($helperItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    [Console]::Error.WriteLine("airlock: managed OpenRouter model helper is unsafe: $OpenRouterModelsHelper")
    return
  }
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python is required for OpenRouter model registry management.')
    return
  }
  & $python $OpenRouterModelsHelper --registry $OpenRouterRegistryFile @ModelArguments
  $Result.Value = $LASTEXITCODE
}

function Test-FastRootModel {
  param([string]$Model, [switch]$Ephemeral)
  $route = if ($Model -eq 'gpt-5.6-sol-fast') {
    'sol-fast'
  } elseif ($Model -eq 'gpt-5.6-luna-fast') {
    'luna-fast'
  } else {
    return
  }
  $policyArguments = @('fast-check', '--route', $route, '--quiet')
  if ($Ephemeral) { $policyArguments += '--ephemeral' }
  $exitCode = Invoke-AccessPolicy -PolicyArguments $policyArguments
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
    '--component', "bin/airlock_policy.py=$PolicyHelper",
    '--component', "bin/airlock_openrouter_auth.py=$OpenRouterAuthHelper",
    '--component', "bin/airlock_openrouter_presets.py=$OpenRouterPresetsHelper",
    '--component', "bin/airlock_openrouter_models.py=$OpenRouterModelsHelper",
    '--component', "bin/airlock-update.py=$UpdateHelper",
    '--component', "bin/airlock-router.py=$RouterHelper",
    '--component', "bin/airlock-hybrid.py=$(Join-Path $ManagedBinDir 'airlock-hybrid.py')",
    '--component', "config/openai-direct-agents.json=$OpenAIDirectAgentsFile",
    '--component', "config/anthropic-direct-agents.json=$AnthropicDirectAgentsFile",
    '--component', "config/hybrid-agents.json=$OpenAIWrapperAgentsFile",
    '--component', "config/claude-agents.json=$AnthropicWrapperAgentsFile",
    '--component', "config/grok-agents.json=$GrokAgentsFile",
    '--component', "plugins/airlock/.claude-plugin/plugin.json=$(Join-Path $PluginDir '.claude-plugin\plugin.json')",
    '--component', "plugins/airlock/hooks/hooks.json=$(Join-Path $PluginDir 'hooks\hooks.json')",
    '--component', "plugins/airlock/skills/usage/SKILL.md=$(Join-Path $PluginDir 'skills\usage\SKILL.md')",
    '--component', "plugins/airlock/skills/airlock-fast/SKILL.md=$(Join-Path $PluginDir 'skills\airlock-fast\SKILL.md')",
    '--component', "plugins/airlock/scripts/fast-session-end.sh=$(Join-Path $PluginDir 'scripts\fast-session-end.sh')",
    '--component', "plugins/airlock/scripts/fast-session-end.py=$(Join-Path $PluginDir 'scripts\fast-session-end.py')",
    '--component', "plugins/airlock/scripts/agent-guard.sh=$(Join-Path $PluginDir 'scripts\agent-guard.sh')",
    '--component', "plugins/airlock/scripts/agent-guard.py=$(Join-Path $PluginDir 'scripts\agent-guard.py')",
    '--component', "plugins/airlock/scripts/secret-guard.sh=$(Join-Path $PluginDir 'scripts\secret-guard.sh')",
    '--component', "plugins/airlock/scripts/secret-guard.py=$(Join-Path $PluginDir 'scripts\secret-guard.py')",
    '--component', "plugins/airlock/scripts/update-notice.sh=$(Join-Path $PluginDir 'scripts\update-notice.sh')",
    '--component', "plugins/airlock/scripts/update-notice.py=$(Join-Path $PluginDir 'scripts\update-notice.py')",
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

function Invoke-ProxyCommand {
  param([string[]]$ProxyArguments)
  $proxy = Get-Command claude-code-proxy -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $proxy) { $proxy = Get-Command claude-code-proxy.exe -ErrorAction SilentlyContinue | Select-Object -First 1 }
  if (-not $proxy) {
    [Console]::Error.WriteLine('airlock: claude-code-proxy is not on PATH.')
    return 1
  }
  $oldConfigDir = [Environment]::GetEnvironmentVariable('CCP_CONFIG_DIR', 'Process')
  $oldStateHome = [Environment]::GetEnvironmentVariable('XDG_STATE_HOME', 'Process')
  try {
    if ($ProxyConfigDir) { $env:CCP_CONFIG_DIR = $ProxyConfigDir }
    if ($ProxyStateHome) { $env:XDG_STATE_HOME = $ProxyStateHome }
    & $proxy.Source @ProxyArguments
    return $LASTEXITCODE
  } finally {
    if ($null -eq $oldConfigDir) { Remove-Item Env:CCP_CONFIG_DIR -ErrorAction SilentlyContinue } else { $env:CCP_CONFIG_DIR = $oldConfigDir }
    if ($null -eq $oldStateHome) { Remove-Item Env:XDG_STATE_HOME -ErrorAction SilentlyContinue } else { $env:XDG_STATE_HOME = $oldStateHome }
  }
}

function Test-ProxyHealth {
  try {
    Invoke-WebRequest -Uri "$ProxyUrl/healthz" -TimeoutSec 1 -UseBasicParsing | Out-Null
    return $true
  } catch { return $false }
}

function Start-ProxyIfNeeded {
  if ($env:AIRLOCK_SKIP_HEALTH_CHECK -eq '1') { return }
  if (Test-ProxyHealth) { return }
  $proxyExe = (Get-Command claude-code-proxy.exe -ErrorAction SilentlyContinue).Source
  if (-not $proxyExe) { $proxyExe = Join-Path $HOME '.local\bin\claude-code-proxy.exe' }
  if (Test-Path -LiteralPath $proxyExe -PathType Leaf) {
    $logDir = Join-Path $HOME '.local\state\claude-code-proxy'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $oldConfigDir = [Environment]::GetEnvironmentVariable('CCP_CONFIG_DIR', 'Process')
    $oldStateHome = [Environment]::GetEnvironmentVariable('XDG_STATE_HOME', 'Process')
    try {
      if ($ProxyConfigDir) { $env:CCP_CONFIG_DIR = $ProxyConfigDir }
      if ($ProxyStateHome) { $env:XDG_STATE_HOME = $ProxyStateHome }
      Start-Process -FilePath $proxyExe -ArgumentList 'serve','--no-monitor' -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir 'service.out.log') `
        -RedirectStandardError  (Join-Path $logDir 'service.err.log')
    } finally {
      if ($null -eq $oldConfigDir) { Remove-Item Env:CCP_CONFIG_DIR -ErrorAction SilentlyContinue } else { $env:CCP_CONFIG_DIR = $oldConfigDir }
      if ($null -eq $oldStateHome) { Remove-Item Env:XDG_STATE_HOME -ErrorAction SilentlyContinue } else { $env:XDG_STATE_HOME = $oldStateHome }
    }
  }
  for ($i = 0; $i -lt 50; $i++) {
    if (Test-ProxyHealth) { return }
    Start-Sleep -Milliseconds 200
  }
  Write-Error "airlock: proxy did not become healthy at $ProxyUrl/healthz"
  exit 1
}

# Claude Code decides whether a model supports effort by matching the model ID
# against known Anthropic patterns. A pinned GPT or Grok ID matches nothing,
# which would leave /effort unavailable, so declare the levels explicitly. Only
# do this for non-Claude IDs: declaring capabilities for a real Claude ID would
# disable every capability left off the list, and built-in detection already
# gets those right.
function Set-GptEffortCapabilities {
  param([string]$Variable, [string]$Model)
  if (-not $Model -or $Model.StartsWith('claude-')) { return }
  Set-Item -LiteralPath "Env:${Variable}_SUPPORTED_CAPABILITIES" -Value $GptEffortCapabilities
}

function Set-OpenAIEnvironment {
  param([string]$Model, [string]$ModelName, [string]$ProviderLabel = 'OpenAI subscription')
  Start-ProxyIfNeeded
  Remove-Item Env:\AIRLOCK_SESSION_ROUTER_URL -ErrorAction SilentlyContinue
  $env:ANTHROPIC_BASE_URL   = $ProxyUrl
  $env:ANTHROPIC_AUTH_TOKEN = 'unused'
  $env:ANTHROPIC_MODEL      = $Model
  $env:ANTHROPIC_DEFAULT_FABLE_MODEL = $Model
  $env:ANTHROPIC_DEFAULT_OPUS_MODEL = $Model
  $env:ANTHROPIC_DEFAULT_SONNET_MODEL = $Model
  $env:ANTHROPIC_DEFAULT_HAIKU_MODEL = $SmallFast
  $env:ANTHROPIC_SMALL_FAST_MODEL = $SmallFast
  $env:ANTHROPIC_CUSTOM_MODEL_OPTION = $Model
  $env:ANTHROPIC_CUSTOM_MODEL_OPTION_NAME = "$ModelName ($ProviderLabel)"
  $env:ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION = "Selected Airlock root ($Model)"
  Set-GptEffortCapabilities 'ANTHROPIC_DEFAULT_FABLE_MODEL' $Model
  Set-GptEffortCapabilities 'ANTHROPIC_DEFAULT_OPUS_MODEL' $Model
  Set-GptEffortCapabilities 'ANTHROPIC_DEFAULT_SONNET_MODEL' $Model
  Set-GptEffortCapabilities 'ANTHROPIC_DEFAULT_HAIKU_MODEL' $SmallFast
  Set-GptEffortCapabilities 'ANTHROPIC_CUSTOM_MODEL_OPTION' $Model
  # OpenAI and Grok custom roots keep the conservative process-wide fallback
  # unless the user explicitly exports another value or selects auto.
  if ($UserContextWin) {
    $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = $UserContextWin
  } elseif ($ExplicitContextWin -and $ContextWin -ne 'auto') {
    $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = $ContextWin
  } elseif ($ContextWin -eq 'auto') {
    Remove-Item Env:\CLAUDE_CODE_AUTO_COMPACT_WINDOW -ErrorAction SilentlyContinue
  } else {
    $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = $ContextWin
  }
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

function Resolve-GrokAlias {
  param([string]$Value)
  switch ($Value) {
    'grok' { return 'grok' }
    'grok-4.5' { return 'grok' }
    'composer' { return 'composer' }
    'grok-composer' { return 'composer' }
    'grok-composer-2.5-fast' { return 'composer' }
  }
  return $null
}

function Get-ModelProvider {
  param([string]$Model)
  if ($Model -like 'gpt-*') { return 'openai' }
  if ($Model -like 'claude-*') { return 'anthropic' }
  if ($Model -like 'grok-*') { return 'grok' }
  return $null
}

function Resolve-OpenAIAlias {
  param([string]$Model)
  $Model = Normalize-OpenAIModelId $Model
  if ($Models.ContainsKey($Model)) { return $Model }
  foreach ($alias in $Models.Keys) {
    $exact = [string]$Models[$alias][0]
    if ($Model -eq $exact) { return $alias }
  }
  return $null
}

function Add-DefaultEffort {
  param([string[]]$ChildArguments, [string]$Effort)
  foreach ($argument in $ChildArguments) {
    if ($argument -eq '--effort' -or $argument -like '--effort=*') { return $ChildArguments }
  }
  return @('--effort', $Effort) + $ChildArguments
}

function Get-SessionFastMode {
  param([string]$RootModel)
  # The root model carries a [1m] suffix on the models that need it, so this
  # gate has to compare the base name rather than the whole string.
  if (($RootModel -replace '\[1m\]$', '') -ne 'claude-opus-5' -or $AnthropicFast -ne 'on') { return 'off' }
  switch ($ExtraUsagePolicy) {
    'allow' { return 'on' }
    'never' {
      [Console]::Error.WriteLine('airlock: Anthropic Fast uses paid usage credits and is blocked by the extra-usage policy.')
      exit 2
    }
    'ask' {
      if ($env:AIRLOCK_ANTHROPIC_FAST_AUTHORIZED -eq 'yes') { return 'on' }
      $interactive = [Environment]::UserInteractive
      try { $interactive = $interactive -and -not [Console]::IsInputRedirected } catch { }
      if (-not $interactive) {
        [Console]::Error.WriteLine('airlock: Anthropic Fast needs confirmation because it uses paid usage credits. Set AIRLOCK_ANTHROPIC_FAST_AUTHORIZED=yes for this launch or use an interactive terminal.')
        exit 2
      }
      $answer = Read-Host 'Anthropic Fast uses paid usage credits from the first token. Enable it for this Opus session? [y/N]'
      if ($answer -in @('y', 'Y', 'yes', 'YES', 'Yes')) { return 'on' }
      [Console]::Error.WriteLine('airlock: Anthropic Fast launch cancelled.')
      exit 2
    }
    default {
      [Console]::Error.WriteLine("airlock: unsupported extra-usage policy '$ExtraUsagePolicy'")
      exit 2
    }
  }
}

function Invoke-AirlockSession {
  param(
    [string]$Profile,
    [string]$RootModel,
    [string]$RootName,
    [string[]]$ChildArguments,
    [string]$OpenRouterRootRoute = ''
  )

  if ($Profile -eq 'openrouter-pure') {
    if (-not $OpenRouterRootRoute) {
      [Console]::Error.WriteLine('airlock: openrouter-pure requires an exact OpenRouter root route.')
      exit 2
    }
    if ($RootModel -or $RootName) {
      [Console]::Error.WriteLine('airlock: the OpenRouter root identity must be derived by the managed bridge.')
      exit 2
    }
    foreach ($argument in $ChildArguments) {
      if ($argument -in @('--model', '-m') -or $argument -like '--model=*') {
        [Console]::Error.WriteLine('airlock: OpenRouter roots are selected by exact registry route; --model and -m cannot be forwarded.')
        exit 2
      }
    }
  } elseif ($OpenRouterRootRoute) {
    [Console]::Error.WriteLine('airlock: an OpenRouter root route is valid only for openrouter-pure.')
    exit 2
  }

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
  $env:AIRLOCK_UPDATE_NOTICE_FILE = [IO.Path]::GetFullPath($UpdateNoticeFile)
  $env:AIRLOCK_ACCESS_HELPER = [IO.Path]::GetFullPath($AccessHelper)
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
    grok_direct = [IO.Path]::GetFullPath($GrokAgentsFile)
    grok_wrappers = [IO.Path]::GetFullPath($GrokAgentsFile)
  }
  $launchDirectory = Join-Path $env:LOCALAPPDATA 'Airlock\launch'
  New-Item -ItemType Directory -Force -Path $launchDirectory | Out-Null
  $launchRequestPath = Join-Path $launchDirectory ("{0}.json" -f [Guid]::NewGuid().ToString('N'))
  $transitionChannel = ''
  $transitionNonce = ''
  $launchCwd = [IO.Path]::GetFullPath((Get-Location).Path)
  $transitionRaw = @(& $pythonBin $AccessHelper 'fast-transition-create' '--launcher-pid' ([string]$PID) '--cwd' $launchCwd)
  if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath 'Env:AIRLOCK_FAST_TRANSITION_CHANNEL' -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Env:AIRLOCK_FAST_TRANSITION_NONCE' -ErrorAction SilentlyContinue
    exit $LASTEXITCODE
  }
  $transitionLine = $transitionRaw -join "`n"
  $transitionParts = $transitionLine -split "`t", -1
  if ($transitionParts.Count -ne 2 -or -not $transitionParts[0] -or -not $transitionParts[1] -or $transitionLine.Contains("`n")) {
    Remove-Item -LiteralPath 'Env:AIRLOCK_FAST_TRANSITION_CHANNEL' -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Env:AIRLOCK_FAST_TRANSITION_NONCE' -ErrorAction SilentlyContinue
    [Console]::Error.WriteLine('airlock: Fast handoff helper returned invalid channel metadata.')
    exit 1
  }
  $transitionChannel = [string]$transitionParts[0]
  $transitionNonce = [string]$transitionParts[1]
  $env:AIRLOCK_FAST_TRANSITION_CHANNEL = $transitionChannel
  $env:AIRLOCK_FAST_TRANSITION_NONCE = $transitionNonce
  $sessionFastMode = Get-SessionFastMode -RootModel $RootModel
  $launchRequest = [ordered]@{
    profile = $Profile
    claude = [string]$claudeBin
    catalog_files = $catalogFiles
    plugin_dir = [IO.Path]::GetFullPath($PluginDir)
    router_helper = [IO.Path]::GetFullPath($RouterHelper)
    context_window = [string]$ContextWin
    force_context_window = [bool]$ExplicitContextWin
    max_agents = [string]$MaxAgents
    fast_mode = [string]$sessionFastMode
    fast_transition_launcher_pid = [int64]$PID
    fast_transition_cwd = [string]$launchCwd
    args = [string[]]$ChildArguments
  }
  if ($Profile -eq 'openrouter-pure') {
    $launchRequest['openrouter_root_route'] = [string]$OpenRouterRootRoute
  } else {
    $launchRequest['proxy_url'] = [string]$ProxyUrl
    $launchRequest['root_model'] = [string]$RootModel
    $launchRequest['root_name'] = [string]$RootName
  }
  $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
  try {
    [IO.File]::WriteAllText(
      $launchRequestPath,
      (ConvertTo-Json -InputObject $launchRequest -Depth 4 -Compress),
      $utf8WithoutBom
    )

    & $pythonBin $launcher --request-file $launchRequestPath
    $exitCode = $LASTEXITCODE
  } finally {
    Remove-Item -LiteralPath $launchRequestPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Env:AIRLOCK_FAST_TRANSITION_CHANNEL' -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Env:AIRLOCK_FAST_TRANSITION_NONCE' -ErrorAction SilentlyContinue
    if ($transitionChannel -and $transitionNonce) {
      & $pythonBin $AccessHelper 'fast-transition-cleanup' '--launcher-pid' ([string]$PID) '--cwd' $launchCwd '--channel' $transitionChannel '--nonce' $transitionNonce 2>$null
    }
  }
  exit $exitCode
}

function Select-HybridRoot {
  $interactive = [Environment]::UserInteractive
  try { $interactive = $interactive -and -not [Console]::IsInputRedirected } catch { }
  if (-not $interactive) {
    [Console]::Error.WriteLine('airlock: hybrid choose needs an interactive terminal; otherwise use airlock hybrid MODEL')
    exit 2
  }
  Write-Host 'Choose the Airlock hybrid orchestrator:'
  Write-Host '  1) Claude Sonnet 5 (claude-sonnet-5[1m])'
  Write-Host '  2) GPT-5.6 Sol (gpt-5.6-sol)'
  Write-Host '  3) GPT-5.6 Terra (gpt-5.6-terra)'
  Write-Host '  4) GPT-5.6 Luna (gpt-5.6-luna)'
  Write-Host '  5) Claude Opus 5 (claude-opus-5[1m])'
  Write-Host '  6) Claude Fable 5 (claude-fable-5[1m]; may use extra usage)'
  Write-Host '  7) Claude Haiku 4.5 (claude-haiku-4-5-20251001)'
  Write-Host '  8) Grok 4.5 (grok-4.5; requires Grok OAuth)'
  Write-Host '  9) Grok Composer 2.5 Fast (grok-composer-2.5-fast; requires Grok OAuth)'
  $selection = Read-Host 'Selection [1-9]'
  $choices = @{ '1' = 'sonnet'; '2' = 'sol'; '3' = 'terra'; '4' = 'luna'; '5' = 'opus'; '6' = 'fable'; '7' = 'haiku'; '8' = 'grok'; '9' = 'composer' }
  if (-not $choices.ContainsKey($selection)) {
    [Console]::Error.WriteLine('airlock: invalid hybrid root selection.')
    exit 2
  }
  return $choices[$selection]
}

if ($DefaultProfile -notin @('openai', 'hybrid', 'grok')) {
  [Console]::Error.WriteLine("airlock: unsupported default profile '$DefaultProfile' (expected openai, hybrid, or grok)")
  exit 2
}
if (-not (Resolve-GrokAlias $DefaultGrokModel)) {
  [Console]::Error.WriteLine("airlock: unsupported saved Grok model '$DefaultGrokModel'")
  exit 2
}
if ($OpenAIFast -notin @('on', 'off')) {
  [Console]::Error.WriteLine("airlock: unsupported OpenAI Fast policy '$OpenAIFast' (expected on or off)")
  exit 2
}
if ($AnthropicFast -notin @('on', 'off')) {
  [Console]::Error.WriteLine("airlock: unsupported Anthropic Fast policy '$AnthropicFast' (expected on or off)")
  exit 2
}
if ($ExtraUsagePolicy -notin @('ask', 'never', 'allow')) {
  [Console]::Error.WriteLine("airlock: unsupported extra-usage policy '$ExtraUsagePolicy'")
  exit 2
}
$DefaultOpenAIAlias = Resolve-OpenAIAlias $DefaultOpenAIModel
if (-not $DefaultOpenAIAlias) {
  [Console]::Error.WriteLine("airlock: unsupported saved OpenAI model '$DefaultOpenAIModel'")
  exit 2
}
$DefaultOpenAIModel = $DefaultOpenAIAlias
$hybridOpenAIAlias = Resolve-OpenAIAlias $DefaultHybridModel
if (-not $HybridRoots.ContainsKey($DefaultHybridModel) -and $hybridOpenAIAlias -notin @('sol', 'terra', 'luna')) {
  [Console]::Error.WriteLine("airlock: unsupported saved hybrid model '$DefaultHybridModel'")
  exit 2
}
if ($hybridOpenAIAlias -in @('sol', 'terra', 'luna')) { $DefaultHybridModel = $hybridOpenAIAlias }
$DefaultBgAlias = Resolve-OpenAIAlias $DefaultBgModel
if (-not $DefaultBgAlias) {
  [Console]::Error.WriteLine("airlock: unsupported saved background model '$DefaultBgModel'")
  exit 2
}
$DefaultBgModel = $DefaultBgAlias
if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'orp') {
  [Console]::Error.WriteLine("airlock: command 'orp' was renamed to 'opr'; use airlock opr.")
  exit 2
}
$ExplicitCommands = @(
  'mode', 'usage', 'session-usage', 'bundle', 'access', 'openrouter', 'opr', 'proxy', 'models', '--models', 'config', '--config',
  'version', 'update', 'hybrid', 'openai', 'grok', 'fast', 'bg', 'background', 'sol', 'sol-fast', 'terra',
  'luna', '5.5', '5.4', 'mini', '5.3', 'spark', '5.2'
)
if ($DefaultProfile -eq 'grok') {
  $firstArgument = if ($Arguments.Count -gt 0) { $Arguments[0] } else { '' }
  if ($firstArgument -notin $ExplicitCommands) {
    if ($firstArgument -in @('--model', '-m') -or $firstArgument -like '--model=*') {
      $Arguments = @('grok') + $Arguments
    } else {
      $Arguments = @('grok', $DefaultGrokModel) + $Arguments
    }
  }
}
if ($DefaultProfile -eq 'hybrid') {
  $firstArgument = if ($Arguments.Count -gt 0) { $Arguments[0] } else { '' }
  $explicitCommands = $ExplicitCommands
  if ($firstArgument -notin $explicitCommands) {
    if ($firstArgument -in @('--model', '-m') -or $firstArgument -like '--model=*') {
      $Arguments = @('hybrid') + $Arguments
    } else {
      $Arguments = @('hybrid', $DefaultHybridModel) + $Arguments
    }
  }
}

if ($Arguments.Count -gt 0) {
  if ($Arguments[0] -eq 'background') { $Arguments[0] = 'bg' }
  if ($Arguments[0] -eq '--config') { $Arguments[0] = 'config' }
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

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'session-usage') {
  if (-not $env:AIRLOCK_SESSION_ROUTER_URL) {
    [Console]::Error.WriteLine('airlock session-usage: this command is available only inside an active hybrid Airlock session.')
    exit 1
  }
  $sessionUsageArguments = @('session-usage', '--router-url', $env:AIRLOCK_SESSION_ROUTER_URL)
  if ($Arguments.Count -gt 1) {
    $sessionUsageArguments += @($Arguments[1..($Arguments.Count - 1)])
  }
  exit (Invoke-AccessPolicy -PolicyArguments $sessionUsageArguments)
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'version') {
  if ($Arguments.Count -ne 1) {
    [Console]::Error.WriteLine('airlock: usage: airlock version')
    exit 2
  }
  if ((Test-ManagedBundle) -ne 0) { exit 1 }
  if (-not (Test-Path -LiteralPath $UpdateHelper -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed update helper is missing: $UpdateHelper")
    exit 1
  }
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python is required for release updates.')
    exit 1
  }
  & $python $UpdateHelper version --manifest (Join-Path $PluginDir '.claude-plugin\plugin.json')
  exit $LASTEXITCODE
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'update') {
  $updateOption = if ($Arguments.Count -eq 2) { $Arguments[1] } else { $null }
  if ($Arguments.Count -gt 2 -or ($Arguments.Count -eq 2 -and $updateOption -notin @('--check', '--yes', '--help', '-h'))) {
    [Console]::Error.WriteLine('airlock: usage: airlock update [--check|--yes|--help]')
    exit 2
  }
  if ((Test-ManagedBundle) -ne 0) { exit 1 }
  if (-not (Test-Path -LiteralPath $UpdateHelper -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed update helper is missing: $UpdateHelper")
    exit 1
  }
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python is required for release updates.')
    exit 1
  }
  $updateArguments = @(
    'update', '--manifest', (Join-Path $PluginDir '.claude-plugin\plugin.json'),
    '--notice-file', $UpdateNoticeFile
  )
  if ($updateOption) { $updateArguments += $updateOption }
  & $python $UpdateHelper @updateArguments
  exit $LASTEXITCODE
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

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'openrouter') {
  $openRouterRest = @()
  if ($Arguments.Count -gt 1) { $openRouterRest = @($Arguments[1..($Arguments.Count - 1)]) }
  if ($openRouterRest.Count -eq 0) {
    [Console]::Error.WriteLine('airlock: usage: airlock openrouter auth|models ...')
    exit 2
  }
  if ($openRouterRest[0] -eq 'models') {
    $modelRest = @()
    if ($openRouterRest.Count -gt 1) { $modelRest = @($openRouterRest[1..($openRouterRest.Count - 1)]) }
    $modelExitCode = 1
    Invoke-OpenRouterModels -ModelArguments $modelRest -Result ([ref]$modelExitCode)
    exit $modelExitCode
  }
  if ($openRouterRest[0] -ne 'auth') {
    [Console]::Error.WriteLine('airlock: usage: airlock openrouter auth|models ...')
    exit 2
  }
  $authAction = if ($openRouterRest.Count -gt 1) { $openRouterRest[1] } else { 'status' }
  $authRest = @()
  if ($openRouterRest.Count -gt 2) { $authRest = @($openRouterRest[2..($openRouterRest.Count - 1)]) }
  switch ($authAction) {
    'set-key' {
      if ($authRest.Count -gt 1 -or ($authRest.Count -eq 1 -and $authRest[0] -ne '--stdin')) {
        [Console]::Error.WriteLine('airlock: usage: airlock openrouter auth set-key [--stdin]')
        exit 2
      }
    }
    'status' {
      if ($authRest.Count -ne 0) {
        [Console]::Error.WriteLine('airlock: usage: airlock openrouter auth status')
        exit 2
      }
    }
    'logout' {
      if ($authRest.Count -gt 1 -or ($authRest.Count -eq 1 -and $authRest[0] -ne '--yes')) {
        [Console]::Error.WriteLine('airlock: usage: airlock openrouter auth logout [--yes]')
        exit 2
      }
    }
    default {
      [Console]::Error.WriteLine("airlock: unsupported OpenRouter auth action: $authAction")
      exit 2
    }
  }
  $authExitCode = 1
  Invoke-OpenRouterAuth -AuthArguments (@($authAction) + $authRest) -Result ([ref]$authExitCode)
  exit $authExitCode
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'opr') {
  $oprArguments = @()
  if ($Arguments.Count -gt 1) {
    $oprArguments = @($Arguments[1..($Arguments.Count - 1)])
  }
  if ($oprArguments.Count -gt 0 -and $oprArguments[0] -in @('--help', '-h')) {
    Write-Host 'Usage: airlock opr [ROUTE] [Claude arguments...]'
    Write-Host ''
    Write-Host 'Start an OpenRouter-only session on one exact enabled registry route.'
    Write-Host 'Omit ROUTE in an interactive terminal to choose from the offline registry.'
    Write-Host 'Use airlock openrouter auth and airlock openrouter models to manage access.'
    return
  }
  $openRouterRootRoute = ''
  if ($oprArguments.Count -gt 0 -and $oprArguments[0] -notlike '-*') {
    $openRouterRootRoute = [string]$oprArguments[0]
    $oprArguments = if ($oprArguments.Count -gt 1) { @($oprArguments[1..($oprArguments.Count - 1)]) } else { @() }
  } else {
    $openRouterRootRoute = Select-OpenRouterRootRoute
  }
  $null = Resolve-OpenRouterRootRoute -Route $openRouterRootRoute
  foreach ($argument in $oprArguments) {
    if ($argument -in @('--model', '-m') -or $argument -like '--model=*') {
      [Console]::Error.WriteLine('airlock: OpenRouter roots are selected by exact registry route; --model and -m cannot be forwarded.')
      exit 2
    }
  }
  foreach ($marker in @('AIRLOCK_HYBRID', 'AIRLOCK_GPT_HYBRID', 'AIRLOCK_GROK_HYBRID')) {
    Remove-Item -LiteralPath "Env:$marker" -ErrorAction SilentlyContinue
  }
  Invoke-AirlockSession 'openrouter-pure' '' '' $oprArguments $openRouterRootRoute
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'proxy') {
  $proxyRest = @()
  if ($Arguments.Count -gt 1) { $proxyRest = @($Arguments[1..($Arguments.Count - 1)]) }
  $proxyProvider = 'codex'
  if ($proxyRest.Count -gt 0 -and $proxyRest[0] -eq 'grok') {
    $proxyProvider = 'grok'
    if ($proxyRest.Count -gt 1) { $proxyRest = @($proxyRest[1..($proxyRest.Count - 1)]) } else { $proxyRest = @() }
  }
  if ($proxyRest.Count -lt 1 -or $proxyRest[0] -ne 'auth' -or $proxyRest.Count -gt 2) {
    [Console]::Error.WriteLine('airlock: usage: airlock proxy [grok] auth [status|login|device]')
    exit 2
  }
  $proxyAuthAction = if ($proxyRest.Count -eq 2) { $proxyRest[1] } else { 'status' }
  if ($proxyAuthAction -notin @('status', 'login', 'device')) {
    [Console]::Error.WriteLine("airlock: unsupported proxy auth action: $proxyAuthAction")
    exit 2
  }
  exit (Invoke-ProxyCommand -ProxyArguments @($proxyProvider, 'auth', $proxyAuthAction))
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'grok') {
  $grokArgs = @()
  if ($Arguments.Count -gt 1) { $grokArgs = @($Arguments[1..($Arguments.Count - 1)]) }
  $requestedGrok = $DefaultGrokModel
  if ($grokArgs.Count -gt 0) {
    if ($grokArgs[0] -eq '--model' -or $grokArgs[0] -eq '-m') {
      if ($grokArgs.Count -lt 2) {
        [Console]::Error.WriteLine("airlock: $($grokArgs[0]) requires a model")
        exit 2
      }
      $requestedGrok = $grokArgs[1]
      $grokArgs = if ($grokArgs.Count -gt 2) { @($grokArgs[2..($grokArgs.Count - 1)]) } else { @() }
    } elseif ($grokArgs[0] -like '--model=*') {
      $requestedGrok = $grokArgs[0].Substring('--model='.Length)
      if (-not $requestedGrok) {
        [Console]::Error.WriteLine('airlock: --model requires a model')
        exit 2
      }
      $grokArgs = if ($grokArgs.Count -gt 1) { @($grokArgs[1..($grokArgs.Count - 1)]) } else { @() }
    } elseif (Resolve-GrokAlias $grokArgs[0]) {
      $requestedGrok = $grokArgs[0]
      $grokArgs = if ($grokArgs.Count -gt 1) { @($grokArgs[1..($grokArgs.Count - 1)]) } else { @() }
    }
  }
  $grokAlias = Resolve-GrokAlias $requestedGrok
  if (-not $grokAlias) {
    [Console]::Error.WriteLine("airlock: unsupported Grok model '$requestedGrok' (expected grok or composer)")
    exit 2
  }
  $grokModel = $GrokModels[$grokAlias][0]
  $grokName = $GrokModels[$grokAlias][1]
  # Pure Grok needs enabled Grok routes even when the saved config leaves
  # them off, because the user asked for this provider by name.
  if ($null -eq $env:AIRLOCK_GROK_MODELS) { $env:AIRLOCK_GROK_MODELS = 'grok,composer' }
  Set-OpenAIEnvironment $grokModel $grokName 'Grok subscription'
  foreach ($marker in @('AIRLOCK_HYBRID', 'AIRLOCK_GPT_HYBRID', 'AIRLOCK_GROK_HYBRID')) {
    Remove-Item -LiteralPath "Env:$marker" -ErrorAction SilentlyContinue
  }
  $grokCmdArgs = @('--model', $grokModel)
  $grokCmdArgs += Add-DefaultEffort $grokArgs $MainEffort
  Invoke-AirlockSession 'grok-pure' $grokModel $grokName $grokCmdArgs
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'hybrid') {
  $hybridArgs = @()
  if ($Arguments.Count -gt 1) { $hybridArgs = @($Arguments[1..($Arguments.Count - 1)]) }
  $chooseRoot = $false
  if ($hybridArgs.Count -gt 0 -and $hybridArgs[0] -eq 'choose') {
    $chooseRoot = $true
    if ($hybridArgs.Count -gt 1) { $hybridArgs = @($hybridArgs[1..($hybridArgs.Count - 1)]) } else { $hybridArgs = @() }
  }

  $rootAlias = $null
  if ($hybridArgs.Count -gt 0) {
    $hybridCandidate = Normalize-OpenAIModelId $hybridArgs[0]
    if ($HybridRoots.ContainsKey($hybridCandidate)) {
      $rootAlias = $hybridCandidate
    } else {
      $hybridCandidateAlias = Resolve-OpenAIAlias $hybridCandidate
      if ($hybridCandidateAlias -in @('sol', 'terra', 'luna')) { $rootAlias = $hybridCandidateAlias }
    }
    if ($rootAlias) {
      if ($hybridArgs.Count -gt 1) { $hybridArgs = @($hybridArgs[1..($hybridArgs.Count - 1)]) } else { $hybridArgs = @() }
    }
  }

  for ($i = 0; $i -lt $hybridArgs.Count; $i++) {
    if ($hybridArgs[$i] -eq '--model' -or $hybridArgs[$i] -eq '-m') {
      if ($i + 1 -lt $hybridArgs.Count) { $hybridArgs[$i + 1] = Normalize-OpenAIModelId $hybridArgs[$i + 1] }
    } elseif ($hybridArgs[$i] -like '--model=*') {
      $hybridArgs[$i] = '--model=' + (Normalize-OpenAIModelId $hybridArgs[$i].Substring('--model='.Length))
    }
  }
  $explicitModel = Get-ExplicitModel $hybridArgs
  if (-not $rootAlias -and -not $explicitModel) {
    $rootAlias = if ($chooseRoot) { Select-HybridRoot } else { $DefaultHybridModel }
  }

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
      [Console]::Error.WriteLine('airlock: cannot determine the hybrid root provider from --model; use a gpt-*, claude-*, or grok-* model ID.')
      exit 2
    }
  }
  $hybridArgs = Add-DefaultEffort $hybridArgs $MainEffort

  Start-ProxyIfNeeded
  foreach ($marker in @('AIRLOCK_HYBRID', 'AIRLOCK_GPT_HYBRID', 'AIRLOCK_GROK_HYBRID')) {
    Remove-Item -LiteralPath "Env:$marker" -ErrorAction SilentlyContinue
  }
  if ($rootProvider -eq 'openai') {
    Test-FastRootModel $rootModel
    $env:AIRLOCK_GPT_HYBRID = '1'
    Invoke-AirlockSession 'hybrid-openai-root' $rootModel $rootName $hybridArgs
  }
  if ($rootProvider -eq 'grok') {
    # A Grok root cannot start without an enabled Grok route, so an explicit
    # Grok root turns the routes on. Every other hybrid root leaves Grok off
    # unless the saved config enables it.
    if ($null -eq $env:AIRLOCK_GROK_MODELS) { $env:AIRLOCK_GROK_MODELS = 'grok,composer' }
    $env:AIRLOCK_GROK_HYBRID = '1'
    Invoke-AirlockSession 'hybrid-grok-root' $rootModel $rootName $hybridArgs
  }

  $env:AIRLOCK_HYBRID = '1'
  Invoke-AirlockSession 'hybrid-anthropic-root' $rootModel $rootName $hybridArgs
}

$requested = $DefaultOpenAIModel
$effort = $MainEffort
$rest = @()
$ephemeralFast = $false

if ($Arguments.Count -gt 0) {
  switch ($Arguments[0]) {
    'models'   { Show-Models; return }
    '--models' { Show-Models; return }
    'config'   {
      $openAIName = "$($Models[$DefaultOpenAIModel][1]) ($($Models[$DefaultOpenAIModel][0]))"
      $hybridName = "$($HybridRoots[$DefaultHybridModel][1]) ($($HybridRoots[$DefaultHybridModel][0]))"
      $defaultName = if ($DefaultProfile -eq 'hybrid') { $hybridName } else { $openAIName }
      Write-Host "Config file: $ConfigFile"
      Write-Host "Default profile: $DefaultProfile"
      Write-Host "Default command: airlock -> $defaultName"
      Write-Host "Hybrid root: $hybridName"
      Write-Host "OpenAI root: $openAIName"
      Write-Host "Main effort: $MainEffort"
      Write-Host "Background command: airlock bg -> $($Models[$DefaultBgModel][1]) ($($Models[$DefaultBgModel][0])) / $BgEffort effort"
      Write-Host "Utility model: $SmallFast"
      if ($ContextWin -eq 'auto') {
        $contextWinDisplay = 'auto (Claude Code decides)'
      } elseif ($ExplicitContextWin) {
        $contextWinDisplay = "$ContextWin (explicit process-wide override)"
      } else {
        $contextWinDisplay = "$ContextWin (saved fallback for OpenAI and Grok roots)"
      }
      Write-Host "Context window: $contextWinDisplay"
      Write-Host "Proxy URL: $ProxyUrl"
      $proxyStorageDisplay = if ($ProxyConfigDir -or $ProxyStateHome) { 'configured proxy directories' } else { 'upstream defaults' }
      Write-Host "Proxy storage: $proxyStorageDisplay"
      Write-Host "OpenAI direct agents: $OpenAIDirectAgentsFile"
      Write-Host "Anthropic direct agents: $AnthropicDirectAgentsFile"
      Write-Host "OpenAI bridge agents: $OpenAIWrapperAgentsFile"
      Write-Host "Anthropic bridge agents: $AnthropicWrapperAgentsFile"
      Write-Host "Managed plugin: $PluginDir"
      Write-Host "Max concurrent top-level subagents: $MaxAgents"
      Write-Host "OpenAI Fast routes: $OpenAIFast"
      Write-Host "Anthropic Fast startup: $AnthropicFast (supported Opus roots only)"
      $accessExitCode = Invoke-AccessPolicy -PolicyArguments @('show')
      if ($accessExitCode -ne 0) { exit $accessExitCode }
      return
    }
    '--model' {
      if ($Arguments.Count -lt 2) {
        [Console]::Error.WriteLine('airlock: --model requires a model')
        exit 2
      }
      $requested = $Arguments[1]
      if ($Arguments.Count -gt 2) { $rest = @($Arguments[2..($Arguments.Count - 1)]) }
    }
    '-m' {
      if ($Arguments.Count -lt 2) {
        [Console]::Error.WriteLine('airlock: -m requires a model')
        exit 2
      }
      $requested = $Arguments[1]
      if ($Arguments.Count -gt 2) { $rest = @($Arguments[2..($Arguments.Count - 1)]) }
    }
    'openai' {
      if ($Arguments.Count -gt 1) {
        $openAIArgument = $Arguments[1]
        $openAIAlias = Resolve-OpenAIAlias $openAIArgument
        if ($openAIArgument -in @('--model', '-m')) {
          if ($Arguments.Count -lt 3) {
            [Console]::Error.WriteLine("airlock: $openAIArgument requires a model")
            exit 2
          }
          $requested = $Arguments[2]
          if ($Arguments.Count -gt 3) { $rest = @($Arguments[3..($Arguments.Count - 1)]) }
        } elseif ($openAIArgument -like '--model=*') {
          $requested = $openAIArgument.Substring('--model='.Length)
          if (-not $requested) {
            [Console]::Error.WriteLine('airlock: --model requires a model')
            exit 2
          }
          if ($Arguments.Count -gt 2) { $rest = @($Arguments[2..($Arguments.Count - 1)]) }
        } elseif ($openAIAlias) {
          $requested = $openAIAlias
          if ($Arguments.Count -gt 2) { $rest = @($Arguments[2..($Arguments.Count - 1)]) }
        } else {
          $rest = @($Arguments[1..($Arguments.Count - 1)])
        }
      }
    }
    'bg' {
      $requested = $DefaultBgModel
      $effort = $BgEffort
      if ($Arguments.Count -gt 1) { $rest = @($Arguments[1..($Arguments.Count - 1)]) }
    }
    'fast' {
      $requested = 'sol-fast'
      $ephemeralFast = $true
      if ($Arguments.Count -gt 1) {
        $rest = @($Arguments[1..($Arguments.Count - 1)])
        foreach ($argument in $rest) {
          if ($argument -in @('--model', '-m') -or $argument -like '--model=*') {
            [Console]::Error.WriteLine("airlock: Fast uses the fixed gpt-5.6-sol-fast root; $argument cannot be forwarded.")
            exit 2
          }
        }
      }
    }
    default {
      if ($Arguments[0] -like '--model=*') {
        $requested = $Arguments[0].Substring('--model='.Length)
        if (-not $requested) {
          [Console]::Error.WriteLine('airlock: --model requires a model')
          exit 2
        }
        if ($Arguments.Count -gt 1) { $rest = @($Arguments[1..($Arguments.Count - 1)]) }
      } elseif ($Models.ContainsKey($Arguments[0])) {
        $requested = $Arguments[0]
        if ($Arguments.Count -gt 1) { $rest = @($Arguments[1..($Arguments.Count - 1)]) }
      } else {
        $rest = $Arguments
      }
    }
  }
}

if ($Arguments.Count -ge 2 -and $Arguments[0] -eq 'fast' -and $Arguments[1] -eq '--arm') {
  if ($Arguments.Count -ne 4 -or $Arguments[2] -ne '--session-id' -or -not $Arguments[3]) {
    [Console]::Error.WriteLine('airlock: usage: airlock fast --arm --session-id SESSION_ID')
    exit 2
  }
  if ((Test-ManagedBundle) -ne 0) { exit 1 }
  Test-FastRootModel 'gpt-5.6-sol-fast' -Ephemeral
  exit (Invoke-AccessPolicy -PolicyArguments @('fast-transition-arm', '--session-id', [string]$Arguments[3]))
}

$requestedAlias = Resolve-OpenAIAlias $requested
if (-not $requestedAlias) {
  [Console]::Error.WriteLine("airlock: unsupported OpenAI model '$requested'")
  exit 2
}
$selected = $Models[$requestedAlias][0]
$modelName = $Models[$requestedAlias][1]
Test-FastRootModel $selected -Ephemeral:$ephemeralFast
Set-OpenAIEnvironment $selected $modelName
Remove-Item -LiteralPath 'Env:AIRLOCK_HYBRID' -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'Env:AIRLOCK_GPT_HYBRID' -ErrorAction SilentlyContinue

$cmdArgs = @('--model', $selected)
$cmdArgs += Add-DefaultEffort $rest $effort
Invoke-AirlockSession 'openai-pure' $selected $modelName $cmdArgs
