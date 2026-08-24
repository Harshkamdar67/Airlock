# Stub-based native Windows checks. These tests do not use OAuth or a model.
$ErrorActionPreference = 'Stop'

# A suite that runs inside an Airlock session must not inherit that session's
# helpers, saved depth, or armed Fast transition credentials.
foreach ($name in @(
  'AIRLOCK_ACCESS_HELPER', 'AIRLOCK_POLICY_HELPER', 'AIRLOCK_SESSION_ROUTER_URL',
  'AIRLOCK_UPDATE_NOTICE_FILE', 'AIRLOCK_SESSION_SNAPSHOT',
  'AIRLOCK_SESSION_SNAPSHOT_SHA256', 'AIRLOCK_AGENT_DEPTH',
  'AIRLOCK_FAST_TRANSITION_CHANNEL', 'AIRLOCK_FAST_TRANSITION_NONCE'
)) {
  Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}

$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$PowerShellFiles = @(
  (Join-Path $Root 'bin\airlock.ps1'),
  (Join-Path $Root 'scripts\install.ps1'),
  (Join-Path $Root 'scripts\doctor.ps1')
)
# PowerShell reads an undefined variable as empty instead of failing, so a
# parse check alone would let a typo or a missing default through. Compare the
# variables each script reads against the ones it assigns.
$AutomaticVariables = @(
  'args', 'false', 'true', 'null', 'HOME', 'PSScriptRoot', 'PSCommandPath',
  'LASTEXITCODE', 'Matches', '_', 'PSItem', 'MyInvocation', 'PID', 'Host',
  'Error', 'input', 'foreach', 'switch', 'PSVersionTable', 'IsWindows',
  'ErrorActionPreference', 'ProgressPreference', 'InformationPreference',
  'WarningPreference', 'VerbosePreference', 'DebugPreference', 'OutputEncoding'
)
# UnqualifiedPath is empty for a plain name on Windows PowerShell 5.1, so strip
# the scope prefix by hand instead. Drive-qualified names such as $env:PATH are
# skipped before this runs.
function Get-BareVariableName($VariablePath) {
  $name = $VariablePath.UserPath
  $separator = $name.LastIndexOf(':')
  if ($separator -ge 0) { return $name.Substring($separator + 1) }
  return $name
}
foreach ($path in $PowerShellFiles) {
  $tokens = $null
  $errors = $null
  $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
  if ($errors.Count -gt 0) {
    throw "PowerShell parse failed for $path`: $($errors[0].Message)"
  }
  # Compare bare names so a scope prefix such as $script:Failures matches the
  # plain $Failures that defined it.
  $assigned = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach ($name in $AutomaticVariables) { [void]$assigned.Add($name) }
  foreach ($node in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] }, $true)) {
    if ($node.Left -is [Management.Automation.Language.VariableExpressionAst]) {
      [void]$assigned.Add((Get-BareVariableName $node.Left.VariablePath))
    }
  }
  foreach ($node in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.ParameterAst] }, $true)) {
    [void]$assigned.Add((Get-BareVariableName $node.Name.VariablePath))
  }
  foreach ($node in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.ForEachStatementAst] }, $true)) {
    [void]$assigned.Add((Get-BareVariableName $node.Variable.VariablePath))
  }
  foreach ($node in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.ConvertExpressionAst] }, $true)) {
    if ($node.Type.TypeName.Name -eq 'ref' -and $node.Child -is [Management.Automation.Language.VariableExpressionAst]) {
      [void]$assigned.Add((Get-BareVariableName $node.Child.VariablePath))
    }
  }
  foreach ($node in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.VariableExpressionAst] }, $true)) {
    if ($node.VariablePath.IsDriveQualified) { continue }
    if (-not $assigned.Contains((Get-BareVariableName $node.VariablePath))) {
      throw "PowerShell script $path reads `$$($node.VariablePath.UserPath) without assigning it"
    }
  }
}

# The launcher must offer effort levels for pinned GPT models and leave real
# Claude model IDs to Claude Code's own detection.
$LauncherText = [IO.File]::ReadAllText((Join-Path $Root 'bin\airlock.ps1'))
foreach ($variable in @(
  'ANTHROPIC_DEFAULT_FABLE_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'ANTHROPIC_CUSTOM_MODEL_OPTION'
)) {
  if ($LauncherText -notmatch [regex]::Escape("'${variable}_SUPPORTED_CAPABILITIES'")) {
    throw "bin\airlock.ps1 does not clear ${variable}_SUPPORTED_CAPABILITIES between sessions"
  }
  if ($LauncherText -notmatch [regex]::Escape("Set-GptEffortCapabilities '$variable'")) {
    throw "bin\airlock.ps1 does not declare effort capabilities for $variable"
  }
}
if ($LauncherText -notmatch [regex]::Escape("StartsWith('claude-')")) {
  throw 'bin\airlock.ps1 must skip capability declarations for Claude model IDs'
}

$RealPython = (Get-Command python.exe -ErrorAction Stop | Select-Object -First 1).Source
$PowerShellExe = (Get-Process -Id $PID).Path
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("airlock-windows-test-{0}" -f [Guid]::NewGuid().ToString('N'))
$StubDir = Join-Path $TempRoot 'stubs'
$InstallDir = Join-Path $TempRoot 'bin'
$ConfigDir = Join-Path $TempRoot 'config'
$AgentDir = Join-Path $TempRoot 'agents'
New-Item -ItemType Directory -Path $StubDir -Force | Out-Null

$ClaudeStub = Join-Path $StubDir 'claude-launch-stub.exe'
$ClaudeStubSource = @'
using System;
using System.IO;

public static class ClaudeLaunchStub {
  public static int Main(string[] args) {
    Console.WriteLine("MODEL=" + (Environment.GetEnvironmentVariable("ANTHROPIC_MODEL") ?? "unset"));
    Console.WriteLine("CUSTOM_MODEL=" + (Environment.GetEnvironmentVariable("ANTHROPIC_CUSTOM_MODEL_OPTION") ?? "unset"));
    Console.WriteLine("DEFAULT_FABLE=" + (Environment.GetEnvironmentVariable("ANTHROPIC_DEFAULT_FABLE_MODEL") ?? "unset"));
    Console.WriteLine("DEFAULT_OPUS=" + (Environment.GetEnvironmentVariable("ANTHROPIC_DEFAULT_OPUS_MODEL") ?? "unset"));
    Console.WriteLine("DEFAULT_SONNET=" + (Environment.GetEnvironmentVariable("ANTHROPIC_DEFAULT_SONNET_MODEL") ?? "unset"));
    Console.WriteLine("DEFAULT_HAIKU=" + (Environment.GetEnvironmentVariable("ANTHROPIC_DEFAULT_HAIKU_MODEL") ?? "unset"));
    Console.WriteLine("SMALL_FAST=" + (Environment.GetEnvironmentVariable("ANTHROPIC_SMALL_FAST_MODEL") ?? "unset"));
    Console.WriteLine("AUTO_MODE_MODEL=" + (Environment.GetEnvironmentVariable("CLAUDE_CODE_AUTO_MODE_MODEL") ?? "unset"));
    Console.WriteLine("OPENROUTER_BRIDGE=" + (Environment.GetEnvironmentVariable("AIRLOCK_OPENROUTER_HYBRID") ?? "unset"));
    Console.WriteLine("OPENROUTER_KEY_SET=" + (
      String.IsNullOrEmpty(Environment.GetEnvironmentVariable("OPENROUTER_API_KEY")) ? "no" : "yes"
    ));
    Console.WriteLine("FABLE_NAME=" + (Environment.GetEnvironmentVariable("ANTHROPIC_DEFAULT_FABLE_MODEL_NAME") ?? "unset"));
    Console.WriteLine("ACTIVE_PROFILE=" + (Environment.GetEnvironmentVariable("AIRLOCK_ACTIVE_PROFILE") ?? "unset"));
    Console.WriteLine("UPDATE_NOTICE=" + (Environment.GetEnvironmentVariable("AIRLOCK_UPDATE_NOTICE_FILE") ?? "unset"));
    Console.WriteLine("ROOT_MODEL=" + (Environment.GetEnvironmentVariable("AIRLOCK_ROOT_MODEL") ?? "unset"));
    Console.WriteLine("DISCOVERY_MODEL=" + (Environment.GetEnvironmentVariable("AIRLOCK_DISCOVERY_MODEL") ?? "unset"));
    Console.WriteLine("SESSION_ROUTER=" + (Environment.GetEnvironmentVariable("AIRLOCK_SESSION_ROUTER_URL") ?? "unset"));
    Console.WriteLine("POLICY_HELPER=" + (Environment.GetEnvironmentVariable("AIRLOCK_POLICY_HELPER") ?? "unset"));
    string snapshot = Environment.GetEnvironmentVariable("AIRLOCK_SESSION_SNAPSHOT");
    Console.WriteLine("SESSION_SNAPSHOT=" + (snapshot ?? "unset"));
    Console.WriteLine("SNAPSHOT_SHA256=" + (Environment.GetEnvironmentVariable("AIRLOCK_SESSION_SNAPSHOT_SHA256") ?? "unset"));
    Console.WriteLine("SNAPSHOT_EXISTS=" + (snapshot != null && File.Exists(snapshot) ? "yes" : "no"));
    Console.WriteLine("COMPACT_WINDOW=" + (Environment.GetEnvironmentVariable("CLAUDE_CODE_AUTO_COMPACT_WINDOW") ?? "unset"));
    Console.WriteLine("MAX_CONTEXT=" + (Environment.GetEnvironmentVariable("CLAUDE_CODE_MAX_CONTEXT_TOKENS") ?? "unset"));
    for (int index = 0; index < args.Length; index++) {
      Console.WriteLine("ARG=" + args[index]);
      if (args[index] == "--settings" && index + 1 < args.Length) {
        string settingsPath = args[index + 1];
        bool settingsFile = File.Exists(settingsPath);
        string settings = settingsFile ? File.ReadAllText(settingsPath) : settingsPath;
        Console.WriteLine("SETTINGS_FILE=" + (settingsFile ? "yes" : "no"));
        Console.WriteLine("SETTINGS_PATH=" + settingsPath);
        settings = settings.Replace(" ", "");
        if (settings.Contains("\"fastMode\":true")) Console.WriteLine("FAST_MODE=on");
        else if (settings.Contains("\"fastMode\":false")) Console.WriteLine("FAST_MODE=off");
        else Console.WriteLine("FAST_MODE=inherit");
      }
      if (args[index] == "--append-system-prompt-file" && index + 1 < args.Length) {
        string guidancePath = args[index + 1];
        bool guidanceFile = File.Exists(guidancePath);
        Console.WriteLine("GUIDANCE_FILE=" + (guidanceFile ? "yes" : "no"));
        Console.WriteLine("GUIDANCE_PATH=" + guidancePath);
        Console.WriteLine("GUIDANCE_NONEMPTY=" + (
          guidanceFile && File.ReadAllText(guidancePath).Length > 0 ? "yes" : "no"
        ));
      }
    }
    return 0;
  }
}
'@
Add-Type -TypeDefinition $ClaudeStubSource -Language CSharp -OutputAssembly $ClaudeStub -OutputType ConsoleApplication

