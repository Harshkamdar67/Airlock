# Stub-based native Windows checks. These tests do not use OAuth or a model.
$ErrorActionPreference = 'Stop'

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
  'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_CUSTOM_MODEL_OPTION'
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

public static class ClaudeLaunchStub {
  public static int Main(string[] args) {
    Console.WriteLine("MODEL=" + (Environment.GetEnvironmentVariable("ANTHROPIC_MODEL") ?? "unset"));
    Console.WriteLine("CUSTOM_MODEL=" + (Environment.GetEnvironmentVariable("ANTHROPIC_CUSTOM_MODEL_OPTION") ?? "unset"));
    Console.WriteLine("ACTIVE_PROFILE=" + (Environment.GetEnvironmentVariable("AIRLOCK_ACTIVE_PROFILE") ?? "unset"));
    foreach (string argument in args) Console.WriteLine("ARG=" + argument);
    return 0;
  }
}
'@
Add-Type -TypeDefinition $ClaudeStubSource -Language CSharp -OutputAssembly $ClaudeStub -OutputType ConsoleApplication

function Invoke-LauncherProcess([string]$Launcher, [string[]]$LauncherArguments, [bool]$ExpectSuccess = $true) {
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
    'airlock', 'airlock.cmd', 'airlock.ps1', 'airlock-access.py', 'airlock-router.py', 'airlock-hybrid.py'
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
  if (-not (Test-Path -LiteralPath (Join-Path $ConfigDir 'plugins\airlock\hooks\hooks.json') -PathType Leaf)) {
    throw 'Windows installer missed the managed plugin.'
  }
  foreach ($relative in @(
    'scripts\file_safety.py', 'scripts\worktree.py',
    'scripts\worktree-create.sh', 'scripts\worktree-remove.sh'
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
  $env:AIRLOCK_REAL_CLAUDE = $ClaudeStub
  $env:AIRLOCK_SKIP_HEALTH_CHECK = '1'

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
  if ($LegacyLaunch.Output -notmatch '(?m)^MODEL=gpt-5\.6-terra\[1m\]$' -or
      $LegacyLaunch.Output -notmatch '(?m)^ACTIVE_PROFILE=openai-pure$') {
    throw "Legacy config did not preserve the OpenAI-only bare command: $($LegacyLaunch.Output)"
  }

  $HybridConfig = "AIRLOCK_DEFAULT_PROFILE=hybrid`nAIRLOCK_HYBRID_MODEL=sonnet`n$LegacyConfig"
  [IO.File]::WriteAllText($InstalledConfig, $HybridConfig, (New-Object Text.UTF8Encoding($false)))
  $HybridLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test')
  if ($HybridLaunch.Output -notmatch '(?m)^CUSTOM_MODEL=claude-sonnet-5$' -or
      $HybridLaunch.Output -notmatch '(?m)^ACTIVE_PROFILE=hybrid-anthropic-root$' -or
      $HybridLaunch.Output -notmatch '(?m)^ARG=claude-sonnet-5$') {
    throw "Saved Claude hybrid root did not launch: $($HybridLaunch.Output)"
  }
  $ExplicitOpenAI = Invoke-LauncherProcess $InstalledLauncher @('openai', '-p', 'test')
  if ($ExplicitOpenAI.Output -notmatch '(?m)^MODEL=gpt-5\.6-terra\[1m\]$' -or
      $ExplicitOpenAI.Output -notmatch '(?m)^ACTIVE_PROFILE=openai-pure$') {
    throw "Explicit OpenAI profile did not override the hybrid default: $($ExplicitOpenAI.Output)"
  }
  $ExactOpenAI = Invoke-LauncherProcess $InstalledLauncher @('openai', 'gpt-5.6-luna[1m]', '-p', 'test')
  if ($ExactOpenAI.Output -notmatch '(?m)^MODEL=gpt-5\.6-luna\[1m\]$') {
    throw "Exact OpenAI model ID did not launch: $($ExactOpenAI.Output)"
  }
  $EqualsOpenAI = Invoke-LauncherProcess $InstalledLauncher @('openai', '--model=gpt-5.6-sol[1m]', '-p', 'test')
  if ($EqualsOpenAI.Output -notmatch '(?m)^MODEL=gpt-5\.6-sol\[1m\]$') {
    throw "OpenAI --model= form did not launch: $($EqualsOpenAI.Output)"
  }
  $BackgroundLaunch = Invoke-LauncherProcess $InstalledLauncher @('background', '-p', 'test')
  if ($BackgroundLaunch.Output -notmatch '(?m)^MODEL=gpt-5\.6-luna\[1m\]$' -or
      $BackgroundLaunch.Output -notmatch '(?m)^ARG=low$') {
    throw "Background alias did not use the saved background route: $($BackgroundLaunch.Output)"
  }
  $ConfigAlias = Invoke-LauncherProcess $InstalledLauncher @('--config')
  if ($ConfigAlias.Output -notmatch '(?m)^Default profile: hybrid$') {
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

  $HybridGptConfig = $HybridConfig.Replace('AIRLOCK_HYBRID_MODEL=sonnet', 'AIRLOCK_HYBRID_MODEL=terra')
  [IO.File]::WriteAllText($InstalledConfig, $HybridGptConfig, (New-Object Text.UTF8Encoding($false)))
  $HybridGptLaunch = Invoke-LauncherProcess $InstalledLauncher @('-p', 'test')
  if ($HybridGptLaunch.Output -notmatch '(?m)^CUSTOM_MODEL=gpt-5\.6-terra\[1m\]$' -or
      $HybridGptLaunch.Output -notmatch '(?m)^ACTIVE_PROFILE=hybrid-openai-root$') {
    throw "Saved GPT hybrid root did not launch: $($HybridGptLaunch.Output)"
  }

  [IO.File]::WriteAllText($InstalledConfig, "AIRLOCK_DEFAULT_PROFILE=invalid`n", (New-Object Text.UTF8Encoding($false)))
  [void](Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false)
  [IO.File]::WriteAllText($InstalledConfig, "AIRLOCK_MODEL=invalid`n", (New-Object Text.UTF8Encoding($false)))
  [void](Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false)
  [IO.File]::WriteAllText($InstalledConfig, "AIRLOCK_HYBRID_MODEL=invalid`n", (New-Object Text.UTF8Encoding($false)))
  [void](Invoke-LauncherProcess $InstalledLauncher @('-p', 'test') $false)
  [IO.File]::WriteAllText($InstalledConfig, $LegacyConfig, (New-Object Text.UTF8Encoding($false)))

  $env:PATH = "$InstallDir;$StubDir;$SystemPath"
  $env:AIRLOCK_PROXY_URL = 'http://127.0.0.1:1'
  $DoctorOutput = ((& (Join-Path $Root 'scripts\doctor.ps1') *>&1 | Out-String).Replace("`r", ''))
  $DoctorExit = $LASTEXITCODE
  if ($DoctorExit -eq 0) { throw 'Windows doctor ignored an unhealthy proxy.' }
  if ($DoctorOutput -notmatch '(?m)^PASS  Claude login is configured$') {
    throw "Windows doctor did not confirm the healthy Claude login stub: $DoctorOutput"
  }

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
  Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'All native Windows tests passed.'
# The last check above runs doctor.ps1 and expects it to fail, which leaves
# $LASTEXITCODE at 1. Exit explicitly so a caller that reads $LASTEXITCODE
# instead of the -File exit code still sees a pass.
exit 0
