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
$AnthropicRateLimit = if ($env:AIRLOCK_ANTHROPIC_RATE_LIMIT) { $env:AIRLOCK_ANTHROPIC_RATE_LIMIT } elseif ($ConfigValues.ContainsKey('AIRLOCK_ANTHROPIC_RATE_LIMIT')) { $ConfigValues['AIRLOCK_ANTHROPIC_RATE_LIMIT'] } else { 'native' }
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
if ($AnthropicRateLimit -notin @('native', 'handoff')) {
  [Console]::Error.WriteLine("airlock: unsupported Anthropic rate-limit policy '$AnthropicRateLimit' (expected native or handoff).")
  exit 2
}
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
$OpenModelHelper = if ($env:AIRLOCK_OPENMODEL_HELPER) { $env:AIRLOCK_OPENMODEL_HELPER } else { Join-Path $PSScriptRoot 'airlock_openmodel.py' }
$OpenModelAdapterHelper = if ($env:AIRLOCK_OPENMODEL_ADAPTER_HELPER) { $env:AIRLOCK_OPENMODEL_ADAPTER_HELPER } else { Join-Path $PSScriptRoot 'airlock_openmodel_adapter.py' }
$OpenModelRegistryFile = if ($env:AIRLOCK_OPENMODEL_REGISTRY_FILE) { $env:AIRLOCK_OPENMODEL_REGISTRY_FILE } else { Join-Path $ConfigDir 'openmodel-registry.json' }
$RouterHelper = if ($env:AIRLOCK_ROUTER_HELPER) { $env:AIRLOCK_ROUTER_HELPER } else { Join-Path $PSScriptRoot 'airlock-router.py' }
$UpdateHelper = if ($env:AIRLOCK_UPDATE_HELPER) { $env:AIRLOCK_UPDATE_HELPER } else { Join-Path $PSScriptRoot 'airlock-update.py' }
$ManagedBinDir = if ($env:AIRLOCK_MANAGED_BIN_DIR) { $env:AIRLOCK_MANAGED_BIN_DIR } else { $PSScriptRoot }
$ConsoleHelper = if ($env:AIRLOCK_CONSOLE_HELPER) { $env:AIRLOCK_CONSOLE_HELPER } else { Join-Path $ManagedBinDir 'airlock_console.py' }
$ConsoleToolsHelper = Join-Path $ManagedBinDir 'airlock_console_tools.py'
$ConsoleHistoryHelper = Join-Path $ManagedBinDir 'airlock_console_history.py'
$ConsoleMcpHelper = Join-Path $PluginDir 'mcp-server\airlock_console_mcp.py'
$ManagedBundleFile = if ($env:AIRLOCK_MANAGED_BUNDLE_FILE) { $env:AIRLOCK_MANAGED_BUNDLE_FILE } else { Join-Path $ConfigDir 'managed-bundle.json' }
$UpdateNoticeFile = Join-Path $ConfigDir 'update-notice.json'

$Models = @{
  'astra'    = @('gpt-6-astra',        'GPT-6 Astra')
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
  'grok'     = @('grok-4.6', 'Grok 4.6')
  'composer' = @('grok-composer-2.5-fast', 'Grok Composer 2.5 Fast')
}

# Astra is off by default because it is the priciest OpenAI route. An explicit
# Astra root cannot start without its route, so selecting it turns the route
# on for this session, the way an explicit Grok root enables Grok. The rest of
# the saved pool is kept as it is, and other roots leave the pool alone.
function Enable-ExplicitOpenAIRoot {
  param([string]$RootModel)
  if ($RootModel -ne 'gpt-6-astra') { return }
  $pool = if ($null -ne $env:AIRLOCK_OPENAI_MODELS) { [string]$env:AIRLOCK_OPENAI_MODELS }
    elseif ($ConfigValues.ContainsKey('AIRLOCK_OPENAI_MODELS')) { $ConfigValues['AIRLOCK_OPENAI_MODELS'] }
    else { '' }
  if ((",$pool,") -like '*,astra,*') { return }
  # No saved pool means the helper's defaults, which enable every OpenAI
  # route except Astra; name them so nothing else changes.
  if (-not $pool) { $pool = 'sol,terra,luna,luna-fast' }
  $env:AIRLOCK_OPENAI_MODELS = "astra,$pool"
}

# Roots whose documented window exceeds the conservative fallback:
# wire ID -> @(hard limit, compaction threshold at 80%). Mirrors
# bin/airlock and bin/airlock-hybrid.py; tests/test-windows.ps1 guards parity.
$DeclaredContextLimits = @{
  'grok-4.6'    = @('500000', '400000')
  'gpt-6-astra' = @('922000', '736000')
}

$HybridRoots = @{
  'astra'  = @('gpt-6-astra', 'GPT-6 Astra', 'openai')
  'sol'    = @('gpt-5.6-sol', 'GPT-5.6 Sol', 'openai')
  'terra'  = @('gpt-5.6-terra', 'GPT-5.6 Terra', 'openai')
  'luna'   = @('gpt-5.6-luna', 'GPT-5.6 Luna', 'openai')
  # Claude Code only grants these models their native 1M window when
  # ANTHROPIC_BASE_URL is unset or points at api.anthropic.com, and Airlock
  # always points it at the session router. The [1m] suffix is the one lever
  # that survives that. Haiku 4.5 is a genuine 200000 model, so it stays bare.
  'opus'   = @('claude-opus-5[1m]', 'Claude Opus 5', 'anthropic')
  'sonnet' = @('claude-sonnet-5[1m]', 'Claude Sonnet 5', 'anthropic')
  'fable'  = @('claude-fable-5-1[1m]', 'Claude Fable 5.1', 'anthropic')
  'haiku'  = @('claude-haiku-4-5-20251001', 'Claude Haiku 4.5', 'anthropic')
  'grok'     = @('grok-4.6', 'Grok 4.6', 'grok')
  'grok-4.6' = @('grok-4.6', 'Grok 4.6', 'grok')
  'grok-4.5' = @('grok-4.6', 'Grok 4.6', 'grok')
  'composer' = @('grok-composer-2.5-fast', 'Grok Composer 2.5 Fast', 'grok')
}

$CustomModelRecords = @{}
$CustomOpenRouterRoutes = @{}

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
  'CLAUDE_CODE_AUTO_COMPACT_WINDOW', 'CLAUDE_CODE_MAX_CONTEXT_TOKENS',
  'CLAUDE_CODE_ALWAYS_ENABLE_EFFORT',
  'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
  'CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK', 'CLAUDE_CODE_SUBAGENT_MODEL'
)