function Invoke-LauncherProcess(
  [string]$Launcher,
  [string[]]$LauncherArguments,
  [bool]$ExpectSuccess = $true,
  [hashtable]$ExtraEnvironment = $null
) {
  $quotedLauncher = '"' + $Launcher.Replace('"', '\"') + '"'
  $quotedArguments = @()
  foreach ($argument in $LauncherArguments) {
    $quotedArguments += '"' + $argument.Replace('"', '\"') + '"'
  }
  $processInfo = New-Object Diagnostics.ProcessStartInfo
  $processInfo.FileName = $PowerShellExe
  $processInfo.Arguments = "-NoProfile -NonInteractive -File $quotedLauncher $($quotedArguments -join ' ')"
  $processInfo.UseShellExecute = $false
  $processInfo.RedirectStandardOutput = $true
  $processInfo.RedirectStandardError = $true
  [void]$processInfo.EnvironmentVariables.Remove('ANTHROPIC_API_KEY')
  [void]$processInfo.EnvironmentVariables.Remove('ANTHROPIC_AUTH_TOKEN')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_DEFAULT_PROFILE')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_HYBRID_MODEL')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_MODEL')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_OPENAI_FAST')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_ANTHROPIC_FAST')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_ANTHROPIC_FAST_AUTHORIZED')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_EXTRA_USAGE_POLICY')
  # The launcher keeps a window the user set themselves, so a suite that runs
  # inside an Airlock session would otherwise read that session's window back as
  # the launcher's own choice.
  [void]$processInfo.EnvironmentVariables.Remove('CLAUDE_CODE_AUTO_COMPACT_WINDOW')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_CONTEXT_WINDOW')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_SESSION_ROUTER_URL')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_UPDATE_NOTICE_FILE')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_POLICY_HELPER')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_SESSION_SNAPSHOT')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_SESSION_SNAPSHOT_SHA256')
  if ($ExtraEnvironment) {
    foreach ($name in $ExtraEnvironment.Keys) {
      $processInfo.EnvironmentVariables[$name] = [string]$ExtraEnvironment[$name]
    }
  }
  $process = [Diagnostics.Process]::Start($processInfo)
  $standardOutput = $process.StandardOutput.ReadToEnd()
  $standardError = $process.StandardError.ReadToEnd()
  $process.WaitForExit()
  if ($ExpectSuccess -and $process.ExitCode -ne 0) {
    throw "Windows launcher exited $($process.ExitCode): $standardError"
  }
  if (-not $ExpectSuccess -and $process.ExitCode -eq 0) {
    throw 'Windows launcher unexpectedly accepted invalid saved configuration.'
  }
  return [pscustomobject]@{
    ExitCode = $process.ExitCode
    Output = $standardOutput.Replace("`r", '')
    Error = $standardError.Replace("`r", '')
  }
}

function Write-StubAt([string]$Directory, [string]$Name, [string]$Body = '@exit /b 0') {
  [IO.File]::WriteAllText(
    (Join-Path $Directory $Name),
    "@echo off`r`n$Body`r`n",
    (New-Object Text.ASCIIEncoding)
  )
}

function Write-Stub([string]$Name, [string]$Body = '@exit /b 0') {
  Write-StubAt $StubDir $Name $Body
}

Write-Stub 'git.cmd'
Write-Stub 'bash.cmd'
Write-Stub 'claude.cmd' "@if `"%1`"==`"--version`" echo Claude Code test`r`n@exit /b 0"
Write-Stub 'claude-code-proxy.cmd' "@if not `"%AIRLOCK_TEST_PROXY_LOG%`"==`"`" echo %CCP_CONFIG_DIR%^|%XDG_STATE_HOME%^|%*>>`"%AIRLOCK_TEST_PROXY_LOG%`"`r`n@if `"%1`"==`"--version`" echo Proxy test`r`n@exit /b 0"
Write-Stub 'python.cmd'

$OldPath = $env:PATH
$OldInstall = $env:AIRLOCK_INSTALL_DIR
$OldConfig = $env:AIRLOCK_CONFIG_DIR
$OldAgent = $env:AIRLOCK_AGENT_DIR
try {
  $SystemPath = "$env:SystemRoot\System32;$env:SystemRoot"
  $MissingClaudeDir = Join-Path $TempRoot 'missing-claude-stubs'
  New-Item -ItemType Directory -Path $MissingClaudeDir -Force | Out-Null
  foreach ($name in @('git.cmd', 'bash.cmd', 'claude-code-proxy.cmd', 'python.cmd')) {
    Write-StubAt $MissingClaudeDir $name
  }
  $env:PATH = "$MissingClaudeDir;$SystemPath"
  $env:AIRLOCK_INSTALL_DIR = Join-Path $TempRoot 'missing-claude-bin'
  $env:AIRLOCK_CONFIG_DIR = Join-Path $TempRoot 'missing-claude-config'
  $env:AIRLOCK_AGENT_DIR = Join-Path $TempRoot 'missing-claude-agents'
  $missingClaudeBlocked = $false
  try {
    & (Join-Path $Root 'scripts\install.ps1') *> $null
  } catch {
    $missingClaudeBlocked = $_.Exception.Message -like '*Claude Code is required and was not found on PATH*'
  }
  if (-not $missingClaudeBlocked) { throw 'Windows installer accepted a missing Claude Code binary.' }

  $MissingProxyDir = Join-Path $TempRoot 'missing-proxy-stubs'
  New-Item -ItemType Directory -Path $MissingProxyDir -Force | Out-Null
  foreach ($name in @('git.cmd', 'bash.cmd', 'claude.cmd', 'python.cmd')) {
    Write-StubAt $MissingProxyDir $name
  }
  $env:PATH = "$MissingProxyDir;$SystemPath"
  $env:AIRLOCK_INSTALL_DIR = Join-Path $TempRoot 'missing-proxy-bin'
  $env:AIRLOCK_CONFIG_DIR = Join-Path $TempRoot 'missing-proxy-config'
  $env:AIRLOCK_AGENT_DIR = Join-Path $TempRoot 'missing-proxy-agents'
  $missingProxyBlocked = $false
  try {
    & (Join-Path $Root 'scripts\install.ps1') *> $null
  } catch {
    $missingProxyBlocked = $_.Exception.Message -like '*claude-code-proxy is required and was not found on PATH*'
  }
  if (-not $missingProxyBlocked) { throw 'Windows installer accepted a missing claude-code-proxy binary.' }

  $env:PATH = "$StubDir;$OldPath"
  $env:AIRLOCK_INSTALL_DIR = $InstallDir
  $env:AIRLOCK_CONFIG_DIR = $ConfigDir
  $env:AIRLOCK_AGENT_DIR = $AgentDir

  & (Join-Path $Root 'scripts\install.ps1') -WithAgent
  if ($LASTEXITCODE -ne 0) { throw 'Windows installer returned a failure.' }

  foreach ($relative in @(
    'airlock', 'airlock.cmd', 'airlock.ps1', 'airlock-access.py',
    'airlock_policy.py', 'airlock_openrouter_auth.py',
    'airlock_openrouter_presets.py', 'airlock_openrouter_models.py',
    'airlock-update.py', 'airlock-router.py', 'airlock-hybrid.py'
  )) {
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $relative) -PathType Leaf)) {
      throw "Windows installer missed $relative"
    }
  }
  foreach ($removed in @(
    'airlock_runtime.py', 'airlock-delegate.py', 'airlock-workflow.py',
    'airlock-child.py', 'airlock-check.py'
  )) {
    if (Test-Path -LiteralPath (Join-Path $InstallDir $removed)) {
      throw "Windows installer wrote a removed legacy file: $removed"
    }
  }
  if (-not (Test-Path -LiteralPath (Join-Path $ConfigDir 'managed-bundle.json') -PathType Leaf)) {
    throw 'Windows installer missed the managed bundle marker.'
  }
  if (Test-Path -LiteralPath (Join-Path $ConfigDir 'openrouter-registry.json')) {
    throw 'Windows installer enabled OpenRouter without an explicit add command.'
  }
  if (-not (Test-Path -LiteralPath (Join-Path $ConfigDir 'plugins\airlock\hooks\hooks.json') -PathType Leaf)) {
    throw 'Windows installer missed the managed plugin.'
  }
  foreach ($relative in @(
    'skills\airlock-fast\SKILL.md',
    'scripts\fast-session-end.sh', 'scripts\fast-session-end.py',
    'scripts\file_safety.py', 'scripts\update-notice.sh', 'scripts\update-notice.py',
    'scripts\worktree.py', 'scripts\worktree-create.sh', 'scripts\worktree-remove.sh'
  )) {
    if (-not (Test-Path -LiteralPath (Join-Path $ConfigDir "plugins\airlock\$relative") -PathType Leaf)) {
      throw "Windows installer missed plugin file $relative"
    }
  }
  if (-not (Test-Path -LiteralPath (Join-Path $AgentDir 'airlock-worker.md') -PathType Leaf)) {
    throw 'Windows installer missed the optional worker.'
  }

  [IO.File]::WriteAllText((Join-Path $InstallDir 'airlock.cmd'), 'unmanaged file')
  $blocked = $false
  try {
    & (Join-Path $Root 'scripts\install.ps1') *> $null
  } catch {
    $blocked = $_.Exception.Message -like '*refusing to overwrite an unmanaged file*'
  }
  if (-not $blocked) { throw 'Windows installer replaced or accepted an unmanaged launcher.' }
  [IO.File]::Copy((Join-Path $Root 'bin\airlock.cmd'), (Join-Path $InstallDir 'airlock.cmd'), $true)

  $InstalledLauncher = Join-Path $InstallDir 'airlock.ps1'
  $InstalledConfig = Join-Path $ConfigDir 'config'
  $env:AIRLOCK_PYTHON = $RealPython
  $env:AIRLOCK_CONFIG_FILE = $InstalledConfig
  $env:AIRLOCK_ACCESS_FILE = Join-Path $ConfigDir 'access.json'
  $env:AIRLOCK_OPENAI_DIRECT_AGENTS_FILE = Join-Path $ConfigDir 'openai-direct-agents.json'
  $env:AIRLOCK_ANTHROPIC_DIRECT_AGENTS_FILE = Join-Path $ConfigDir 'anthropic-direct-agents.json'
  $env:AIRLOCK_HYBRID_AGENTS_FILE = Join-Path $ConfigDir 'hybrid-agents.json'
  $env:AIRLOCK_CLAUDE_AGENTS_FILE = Join-Path $ConfigDir 'claude-agents.json'
  $env:AIRLOCK_PLUGIN_DIR = Join-Path $ConfigDir 'plugins\airlock'
  $env:AIRLOCK_MANAGED_BUNDLE_FILE = Join-Path $ConfigDir 'managed-bundle.json'
  $env:AIRLOCK_MANAGED_BIN_DIR = $InstallDir
  $env:AIRLOCK_SESSION_RUNTIME_DIR = Join-Path $TempRoot 'sessions'
  $env:AIRLOCK_REAL_CLAUDE = $ClaudeStub
  $env:AIRLOCK_SKIP_HEALTH_CHECK = '1'

  $VersionCommand = Invoke-LauncherProcess $InstalledLauncher @('version')
  $ExpectedVersion = (Get-Content -LiteralPath (Join-Path $Root 'VERSION') -Raw).Trim()
  $ExpectedVersionPattern = '(?m)^Airlock ' + [regex]::Escape($ExpectedVersion) + '$'
  if ($VersionCommand.Output -notmatch $ExpectedVersionPattern) {
    throw "Windows version command returned unexpected output: $($VersionCommand.Output)"
  }
  $ModelsCommand = Invoke-LauncherProcess $InstalledLauncher @('models')
  foreach ($expected in @(
    'airlock grok [model] [claude arguments]',
    'airlock opr [route] [claude arguments]',
    'airlock grok     Start the saved Grok-only orchestrator (subscription proxy)',
    'airlock opr      Start an OpenRouter-only session on an exact registry route',
    'Grok root aliases: grok, composer',
    'Hybrid root aliases: auto, sonnet, sol, terra, luna, opus, fable, haiku, grok, composer'
  )) {
    if (-not $ModelsCommand.Output.Contains($expected)) {
      throw "Windows models command omitted '$expected': $($ModelsCommand.Output)"
    }
  }
  $OprHelp = Invoke-LauncherProcess $InstalledLauncher @('opr', '--help')
  $OprShortHelp = Invoke-LauncherProcess $InstalledLauncher @('opr', '-h')
  if ($OprHelp.Output -notmatch '(?m)^Usage: airlock opr \[ROUTE\] \[Claude arguments\.\.\.\]$' -or
      $OprHelp.Output -notmatch 'offline registry' -or
      $OprShortHelp.Output -ne $OprHelp.Output -or
      $OprHelp.Output -match 'airlock orp') {
    throw "Windows OPR help was not dispatched cleanly: $($OprHelp.Output)"
  }
  $UpdateHelp = Invoke-LauncherProcess $InstalledLauncher @('update', '--help')
  if ($UpdateHelp.Output -notmatch '(?m)^usage: airlock update') {
    throw "Windows update help was not dispatched: $($UpdateHelp.Output)"
  }
  [void](Invoke-LauncherProcess $InstalledLauncher @('update', '--check', '--yes') $false)

  # Exercise only the launcher dispatch here. The credential backend itself has
  # isolated DPAPI tests, so this mock cannot read or write a real key.
  $OpenRouterAuthStub = Join-Path $TempRoot 'openrouter-auth-stub.py'
  [IO.File]::WriteAllText(
    $OpenRouterAuthStub,
    "import os, sys`nprint('MOCK_OPENROUTER_AUTH=' + '|'.join(sys.argv[1:]))`nsys.exit(int(os.environ.get('AIRLOCK_TEST_HELPER_EXIT', '0')))`n",
    (New-Object Text.UTF8Encoding($false))
  )
  $OpenRouterStatus = Invoke-LauncherProcess $InstalledLauncher @('openrouter', 'auth', 'status') $true `
    @{ AIRLOCK_OPENROUTER_AUTH_HELPER = $OpenRouterAuthStub }
  if ($OpenRouterStatus.Output -notmatch '(?m)^MOCK_OPENROUTER_AUTH=status$') {
    throw "Windows OpenRouter auth status was not dispatched safely: $($OpenRouterStatus.Output)"
  }
  $FailedOpenRouterStatus = Invoke-LauncherProcess $InstalledLauncher @('openrouter', 'auth', 'status') $false @{
    AIRLOCK_OPENROUTER_AUTH_HELPER = $OpenRouterAuthStub
    AIRLOCK_TEST_HELPER_EXIT = '23'
  }
  if ($FailedOpenRouterStatus.ExitCode -ne 23) {
    throw "Windows OpenRouter auth helper exit status was lost: $($FailedOpenRouterStatus.ExitCode)"
  }
  $InvalidOpenRouterStatus = Invoke-LauncherProcess $InstalledLauncher `
    @('openrouter', 'auth', 'status', '--online') $false `
    @{ AIRLOCK_OPENROUTER_AUTH_HELPER = $OpenRouterAuthStub }
  if ($InvalidOpenRouterStatus.ExitCode -ne 2 -or
      $InvalidOpenRouterStatus.Error -notmatch 'usage: airlock openrouter auth status') {
    throw 'Windows OpenRouter auth status accepted an unsupported online probe.'
  }

  # The model helper is also mocked so this dispatch test remains offline and
  # cannot inspect a real registry or contact the public catalog.
  $OpenRouterModelsStub = Join-Path $TempRoot 'openrouter-models-stub.py'
  [IO.File]::WriteAllText(
    $OpenRouterModelsStub,
    "import os, sys`nprint('MOCK_OPENROUTER_MODELS=' + '|'.join(sys.argv[1:]))`nsys.exit(int(os.environ.get('AIRLOCK_TEST_HELPER_EXIT', '0')))`n",
    (New-Object Text.UTF8Encoding($false))
  )
  $OpenRouterRegistry = Join-Path `
    (Join-Path $TempRoot 'openrouter-private') 'openrouter-registry.json'
  $OpenRouterModels = Invoke-LauncherProcess $InstalledLauncher @('openrouter', 'models', 'list') $true @{
    AIRLOCK_OPENROUTER_MODELS_HELPER = $OpenRouterModelsStub
    AIRLOCK_OPENROUTER_REGISTRY_FILE = $OpenRouterRegistry
  }
  $ExpectedModelsDispatch = "MOCK_OPENROUTER_MODELS=--registry|$OpenRouterRegistry|list"
  if (-not $OpenRouterModels.Output.Contains($ExpectedModelsDispatch)) {
    throw "Windows OpenRouter models command was not dispatched safely: $($OpenRouterModels.Output)"
  }
  $FailedOpenRouterModels = Invoke-LauncherProcess $InstalledLauncher @('openrouter', 'models', 'list') $false @{
    AIRLOCK_OPENROUTER_MODELS_HELPER = $OpenRouterModelsStub
    AIRLOCK_OPENROUTER_REGISTRY_FILE = $OpenRouterRegistry
    AIRLOCK_TEST_HELPER_EXIT = '24'
  }
  if ($FailedOpenRouterModels.ExitCode -ne 24) {
    throw "Windows OpenRouter model helper exit status was lost: $($FailedOpenRouterModels.ExitCode)"
  }
  $OpenRouterPresets = Invoke-LauncherProcess $InstalledLauncher @('openrouter', 'models', 'presets') $true @{
    AIRLOCK_OPENROUTER_MODELS_HELPER = $OpenRouterModelsStub
    AIRLOCK_OPENROUTER_REGISTRY_FILE = $OpenRouterRegistry
  }
  $ExpectedPresetsDispatch = "MOCK_OPENROUTER_MODELS=--registry|$OpenRouterRegistry|presets"
  if (-not $OpenRouterPresets.Output.Contains($ExpectedPresetsDispatch)) {
    throw "Windows OpenRouter models presets was not dispatched safely: $($OpenRouterPresets.Output)"
  }
  $OpenRouterAddPreset = Invoke-LauncherProcess $InstalledLauncher `
    @('openrouter', 'models', 'add-preset', 'kimi-k3', '--yes') $true @{
    AIRLOCK_OPENROUTER_MODELS_HELPER = $OpenRouterModelsStub
    AIRLOCK_OPENROUTER_REGISTRY_FILE = $OpenRouterRegistry
  }
  $ExpectedAddPresetDispatch = "MOCK_OPENROUTER_MODELS=--registry|$OpenRouterRegistry|add-preset|kimi-k3|--yes"
  if (-not $OpenRouterAddPreset.Output.Contains($ExpectedAddPresetDispatch)) {
    throw "Windows OpenRouter models add-preset was not dispatched safely: $($OpenRouterAddPreset.Output)"
  }

  # An interactive helper prompt must reach the terminal before the helper reads
  # its answer. Capturing child stdout in a PowerShell variable hides the prompt
  # until exit and leaves the user waiting on an invisible question.
  $InteractiveModelsStub = Join-Path $TempRoot 'openrouter-models-interactive-stub.py'
  [IO.File]::WriteAllText(
    $InteractiveModelsStub,
    "import sys`nprint('PROMPT_VISIBLE', flush=True)`nsys.stdin.readline()`n",
    (New-Object Text.UTF8Encoding($false))
  )
  $interactiveInfo = New-Object Diagnostics.ProcessStartInfo
  $interactiveInfo.FileName = $PowerShellExe
  $interactiveInfo.Arguments = "-NoProfile -NonInteractive -File `"$InstalledLauncher`" openrouter models add-preset kimi-k3"
  $interactiveInfo.UseShellExecute = $false
  $interactiveInfo.RedirectStandardInput = $true
  $interactiveInfo.RedirectStandardOutput = $true
  $interactiveInfo.RedirectStandardError = $true
  $interactiveInfo.EnvironmentVariables['AIRLOCK_OPENROUTER_MODELS_HELPER'] = $InteractiveModelsStub
  $interactiveInfo.EnvironmentVariables['AIRLOCK_OPENROUTER_REGISTRY_FILE'] = $OpenRouterRegistry
  $interactiveProcess = [Diagnostics.Process]::Start($interactiveInfo)
  $promptTask = $interactiveProcess.StandardOutput.ReadLineAsync()
  if (-not $promptTask.Wait(5000)) {
    try { $interactiveProcess.Kill() } catch {}
    throw 'Windows OpenRouter helper buffered an interactive confirmation prompt.'
  }
  if ($promptTask.Result -ne 'PROMPT_VISIBLE') {
    try { $interactiveProcess.Kill() } catch {}
    throw "Windows OpenRouter helper emitted an unexpected prompt: $($promptTask.Result)"
  }
  $interactiveProcess.StandardInput.WriteLine('n')
  $interactiveProcess.StandardInput.Close()
  $interactiveError = $interactiveProcess.StandardError.ReadToEnd()
  $interactiveProcess.WaitForExit()
  if ($interactiveProcess.ExitCode -ne 0) {
    throw "Windows interactive OpenRouter dispatch failed: $interactiveError"
  }

  foreach ($expected in @(
    'airlock openrouter models presets',
    'airlock openrouter models add-preset NAME [--yes]'
  )) {
    if (-not $ModelsCommand.Output.Contains($expected)) {
      throw "Windows models help omitted '$expected': $($ModelsCommand.Output)"
    }
  }
  $InvalidOpenRouterCommand = Invoke-LauncherProcess $InstalledLauncher `
    @('openrouter', 'unsupported') $false
  if ($InvalidOpenRouterCommand.ExitCode -ne 2 -or
      $InvalidOpenRouterCommand.Error -notmatch 'usage: airlock openrouter auth\|models') {
    throw 'Windows launcher accepted an unsupported OpenRouter command.'
  }

  # Capture the protected OPR launch request without starting a router or reading
  # a real credential. The bridge itself has separate Python tests.
  $OprAccessStub = Join-Path $TempRoot 'opr-access-stub.py'
  $OprAccessStubSource = @'