function Show-Models {
  Write-Host 'Usage: airlock [claude arguments]'
  Write-Host '       airlock console [--port N] [--no-open] [--scan] [--once]'
  Write-Host '       airlock openai [model] [claude arguments]'
  Write-Host '       airlock grok [model] [claude arguments]'
  Write-Host '       airlock hybrid [model|choose] [claude arguments]'
  Write-Host '       airlock opr [route] [claude arguments]'
  Write-Host '       airlock om [route] [claude arguments]'
  Write-Host '       airlock [sol|terra|luna|...] [claude arguments]'
  Write-Host ''
  Write-Host 'Profiles:'
  Write-Host '  airlock          Start the saved default profile and orchestrator'
  Write-Host '  airlock openai   Start the saved OpenAI-only orchestrator'
  Write-Host '  airlock grok     Start the saved Grok-only orchestrator (subscription proxy)'
  Write-Host '  airlock hybrid   Start the saved hybrid orchestrator'
  Write-Host '  airlock fast     Start one session with OpenAI Fast without saving the mode'
  Write-Host '  airlock opr      Start an OpenRouter-only session on an exact registry route'
  Write-Host '  airlock om       Start a local open-model-only session on an exact registry route'
  Write-Host '  airlock console  Start the local-only session console on 127.0.0.1'
  Write-Host '  claude           Start the native Anthropic CLI without Airlock'
  Write-Host ''
  Write-Host 'OpenAI root aliases: astra, sol, sol-fast, terra, luna, 5.5, 5.4, mini, 5.3, spark, 5.2'
  Write-Host 'Grok root aliases: grok, composer'
  Write-Host 'Hybrid root aliases: auto, sonnet, astra, sol, terra, luna, opus, fable, haiku, grok, composer'
  Write-Host '  auto resolves to fable when Fable is neither extra nor unavailable, otherwise'
  Write-Host '  opus under the same rule, otherwise sonnet.'
  Write-Host 'OpenRouter roots: exact enabled route slugs from airlock openrouter models list'
  Write-Host 'Open-model roots: exact enabled local registry routes from airlock open-model list'
  Write-Host 'Other commands: console, fast, bg, mode, usage, session-usage, status, access, bundle, config, models, openrouter auth/models, open-model, proxy auth, version, update'
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
  Write-Host 'Open-model registry commands (private values use a hidden prompt or --stdin):'
  Write-Host '  airlock open-model endpoint list'
  Write-Host '  airlock open-model endpoint add ID --max-concurrency N [--stdin] [--disabled]'
  Write-Host '  airlock open-model endpoint remove ID [--yes]'
  Write-Host '  airlock open-model list'
  Write-Host '  airlock open-model add ROUTE ENDPOINT [--stdin] --context-window N --max-output-tokens N (--streaming|--no-streaming) --tools none|single|parallel [--tool-choice MODE ...] (--worker|--no-worker) [--disabled]'
  Write-Host '  airlock open-model remove ROUTE [--yes]'
  Write-Host '  airlock open-model check ROUTE'
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
  Write-Host '  airlock mode anthropic-rate-limit native|handoff'
  Write-Host '  airlock mode max-agents off|1..20'
  Write-Host '  airlock mode fast all|openai|anthropic|off'
  Write-Host '  airlock mode openai-fast on|off'
  Write-Host '  airlock mode anthropic-fast on|off'
  Write-Host '  airlock mode swarm-fast auto|on|off'
  Write-Host '  airlock mode set --routing economy --extra-usage never --failover ask --anthropic-rate-limit native --max-agents off --openai-fast off --anthropic-fast off --swarm-fast auto'
  Write-Host '  airlock usage                    Show cached sanitized subscription usage'
  Write-Host '  airlock usage refresh            Refresh OpenAI quota windows without a model call'
  Write-Host '  airlock usage set --claude-plan pro|max5x|max20x|unknown'
  Write-Host '  airlock usage set --openai-capacity auto|1x|5x|20x'
  Write-Host '  airlock usage defaults           Clear capacity overrides'
  Write-Host '  airlock session-usage [--json]   Show router-observed token counts for this hybrid session'
  Write-Host "  airlock status                   Show this terminal's router profile and recent actions"
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
  Write-Host '  airlock om my-local-route        # exact local open-model-only root'
  Write-Host '  airlock hybrid om:my-local-route # that local open model drives a hybrid session'
  Write-Host '  airlock console --no-open           # foreground console without opening a browser'
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
    [Console]::Error.WriteLine('airlock: access helper returned invalid JSON.')
    exit 1
  }
}

function Reset-CustomModels {
  $CustomModelRecords.Clear()
  $CustomOpenRouterRoutes.Clear()
}