import json
import os
from pathlib import Path
import sys
marker = os.environ.get("AIRLOCK_TEST_ACCESS_MARKER")
if marker:
    Path(marker).write_text("called", encoding="utf-8")
args = sys.argv[1:]
route = {
    "route": "kimi-k3",
    "model": "moonshotai/kimi-k3",
    "endpoint_provider": "digitalocean",
    "provider_name": "DigitalOcean",
    "provider_slug": "digitalocean",
    "quantization": "unknown",
    "canonical_slug": "moonshotai/kimi-k3-20260715",
}
if args and args[0] == "bundle-check":
    raise SystemExit(0)
if args and args[0] == "fast-transition-create":
    print("fast-transition-0123456789abcdef0123456789abcdef.json\t" + "a" * 64)
    raise SystemExit(0)
if args and args[0] == "fast-transition-cleanup":
    raise SystemExit(0)
if args == ["openrouter-routes"]:
    print(json.dumps([route], separators=(",", ":")))
    raise SystemExit(0)
if len(args) == 2 and args[0] == "openrouter-resolve" and args[1] == route["route"]:
    print(json.dumps(route, separators=(",", ":")))
    raise SystemExit(0)
print("unknown or disabled OpenRouter route", file=sys.stderr)
raise SystemExit(2)
'@
  [IO.File]::WriteAllText(
    $OprAccessStub,
    $OprAccessStubSource,
    (New-Object Text.UTF8Encoding($false))
  )
  $InstalledBridge = Join-Path $InstallDir 'airlock-hybrid.py'
  $InstalledBridgeBytes = [IO.File]::ReadAllBytes($InstalledBridge)
  $CaptureBridgeSource = @'