function Import-CustomModels {
  # The catalog load follows helper-version semantics instead of demanding a
  # full-protocol helper on every invocation. Exit code 2 means the helper
  # predates custom-models support and simply leaves the catalog empty; other
  # nonzero exits are the helper's own named diagnostics for a malformed
  # user-owned file and stay fatal. Unusable output from a helper that claimed
  # success degrades to no declarations rather than bricking the session,
  # because declarations are opt-in and can only add routes, never remove them.
  if (-not (Test-Path -LiteralPath $AccessHelper -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed access helper is missing: $AccessHelper")
    exit 1
  }
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python is required for access-policy handling.')
    exit 1
  }
  # Windows PowerShell 5.1 turns redirected native stderr into a terminating
  # error under Stop preference, so this one probe runs under Continue.
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $raw = @(& $python $AccessHelper 'custom-models' 2>$null)
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  $rawExit = $LASTEXITCODE
  if ($rawExit -eq 2) { return }
  if ($rawExit -ne 0) { exit $rawExit }
  try {
    $entries = @((($raw -join "`n") | ConvertFrom-Json -ErrorAction Stop))
  } catch {
    [Console]::Error.WriteLine('airlock: ignoring models.json declarations; the access helper returned unusable output.')
    Reset-CustomModels
    return
  }
  foreach ($entry in $entries) {
    if ($null -eq $entry) { continue }
    foreach ($field in @('id', 'provider', 'routed_provider', 'agent', 'effort_ceiling', 'cost')) {
      $property = $entry.PSObject.Properties[$field]
      if ($null -eq $property -or -not ($property.Value -is [string]) -or -not $property.Value) {
        [Console]::Error.WriteLine('airlock: ignoring models.json declarations; the access helper returned unusable output.')
        Reset-CustomModels
        return
      }
    }
    $id = [string]$entry.id
    $provider = [string]$entry.provider
    if ($CustomModelRecords.ContainsKey($id)) {
      [Console]::Error.WriteLine('airlock: ignoring models.json declarations; the access helper returned unusable output.')
      Reset-CustomModels
      return
    }
    $CustomModelRecords[$id] = $entry
    switch ($provider) {
      'codex' {
        if ([string]$entry.routed_provider -cne 'openai') {
          [Console]::Error.WriteLine('airlock: ignoring models.json declarations; the access helper returned unusable output.')
          Reset-CustomModels
          return
        }
        $Models[$id] = @($id, "Custom Codex ($id)")
        $HybridRoots[$id] = @($id, "Custom Codex ($id)", 'openai')
      }
      'grok' {
        if ([string]$entry.routed_provider -cne 'grok') {
          [Console]::Error.WriteLine('airlock: ignoring models.json declarations; the access helper returned unusable output.')
          Reset-CustomModels
          return
        }
        $GrokModels[$id] = @($id, "Custom Grok ($id)")
        $HybridRoots[$id] = @($id, "Custom Grok ($id)", 'grok')
      }
      'openrouter' {
        $routeProperty = $entry.PSObject.Properties['openrouter_route']
        if ([string]$entry.routed_provider -cne 'openrouter' -or
            $null -eq $routeProperty -or -not ($routeProperty.Value -is [string]) -or -not $routeProperty.Value) {
          [Console]::Error.WriteLine('airlock: ignoring models.json declarations; the access helper returned unusable output.')
          Reset-CustomModels
          return
        }
        $CustomOpenRouterRoutes[$id] = [string]$entry.openrouter_route
      }
      default {
        [Console]::Error.WriteLine('airlock: ignoring models.json declarations; the access helper returned unusable output.')
        Reset-CustomModels
        return
      }
    }
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

function Test-OpenRouterRouteEnabled {
  # Non-exiting registry probe used before committing to a route-shaped hybrid
  # candidate. Returns $true only when openrouter-resolve accepts the route.
  param([string]$Route)
  if (-not (Test-Path -LiteralPath $AccessHelper -PathType Leaf)) { return $false }
  $python = Resolve-Python
  if (-not $python) { return $false }
  $previousErrorAction = $ErrorActionPreference
  try {
    # Windows PowerShell 5 turns a native process's redirected stderr into an
    # ErrorRecord. This is an expected negative probe, not a script failure.
    $ErrorActionPreference = 'SilentlyContinue'
    & $python $AccessHelper 'openrouter-resolve' $Route *> $null
    $routeStatus = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorAction
  }
  return ($routeStatus -eq 0)
}

function Test-OpenModelRouteRecord {
  param([object]$Value, [string]$ExpectedRoute = '')
  if ($null -eq $Value) { return $false }
  foreach ($field in @('route', 'model')) {
    $property = $Value.PSObject.Properties[$field]
    if ($null -eq $property -or -not ($property.Value -is [string]) -or -not $property.Value) {
      return $false
    }
  }
  return (-not $ExpectedRoute -or [string]$Value.route -ceq $ExpectedRoute)
}

function Resolve-OpenModelRootRoute {
  param([string]$Route)
  $resolved = Invoke-AccessJson -PolicyArguments @('openmodel-resolve', $Route)
  if (-not (Test-OpenModelRouteRecord -Value $resolved -ExpectedRoute $Route)) {
    [Console]::Error.WriteLine('airlock: local open-model route resolver returned an invalid result.')
    exit 1
  }
  return $resolved
}

function Test-OpenModelRouteEnabled {
  # Non-exiting registry probe used before committing to a route-shaped hybrid
  # candidate. Returns $true only when openmodel-resolve accepts the route.
  param([string]$Route)
  if (-not (Test-Path -LiteralPath $AccessHelper -PathType Leaf)) { return $false }
  $python = Resolve-Python
  if (-not $python) { return $false }
  $previousErrorAction = $ErrorActionPreference
  try {
    # As above, a rejected route is ordinary probe output under Windows
    # PowerShell 5 and must not become a terminating NativeCommandError.
    $ErrorActionPreference = 'SilentlyContinue'
    & $python $AccessHelper 'openmodel-resolve' $Route *> $null
    $routeStatus = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorAction
  }
  return ($routeStatus -eq 0)
}

function Select-OpenModelRootRoute {
  $interactive = [Environment]::UserInteractive
  try {
    $interactive = $interactive -and -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected
  } catch { }
  if (-not $interactive) {
    [Console]::Error.WriteLine('airlock: a local open-model route is required outside an interactive terminal; use airlock om ROUTE.')
    exit 2
  }
  $routes = @(Invoke-AccessJson -PolicyArguments @('openmodel-routes'))
  foreach ($route in $routes) {
    if (-not (Test-OpenModelRouteRecord -Value $route)) {
      [Console]::Error.WriteLine('airlock: local open-model route list returned an invalid result.')
      exit 1
    }
  }
  if ($routes.Count -eq 0) {
    [Console]::Error.WriteLine('airlock: no enabled local open-model routes are available; add one with airlock open-model add.')
    exit 2
  }
  Write-Host 'Choose the exact local open-model root route:'
  for ($index = 0; $index -lt $routes.Count; $index++) {
    Write-Host ("  {0}) {1}" -f ($index + 1), $routes[$index].route)
  }
  $selection = Read-Host "Selection [1-$($routes.Count)]"
  $selectedIndex = 0
  if (-not [int]::TryParse($selection, [ref]$selectedIndex) -or $selectedIndex -lt 1 -or $selectedIndex -gt $routes.Count) {
    [Console]::Error.WriteLine('airlock: invalid local open-model route selection.')
    exit 2
  }
  return [string]$routes[$selectedIndex - 1].route
}

function Resolve-AutoHybridRoot {
  # The reserved 'auto' hybrid root resolves at launch time under the local
  # access policy: Fable when its access class is neither extra nor
  # unavailable, otherwise Opus under the same rule, otherwise Sonnet as a
  # final fallback. Auto selection therefore never picks an extra-class model
  # and never creates silent metered spend. The access helper owns the rule so
  # both launchers cannot drift; this validates its output and caches it.
  if ($script:ResolvedAutoAlias) { return $script:ResolvedAutoAlias }
  if (-not (Test-Path -LiteralPath $AccessHelper -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed access helper is missing: $AccessHelper")
    exit 1
  }
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python is required for access-policy handling.')
    exit 1
  }
  $raw = @(& $python $AccessHelper 'hybrid-default-root')
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  $value = ''
  foreach ($line in $raw) {
    $trimmed = ('' + $line).Trim()
    if (-not $trimmed) { continue }
    if ($value) {
      [Console]::Error.WriteLine('airlock: auto hybrid root resolver returned an invalid result.')
      exit 1
    }
    $value = $trimmed
  }
  if ($value -notin @('fable', 'opus', 'sonnet')) {
    [Console]::Error.WriteLine('airlock: auto hybrid root resolver returned an invalid result.')
    exit 1
  }
  $script:ResolvedAutoAlias = $value
  return $value
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

function Show-ConsoleUsage {
  Write-Host 'Usage: airlock console [--port N] [--no-open] [--scan] [--once]'
  Write-Host ''
  Write-Host 'Options:'
  Write-Host '  --port N    Listen on 127.0.0.1:N (default 4783)'
  Write-Host '  --no-open   Do not open the platform browser'
  Write-Host '  --scan      Include bounded process-table fallback discovery'
  Write-Host '  --once      Print one JSON overview and exit without opening a browser'
  Write-Host '  -h, --help  Show this help'
}

function ConvertTo-WindowsCommandLineArgument {
  param([AllowEmptyString()][string]$Argument)
  if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') { return $Argument }
  $builder = New-Object Text.StringBuilder
  $null = $builder.Append('"')
  $backslashes = 0
  foreach ($character in $Argument.ToCharArray()) {
    if ($character -eq '\') {
      $backslashes++
      continue
    }
    if ($character -eq '"') {
      if ($backslashes -gt 0) {
        $null = $builder.Append((('\' * (2 * $backslashes + 1)) -join ''))
      } else {
        $null = $builder.Append('\')
      }
      $null = $builder.Append('"')
      $backslashes = 0
      continue
    }
    if ($backslashes -gt 0) {
      $null = $builder.Append((('\' * $backslashes) -join ''))
      $backslashes = 0
    }
    $null = $builder.Append($character)
  }
  if ($backslashes -gt 0) {
    $null = $builder.Append((('\' * (2 * $backslashes)) -join ''))
  }
  $null = $builder.Append('"')
  return $builder.ToString()
}

function Get-ExistingAirlockConsoleUrl {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$ErrorText)
  $prefix = 'airlock-console: console already running at '
  if (-not $ErrorText.StartsWith($prefix)) { return $null }
  $url = $ErrorText.Substring($prefix.Length)
  if ($url -notmatch '^http://127\.0\.0\.1:([1-9][0-9]{0,4})$') { return $null }
  $port = [int]$Matches[1]
  if ($port -gt 65535) { return $null }
  return $url
}

function Test-AirlockConsoleEndpoint {
  param([Parameter(Mandatory)][string]$Url)
  $response = $null
  $reader = $null
  try {
    $request = [Net.HttpWebRequest]::Create("$Url/healthz")
    $request.Method = 'GET'
    $request.Timeout = 250
    $request.ReadWriteTimeout = 250
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

function Open-AirlockConsoleBrowser {
  param([Parameter(Mandatory)][string]$Url)
  try {
    Start-Process -FilePath $Url | Out-Null
  } catch {
    [Console]::Error.WriteLine('airlock console: the platform browser could not be opened.')
  }
}

function Invoke-AirlockConsole {
  param([string[]]$ConsoleArguments)
  $portText = '4783'
  $noOpen = $false
  $scan = $false
  $once = $false
  $index = 0
  while ($index -lt $ConsoleArguments.Count) {
    $option = $ConsoleArguments[$index]
    switch -Wildcard ($option) {
      '--port' {
        if ($index + 1 -ge $ConsoleArguments.Count) {
          [Console]::Error.WriteLine('airlock console: --port requires a value.')
          return 2
        }
        $portText = $ConsoleArguments[$index + 1]
        $index += 2
        continue
      }
      '--port=*' {
        $portText = $option.Substring('--port='.Length)
        $index++
        continue
      }
      '--no-open' { $noOpen = $true; $index++; continue }
      '--scan' { $scan = $true; $index++; continue }
      '--once' { $once = $true; $index++; continue }
      '-h' { Show-ConsoleUsage; return 0 }
      '--help' { Show-ConsoleUsage; return 0 }
      default {
        [Console]::Error.WriteLine("airlock console: unknown option: $option")
        return 2
      }
    }
  }
  if ($portText -notmatch '^[1-9][0-9]{0,4}$') {
    [Console]::Error.WriteLine('airlock console: --port must be an integer from 1 to 65535.')
    return 2
  }
  $port = [int]$portText
  if ($port -gt 65535) {
    [Console]::Error.WriteLine('airlock console: --port must be an integer from 1 to 65535.')
    return 2
  }
  if ((Test-ManagedBundle) -ne 0) { return 1 }
  if (-not (Test-Path -LiteralPath $ConsoleHelper -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed console helper is missing or unsafe: $ConsoleHelper")
    return 1
  }
  $consoleItem = Get-Item -LiteralPath $ConsoleHelper -Force
  if ($consoleItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    [Console]::Error.WriteLine("airlock: managed console helper is missing or unsafe: $ConsoleHelper")
    return 1
  }
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python 3 is required for Airlock Console.')
    return 1
  }

  $managedSite = Join-Path $ManagedBinDir 'share\airlock\console'
  $checkoutSite = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\console\dist'))
  if ($env:AIRLOCK_CONSOLE_SITE) {
    $site = $env:AIRLOCK_CONSOLE_SITE
  } elseif (Test-Path -LiteralPath $managedSite) {
    $site = $managedSite
  } elseif (Test-Path -LiteralPath $checkoutSite -PathType Container) {
    $site = $checkoutSite
  } else {
    $site = $managedSite
  }
  if (Test-Path -LiteralPath $site) {
    $siteItem = Get-Item -LiteralPath $site -Force
    if (-not $siteItem.PSIsContainer -or ($siteItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      [Console]::Error.WriteLine("airlock: Airlock Console site is unsafe: $site")
      return 1
    }
  }
  $helperArguments = @(
    $ConsoleHelper,
    '--port', [string]$port,
    '--site', $site,
    '--access-helper', $AccessHelper,
    '--tools-helper', $ConsoleToolsHelper
  )
  if ($scan) { $helperArguments += '--scan' }
  if ($once) { $helperArguments += '--once' }
  if ($once) {
    # The dispatcher evaluates this function inside exit (...), which captures
    # success-stream output. Write helper output directly to stdout so --once
    # remains observable while the function returns only its numeric status.
    & $python @helperArguments | ForEach-Object {
      [Console]::Out.WriteLine([string]$_)
    }
    return $LASTEXITCODE
  }

  $stderrPath = [IO.Path]::GetTempFileName()
  $process = $null
  try {
    $nativeArguments = (($helperArguments | ForEach-Object {
      ConvertTo-WindowsCommandLineArgument ([string]$_)
    }) -join ' ')
    $process = Start-Process -FilePath $python -ArgumentList $nativeArguments `
      -NoNewWindow -PassThru -RedirectStandardError $stderrPath
    # Windows PowerShell 5.1 can lose ExitCode when Start-Process has not
    # opened its process handle before a short-lived helper exits.
    $null = $process.Handle
  } catch {
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    [Console]::Error.WriteLine('airlock console: the console helper could not be started.')
    return 1
  }
  $url = "http://127.0.0.1:$port"
  try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
      if ($process.HasExited) { break }
      if (Test-AirlockConsoleEndpoint -Url $url) {
        $ready = $true
        break
      }
      Start-Sleep -Milliseconds 50
    }
    if ($ready) {
      Write-Host "Airlock Console: $url"
      if (-not $noOpen) { Open-AirlockConsoleBrowser -Url $url }
      while (-not $process.WaitForExit(250)) { }
      $exitCode = $process.ExitCode
      $stderrText = if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
        [IO.File]::ReadAllText($stderrPath)
      } else { '' }
      $trimmedError = $stderrText.TrimEnd([char[]]@("`r", "`n"))
      $existingUrl = Get-ExistingAirlockConsoleUrl -ErrorText $trimmedError
      if ($exitCode -eq 2 -and $existingUrl) {
        return 0
      }
      if ($stderrText) { [Console]::Error.Write($stderrText) }
      return $exitCode
    }

    if (-not $process.HasExited) {
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $stderrText = if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
      [IO.File]::ReadAllText($stderrPath)
    } else { '' }
    $trimmedError = $stderrText.TrimEnd([char[]]@("`r", "`n"))
    $existingUrl = Get-ExistingAirlockConsoleUrl -ErrorText $trimmedError
    if ($exitCode -eq 2 -and $existingUrl) {
      Write-Host "Airlock Console: $existingUrl"
      if (-not $noOpen) { Open-AirlockConsoleBrowser -Url $existingUrl }
      return 0
    }
    if ($stderrText) { [Console]::Error.Write($stderrText) }
    if ($exitCode -eq 0) {
      [Console]::Error.WriteLine('airlock console: helper exited before its loopback health check passed.')
      return 1
    }
    return $exitCode
  } finally {
    if ($process) {
      try {
        if (-not $process.HasExited) {
          # Ctrl+C reaches both this launcher and the Console helper because
          # they share the native console. Give Python's cleanup path time to
          # remove its address marker before forced termination is the fallback.
          if (-not $process.WaitForExit(2000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
          }
        }
      } catch { }
      try { $process.WaitForExit() } catch { }
      $process.Dispose()
    }
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
  }
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

function Invoke-OpenModelRegistry {
  param([string[]]$ModelArguments, [ref]$Result)
  $Result.Value = 1
  if (-not (Test-Path -LiteralPath $OpenModelHelper -PathType Leaf)) {
    [Console]::Error.WriteLine("airlock: managed open-model registry helper is missing: $OpenModelHelper")
    return
  }
  $helperItem = Get-Item -LiteralPath $OpenModelHelper
  if (($helperItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    [Console]::Error.WriteLine("airlock: managed open-model registry helper is unsafe: $OpenModelHelper")
    return
  }
  $python = Resolve-Python
  if (-not $python) {
    [Console]::Error.WriteLine('airlock: Python is required for local open-model registry management.')
    return
  }
  & $python $OpenModelHelper --registry $OpenModelRegistryFile @ModelArguments
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
    '--component', "bin/airlock_console.py=$ConsoleHelper",
    '--component', "bin/airlock_console_tools.py=$ConsoleToolsHelper",
    '--component', "bin/airlock_console_history.py=$ConsoleHistoryHelper",
    '--component', "bin/airlock_policy.py=$PolicyHelper",
    '--component', "bin/airlock_openrouter_auth.py=$OpenRouterAuthHelper",
    '--component', "bin/airlock_openrouter_presets.py=$OpenRouterPresetsHelper",
    '--component', "bin/airlock_openrouter_models.py=$OpenRouterModelsHelper",
    '--component', "bin/airlock_openmodel.py=$OpenModelHelper",
    '--component', "bin/airlock_openmodel_adapter.py=$OpenModelAdapterHelper",
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
    '--component', "plugins/airlock/mcp-server/airlock_web_tools.py=$(Join-Path $PluginDir 'mcp-server\airlock_web_tools.py')",
    '--component', "plugins/airlock/mcp-server/airlock_console_mcp.py=$ConsoleMcpHelper",
    '--component', "plugins/airlock/scripts/fast-session-end.sh=$(Join-Path $PluginDir 'scripts\fast-session-end.sh')",
    '--component', "plugins/airlock/scripts/fast-session-end.py=$(Join-Path $PluginDir 'scripts\fast-session-end.py')",
    '--component', "plugins/airlock/scripts/router-session-end.sh=$(Join-Path $PluginDir 'scripts\router-session-end.sh')",
    '--component', "plugins/airlock/scripts/router-session-end.py=$(Join-Path $PluginDir 'scripts\router-session-end.py')",
    '--component', "plugins/airlock/scripts/router-turn-notice.sh=$(Join-Path $PluginDir 'scripts\router-turn-notice.sh')",
    '--component', "plugins/airlock/scripts/router-turn-notice.py=$(Join-Path $PluginDir 'scripts\router-turn-notice.py')",
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
    $oldRetries = [Environment]::GetEnvironmentVariable('CCP_MAX_RATE_LIMIT_RETRIES', 'Process')
    # Airlock picks the replacement model itself, so a rate limit retried
    # inside the proxy is a delay paid for nothing. Measured at 175 seconds
    # before a handoff that then took 9.
    $proxyRetries = $env:AIRLOCK_PROXY_RATE_LIMIT_RETRIES
    if ($proxyRetries -notmatch '^[0-9]$') { $proxyRetries = '0' }
    try {
      if ($ProxyConfigDir) { $env:CCP_CONFIG_DIR = $ProxyConfigDir }
      if ($ProxyStateHome) { $env:XDG_STATE_HOME = $ProxyStateHome }
      $env:CCP_MAX_RATE_LIMIT_RETRIES = $proxyRetries
      Start-Process -FilePath $proxyExe -ArgumentList 'serve','--no-monitor' -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir 'service.out.log') `
        -RedirectStandardError  (Join-Path $logDir 'service.err.log')
    } finally {
      if ($null -eq $oldConfigDir) { Remove-Item Env:CCP_CONFIG_DIR -ErrorAction SilentlyContinue } else { $env:CCP_CONFIG_DIR = $oldConfigDir }
      if ($null -eq $oldStateHome) { Remove-Item Env:XDG_STATE_HOME -ErrorAction SilentlyContinue } else { $env:XDG_STATE_HOME = $oldStateHome }
      if ($null -eq $oldRetries) { Remove-Item Env:CCP_MAX_RATE_LIMIT_RETRIES -ErrorAction SilentlyContinue } else { $env:CCP_MAX_RATE_LIMIT_RETRIES = $oldRetries }
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
  # grok-4.6 is documented at 500000 and gpt-6-astra at 1050000 total with
  # 922000 of input, which is the ceiling a request must fit. Declare that
  # hard limit and compact at 80% so the summary request still fits. Other
  # OpenAI and Grok roots keep the conservative fallback unless the user
  # overrides it, and any inherited value is dropped so it can never silently
  # cap this session's workers. Airlock has not proved the Astra window live.
  if ($DeclaredContextLimits.ContainsKey($Model)) {
    $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS = $DeclaredContextLimits[$Model][0]
  } else {
    Remove-Item Env:\CLAUDE_CODE_MAX_CONTEXT_TOKENS -ErrorAction SilentlyContinue
  }
  if ($UserContextWin) {
    $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = $UserContextWin
  } elseif ($ExplicitContextWin -and $ContextWin -ne 'auto') {
    $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = $ContextWin
  } elseif ($ContextWin -eq 'auto') {
    Remove-Item Env:\CLAUDE_CODE_AUTO_COMPACT_WINDOW -ErrorAction SilentlyContinue
  } elseif ($DeclaredContextLimits.ContainsKey($Model)) {
    $env:CLAUDE_CODE_AUTO_COMPACT_WINDOW = $DeclaredContextLimits[$Model][1]
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
  $model = $null
  $selectorCount = 0
  for ($i = 0; $i -lt $ChildArguments.Count; $i++) {
    $argument = [string]$ChildArguments[$i]
    if ($argument -eq '--') { break }
    if ($argument -in @('--model', '-m')) {
      if ($i + 1 -ge $ChildArguments.Count -or -not [string]$ChildArguments[$i + 1]) {
        [Console]::Error.WriteLine("airlock: $argument requires a value.")
        exit 2
      }
      $selectorCount++
      if ($selectorCount -gt 1) {
        [Console]::Error.WriteLine('airlock: --model or -m may be provided only once.')
        exit 2
      }
      $model = [string]$ChildArguments[$i + 1]
      $i++
      continue
    }
    if ($argument -like '--model=*' -or $argument -like '-m=*') {
      $value = $argument.Substring($argument.IndexOf('=') + 1)
      if (-not $value) {
        [Console]::Error.WriteLine("airlock: $($argument.Split('=')[0]) requires a value.")
        exit 2
      }
      $selectorCount++
      if ($selectorCount -gt 1) {
        [Console]::Error.WriteLine('airlock: --model or -m may be provided only once.')
        exit 2
      }
      $model = $value
    }
  }
  return $model
}

function Resolve-GrokAlias {
  param([string]$Value)
  switch ($Value) {
    'grok' { return 'grok' }
    'grok-4.6' { return 'grok' }
    'grok-4.5' { return 'grok' }
    'composer' { return 'composer' }
    'grok-composer' { return 'composer' }
    'grok-composer-2.5-fast' { return 'composer' }
  }
  if ($CustomModelRecords.ContainsKey($Value) -and
      [string]$CustomModelRecords[$Value].provider -ceq 'grok' -and
      [string]$CustomModelRecords[$Value].id -ceq $Value) {
    return $Value
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
  if ($CustomModelRecords.ContainsKey($Model)) {
    if ([string]$CustomModelRecords[$Model].provider -ceq 'codex' -and
        [string]$CustomModelRecords[$Model].id -ceq $Model) {
      return $Model
    }
    return $null
  }
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
    [string]$OpenRouterRootRoute = '',
    [string]$OpenModelRootRoute = ''
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
      if ($argument -in @('--model', '-m') -or $argument -like '--model=*' -or $argument -like '-m=*') {
        [Console]::Error.WriteLine('airlock: OpenRouter roots are selected by exact registry route; --model and -m cannot be forwarded.')
        exit 2
      }
    }
  } elseif ($Profile -eq 'hybrid-openrouter-root') {
    if (-not $OpenRouterRootRoute) {
      [Console]::Error.WriteLine('airlock: hybrid-openrouter-root requires an exact OpenRouter root route.')
      exit 2
    }
    if ($RootModel -or $RootName) {
      [Console]::Error.WriteLine('airlock: the OpenRouter root identity must be derived by the managed bridge.')
      exit 2
    }
  } elseif ($OpenRouterRootRoute) {
    [Console]::Error.WriteLine('airlock: an OpenRouter root route is valid only for openrouter-pure or hybrid-openrouter-root.')
    exit 2
  }

  if ($Profile -eq 'openmodel-pure') {
    if (-not $OpenModelRootRoute) {
      [Console]::Error.WriteLine('airlock: openmodel-pure requires an exact local open-model root route.')
      exit 2
    }
    if ($RootModel -or $RootName) {
      [Console]::Error.WriteLine('airlock: the local open-model root identity must be derived by the managed bridge.')
      exit 2
    }
    foreach ($argument in $ChildArguments) {
      if ($argument -in @('--model', '-m') -or $argument -like '--model=*' -or $argument -like '-m=*') {
        [Console]::Error.WriteLine('airlock: local open-model roots are selected by exact registry route; --model and -m cannot be forwarded.')
        exit 2
      }
    }
  } elseif ($Profile -eq 'hybrid-openmodel-root') {
    if (-not $OpenModelRootRoute) {
      [Console]::Error.WriteLine('airlock: hybrid-openmodel-root requires an exact local open-model root route.')
      exit 2
    }
    if ($RootModel -or $RootName) {
      [Console]::Error.WriteLine('airlock: the local open-model root identity must be derived by the managed bridge.')
      exit 2
    }
  } elseif ($OpenModelRootRoute) {
    [Console]::Error.WriteLine('airlock: a local open-model root route is valid only for openmodel-pure or hybrid-openmodel-root.')
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
  # Set this only for a real session launch. Exporting it before `airlock mode`
  # would turn the saved value into an environment override and make a newly
  # written mode appear not to take effect.
  $env:AIRLOCK_ANTHROPIC_RATE_LIMIT = $AnthropicRateLimit
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
  $localOpenModelProfile = $Profile -in @('openmodel-pure', 'hybrid-openmodel-root')
  if (-not $localOpenModelProfile) {
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
  } else {
    # Local routes are explicit-only and can never be selected by Fast handoff.
    Remove-Item -LiteralPath 'Env:AIRLOCK_FAST_TRANSITION_CHANNEL' -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Env:AIRLOCK_FAST_TRANSITION_NONCE' -ErrorAction SilentlyContinue
  }
  $sessionFastMode = Get-SessionFastMode -RootModel $RootModel
  $launchRequest = [ordered]@{
    profile = $Profile
    claude = [string]$claudeBin
    catalog_files = $catalogFiles
    plugin_dir = [IO.Path]::GetFullPath($PluginDir)
    router_helper = [IO.Path]::GetFullPath($RouterHelper)
    workdir = [string]$launchCwd
    context_window = [string]$ContextWin
    force_context_window = [bool]$ExplicitContextWin
    max_agents = [string]$MaxAgents
    fast_mode = [string]$sessionFastMode
    args = [string[]]$ChildArguments
  }
  if (-not $localOpenModelProfile) {
    $launchRequest['fast_transition_launcher_pid'] = [int64]$PID
    $launchRequest['fast_transition_cwd'] = [string]$launchCwd
  }
  if ($Profile -eq 'openrouter-pure') {
    $launchRequest['openrouter_root_route'] = [string]$OpenRouterRootRoute
  } elseif ($Profile -eq 'hybrid-openrouter-root') {
    # The wrapper catalogs keep GPT and Grok routes in the snapshot, and the
    # router refuses to start without a subscription endpoint for them. The
    # root identity itself stays bridge-derived from the exact route.
    $launchRequest['openrouter_root_route'] = [string]$OpenRouterRootRoute
    $launchRequest['proxy_url'] = [string]$ProxyUrl
  } elseif ($Profile -eq 'openmodel-pure') {
    $launchRequest['openmodel_root_route'] = [string]$OpenModelRootRoute
  } elseif ($Profile -eq 'hybrid-openmodel-root') {
    # Hybrid open-model retains the existing mixed non-local worker pool, so
    # the router still needs a subscription endpoint for GPT/Grok routes. The
    # root identity itself stays bridge-derived from the exact route.
    $launchRequest['openmodel_root_route'] = [string]$OpenModelRootRoute
    $launchRequest['proxy_url'] = [string]$ProxyUrl
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
  Write-Host '  6) Claude Fable 5.1 (claude-fable-5-1[1m]; may use extra usage)'
  Write-Host '  7) Claude Haiku 4.5 (claude-haiku-4-5-20251001)'
  Write-Host '  8) Grok 4.6 (grok-4.6; requires Grok OAuth)'
  Write-Host '  9) Grok Composer 2.5 Fast (grok-composer-2.5-fast; requires Grok OAuth)'
  Write-Host ' 10) GPT-6 Astra (gpt-6-astra; premium usage)'
  Write-Host ' 11) An exact OpenRouter registry route'
  Write-Host ' 12) An exact local open-model registry route'
  $selection = Read-Host 'Selection [1-12]'
  if ($selection -eq '11') {
    $route = Select-OpenRouterRootRoute
    $record = Resolve-OpenRouterRootRoute -Route $route
    $script:HybridSelectedRoute = [string]$record.route
    $script:HybridSelectedModel = [string]$record.model
    return $null
  }
  if ($selection -eq '12') {
    $route = Select-OpenModelRootRoute
    $record = Resolve-OpenModelRootRoute -Route $route
    $script:HybridSelectedOpenModelRoute = [string]$record.route
    $script:HybridSelectedOpenModelModel = [string]$record.model
    return $null
  }
  $choices = @{ '1' = 'sonnet'; '2' = 'sol'; '3' = 'terra'; '4' = 'luna'; '5' = 'opus'; '6' = 'fable'; '7' = 'haiku'; '8' = 'grok'; '9' = 'composer'; '10' = 'astra' }
  if (-not $choices.ContainsKey($selection)) {
    [Console]::Error.WriteLine('airlock: invalid hybrid root selection.')
    exit 2
  }
  return $choices[$selection]
}

# Renamed-command refusals never depend on access-helper health, so they run
# before the user-owned catalog load.
if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'orp') {
  [Console]::Error.WriteLine("airlock: command 'orp' was renamed to 'opr'; use airlock opr.")
  exit 2
}
if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'console') {
  $consoleArguments = @()
  if ($Arguments.Count -gt 1) {
    $consoleArguments = @($Arguments[1..($Arguments.Count - 1)])
  }
  exit (Invoke-AirlockConsole -ConsoleArguments $consoleArguments)
}

# Parse the user-owned catalog before any saved-model probes. The helper emits
# models[index] diagnostics for malformed entries and never changes the file.
Import-CustomModels

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
# Eager saved-value validation runs on every invocation, exactly as before.
# The reserved 'auto' value is validated through the access helper instead of
# the alias tables; the resolved alias stays cached for the hybrid branch.
$script:ResolvedAutoAlias = $null
$script:HybridSelectedRoute = $null
$script:HybridSelectedOpenModelRoute = $null
if ($DefaultHybridModel -eq 'auto') {
  $null = Resolve-AutoHybridRoot
} else {
  $hybridOpenAIAlias = Resolve-OpenAIAlias $DefaultHybridModel
  $customOpenRouterDefault = (
    $CustomOpenRouterRoutes.ContainsKey($DefaultHybridModel) -and
    [string]$CustomModelRecords[$DefaultHybridModel].id -ceq $DefaultHybridModel
  )
  if (-not $HybridRoots.ContainsKey($DefaultHybridModel) -and
      -not $customOpenRouterDefault -and
      $hybridOpenAIAlias -notin @('astra', 'sol', 'terra', 'luna')) {
    [Console]::Error.WriteLine("airlock: unsupported saved hybrid model '$DefaultHybridModel'")
    exit 2
  }
  if ($hybridOpenAIAlias -in @('astra', 'sol', 'terra', 'luna')) { $DefaultHybridModel = $hybridOpenAIAlias }
}
$DefaultBgAlias = Resolve-OpenAIAlias $DefaultBgModel
if (-not $DefaultBgAlias) {
  [Console]::Error.WriteLine("airlock: unsupported saved background model '$DefaultBgModel'")
  exit 2
}
$DefaultBgModel = $DefaultBgAlias
$ExplicitCommands = @(
  'console', 'mode', 'handoff', 'usage', 'session-usage', 'status', 'bundle', 'access', 'openrouter', 'opr', 'om', 'open-model', 'proxy', 'models', '--models', 'config', '--config',
  'version', 'update', 'hybrid', 'openai', 'grok', 'fast', 'bg', 'background', 'astra', 'sol', 'sol-fast', 'terra',
  'luna', '5.5', '5.4', 'mini', '5.3', 'spark', '5.2'
)
if ($DefaultProfile -eq 'grok') {
  $firstArgument = if ($Arguments.Count -gt 0) { $Arguments[0] } else { '' }
  $exactCustomGrok = (
    $CustomModelRecords.ContainsKey($firstArgument) -and
    [string]$CustomModelRecords[$firstArgument].provider -ceq 'grok' -and
    [string]$CustomModelRecords[$firstArgument].id -ceq $firstArgument
  )
  if ($exactCustomGrok) {
    $Arguments = @('grok') + $Arguments
  } elseif ($firstArgument -notin $ExplicitCommands) {
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
  $exactCustomRoot = (
    $CustomModelRecords.ContainsKey($firstArgument) -and
    [string]$CustomModelRecords[$firstArgument].id -ceq $firstArgument
  )
  if ($exactCustomRoot) {
    $Arguments = @('hybrid') + $Arguments
  } elseif ($firstArgument -notin $explicitCommands) {
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

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'handoff') {
  $handoffArguments = @('handoff')
  if ($Arguments.Count -gt 1) {
    $handoffArguments += @($Arguments[1..($Arguments.Count - 1)])
  }
  exit (Invoke-AccessPolicy -PolicyArguments $handoffArguments)
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

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'status') {
  if ($Arguments.Count -ne 1) {
    [Console]::Error.WriteLine('airlock status: this command does not accept arguments.')
    exit 2
  }
  $statusArguments = @('status')
  if ($env:AIRLOCK_SESSION_ROUTER_URL) {
    $statusArguments += @('--router-url', $env:AIRLOCK_SESSION_ROUTER_URL)
  }
  exit (Invoke-AccessPolicy -PolicyArguments $statusArguments)
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
  if ($CustomOpenRouterRoutes.ContainsKey($openRouterRootRoute) -and
      [string]$CustomModelRecords[$openRouterRootRoute].id -ceq $openRouterRootRoute) {
    $openRouterRootRoute = [string]$CustomOpenRouterRoutes[$openRouterRootRoute]
  }
  $null = Resolve-OpenRouterRootRoute -Route $openRouterRootRoute
  foreach ($argument in $oprArguments) {
    if ($argument -in @('--model', '-m') -or $argument -like '--model=*' -or $argument -like '-m=*') {
      [Console]::Error.WriteLine('airlock: OpenRouter roots are selected by exact registry route; --model and -m cannot be forwarded.')
      exit 2
    }
  }
  foreach ($marker in @('AIRLOCK_HYBRID', 'AIRLOCK_GPT_HYBRID', 'AIRLOCK_GROK_HYBRID', 'AIRLOCK_OPENROUTER_HYBRID', 'AIRLOCK_OPENMODEL_HYBRID')) {
    Remove-Item -LiteralPath "Env:$marker" -ErrorAction SilentlyContinue
  }
  # Every other root seeds the session effort here. Without it the Fast
  # transition binding has no managed effort to validate and the launch fails.
  $oprArguments = Add-DefaultEffort $oprArguments $MainEffort
  Invoke-AirlockSession 'openrouter-pure' '' '' $oprArguments $openRouterRootRoute
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'om') {
  $omArguments = @()
  if ($Arguments.Count -gt 1) {
    $omArguments = @($Arguments[1..($Arguments.Count - 1)])
  }
  if ($omArguments.Count -gt 0 -and $omArguments[0] -in @('--help', '-h')) {
    Write-Host 'Usage: airlock om [ROUTE] [Claude arguments...]'
    Write-Host ''
    Write-Host 'Start a local open-model-only session on one exact enabled registry route.'
    Write-Host 'Omit ROUTE in an interactive terminal to choose from the offline registry.'
    Write-Host 'Use airlock open-model to manage the loopback registry.'
    return
  }
  $openModelRootRoute = ''
  if ($omArguments.Count -gt 0 -and $omArguments[0] -notlike '-*') {
    $openModelRootRoute = [string]$omArguments[0]
    $omArguments = if ($omArguments.Count -gt 1) { @($omArguments[1..($omArguments.Count - 1)]) } else { @() }
  } else {
    $openModelRootRoute = Select-OpenModelRootRoute
  }
  $null = Resolve-OpenModelRootRoute -Route $openModelRootRoute
  foreach ($argument in $omArguments) {
    if ($argument -in @('--model', '-m') -or $argument -like '--model=*' -or $argument -like '-m=*') {
      [Console]::Error.WriteLine('airlock: local open-model roots are selected by exact registry route; --model and -m cannot be forwarded.')
      exit 2
    }
  }
  foreach ($marker in @('AIRLOCK_HYBRID', 'AIRLOCK_GPT_HYBRID', 'AIRLOCK_GROK_HYBRID', 'AIRLOCK_OPENROUTER_HYBRID', 'AIRLOCK_OPENMODEL_HYBRID')) {
    Remove-Item -LiteralPath "Env:$marker" -ErrorAction SilentlyContinue
  }
  # No Add-DefaultEffort here on purpose: the loopback adapter's
  # translate_request() accepts no thinking/reasoning/effort field, so this
  # root never advertises effort capabilities in the first place. The exact
  # wire model itself is resolved server-side from the registry route.
  Invoke-AirlockSession 'openmodel-pure' '' '' $omArguments '' $openModelRootRoute
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'open-model') {
  $openModelRest = @()
  if ($Arguments.Count -gt 1) { $openModelRest = @($Arguments[1..($Arguments.Count - 1)]) }
  $openModelExitCode = 1
  Invoke-OpenModelRegistry -ModelArguments $openModelRest -Result ([ref]$openModelExitCode)
  exit $openModelExitCode
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
  foreach ($marker in @('AIRLOCK_HYBRID', 'AIRLOCK_GPT_HYBRID', 'AIRLOCK_GROK_HYBRID', 'AIRLOCK_OPENROUTER_HYBRID', 'AIRLOCK_OPENMODEL_HYBRID')) {
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
    if ($hybridCandidate -eq 'auto') {
      # Reserved value: resolve under the local access policy, never an alias.
      $rootAlias = 'auto'
    } elseif ($hybridCandidate -like 'om:*') {
      # om: is the sole hybrid local-open-model namespace, checked before the
      # generic route-shaped probe below so it is never ambiguous with an
      # OpenRouter registry lookup.
      $omHybridRoute = $hybridCandidate.Substring(3)
      if (Test-OpenModelRouteEnabled -Route $omHybridRoute) {
        $record = Resolve-OpenModelRootRoute -Route $omHybridRoute
        $script:HybridSelectedOpenModelRoute = [string]$record.route
        $script:HybridSelectedOpenModelModel = [string]$record.model
      } else {
        [Console]::Error.WriteLine("airlock: '$omHybridRoute' is not an enabled local open-model route.")
        exit 2
      }
    } elseif ($CustomOpenRouterRoutes.ContainsKey($hybridCandidate) -and
              [string]$CustomModelRecords[$hybridCandidate].id -ceq $hybridCandidate) {
      $customRoute = [string]$CustomOpenRouterRoutes[$hybridCandidate]
      $record = Resolve-OpenRouterRootRoute -Route $customRoute
      $script:HybridSelectedRoute = [string]$record.route
      $script:HybridSelectedModel = [string]$record.model
    } elseif ($HybridRoots.ContainsKey($hybridCandidate)) {
      if ($CustomModelRecords.ContainsKey($hybridCandidate) -and
          [string]$CustomModelRecords[$hybridCandidate].id -cne $hybridCandidate) {
        [Console]::Error.WriteLine("airlock: custom model IDs must be used exactly as declared: $hybridCandidate")
        exit 2
      }
      $rootAlias = $hybridCandidate
    } else {
      $hybridCandidateAlias = Resolve-OpenAIAlias $hybridCandidate
      if ($hybridCandidateAlias -in @('astra', 'sol', 'terra', 'luna')) { $rootAlias = $hybridCandidateAlias }
    }
    if (-not $rootAlias -and -not $script:HybridSelectedRoute -and
        -not $script:HybridSelectedOpenModelRoute -and $hybridCandidate -and
        $hybridCandidate -notlike '-*' -and
        $hybridCandidate -cmatch '^[a-z0-9]+([._:-][a-z0-9]+)*$') {
      # A route-shaped first word tries the OpenRouter registry before anything
      # else. Anything that cannot be a route slug (flags, prose) stays untouched.
      # The regex is case sensitive on purpose so it matches the POSIX launcher.
      if (Test-OpenRouterRouteEnabled -Route $hybridCandidate) {
        $record = Resolve-OpenRouterRootRoute -Route $hybridCandidate
        $script:HybridSelectedRoute = [string]$record.route
        $script:HybridSelectedModel = [string]$record.model
        $rootAlias = $null
      } else {
        [Console]::Error.WriteLine("airlock: '$hybridCandidate' is neither a hybrid root alias nor an enabled OpenRouter route.")
        exit 2
      }
    }
    if ($rootAlias -or $script:HybridSelectedRoute -or $script:HybridSelectedOpenModelRoute) {
      if ($hybridArgs.Count -gt 1) { $hybridArgs = @($hybridArgs[1..($hybridArgs.Count - 1)]) } else { $hybridArgs = @() }
    }
  }

  for ($i = 0; $i -lt $hybridArgs.Count; $i++) {
    if ($hybridArgs[$i] -eq '--model' -or $hybridArgs[$i] -eq '-m') {
      if ($i + 1 -lt $hybridArgs.Count) { $hybridArgs[$i + 1] = Normalize-OpenAIModelId $hybridArgs[$i + 1] }
    } elseif ($hybridArgs[$i] -like '--model=*' -or $hybridArgs[$i] -like '-m=*') {
      $separator = $hybridArgs[$i].IndexOf('=')
      $hybridArgs[$i] = $hybridArgs[$i].Substring(0, $separator + 1) + (Normalize-OpenAIModelId $hybridArgs[$i].Substring($separator + 1))
    }
  }
  $explicitModel = Get-ExplicitModel $hybridArgs
  if ($explicitModel) {
    if ($explicitModel -like 'gpt-*') {
      if (-not (Resolve-OpenAIAlias $explicitModel)) {
        [Console]::Error.WriteLine("airlock: model '$explicitModel' is not shipped or enabled in models.json.")
        exit 2
      }
    } elseif ($explicitModel -like 'grok-*') {
      if (-not (Resolve-GrokAlias $explicitModel)) {
        [Console]::Error.WriteLine("airlock: Grok model '$explicitModel' is not shipped or enabled in models.json.")
        exit 2
      }
    } elseif ($explicitModel -like 'claude-*') {
      # Native Claude exact IDs keep their existing launcher behavior.
    } elseif (-not $rootAlias -and -not $script:HybridSelectedRoute -and -not $script:HybridSelectedOpenModelRoute) {
      # A bare unrecognized ID may declare itself through models.json, but only
      # when nothing else has chosen the root; otherwise the provider and
      # OpenRouter mismatch checks below keep their exact messages.
      if ($CustomOpenRouterRoutes.ContainsKey($explicitModel) -and
          [string]$CustomModelRecords[$explicitModel].id -ceq $explicitModel) {
        $customRoute = [string]$CustomOpenRouterRoutes[$explicitModel]
        $record = Resolve-OpenRouterRootRoute -Route $customRoute
        $script:HybridSelectedRoute = [string]$record.route
        $script:HybridSelectedModel = [string]$record.model
      } else {
        [Console]::Error.WriteLine("airlock: model '$explicitModel' is not shipped or enabled in models.json.")
        exit 2
      }
    }
  }
  if (-not $script:HybridSelectedRoute -and -not $script:HybridSelectedOpenModelRoute -and -not $rootAlias -and -not $explicitModel) {
    $rootAlias = if ($chooseRoot) { Select-HybridRoot } else { $DefaultHybridModel }
  }
  if ($rootAlias -eq 'auto') {
    $rootAlias = Resolve-AutoHybridRoot
  }

  if ($script:HybridSelectedOpenModelRoute) {
    # A local open-model route is selected by exact registry slug. A
    # forwarded --model may only restate the resolved root wire model;
    # anything else would move root traffic off the selected route inside
    # one argv.
    $rootModel = $script:HybridSelectedOpenModelModel
    $rootName = "local open-model $($script:HybridSelectedOpenModelRoute)"
    $rootProvider = 'openmodel'
    if ($explicitModel -and $explicitModel -ne $rootModel) {
      [Console]::Error.WriteLine("airlock: forwarded --model disagrees with the selected local open-model root route ($rootModel).")
      exit 2
    }
    if (-not $explicitModel) {
      $hybridArgs = @('--model', $rootModel) + $hybridArgs
    }
  } elseif ($script:HybridSelectedRoute) {
    # An OpenRouter route is selected by exact registry slug. A forwarded
    # --model may only restate the resolved root model; anything else would
    # move root traffic off the selected route inside one argv.
    $rootModel = $script:HybridSelectedModel
    $rootName = "OpenRouter $($script:HybridSelectedRoute)"
    $rootProvider = 'openrouter'
    if ($explicitModel -and $explicitModel -ne $rootModel) {
      [Console]::Error.WriteLine("airlock: forwarded --model disagrees with the selected OpenRouter root route ($rootModel).")
      exit 2
    }
    if (-not $explicitModel) {
      $hybridArgs = @('--model', $rootModel) + $hybridArgs
    }
  } elseif ($rootAlias) {
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
  if ($rootProvider -ne 'openmodel') {
    $hybridArgs = Add-DefaultEffort $hybridArgs $MainEffort
  }
  # No default effort injection for an open-model root: the loopback
  # adapter's translate_request() accepts no thinking/reasoning/effort field.
  # An explicit user --effort still passes through unchanged.

  Start-ProxyIfNeeded
  foreach ($marker in @('AIRLOCK_HYBRID', 'AIRLOCK_GPT_HYBRID', 'AIRLOCK_GROK_HYBRID', 'AIRLOCK_OPENROUTER_HYBRID', 'AIRLOCK_OPENMODEL_HYBRID')) {
    Remove-Item -LiteralPath "Env:$marker" -ErrorAction SilentlyContinue
  }
  if ($rootProvider -eq 'openrouter') {
    $env:AIRLOCK_OPENROUTER_HYBRID = '1'
    # The bridge derives this root's identity from the exact registry route and
    # rejects a request carrying root_model or root_name, so the launcher must
    # not restate them. The POSIX launcher passes them because it builds the
    # child environment itself; this path delegates that to the bridge, so only
    # the route travels.
    Invoke-AirlockSession 'hybrid-openrouter-root' '' '' $hybridArgs $script:HybridSelectedRoute
  }
  if ($rootProvider -eq 'openmodel') {
    $env:AIRLOCK_OPENMODEL_HYBRID = '1'
    # Same rationale as the OpenRouter hybrid root above: only the exact
    # registry route travels, and the bridge resolves the wire model itself.
    Invoke-AirlockSession 'hybrid-openmodel-root' '' '' $hybridArgs '' $script:HybridSelectedOpenModelRoute
  }
  if ($rootProvider -eq 'openai') {
    Test-FastRootModel $rootModel
    Enable-ExplicitOpenAIRoot $rootModel
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
      if ($DefaultHybridModel -eq 'auto') {
        $autoAlias = Resolve-AutoHybridRoot
        $autoRoot = $HybridRoots[$autoAlias]
        $hybridName = "auto (resolves now to $($autoRoot[1]) ($($autoRoot[0])))"
      } elseif ($CustomOpenRouterRoutes.ContainsKey($DefaultHybridModel) -and
                [string]$CustomModelRecords[$DefaultHybridModel].id -ceq $DefaultHybridModel) {
        $hybridName = "Custom OpenRouter ($DefaultHybridModel)"
      } else {
        $hybridName = "$($HybridRoots[$DefaultHybridModel][1]) ($($HybridRoots[$DefaultHybridModel][0]))"
      }
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
  Write-Host "Anthropic rate limits: $AnthropicRateLimit"
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
          if ($argument -in @('--model', '-m') -or $argument -like '--model=*' -or $argument -like '-m=*') {
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
      } elseif (Resolve-OpenAIAlias $Arguments[0]) {
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
Enable-ExplicitOpenAIRoot $selected
Invoke-AirlockSession 'openai-pure' $selected $modelName $cmdArgs