import json
import sys
from pathlib import Path
request_path = Path(sys.argv[sys.argv.index("--request-file") + 1])
request = json.loads(request_path.read_text(encoding="utf-8"))
print("OPR_REQUEST=" + json.dumps(request, separators=(",", ":"), sort_keys=True))
'@
  try {
    [IO.File]::WriteAllText(
      $InstalledBridge,
      $CaptureBridgeSource,
      (New-Object Text.UTF8Encoding($false))
    )
    foreach ($SavedProfile in @('hybrid', 'grok')) {
      $LegacyAccessMarker = Join-Path $TempRoot "orp-${SavedProfile}-access.marker"
      Remove-Item -LiteralPath $LegacyAccessMarker -Force -ErrorAction SilentlyContinue
      $LegacyOrp = Invoke-LauncherProcess $InstalledLauncher `
        @('orp', 'kimi-k3', '-r') $false @{
          AIRLOCK_ACCESS_HELPER = $OprAccessStub
          AIRLOCK_DEFAULT_PROFILE = $SavedProfile
          AIRLOCK_TEST_ACCESS_MARKER = $LegacyAccessMarker
        }
      if ($LegacyOrp.ExitCode -ne 2 -or
          $LegacyOrp.Error -notmatch "command 'orp' was renamed to 'opr'; use airlock opr\." -or
          $LegacyOrp.Output -match 'OPR_REQUEST=' -or
          (Test-Path -LiteralPath $LegacyAccessMarker)) {
        throw "Windows legacy ORP command was not rejected before dispatch under ${SavedProfile}."
      }
    }
    $OprCapture = Invoke-LauncherProcess $InstalledLauncher `
      @('opr', 'kimi-k3', '-r', '--effort', 'high', '-p', 'morpheus-corp') $true @{
        AIRLOCK_ACCESS_HELPER = $OprAccessStub
        AIRLOCK_DEFAULT_PROFILE = 'hybrid'
      }
    $OprMatch = [regex]::Match($OprCapture.Output, '(?m)^OPR_REQUEST=(.+)$')
    if (-not $OprMatch.Success) {
      throw "Windows OPR launch did not reach the protected bridge: $($OprCapture.Output)"
    }
    $OprRequest = $OprMatch.Groups[1].Value | ConvertFrom-Json
    if ($OprRequest.profile -ne 'openrouter-pure' -or
        $OprRequest.openrouter_root_route -ne 'kimi-k3') {
      throw "Windows OPR launch lost its exact route: $($OprMatch.Groups[1].Value)"
    }
    foreach ($ForbiddenField in @('proxy_url', 'root_model', 'root_name')) {
      if ($OprRequest.PSObject.Properties.Name -contains $ForbiddenField) {
        throw "Windows OPR request trusted forbidden field ${ForbiddenField}."
      }
    }
    $OprRequestArguments = @($OprRequest.args)
    $ExpectedTail = @('-r', '--effort', 'high', '-p', 'morpheus-corp')
    $ActualTail = @($OprRequestArguments[($OprRequestArguments.Count - 5)..($OprRequestArguments.Count - 1)])
    if ((ConvertTo-Json -Compress $ActualTail) -ne (ConvertTo-Json -Compress $ExpectedTail)) {
      throw "Windows OPR request changed Claude arguments: $($OprMatch.Groups[1].Value)"
    }
    $OprGrokDefault = Invoke-LauncherProcess $InstalledLauncher `
      @('opr', 'kimi-k3', '-r') $true @{
        AIRLOCK_ACCESS_HELPER = $OprAccessStub
        AIRLOCK_DEFAULT_PROFILE = 'grok'
      }
    $OprGrokMatch = [regex]::Match($OprGrokDefault.Output, '(?m)^OPR_REQUEST=(.+)$')
    if (-not $OprGrokMatch.Success) {
      throw "Windows OPR command was rewritten under the saved Grok default: $($OprGrokDefault.Output)"
    }
    $OprGrokRequest = $OprGrokMatch.Groups[1].Value | ConvertFrom-Json
    if ($OprGrokRequest.profile -ne 'openrouter-pure' -or
        $OprGrokRequest.openrouter_root_route -ne 'kimi-k3' -or
        @($OprGrokRequest.args)[-1] -ne '-r') {
      throw "Windows OPR command changed under the saved Grok default: $($OprGrokMatch.Groups[1].Value)"
    }
    foreach ($Override in @('--model=moonshotai/kimi-k3', '--model', '-m')) {
      $BlockedOpr = Invoke-LauncherProcess $InstalledLauncher `
        @('opr', 'kimi-k3', $Override, 'blocked') $false @{
          AIRLOCK_ACCESS_HELPER = $OprAccessStub
        }
      if ($BlockedOpr.ExitCode -ne 2 -or
          $BlockedOpr.Error -notmatch 'selected by exact registry route') {
        throw "Windows OPR launch accepted model override ${Override}."
      }
    }
    $MissingOprRoute = Invoke-LauncherProcess $InstalledLauncher `
      @('opr', '-r') $false @{ AIRLOCK_ACCESS_HELPER = $OprAccessStub }
    if ($MissingOprRoute.ExitCode -ne 2 -or
        $MissingOprRoute.Error -notmatch 'route is required outside an interactive terminal') {
      throw 'Windows noninteractive OPR launch accepted a missing route.'
    }
    $BareOpr = Invoke-LauncherProcess $InstalledLauncher `
      @('opr') $false @{ AIRLOCK_ACCESS_HELPER = $OprAccessStub }
    if ($BareOpr.ExitCode -ne 2 -or
        $BareOpr.Error -notmatch 'route is required outside an interactive terminal') {
      throw 'Windows noninteractive bare OPR launch accepted a missing route.'
    }
    $UnknownOprRoute = Invoke-LauncherProcess $InstalledLauncher `
      @('opr', 'missing-route', '-p', 'test') $false @{
        AIRLOCK_ACCESS_HELPER = $OprAccessStub
      }
    if ($UnknownOprRoute.ExitCode -ne 2 -or
        $UnknownOprRoute.Error -notmatch 'unknown or disabled OpenRouter route') {
      throw 'Windows OPR launch accepted an unknown route.'
    }
  } finally {
    [IO.File]::WriteAllBytes($InstalledBridge, $InstalledBridgeBytes)
  }

  $LegacyConfig = @'
AIRLOCK_MODEL=terra
AIRLOCK_MAIN_EFFORT=high
AIRLOCK_BG_MODEL=luna
AIRLOCK_BG_EFFORT=low
AIRLOCK_SMALL_FAST_MODEL=gpt-5.6-luna[1m]
AIRLOCK_WORKER_EFFORT=inherit
AIRLOCK_EXTRA_USAGE_POLICY=ask
AIRLOCK_ROUTING_POLICY=balanced
AIRLOCK_MAX_CONCURRENT_SUBAGENTS=off
AIRLOCK_OPENAI_FAST=off
AIRLOCK_ANTHROPIC_FAST=off
AIRLOCK_SWARM_FAST=off
AIRLOCK_FAILOVER_POLICY=ask
AIRLOCK_ANTHROPIC_MODELS=opus,sonnet
AIRLOCK_OPENAI_MODELS=sol,terra,luna
AIRLOCK_ANTHROPIC_EXTRA_MODELS=
AIRLOCK_OPENAI_EXTRA_MODELS=
AIRLOCK_GPT_EFFORT_CAPABILITIES=effort,xhigh_effort,max_effort
AIRLOCK_PROXY_URL=http://127.0.0.1:18765
'@
  [IO.File]::WriteAllText($InstalledConfig, $LegacyConfig, (New-Object Text.UTF8Encoding($false)))
  $LegacyLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test')
  if ($LegacyLaunch.Output -notmatch '(?m)^MODEL=gpt-5\.6-terra$' -or
      $LegacyLaunch.Output -notmatch '(?m)^ACTIVE_PROFILE=openai-pure$') {
    throw "Legacy config did not preserve the OpenAI-only bare command: $($LegacyLaunch.Output)"
  }
  if ($LegacyLaunch.Output -notmatch '(?m)^ROOT_MODEL=gpt-5\.6-terra$' -or
      $LegacyLaunch.Output -notmatch '(?m)^DISCOVERY_MODEL=gpt-5\.6-luna$' -or
      $LegacyLaunch.Output -notmatch "(?m)^UPDATE_NOTICE=$([regex]::Escape((Join-Path $ConfigDir 'update-notice.json')))$" -or
      $LegacyLaunch.Output -notmatch '(?m)^SESSION_ROUTER=unset$' -or
      $LegacyLaunch.Output -notmatch '(?m)^POLICY_HELPER=.*airlock_policy\.py$' -or
      $LegacyLaunch.Output -notmatch '(?m)^SNAPSHOT_SHA256=[0-9a-f]{64}$' -or
      $LegacyLaunch.Output -notmatch '(?m)^SNAPSHOT_EXISTS=yes$') {
    throw "Windows session did not pin its policy snapshot and economical Explore discovery: $($LegacyLaunch.Output)"
  }
  $LegacySnapshotMatch = [regex]::Match($LegacyLaunch.Output, '(?m)^SESSION_SNAPSHOT=(.+)$')
  if (-not $LegacySnapshotMatch.Success -or
      (Test-Path -LiteralPath $LegacySnapshotMatch.Groups[1].Value -PathType Leaf)) {
    throw "Windows pure-session snapshot remained after Claude exited: $($LegacySnapshotMatch.Groups[1].Value) [stderr] $($LegacyLaunch.Error)"
  }
  foreach ($ExpectedPickerLine in @(
    'DEFAULT_FABLE=gpt-5.6-sol',
    'DEFAULT_OPUS=gpt-5.6-sol',
    'DEFAULT_SONNET=gpt-5.6-terra',
    'DEFAULT_HAIKU=gpt-5.6-luna',
    'FABLE_NAME=gpt-5.6-sol'
  )) {
    if ($LegacyLaunch.Output -notmatch "(?m)^$([regex]::Escape($ExpectedPickerLine))$") {
      throw "Windows OpenAI picker did not keep distinct enabled models: $($LegacyLaunch.Output)"
    }
  }

  # The >300k Sol proof did not pass, so OpenAI roots keep the saved fallback.
  if ($LegacyLaunch.Output -notmatch '(?m)^COMPACT_WINDOW=272000$') {
    throw "Windows OpenAI root lost the conservative context fallback: $($LegacyLaunch.Output)"
  }
  $NoSessionUsage = Invoke-LauncherProcess $InstalledLauncher @('session-usage') $false
  if ($NoSessionUsage.ExitCode -eq 0 -or
      $NoSessionUsage.Error -notmatch 'available only inside an active hybrid Airlock session') {
    throw "Windows session usage did not fail clearly outside a hybrid session: $($NoSessionUsage.Error)"
  }
  $BareUnknownLaunch = Invoke-LauncherProcess $InstalledLauncher @('openai', 'spark', '-p', 'test')
  if ($BareUnknownLaunch.Output -notmatch '(?m)^MODEL=gpt-5\.3-codex-spark$' -or
      $BareUnknownLaunch.Output -notmatch '(?m)^COMPACT_WINDOW=272000$') {
    throw "Windows bare proxy root lost the conservative context fallback: $($BareUnknownLaunch.Output)"
  }
  $AutoWindowLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $true `
    @{ AIRLOCK_CONTEXT_WINDOW = 'auto' }
  if ($AutoWindowLaunch.Output -notmatch '(?m)^COMPACT_WINDOW=unset$') {
    throw "Windows auto context window still set the variable: $($AutoWindowLaunch.Output)"
  }
  $ExplicitAirlockWindowLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $true `
    @{ AIRLOCK_CONTEXT_WINDOW = '450000' }
  if ($ExplicitAirlockWindowLaunch.Output -notmatch '(?m)^COMPACT_WINDOW=450000$') {
    throw "Windows launcher discarded an explicit Airlock context window: $($ExplicitAirlockWindowLaunch.Output)"
  }
  $UserWindowLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $true `
    @{ CLAUDE_CODE_AUTO_COMPACT_WINDOW = '450000' }
  if ($UserWindowLaunch.Output -notmatch '(?m)^COMPACT_WINDOW=450000$') {
    throw "Windows launcher discarded a window the user set: $($UserWindowLaunch.Output)"
  }
  # Claude Code accepts 100000 to 1000000 and silently ignores anything else.
  # A leading zero, a leading plus, and surrounding whitespace all parse as an
  # integer, so the Windows launcher has to reject them on shape the way the
  # POSIX launcher does rather than trusting a parse.
  foreach ($RejectedWindow in @(
    '50000', '99999', '1000001', '2000000', '0272000', '+272000', ' 272000 ', 'notanumber'
  )) {
    $RejectedLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false `
      @{ AIRLOCK_CONTEXT_WINDOW = $RejectedWindow }
    if ($RejectedLaunch.Error -notmatch 'AIRLOCK_CONTEXT_WINDOW must be') {
      throw "Windows launcher accepted out-of-range context window ${RejectedWindow}: $($RejectedLaunch.Error)"
    }
  }

  $HybridConfig = "AIRLOCK_DEFAULT_PROFILE=hybrid`nAIRLOCK_HYBRID_MODEL=sonnet`n$LegacyConfig"
  [IO.File]::WriteAllText($InstalledConfig, $HybridConfig, (New-Object Text.UTF8Encoding($false)))
  $HybridLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test')
  if ($HybridLaunch.Output -notmatch '(?m)^CUSTOM_MODEL=claude-sonnet-5\[1m\]$' -or
      $HybridLaunch.Output -notmatch '(?m)^ACTIVE_PROFILE=hybrid-anthropic-root$' -or
      $HybridLaunch.Output -notmatch '(?m)^SESSION_ROUTER=http://127\.0\.0\.1:[1-9][0-9]*$' -or
      $HybridLaunch.Output -notmatch '(?m)^POLICY_HELPER=.*airlock_policy\.py$' -or
      $HybridLaunch.Output -notmatch '(?m)^SNAPSHOT_SHA256=[0-9a-f]{64}$' -or
      $HybridLaunch.Output -notmatch '(?m)^SNAPSHOT_EXISTS=yes$' -or
      $HybridLaunch.Output -notmatch '(?m)^FAST_MODE=off$' -or
      $HybridLaunch.Output -notmatch '(?m)^ARG=claude-sonnet-5\[1m\]$') {
    throw "Saved Claude hybrid root did not launch with a pinned policy snapshot: $($HybridLaunch.Output)"
  }
  $HybridSnapshotMatch = [regex]::Match($HybridLaunch.Output, '(?m)^SESSION_SNAPSHOT=(.+)$')
  if (-not $HybridSnapshotMatch.Success -or
      (Test-Path -LiteralPath $HybridSnapshotMatch.Groups[1].Value -PathType Leaf)) {
    throw "Windows hybrid-session snapshot remained after Claude exited: $($HybridSnapshotMatch.Groups[1].Value) [stderr] $($HybridLaunch.Error)"
  }
  $RegistryWriter = Join-Path $TempRoot 'write-openrouter-registry.py'
  $RegistryWriterSource = @'
import sys
import time

sys.path.insert(0, sys.argv[1])
import airlock_policy as policy

policy.write_openrouter_registry(sys.argv[2], {
    "schema_version": 1,
    "models": [
        {
            "route": "kimi-k3",
            "model": "moonshotai/kimi-k3",
            "endpoint_provider": "digitalocean",
            "provider_name": "DigitalOcean",
            "provider_slug": "digitalocean",
            "quantization": "unknown",
            "canonical_slug": "moonshotai/kimi-k3-20260715",
            "alias_target": None,
            "supported_parameters": ["tool_choice", "tools"],
            "expiration_date": None,
            "checked_at": int(time.time()),
            "enabled": True,
        },
        {
            "route": "ox-alpha",
            "model": "stealth/ox-alpha",
            "endpoint_provider": "stealth",
            "provider_name": "Stealth",
            "provider_slug": "stealth",
            "quantization": "unknown",
            "canonical_slug": "stealth/ox-alpha",
            "alias_target": None,
            "supported_parameters": ["tool_choice", "tools"],
            "expiration_date": None,
            "checked_at": int(time.time()),
            "enabled": True,
        },
    ],
})
'@
  [IO.File]::WriteAllText(
    $RegistryWriter,
    $RegistryWriterSource,
    (New-Object Text.UTF8Encoding($false))
  )
  & $RealPython $RegistryWriter $InstallDir $OpenRouterRegistry
  if ($LASTEXITCODE -ne 0) {
    throw 'Windows test could not create a protected synthetic OpenRouter registry.'
  }
  # The credential lives under LOCALAPPDATA on Windows, so the suite keeps its
  # own application-data root and never touches a real developer credential.
  $OpenRouterAppData = Join-Path $TempRoot 'openrouter-appdata'
  $EmptyAppData = Join-Path $TempRoot 'openrouter-appdata-empty'
  New-Item -ItemType Directory -Path $OpenRouterAppData -Force | Out-Null
  New-Item -ItemType Directory -Path $EmptyAppData -Force | Out-Null

  $MissingKeyLaunch = Invoke-LauncherProcess `
    $InstalledLauncher @('hybrid', 'sol', '-r') $false @{
      AIRLOCK_OPENROUTER_REGISTRY_FILE = $OpenRouterRegistry
      LOCALAPPDATA = $EmptyAppData
    }
  if ($MissingKeyLaunch.Error -notmatch 'OpenRouter credential is missing') {
    throw "Windows session did not name the missing OpenRouter credential: $($MissingKeyLaunch.Error)"
  }

  $KeyWriter = Join-Path $TempRoot 'store-openrouter-key.py'
  $KeyWriterSource = @'
import sys

sys.path.insert(0, sys.argv[1])
import airlock_openrouter_auth as auth

auth.store_key(b"sk-or-v1-WINDOWSTESTSENTINELKEY0000000000")
'@
  [IO.File]::WriteAllText(
    $KeyWriter,
    $KeyWriterSource,
    (New-Object Text.UTF8Encoding($false))
  )
  $PreviousLocalAppData = $env:LOCALAPPDATA
  try {
    $env:LOCALAPPDATA = $OpenRouterAppData
    & $RealPython $KeyWriter $InstallDir
  } finally {
    $env:LOCALAPPDATA = $PreviousLocalAppData
  }
  if ($LASTEXITCODE -ne 0) {
    throw 'Windows test could not store a synthetic OpenRouter credential.'
  }

  # Exercise the full Windows OpenRouter hybrid-root launch with an isolated
  # DPAPI key, protected registry, real bridge and router, and a Claude stub.
  # No provider request is made. The root must stay exact while every family
  # alias remains wrapper backed and the separate key stays out of the child.
  $HybridOpenRouterLaunch = Invoke-LauncherProcess `
    $InstalledLauncher @('hybrid', 'ox-alpha', '-p', 'test') $true @{
      AIRLOCK_OPENROUTER_REGISTRY_FILE = $OpenRouterRegistry
      LOCALAPPDATA = $OpenRouterAppData
      OPENROUTER_API_KEY = 'synthetic-should-not-reach-child'
    }
  foreach ($ExpectedHybridOpenRouterLine in @(
    'CUSTOM_MODEL=stealth/ox-alpha',
    'ACTIVE_PROFILE=hybrid-openrouter-root',
    'ROOT_MODEL=stealth/ox-alpha',
    'DEFAULT_OPUS=claude-opus-5[1m]',
    'DEFAULT_SONNET=claude-sonnet-5[1m]',
    'DEFAULT_HAIKU=claude-sonnet-5[1m]',
    'SMALL_FAST=claude-sonnet-5[1m]',
    'AUTO_MODE_MODEL=claude-sonnet-5[1m]',
    'OPENROUTER_BRIDGE=1',
    'OPENROUTER_KEY_SET=no'
  )) {
    if ($HybridOpenRouterLaunch.Output -notmatch "(?m)^$([regex]::Escape($ExpectedHybridOpenRouterLine))$") {
      throw "Windows OpenRouter hybrid root lost '${ExpectedHybridOpenRouterLine}': $($HybridOpenRouterLaunch.Output)"
    }
  }
  if ($HybridOpenRouterLaunch.Output -notmatch '(?ms)^ARG=--model\nARG=stealth/ox-alpha$') {
    throw "Windows OpenRouter hybrid root did not pin its exact model argument: $($HybridOpenRouterLaunch.Output)"
  }
  $HybridOpenRouterSnapshot = [regex]::Match(
    $HybridOpenRouterLaunch.Output, '(?m)^SESSION_SNAPSHOT=(.+)$'
  )
  if (-not $HybridOpenRouterSnapshot.Success -or
      (Test-Path -LiteralPath $HybridOpenRouterSnapshot.Groups[1].Value -PathType Leaf)) {
    throw "Windows OpenRouter hybrid snapshot remained after Claude exited: $($HybridOpenRouterLaunch.Output)"
  }
  $MismatchedHybridOpenRouter = Invoke-LauncherProcess `
    $InstalledLauncher @('hybrid', 'ox-alpha', '--model', 'moonshotai/kimi-k3', '-p', 'test') $false @{
      AIRLOCK_OPENROUTER_REGISTRY_FILE = $OpenRouterRegistry
      LOCALAPPDATA = $OpenRouterAppData
    }
  if ($MismatchedHybridOpenRouter.ExitCode -ne 2 -or
      $MismatchedHybridOpenRouter.Error -notmatch 'forwarded --model disagrees with the selected OpenRouter root route') {
    throw 'Windows OpenRouter hybrid root accepted a mismatched model override.'
  }

  $ResumeLaunch = Invoke-LauncherProcess `
    $InstalledLauncher @('hybrid', 'sol', '-r') $true @{
      AIRLOCK_OPENROUTER_REGISTRY_FILE = $OpenRouterRegistry
      LOCALAPPDATA = $OpenRouterAppData
    }
  $ResumeLines = $ResumeLaunch.Output -split "`n"
  $AgentsIndex = [Array]::IndexOf($ResumeLines, 'ARG=--agents')
  if ($ResumeLaunch.Output -notmatch '(?m)^ACTIVE_PROFILE=hybrid-openai-root$' -or
      $ResumeLaunch.Output -notmatch '(?m)^ARG=-r$' -or
      $ResumeLaunch.Output -notmatch '(?m)^SETTINGS_FILE=yes$' -or
      $ResumeLaunch.Output -notmatch '(?m)^GUIDANCE_FILE=yes$' -or
      $ResumeLaunch.Output -notmatch '(?m)^GUIDANCE_NONEMPTY=yes$' -or
      $ResumeLaunch.Output -notmatch '(?m)^ARG=Agent\(airlock-or-kimi-k3\)$' -or
      $AgentsIndex -lt 0 -or
      $AgentsIndex + 1 -ge $ResumeLines.Count -or
      $ResumeLines[$AgentsIndex + 1] -notmatch '"airlock-or-kimi-k3"') {
    throw "Windows hybrid resume did not retain the managed current-policy OpenRouter launch: $($ResumeLaunch.Output)"
  }
  $ResumeSettingsMatch = [regex]::Match(
    $ResumeLaunch.Output, '(?m)^SETTINGS_PATH=(.+)$'
  )
  $ResumeGuidanceMatch = [regex]::Match(
    $ResumeLaunch.Output, '(?m)^GUIDANCE_PATH=(.+)$'
  )
  if (-not $ResumeSettingsMatch.Success -or
      -not $ResumeGuidanceMatch.Success -or
      (Test-Path -LiteralPath $ResumeSettingsMatch.Groups[1].Value -PathType Leaf) -or
      (Test-Path -LiteralPath $ResumeGuidanceMatch.Groups[1].Value -PathType Leaf)) {
    throw 'Windows hybrid resume left a managed settings or guidance file after Claude exited.'
  }

  $OverflowLocalAppData = Join-Path $TempRoot 'overflow-local-app-data'
  $OverflowLaunchDirectory = Join-Path $OverflowLocalAppData 'Airlock\launch'
  $OverflowRuntime = Join-Path $TempRoot 'overflow-runtime'
  New-Item -ItemType Directory -Path $OverflowLaunchDirectory -Force | Out-Null
  $OverflowRequestPath = Join-Path $OverflowLaunchDirectory 'overflow.json'
  $OverflowRequest = [ordered]@{
    profile = 'hybrid-openai-root'
    claude = $ClaudeStub
    catalog_files = [ordered]@{
      openai_direct = Join-Path $ConfigDir 'openai-direct-agents.json'
      anthropic_direct = Join-Path $ConfigDir 'anthropic-direct-agents.json'
      openai_wrappers = Join-Path $ConfigDir 'hybrid-agents.json'
      anthropic_wrappers = Join-Path $ConfigDir 'claude-agents.json'
      grok_direct = Join-Path $ConfigDir 'grok-agents.json'
      grok_wrappers = Join-Path $ConfigDir 'grok-agents.json'
    }
    plugin_dir = Join-Path $ConfigDir 'plugins\airlock'
    router_helper = Join-Path $InstallDir 'airlock-router.py'
    context_window = '272000'
    force_context_window = $false
    max_agents = 'off'
    fast_mode = 'off'
    args = @((('x' * 32767) -join ''))
    proxy_url = 'http://127.0.0.1:18765'
    root_model = 'gpt-5.6-sol'
    root_name = 'GPT-5.6 Sol'
  }
  [IO.File]::WriteAllText(
    $OverflowRequestPath,
    (ConvertTo-Json -InputObject $OverflowRequest -Depth 4 -Compress),
    (New-Object Text.UTF8Encoding($false))
  )
  $overflowInfo = New-Object Diagnostics.ProcessStartInfo
  $overflowInfo.FileName = $RealPython
  $overflowInfo.Arguments = '"' + (Join-Path $InstallDir 'airlock-hybrid.py') + '" --request-file "' + $OverflowRequestPath + '"'
  $overflowInfo.UseShellExecute = $false
  $overflowInfo.RedirectStandardOutput = $true
  $overflowInfo.RedirectStandardError = $true
  [void]$overflowInfo.EnvironmentVariables.Remove('ANTHROPIC_API_KEY')
  [void]$overflowInfo.EnvironmentVariables.Remove('ANTHROPIC_AUTH_TOKEN')
  $overflowInfo.EnvironmentVariables['LOCALAPPDATA'] = $OverflowLocalAppData
  $overflowInfo.EnvironmentVariables['AIRLOCK_SESSION_RUNTIME_DIR'] = $OverflowRuntime
  $overflowInfo.EnvironmentVariables['AIRLOCK_CONFIG_DIR'] = $ConfigDir
  $overflowInfo.EnvironmentVariables['AIRLOCK_OPENROUTER_REGISTRY_FILE'] = $OpenRouterRegistry
  $overflowInfo.EnvironmentVariables['AIRLOCK_GPT_HYBRID'] = '1'
  $overflowProcess = [Diagnostics.Process]::Start($overflowInfo)
  $overflowOutput = $overflowProcess.StandardOutput.ReadToEnd().Replace("`r", '')
  $overflowError = $overflowProcess.StandardError.ReadToEnd().Replace("`r", '')
  $overflowProcess.WaitForExit()
  if ($overflowProcess.ExitCode -ne 2 -or
      $overflowError -notmatch 'Claude Code launch command is too long for Windows' -or
      $overflowError -match 'WinError 206' -or
      $overflowOutput -match '(?m)^MODEL=' -or
      (Test-Path -LiteralPath $OverflowRequestPath -PathType Leaf) -or
      ((Test-Path -LiteralPath $OverflowRuntime -PathType Container) -and
       @(Get-ChildItem -LiteralPath $OverflowRuntime -File).Count -ne 0)) {
    throw "Windows command preflight did not fail cleanly before Claude: $overflowError$overflowOutput"
  }

  foreach ($ExpectedFamilyLine in @(
    'DEFAULT_FABLE=gpt-5.6-sol',
    'DEFAULT_OPUS=claude-opus-5[1m]',
    'DEFAULT_SONNET=claude-sonnet-5[1m]',
    'DEFAULT_HAIKU=claude-sonnet-5[1m]',
    'SMALL_FAST=claude-sonnet-5[1m]'
  )) {
    if ($HybridLaunch.Output -notmatch "(?m)^$([regex]::Escape($ExpectedFamilyLine))$") {
      throw "Windows hybrid launch did not bind a validated family model: $($HybridLaunch.Output)"
    }
  }
  $AnthropicFastConfig = $HybridConfig.Replace(
    'AIRLOCK_HYBRID_MODEL=sonnet', 'AIRLOCK_HYBRID_MODEL=opus'
  ).Replace(
    'AIRLOCK_EXTRA_USAGE_POLICY=ask', 'AIRLOCK_EXTRA_USAGE_POLICY=allow'
  ).Replace(
    'AIRLOCK_ANTHROPIC_FAST=off', 'AIRLOCK_ANTHROPIC_FAST=on'
  )
  [IO.File]::WriteAllText($InstalledConfig, $AnthropicFastConfig, (New-Object Text.UTF8Encoding($false)))
  $AnthropicFastLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test')
  if ($AnthropicFastLaunch.Output -notmatch '(?m)^CUSTOM_MODEL=claude-opus-5\[1m\]$' -or
      $AnthropicFastLaunch.Output -notmatch '(?m)^FAST_MODE=on$') {
    throw "Authorized Anthropic Fast did not reach Claude Code: $($AnthropicFastLaunch.Output)"
  }
  $BlockedFastConfig = $AnthropicFastConfig.Replace(
    'AIRLOCK_EXTRA_USAGE_POLICY=allow', 'AIRLOCK_EXTRA_USAGE_POLICY=never'
  )
  [IO.File]::WriteAllText($InstalledConfig, $BlockedFastConfig, (New-Object Text.UTF8Encoding($false)))
  $BlockedFastLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false
  if ($BlockedFastLaunch.Error -notmatch 'Anthropic Fast uses paid usage credits and is blocked by the extra-usage policy') {
    throw "Anthropic Fast refusal was unclear: $($BlockedFastLaunch.Error)"
  }
  [IO.File]::WriteAllText($InstalledConfig, $HybridConfig, (New-Object Text.UTF8Encoding($false)))
  $ExplicitOpenAI = Invoke-LauncherProcess $InstalledLauncher @('openai', '-p', 'test')
  if ($ExplicitOpenAI.Output -notmatch '(?m)^MODEL=gpt-5\.6-terra$' -or
      $ExplicitOpenAI.Output -notmatch '(?m)^ACTIVE_PROFILE=openai-pure$') {
    throw "Explicit OpenAI profile did not override the hybrid default: $($ExplicitOpenAI.Output)"
  }
  $ExactOpenAI = Invoke-LauncherProcess $InstalledLauncher @('openai', 'gpt-5.6-luna[1m]', '-p', 'test')
  if ($ExactOpenAI.Output -notmatch '(?m)^MODEL=gpt-5\.6-luna$') {
    throw "Exact OpenAI model ID did not launch: $($ExactOpenAI.Output)"
  }
  $LegacyHybridPositional = Invoke-LauncherProcess $InstalledLauncher @('hybrid', 'gpt-5.6-terra[1m]', '-p', 'test')
  if ($LegacyHybridPositional.Output -notmatch '(?m)^ROOT_MODEL=gpt-5\.6-terra$' -or
      $LegacyHybridPositional.Output -notmatch '(?m)^ARG=gpt-5\.6-terra$' -or
      $LegacyHybridPositional.Output -match '(?m)^ARG=gpt-5\.6-terra\[1m\]$') {
    throw "Legacy positional hybrid model was not normalized before launch: $($LegacyHybridPositional.Output)"
  }
  $LegacyHybridFlag = Invoke-LauncherProcess $InstalledLauncher @('hybrid', '--model', 'gpt-5.6-luna[1m]', '-p', 'test')
  if ($LegacyHybridFlag.Output -notmatch '(?m)^ROOT_MODEL=gpt-5\.6-luna$' -or
      $LegacyHybridFlag.Output -notmatch '(?m)^ARG=gpt-5\.6-luna$' -or
      $LegacyHybridFlag.Output -match '(?m)^ARG=gpt-5\.6-luna\[1m\]$') {
    throw "Legacy hybrid --model value was not normalized before launch: $($LegacyHybridFlag.Output)"
  }
  $EqualsOpenAI = Invoke-LauncherProcess $InstalledLauncher @('openai', '--model=gpt-5.6-sol', '-p', 'test')
  if ($EqualsOpenAI.Output -notmatch '(?m)^MODEL=gpt-5\.6-sol$') {
    throw "OpenAI --model= form did not launch: $($EqualsOpenAI.Output)"
  }
  $BackgroundLaunch = Invoke-LauncherProcess $InstalledLauncher @('background', '-p', 'test')
  if ($BackgroundLaunch.Output -notmatch '(?m)^MODEL=gpt-5\.6-luna$' -or
      $BackgroundLaunch.Output -notmatch '(?m)^ARG=low$') {
    throw "Background alias did not use the saved background route: $($BackgroundLaunch.Output)"
  }
  $ConfigAlias = Invoke-LauncherProcess $InstalledLauncher @('--config')
  if ($ConfigAlias.Output -notmatch '(?m)^Default profile: hybrid$' -or
      $ConfigAlias.Output -notmatch '(?m)^Context window: 272000 \(saved fallback for OpenAI and Grok roots\)$') {
    throw "PowerShell --config alias did not show the saved profile: $($ConfigAlias.Output)"
  }

  $ProxyCommandLog = Join-Path $TempRoot 'proxy-command.log'
  $ProxyConfigDir = Join-Path $ConfigDir 'proxy-private'
  $ProxyStateHome = Join-Path $ConfigDir 'proxy-state'
  $ProxyConfig = "$HybridConfig`nAIRLOCK_PROXY_CONFIG_DIR=$ProxyConfigDir`nAIRLOCK_PROXY_STATE_HOME=$ProxyStateHome`n"
  [IO.File]::WriteAllText($InstalledConfig, $ProxyConfig, (New-Object Text.UTF8Encoding($false)))
  $env:AIRLOCK_TEST_PROXY_LOG = $ProxyCommandLog
  $ProxyStatus = Invoke-LauncherProcess $InstalledLauncher @('proxy', 'auth', 'status')
  if ($ProxyStatus.ExitCode -ne 0) { throw "PowerShell proxy status wrapper failed: $($ProxyStatus.Error)" }
  $ProxyLogText = [IO.File]::ReadAllText($ProxyCommandLog).Replace("`r", '')
  $ExpectedProxyLine = "$ProxyConfigDir|$ProxyStateHome|codex auth status"
  if ($ProxyLogText.Trim() -ne $ExpectedProxyLine) {
    throw "PowerShell proxy wrapper did not preserve its configured directories: $ProxyLogText"
  }
  Remove-Item Env:AIRLOCK_TEST_PROXY_LOG -ErrorAction SilentlyContinue

  # Upgrading from a release whose catalogs carried no managed marker must
  # succeed, because the installed bundle already records their hashes. A
  # hand-edited file must still be refused.
  $UpgradeRoot = Join-Path $TempRoot 'upgrade-sim'
  $UpgradeConfig = Join-Path $UpgradeRoot 'config'
  New-Item -ItemType Directory -Force -Path $UpgradeConfig | Out-Null
  $UpgradeComponents = @{}
  foreach ($catalog in @('openai-direct-agents.json', 'grok-agents.json')) {
    $unmarked = (Get-Content -LiteralPath (Join-Path $Root "config\$catalog") -Raw) -replace
      'Managed by https://github\.com/Harshkamdar67/Airlock', 'legacy build'
    $staged = Join-Path $UpgradeConfig $catalog
    [IO.File]::WriteAllText($staged, $unmarked, (New-Object Text.UTF8Encoding($false)))
    $UpgradeComponents["config/$catalog"] = (Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash.ToLower()
    if (Select-String -LiteralPath $staged -SimpleMatch 'Managed by https://github.com/Harshkamdar67/Airlock' -Quiet) {
      throw "upgrade fixture still carries the managed marker: $catalog"
    }
  }
  @{
    bundle_version = 'legacy'
    protocol_version = 3
    managed_by = 'Managed by https://github.com/Harshkamdar67/Airlock'
    components = $UpgradeComponents
  } | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $UpgradeConfig 'managed-bundle.json') -Encoding utf8
  $SavedConfigDir = $env:AIRLOCK_CONFIG_DIR
  $SavedInstallDir = $env:AIRLOCK_INSTALL_DIR
  $env:AIRLOCK_CONFIG_DIR = $UpgradeConfig
  $env:AIRLOCK_INSTALL_DIR = Join-Path $UpgradeRoot 'bin'
  & (Join-Path $Root 'scripts\install.ps1') *> $null
  if ($LASTEXITCODE -ne 0) {
    throw 'installer refused to upgrade an install whose catalogs predate the managed marker'
  }
  foreach ($catalog in @('openai-direct-agents.json', 'grok-agents.json')) {
    $installed = (Get-FileHash -LiteralPath (Join-Path $UpgradeConfig $catalog) -Algorithm SHA256).Hash
    $expected = (Get-FileHash -LiteralPath (Join-Path $Root "config\$catalog") -Algorithm SHA256).Hash
    if ($installed -ne $expected) { throw "upgrade did not replace $catalog" }
  }
  # A file with neither the marker nor a hash the bundle recorded is genuinely
  # unowned and must still be refused. Staged separately, because the upgrade
  # above leaves marked files behind, and a marked file is managed by the
  # pre-existing rule no matter what its hash is.
  $TamperRoot = Join-Path $TempRoot 'tamper-sim'
  $TamperConfig = Join-Path $TamperRoot 'config'
  New-Item -ItemType Directory -Force -Path $TamperConfig | Out-Null
  $unmarked = ((Get-Content -LiteralPath (Join-Path $Root 'config\grok-agents.json') -Raw) -replace
    'Managed by https://github\.com/Harshkamdar67/Airlock', 'hand written') + '   '
  [IO.File]::WriteAllText((Join-Path $TamperConfig 'grok-agents.json'), $unmarked,
    (New-Object Text.UTF8Encoding($false)))
  @{
    bundle_version = 'legacy'
    protocol_version = 3
    managed_by = 'Managed by https://github.com/Harshkamdar67/Airlock'
    components = @{ 'config/grok-agents.json' = ('0' * 64) }
  } | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $TamperConfig 'managed-bundle.json') -Encoding utf8
  $env:AIRLOCK_CONFIG_DIR = $TamperConfig
  $env:AIRLOCK_INSTALL_DIR = Join-Path $TamperRoot 'bin'
  # install.ps1 signals refusal with throw, and calling it in-process surfaces
  # that as a terminating error rather than an exit code.
  $TamperRefused = $false
  try {
    & (Join-Path $Root 'scripts\install.ps1') *> $null
  } catch {
    $TamperRefused = $_.Exception.Message -match 'refusing to overwrite an unmanaged file'
    if (-not $TamperRefused) {
      throw "unowned catalog was refused for the wrong reason: $($_.Exception.Message)"
    }
  }
  if (-not $TamperRefused) {
    throw 'installer overwrote a file it never wrote and cannot recognise'
  }
  $env:AIRLOCK_CONFIG_DIR = $SavedConfigDir
  $env:AIRLOCK_INSTALL_DIR = $SavedInstallDir

  # Grok parity. The POSIX launcher never runs airlock-hybrid.py, so these
  # paths are only ever exercised here.
  $GrokLaunch = Invoke-LauncherProcess $InstalledLauncher @('grok', '-p', 'test')
  if ($GrokLaunch.Output -notmatch '(?m)^MODEL=grok-4\.6$' -or
      $GrokLaunch.Output -notmatch '(?m)^ACTIVE_PROFILE=grok-pure$') {
    throw "Explicit Grok profile did not launch: $($GrokLaunch.Output)"
  }
  if ($GrokLaunch.Output -notmatch '(?m)^COMPACT_WINDOW=400000$' -or
      $GrokLaunch.Output -notmatch '(?m)^MAX_CONTEXT=500000$') {
    throw "Grok 4.6 did not declare its window and compact headroom: $($GrokLaunch.Output)"
  }
  if ($GrokLaunch.Output -notmatch 'airlock-grok' -or $GrokLaunch.Output -notmatch 'airlock-composer') {
    throw "Grok-only session did not expose the Grok workers: $($GrokLaunch.Output)"
  }
  if ($GrokLaunch.Output -match 'airlock-sol' -or $GrokLaunch.Output -match 'airlock-opus') {
    throw "Grok-only session leaked a non-Grok worker: $($GrokLaunch.Output)"
  }
  foreach ($ExpectedPickerLine in @(
    'DEFAULT_FABLE=grok-4.6',
    'DEFAULT_OPUS=grok-4.6',
    'DEFAULT_SONNET=grok-composer-2.5-fast',
    'DEFAULT_HAIKU=grok-composer-2.5-fast',
    'SMALL_FAST=grok-composer-2.5-fast'
  )) {
    if ($GrokLaunch.Output -notmatch "(?m)^$([regex]::Escape($ExpectedPickerLine))$") {
      throw "Windows Grok picker crossed providers or collapsed its models: $($GrokLaunch.Output)"
    }
  }
  $GrokComposer = Invoke-LauncherProcess $InstalledLauncher @('grok', 'composer', '-p', 'test')
  if ($GrokComposer.Output -notmatch '(?m)^MODEL=grok-composer-2\.5-fast$') {
    throw "Grok alias did not select Composer: $($GrokComposer.Output)"
  }
  if ($GrokComposer.Output -notmatch '(?m)^COMPACT_WINDOW=272000$' -or
      $GrokComposer.Output -notmatch '(?m)^MAX_CONTEXT=unset$') {
    throw "Composer root lost the conservative window: $($GrokComposer.Output)"
  }
  $GrokEquals = Invoke-LauncherProcess $InstalledLauncher @('grok', '--model=grok-4.6', '-p', 'test')
  if ($GrokEquals.Output -notmatch '(?m)^MODEL=grok-4\.6$') {
    throw "Grok --model= form did not launch: $($GrokEquals.Output)"
  }
  $GrokLegacy = Invoke-LauncherProcess $InstalledLauncher @('grok', '--model=grok-4.5', '-p', 'test')
  if ($GrokLegacy.Output -notmatch '(?m)^MODEL=grok-4\.6$') {
    throw "Grok --model=grok-4.5 alias did not map to grok-4.6: $($GrokLegacy.Output)"
  }
  # An unrecognized bare argument passes through to Claude Code, matching the
  # POSIX launcher; only an explicit --model names a route and can be rejected.
  $BadGrok = Invoke-LauncherProcess $InstalledLauncher @('grok', '--model=bogus', '-p', 'test') $false
  if ($BadGrok.Error -notmatch 'unsupported Grok model') {
    throw "Unknown Grok model was not rejected clearly: $($BadGrok.Error)"
  }
  $HybridGrok = Invoke-LauncherProcess $InstalledLauncher @('hybrid', 'grok', '-p', 'test')
  if ($HybridGrok.Output -notmatch '(?m)^ACTIVE_PROFILE=hybrid-grok-root$' -or
      $HybridGrok.Output -notmatch '(?m)^CUSTOM_MODEL=grok-4\.6$') {
    throw "Hybrid Grok root did not launch: $($HybridGrok.Output)"
  }
  # Grok stays out of a hybrid session that did not ask for it.
  $HybridWithoutGrok = Invoke-LauncherProcess $InstalledLauncher @('hybrid', 'sonnet', '-p', 'test')
  if ($HybridWithoutGrok.Output -match 'airlock-grok') {
    throw "Hybrid session enabled Grok without an explicit opt-in: $($HybridWithoutGrok.Output)"
  }

  $env:AIRLOCK_TEST_PROXY_LOG = $ProxyCommandLog
  $GrokProxyStatus = Invoke-LauncherProcess $InstalledLauncher @('proxy', 'grok', 'auth', 'status')
  if ($GrokProxyStatus.ExitCode -ne 0) { throw "Grok proxy status wrapper failed: $($GrokProxyStatus.Error)" }
  $GrokProxyLog = [IO.File]::ReadAllText($ProxyCommandLog).Replace("`r", '')
  if ($GrokProxyLog -notmatch 'grok auth status') {
    throw "Grok proxy wrapper did not reach the grok subcommand: $GrokProxyLog"
  }
  Remove-Item Env:AIRLOCK_TEST_PROXY_LOG -ErrorAction SilentlyContinue

  $HybridGptConfig = $HybridConfig.Replace('AIRLOCK_HYBRID_MODEL=sonnet', 'AIRLOCK_HYBRID_MODEL=terra')
  [IO.File]::WriteAllText($InstalledConfig, $HybridGptConfig, (New-Object Text.UTF8Encoding($false)))
  $HybridGptLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test')
  if ($HybridGptLaunch.Output -notmatch '(?m)^CUSTOM_MODEL=gpt-5\.6-terra$' -or
      $HybridGptLaunch.Output -notmatch '(?m)^ACTIVE_PROFILE=hybrid-openai-root$') {
    throw "Saved GPT hybrid root did not launch: $($HybridGptLaunch.Output)"
  }

  [IO.File]::WriteAllText($InstalledConfig, "AIRLOCK_DEFAULT_PROFILE=invalid`n", (New-Object Text.UTF8Encoding($false)))
  [void](Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false)
  [IO.File]::WriteAllText($InstalledConfig, "AIRLOCK_MODEL=invalid`n", (New-Object Text.UTF8Encoding($false)))
  [void](Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false)
  [IO.File]::WriteAllText($InstalledConfig, "AIRLOCK_HYBRID_MODEL=invalid`n", (New-Object Text.UTF8Encoding($false)))
  [void](Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false)
  [IO.File]::WriteAllText($InstalledConfig, "AIRLOCK_OPENAI_FAST=invalid`n", (New-Object Text.UTF8Encoding($false)))
  [void](Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false)
  [IO.File]::WriteAllText($InstalledConfig, "AIRLOCK_ANTHROPIC_FAST=invalid`n", (New-Object Text.UTF8Encoding($false)))
  [void](Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false)
  [IO.File]::WriteAllText($InstalledConfig, $LegacyConfig, (New-Object Text.UTF8Encoding($false)))

  $env:PATH = "$InstallDir;$StubDir;$SystemPath"
  $BundleBeforeDoctor = Invoke-LauncherProcess $InstalledLauncher @('bundle')
  if ($BundleBeforeDoctor.Output -notmatch '(?m)^Managed bundle is current and complete\.$') {
    throw "Windows bundle check was not current before Doctor: $($BundleBeforeDoctor.Output)"
  }
  $env:AIRLOCK_PROXY_URL = 'http://127.0.0.1:1'
  $DoctorOutput = ((& (Join-Path $Root 'scripts\doctor.ps1') *>&1 | Out-String).Replace("`r", ''))
  $DoctorExit = $LASTEXITCODE
  if ($DoctorExit -eq 0) { throw 'Windows doctor ignored an unhealthy proxy.' }
  if ($DoctorOutput -notmatch '(?m)^PASS  Claude login is configured$') {
    throw "Windows doctor did not confirm the healthy Claude login stub: $DoctorOutput"
  }
  $DoctorCompact = $DoctorOutput -replace '\s+', ' '
  if ($DoctorCompact -notmatch 'PASS Release updater: .*airlock-update\.py \(manual checks only\)') {
    throw "Windows doctor did not verify the release updater: $DoctorOutput"
  }
  if ($DoctorOutput -notmatch '(?m)^INFO  OpenRouter registry is not configured: ' -or
      $DoctorOutput -notmatch '(?m)^PASS  OpenRouter credential backend is available: windows-dpapi$' -or
      $DoctorOutput -notmatch '(?m)^INFO  Doctor does not read the OpenRouter credential;') {
    throw "Windows doctor did not report safe OpenRouter state: $DoctorOutput"
  }

  $DoctorModelsStub = Join-Path $TempRoot 'doctor-models-stub.py'
  [IO.File]::WriteAllText(
    $DoctorModelsStub,
    "print('STATE=valid')`nprint('COUNT=1')`nprint('MODEL=flash\tdeepseek/deepseek-v4-flash-0731\tprovider/cheap\tenabled')`n",
    (New-Object Text.UTF8Encoding($false))
  )
  $DoctorAuthStub = Join-Path $TempRoot 'doctor-auth-stub.py'
  [IO.File]::WriteAllText(
    $DoctorAuthStub,
    "print('BACKEND=windows-dpapi')`nprint('STATE=available')`n",
    (New-Object Text.UTF8Encoding($false))
  )
  $env:AIRLOCK_OPENROUTER_MODELS_HELPER = $DoctorModelsStub
  $env:AIRLOCK_OPENROUTER_AUTH_HELPER = $DoctorAuthStub
  $ConfiguredDoctorOutput = ((& (Join-Path $Root 'scripts\doctor.ps1') *>&1 | Out-String).Replace("`r", ''))
  if ($ConfiguredDoctorOutput -notmatch '(?m)^PASS  OpenRouter registry is valid and fresh \(1 model\(s\)\): ' -or
      $ConfiguredDoctorOutput -notmatch '(?m)^INFO  OpenRouter route: airlock-or-flash -> deepseek/deepseek-v4-flash-0731 via provider/cheap \(enabled\)$') {
    throw "Windows doctor did not report configured OpenRouter routes: $ConfiguredDoctorOutput"
  }
  Remove-Item -LiteralPath 'Env:AIRLOCK_OPENROUTER_MODELS_HELPER'
  Remove-Item -LiteralPath 'Env:AIRLOCK_OPENROUTER_AUTH_HELPER'

  Write-Stub 'claude.cmd' "@if `"%1`"==`"--version`" echo Claude Code test`r`n@if `"%1 %2`"==`"auth status`" exit /b 1`r`n@exit /b 0"
  $SignedOutOutput = ((& (Join-Path $Root 'scripts\doctor.ps1') *>&1 | Out-String).Replace("`r", ''))
  if ($LASTEXITCODE -eq 0) { throw 'Windows doctor ignored an unhealthy proxy with Claude signed out.' }
  if ($SignedOutOutput -notmatch '(?m)^INFO  Claude login was not detected; run: claude auth login$' -or
      $SignedOutOutput -notmatch '(?m)^INFO  OpenAI-only sessions can still work, but hybrid and Claude routes need this login\.$') {
    throw "Windows doctor did not explain the signed-out Claude behavior: $SignedOutOutput"
  }
} finally {
  $env:PATH = $OldPath
  $env:AIRLOCK_INSTALL_DIR = $OldInstall
  $env:AIRLOCK_CONFIG_DIR = $OldConfig
  $env:AIRLOCK_AGENT_DIR = $OldAgent
  Remove-Item -LiteralPath 'Env:AIRLOCK_PROXY_URL' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:AIRLOCK_TEST_PROXY_LOG' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:AIRLOCK_OPENROUTER_MODELS_HELPER' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:AIRLOCK_OPENROUTER_AUTH_HELPER' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'All native Windows tests passed.'
# The last check above runs doctor.ps1 and expects it to fail, which leaves
# $LASTEXITCODE at 1. Exit explicitly so a caller that reads $LASTEXITCODE
# instead of the -File exit code still sees a pass.
exit 0
