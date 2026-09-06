# Stub-based native Windows checks. These tests do not use OAuth or a model.
$ErrorActionPreference = 'Stop'

# A suite that runs inside an Airlock session must not inherit that session's
# helpers, saved depth, or armed Fast transition credentials.
foreach ($name in @(
  'AIRLOCK_ACCESS_HELPER', 'AIRLOCK_POLICY_HELPER', 'AIRLOCK_SESSION_ROUTER_URL',
  'AIRLOCK_UPDATE_NOTICE_FILE', 'AIRLOCK_SESSION_SNAPSHOT',
  'AIRLOCK_SESSION_SNAPSHOT_SHA256', 'AIRLOCK_AGENT_DEPTH',
  'AIRLOCK_FAST_TRANSITION_CHANNEL', 'AIRLOCK_FAST_TRANSITION_NONCE',
  'AIRLOCK_CONSOLE_HELPER', 'AIRLOCK_CONSOLE_SITE',
  'AIRLOCK_CONSOLE_TOOLS', 'AIRLOCK_WEB_TOOLS'
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
$ConsoleGracefulWait = $LauncherText.IndexOf('$process.WaitForExit(2000)')
$ConsoleForcedFallback = if ($ConsoleGracefulWait -ge 0) {
  $LauncherText.IndexOf(
    'Stop-Process -Id $process.Id -Force',
    $ConsoleGracefulWait
  )
} else { -1 }
if ($ConsoleGracefulWait -lt 0 -or $ConsoleForcedFallback -lt $ConsoleGracefulWait) {
  throw 'bin\airlock.ps1 must wait for Console marker cleanup before forced termination.'
}
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
$StatusStubProcess = $null
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
    Console.WriteLine("ALWAYS_EFFORT=" + (Environment.GetEnvironmentVariable("CLAUDE_CODE_ALWAYS_ENABLE_EFFORT") ?? "unset"));
    Console.WriteLine("CUSTOM_CAPS=" + (Environment.GetEnvironmentVariable("ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES") ?? "unset"));
    Console.WriteLine("FABLE_CAPS=" + (Environment.GetEnvironmentVariable("ANTHROPIC_DEFAULT_FABLE_MODEL_SUPPORTED_CAPABILITIES") ?? "unset"));
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
        bool settingsConsole = settings.Contains("\"airlock-console-tools\"");
        bool settingsWeb = settings.Contains("\"airlock-web-tools\"");
        Console.WriteLine("SETTINGS_MCP_SERVERS=" + (
          settingsConsole && settingsWeb ? "airlock-console-tools,airlock-web-tools" :
          settingsConsole ? "airlock-console-tools" :
          settingsWeb ? "airlock-web-tools" : ""
        ));
      }
      if (args[index] == "--mcp-config" && index + 1 < args.Length) {
        string mcpPath = args[index + 1];
        string mcp = File.Exists(mcpPath) ? File.ReadAllText(mcpPath) : "";
        bool mcpConsole = mcp.Contains("\"airlock-console-tools\"");
        bool mcpWeb = mcp.Contains("\"airlock-web-tools\"");
        Console.WriteLine("MCP_SERVERS=" + (
          mcpConsole && mcpWeb ? "airlock-console-tools,airlock-web-tools" :
          mcpConsole ? "airlock-console-tools" :
          mcpWeb ? "airlock-web-tools" : "unreadable"
        ));
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
  [hashtable]$ExtraEnvironment = $null,
  [AllowNull()][string]$StandardInput = $null
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
  if ($null -ne $StandardInput) {
    $processInfo.RedirectStandardInput = $true
  }
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
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_WEB_TOOLS')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_CONSOLE_TOOLS')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_CONSOLE_HELPER')
  [void]$processInfo.EnvironmentVariables.Remove('AIRLOCK_CONSOLE_SITE')
  if ($ExtraEnvironment) {
    foreach ($name in $ExtraEnvironment.Keys) {
      $processInfo.EnvironmentVariables[$name] = [string]$ExtraEnvironment[$name]
    }
  }
  $process = [Diagnostics.Process]::Start($processInfo)
  if ($null -ne $StandardInput) {
    $process.StandardInput.Write($StandardInput)
    $process.StandardInput.Close()
  }
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

function Get-FreeLoopbackPort {
  $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
  try {
    $listener.Start()
    return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
  } finally {
    $listener.Stop()
  }
}

function New-ConsoleBackupFixture([string]$Parent, [string]$SourceSite) {
  $container = Join-Path $Parent (
    '.airlock-console-backup-' + [Guid]::NewGuid().ToString('N')
  )
  New-Item -ItemType Directory -Path $container | Out-Null
  Copy-Item -LiteralPath $SourceSite -Destination (Join-Path $container 'site') -Recurse
  return $container
}

function Assert-NoConsoleSwapArtifacts([string]$Parent, [string]$Context) {
  $artifacts = @(
    Get-ChildItem -LiteralPath $Parent -Force |
      Where-Object {
        $_.Name -like '.airlock-console-backup-*' -or
        $_.Name -like '.airlock-console-staging-*'
      }
  )
  if ($artifacts.Count -ne 0) {
    throw "$Context left a Console swap artifact: $($artifacts[0].FullName)"
  }
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
  # Keep the installer offline: proxy acquisition must not run in tests.
  $env:AIRLOCK_PROXY_DOWNLOAD = 'off'
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
    'airlock_openmodel.py', 'airlock_openmodel_adapter.py',
    'airlock-update.py', 'airlock-router.py', 'airlock-hybrid.py',
    'airlock_console.py', 'airlock_console_tools.py', 'airlock_console_history.py'
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
    'mcp-server\airlock_console_mcp.py',
    'scripts\fast-session-end.sh', 'scripts\fast-session-end.py',
    'scripts\router-session-end.sh', 'scripts\router-session-end.py',
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

  $ConsoleSite = Join-Path $InstallDir 'share\airlock\console'
  if (-not (Test-Path -LiteralPath $ConsoleSite -PathType Container)) {
    throw 'Windows installer missed the Console site.'
  }
  $ConsoleSiteItem = Get-Item -LiteralPath $ConsoleSite -Force
  if ($ConsoleSiteItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Windows installer linked the Console site through a reparse point.'
  }
  $ConsoleMarker = Join-Path $ConsoleSite '.airlock-managed'
  $ConsoleMarkerText = [IO.File]::ReadAllText($ConsoleMarker)
  $ExpectedConsoleMarker = 'Managed by https://github.com/Harshkamdar67/Airlock (console site)' + "`n"
  if ($ConsoleMarkerText -cne $ExpectedConsoleMarker) {
    throw 'Windows installer did not mark the Console site as managed.'
  }
  $ConsoleSource = Join-Path $Root 'console\dist'
  $SourceInventory = @{}
  foreach ($item in Get-ChildItem -LiteralPath $ConsoleSource -Recurse -Force -File) {
    $relative = $item.FullName.Substring($ConsoleSource.Length).TrimStart('\')
    $SourceInventory[$relative] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
  }
  $InstalledInventory = @{}
  foreach ($item in Get-ChildItem -LiteralPath $ConsoleSite -Recurse -Force -File) {
    $relative = $item.FullName.Substring($ConsoleSite.Length).TrimStart('\')
    if ($relative -eq '.airlock-managed') { continue }
    $InstalledInventory[$relative] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
  }
  if ($SourceInventory.Count -ne $InstalledInventory.Count) {
    throw 'Windows installer copied an incomplete Console site.'
  }
  foreach ($relative in $SourceInventory.Keys) {
    if (-not $InstalledInventory.ContainsKey($relative) -or
        $InstalledInventory[$relative] -ne $SourceInventory[$relative]) {
      throw "Windows installer changed Console site asset $relative"
    }
  }
  $ConsoleParent = Split-Path -Parent $ConsoleSite
  Assert-NoConsoleSwapArtifacts $ConsoleParent 'Windows first install'

  # The update must move the old tree aside before activating the staged tree.
  # Stub the second Move-Item only, then prove the rollback restored every old
  # byte while leaving siblings alone and cleaning the unused staging tree.
  $OldConsoleIndex = Join-Path $ConsoleSite 'index.html'
  $OldConsoleAsset = Join-Path $ConsoleSite 'old-assets\kept.txt'
  New-Item -ItemType Directory -Path (Split-Path -Parent $OldConsoleAsset) -Force | Out-Null
  [IO.File]::WriteAllText($OldConsoleIndex, 'old console bytes')
  [IO.File]::WriteAllText($OldConsoleAsset, 'old asset bytes')
  $OldConsoleIndexHash = (Get-FileHash -LiteralPath $OldConsoleIndex -Algorithm SHA256).Hash
  $OldConsoleAssetHash = (Get-FileHash -LiteralPath $OldConsoleAsset -Algorithm SHA256).Hash
  $OldConsoleMarkerHash = (Get-FileHash -LiteralPath $ConsoleMarker -Algorithm SHA256).Hash
  $OldBundleHash = (Get-FileHash -LiteralPath (Join-Path $ConfigDir 'managed-bundle.json') -Algorithm SHA256).Hash
  $ConsoleSibling = Join-Path $ConsoleParent 'unrelated.txt'
  [IO.File]::WriteAllText($ConsoleSibling, 'unrelated bytes')
  $global:AirlockInstallerMoveCount = 0
  $ConsoleActivationBlocked = $false
  $ConsoleActivationError = ''
  function Move-Item {
    [CmdletBinding()]
    param(
      [Parameter(Mandatory)][string]$LiteralPath,
      [Parameter(Mandatory)][string]$Destination
    )
    $global:AirlockInstallerMoveCount += 1
    if ($global:AirlockInstallerMoveCount -eq 2) {
      throw 'forced Console activation failure'
    }
    Microsoft.PowerShell.Management\Move-Item `
      -LiteralPath $LiteralPath `
      -Destination $Destination
  }
  try {
    try {
      & (Join-Path $Root 'scripts\install.ps1') *> $null
    } catch {
      $ConsoleActivationError = $_.Exception.Message
      $ConsoleActivationBlocked = $ConsoleActivationError -like '*restored previous Airlock Console site*'
    }
  } finally {
    $ConsoleMoveCount = $global:AirlockInstallerMoveCount
    Remove-Item -LiteralPath 'Function:\Move-Item' -Force
    Remove-Variable -Name AirlockInstallerMoveCount -Scope Global -ErrorAction SilentlyContinue
  }
  if (-not $ConsoleActivationBlocked -or $ConsoleMoveCount -ne 3) {
    throw "Windows installer did not roll back a failed Console activation: $ConsoleActivationError"
  }
  if ((Get-FileHash -LiteralPath $OldConsoleIndex -Algorithm SHA256).Hash -ne $OldConsoleIndexHash -or
      (Get-FileHash -LiteralPath $OldConsoleAsset -Algorithm SHA256).Hash -ne $OldConsoleAssetHash -or
      (Get-FileHash -LiteralPath $ConsoleMarker -Algorithm SHA256).Hash -ne $OldConsoleMarkerHash) {
    throw 'Windows installer rollback did not preserve the old Console bytes and marker.'
  }
  if ((Get-FileHash -LiteralPath (Join-Path $ConfigDir 'managed-bundle.json') -Algorithm SHA256).Hash -ne $OldBundleHash) {
    throw 'Windows installer wrote the bundle marker after a failed Console activation.'
  }
  if ([IO.File]::ReadAllText($ConsoleSibling) -cne 'unrelated bytes') {
    throw 'Windows installer rollback changed an unrelated Console sibling.'
  }
  Assert-NoConsoleSwapArtifacts $ConsoleParent 'Windows activation rollback'

  # A normal update replaces the whole site, so stale assets disappear, and
  # it removes the successful swap's backup only after validating the new site.
  & (Join-Path $Root 'scripts\install.ps1') *> $null
  if (Test-Path -LiteralPath $OldConsoleAsset) {
    throw 'Windows installer merged the Console site instead of replacing it.'
  }
  if ((Get-FileHash -LiteralPath $OldConsoleIndex -Algorithm SHA256).Hash -ne
      $SourceInventory['index.html']) {
    throw 'Windows installer did not activate the new Console index.'
  }
  if ([IO.File]::ReadAllText($ConsoleSibling) -cne 'unrelated bytes') {
    throw 'Windows Console update changed an unrelated sibling.'
  }
  Assert-NoConsoleSwapArtifacts $ConsoleParent 'Windows successful update'

  # If both activation and rollback moves fail, the old site must remain in a
  # named safe backup. The next run recovers that exact site before updating.
  [IO.File]::WriteAllText($OldConsoleIndex, 'recoverable old console bytes')
  New-Item -ItemType Directory -Path (Split-Path -Parent $OldConsoleAsset) -Force | Out-Null
  [IO.File]::WriteAllText($OldConsoleAsset, 'recoverable old asset bytes')
  $RecoverableIndexHash = (Get-FileHash -LiteralPath $OldConsoleIndex -Algorithm SHA256).Hash
  $RecoverableAssetHash = (Get-FileHash -LiteralPath $OldConsoleAsset -Algorithm SHA256).Hash
  $RecoverableMarkerHash = (Get-FileHash -LiteralPath $ConsoleMarker -Algorithm SHA256).Hash
  $global:AirlockInstallerMoveCount = 0
  $RollbackFailureBlocked = $false
  $RollbackFailureError = ''
  function Move-Item {
    [CmdletBinding()]
    param(
      [Parameter(Mandatory)][string]$LiteralPath,
      [Parameter(Mandatory)][string]$Destination
    )
    $global:AirlockInstallerMoveCount += 1
    if ($global:AirlockInstallerMoveCount -in @(2, 3)) {
      throw 'forced Console move failure'
    }
    Microsoft.PowerShell.Management\Move-Item `
      -LiteralPath $LiteralPath `
      -Destination $Destination
  }
  try {
    try {
      & (Join-Path $Root 'scripts\install.ps1') *> $null
    } catch {
      $RollbackFailureError = $_.Exception.Message
      $RollbackFailureBlocked = $RollbackFailureError -like '*rollback failed; previous site retained at*'
    }
  } finally {
    Remove-Item -LiteralPath 'Function:\Move-Item' -Force
    Remove-Variable -Name AirlockInstallerMoveCount -Scope Global -ErrorAction SilentlyContinue
  }
  $RetainedBackups = @(
    Get-ChildItem -LiteralPath $ConsoleParent -Force |
      Where-Object { $_.Name -like '.airlock-console-backup-*' }
  )
  if (-not $RollbackFailureBlocked -or $RetainedBackups.Count -ne 1 -or
      $RollbackFailureError -notlike "*$($RetainedBackups[0].FullName)*" -or
      (Test-Path -LiteralPath $ConsoleSite)) {
    throw "Windows installer did not retain and name the failed rollback backup: $RollbackFailureError"
  }
  $RetainedSite = Join-Path $RetainedBackups[0].FullName 'site'
  if ((Get-FileHash -LiteralPath (Join-Path $RetainedSite 'index.html') -Algorithm SHA256).Hash -ne $RecoverableIndexHash -or
      (Get-FileHash -LiteralPath (Join-Path $RetainedSite 'old-assets\kept.txt') -Algorithm SHA256).Hash -ne $RecoverableAssetHash -or
      (Get-FileHash -LiteralPath (Join-Path $RetainedSite '.airlock-managed') -Algorithm SHA256).Hash -ne $RecoverableMarkerHash) {
    throw 'Windows installer changed the retained rollback backup.'
  }
  $RecoveryOutput = ((& (Join-Path $Root 'scripts\install.ps1') *>&1 | Out-String).Replace("`r", ''))
  if ($RecoveryOutput -notmatch '(?m)^Recovered previous Airlock Console site: ' -or
      -not (Test-Path -LiteralPath $ConsoleSite -PathType Container)) {
    throw "Windows installer did not recover a target-absent Console backup: $RecoveryOutput"
  }
  if ([IO.File]::ReadAllText($ConsoleSibling) -cne 'unrelated bytes') {
    throw 'Windows Console crash recovery changed an unrelated sibling.'
  }
  Assert-NoConsoleSwapArtifacts $ConsoleParent 'Windows crash recovery'

  # Both crash boundaries around the old-site move can leave the same safe
  # target-plus-empty-container shape. A validated target lets the next run
  # remove that container non-recursively instead of blocking permanently.
  $EmptyCommittedBackup = Join-Path $ConsoleParent (
    '.airlock-console-backup-' + [Guid]::NewGuid().ToString('N')
  )
  New-Item -ItemType Directory -Path $EmptyCommittedBackup | Out-Null
  $EmptyCommittedOutput = ((& (Join-Path $Root 'scripts\install.ps1') *>&1 | Out-String).Replace("`r", ''))
  if ((Test-Path -LiteralPath $EmptyCommittedBackup) -or
      $EmptyCommittedOutput -notmatch '(?m)^Removed empty Airlock Console backup: ') {
    throw "Windows installer did not clean an empty backup beside a managed target: $EmptyCommittedOutput"
  }
  Assert-NoConsoleSwapArtifacts $ConsoleParent 'Windows empty-backup cleanup'

  # A committed managed target makes one valid leftover backup stale. It is
  # validated and removed before the ordinary update starts.
  $StaleBackup = New-ConsoleBackupFixture $ConsoleParent $ConsoleSite
  $StaleOutput = ((& (Join-Path $Root 'scripts\install.ps1') *>&1 | Out-String).Replace("`r", ''))
  if ((Test-Path -LiteralPath $StaleBackup) -or
      $StaleOutput -notmatch '(?m)^Removed stale Airlock Console backup: ') {
    throw "Windows installer did not clean one valid stale Console backup: $StaleOutput"
  }
  Assert-NoConsoleSwapArtifacts $ConsoleParent 'Windows stale-backup cleanup'
  $SavedInstallDir = $env:AIRLOCK_INSTALL_DIR
  $SavedConfigDir = $env:AIRLOCK_CONFIG_DIR
  $SavedAgentDir = $env:AIRLOCK_AGENT_DIR
  try {
    $UnmanagedCase = Join-Path $TempRoot 'unmanaged-console-site'
    $env:AIRLOCK_INSTALL_DIR = Join-Path $UnmanagedCase 'bin'
    $env:AIRLOCK_CONFIG_DIR = Join-Path $UnmanagedCase 'config'
    $env:AIRLOCK_AGENT_DIR = Join-Path $UnmanagedCase 'agents'
    $UnmanagedConsoleSite = Join-Path $env:AIRLOCK_INSTALL_DIR 'share\airlock\console'
    New-Item -ItemType Directory -Path $UnmanagedConsoleSite -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $UnmanagedConsoleSite 'index.html'), 'person-owned site')
    $UnmanagedSiteBlocked = $false
    try {
      & (Join-Path $Root 'scripts\install.ps1') *> $null
    } catch {
      $UnmanagedSiteBlocked = $_.Exception.Message -like '*refusing to replace unmanaged Airlock Console site*'
    }
    if (-not $UnmanagedSiteBlocked) {
      throw 'Windows installer replaced or accepted an unmanaged Console site.'
    }
    if ([IO.File]::ReadAllText((Join-Path $UnmanagedConsoleSite 'index.html')) -cne 'person-owned site') {
      throw 'Windows installer changed an unmanaged Console site.'
    }
    if (Test-Path -LiteralPath (Join-Path $env:AIRLOCK_CONFIG_DIR 'managed-bundle.json')) {
      throw 'Windows installer wrote the bundle before refusing an unmanaged Console site.'
    }

    $ReparseCase = Join-Path $TempRoot 'reparse-console-site'
    $env:AIRLOCK_INSTALL_DIR = Join-Path $ReparseCase 'bin'
    $env:AIRLOCK_CONFIG_DIR = Join-Path $ReparseCase 'config'
    $env:AIRLOCK_AGENT_DIR = Join-Path $ReparseCase 'agents'
    $ReparseParent = Join-Path $env:AIRLOCK_INSTALL_DIR 'share\airlock'
    $ReparseOutside = Join-Path $ReparseCase 'outside'
    New-Item -ItemType Directory -Path $ReparseParent -Force | Out-Null
    New-Item -ItemType Directory -Path $ReparseOutside -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $ReparseOutside 'index.html'), 'outside')
    $ReparseConsoleSite = Join-Path $ReparseParent 'console'
    New-Item -ItemType Junction -Path $ReparseConsoleSite -Target $ReparseOutside | Out-Null
    $ReparseSiteBlocked = $false
    try {
      & (Join-Path $Root 'scripts\install.ps1') *> $null
    } catch {
      $ReparseSiteBlocked = $_.Exception.Message -like '*refusing unsafe Airlock Console site directory*'
    }
    if (-not $ReparseSiteBlocked) {
      throw 'Windows installer accepted a reparse-point Console site.'
    }
    if ([IO.File]::ReadAllText((Join-Path $ReparseOutside 'index.html')) -cne 'outside') {
      throw 'Windows installer changed a reparse-point Console target.'
    }
    if (Test-Path -LiteralPath (Join-Path $env:AIRLOCK_CONFIG_DIR 'managed-bundle.json')) {
      throw 'Windows installer wrote the bundle before refusing a reparse-point Console site.'
    }

    $EmptyBackupCase = Join-Path $TempRoot 'empty-console-backup'
    $env:AIRLOCK_INSTALL_DIR = Join-Path $EmptyBackupCase 'bin'
    $env:AIRLOCK_CONFIG_DIR = Join-Path $EmptyBackupCase 'config'
    $env:AIRLOCK_AGENT_DIR = Join-Path $EmptyBackupCase 'agents'
    $EmptyBackupParent = Join-Path $env:AIRLOCK_INSTALL_DIR 'share\airlock'
    New-Item -ItemType Directory -Path $EmptyBackupParent -Force | Out-Null
    $EmptyBackup = Join-Path $EmptyBackupParent (
      '.airlock-console-backup-' + [Guid]::NewGuid().ToString('N')
    )
    New-Item -ItemType Directory -Path $EmptyBackup | Out-Null
    $EmptyBackupBlocked = $false
    try {
      & (Join-Path $Root 'scripts\install.ps1') *> $null
    } catch {
      $EmptyBackupBlocked = $_.Exception.Message -like '*refusing ambiguous empty Airlock Console backup while the target is absent*'
    }
    if (-not $EmptyBackupBlocked -or
        -not (Test-Path -LiteralPath $EmptyBackup -PathType Container) -or
        @(Get-ChildItem -LiteralPath $EmptyBackup -Force).Count -ne 0 -or
        (Test-Path -LiteralPath (Join-Path $EmptyBackupParent 'console'))) {
      throw 'Windows installer changed or accepted an ambiguous empty Console backup.'
    }
    if (Test-Path -LiteralPath (Join-Path $env:AIRLOCK_CONFIG_DIR 'managed-bundle.json')) {
      throw 'Windows installer wrote the bundle before refusing an empty Console backup.'
    }

    $DebrisBackupCase = Join-Path $TempRoot 'debris-console-backup'
    $env:AIRLOCK_INSTALL_DIR = Join-Path $DebrisBackupCase 'bin'
    $env:AIRLOCK_CONFIG_DIR = Join-Path $DebrisBackupCase 'config'
    $env:AIRLOCK_AGENT_DIR = Join-Path $DebrisBackupCase 'agents'
    $DebrisBackupParent = Join-Path $env:AIRLOCK_INSTALL_DIR 'share\airlock'
    New-Item -ItemType Directory -Path $DebrisBackupParent -Force | Out-Null
    $DebrisBackup = Join-Path $DebrisBackupParent (
      '.airlock-console-backup-' + [Guid]::NewGuid().ToString('N')
    )
    New-Item -ItemType Directory -Path $DebrisBackup | Out-Null
    $DebrisFile = Join-Path $DebrisBackup 'owner-file.txt'
    [IO.File]::WriteAllText($DebrisFile, 'keep debris bytes')
    $DebrisBackupBlocked = $false
    try {
      & (Join-Path $Root 'scripts\install.ps1') *> $null
    } catch {
      $DebrisBackupBlocked = $_.Exception.Message -like '*refusing unsafe Airlock Console backup*'
    }
    if (-not $DebrisBackupBlocked -or
        -not (Test-Path -LiteralPath $DebrisBackup -PathType Container) -or
        [IO.File]::ReadAllText($DebrisFile) -cne 'keep debris bytes' -or
        (Test-Path -LiteralPath (Join-Path $DebrisBackupParent 'console'))) {
      throw 'Windows installer changed or accepted a Console backup containing debris.'
    }
    if (Test-Path -LiteralPath (Join-Path $env:AIRLOCK_CONFIG_DIR 'managed-bundle.json')) {
      throw 'Windows installer wrote the bundle before refusing Console backup debris.'
    }
    $MultipleCase = Join-Path $TempRoot 'multiple-console-backups'
    $env:AIRLOCK_INSTALL_DIR = Join-Path $MultipleCase 'bin'
    $env:AIRLOCK_CONFIG_DIR = Join-Path $MultipleCase 'config'
    $env:AIRLOCK_AGENT_DIR = Join-Path $MultipleCase 'agents'
    $MultipleParent = Join-Path $env:AIRLOCK_INSTALL_DIR 'share\airlock'
    New-Item -ItemType Directory -Path $MultipleParent -Force | Out-Null
    $MultipleBackupA = New-ConsoleBackupFixture $MultipleParent $ConsoleSite
    $MultipleBackupB = New-ConsoleBackupFixture $MultipleParent $ConsoleSite
    $MultipleHashA = (Get-FileHash -LiteralPath (Join-Path $MultipleBackupA 'site\index.html') -Algorithm SHA256).Hash
    $MultipleHashB = (Get-FileHash -LiteralPath (Join-Path $MultipleBackupB 'site\index.html') -Algorithm SHA256).Hash
    $MultipleBackupsBlocked = $false
    try {
      & (Join-Path $Root 'scripts\install.ps1') *> $null
    } catch {
      $MultipleBackupsBlocked = $_.Exception.Message -like '*refusing multiple Airlock Console backups*'
    }
    if (-not $MultipleBackupsBlocked -or
        -not (Test-Path -LiteralPath $MultipleBackupA -PathType Container) -or
        -not (Test-Path -LiteralPath $MultipleBackupB -PathType Container) -or
        (Get-FileHash -LiteralPath (Join-Path $MultipleBackupA 'site\index.html') -Algorithm SHA256).Hash -ne $MultipleHashA -or
        (Get-FileHash -LiteralPath (Join-Path $MultipleBackupB 'site\index.html') -Algorithm SHA256).Hash -ne $MultipleHashB) {
      throw 'Windows installer changed or accepted ambiguous Console backups.'
    }
    if (Test-Path -LiteralPath (Join-Path $env:AIRLOCK_CONFIG_DIR 'managed-bundle.json')) {
      throw 'Windows installer wrote the bundle before refusing multiple Console backups.'
    }

    $UnmanagedBackupCase = Join-Path $TempRoot 'unmanaged-console-backup'
    $env:AIRLOCK_INSTALL_DIR = Join-Path $UnmanagedBackupCase 'bin'
    $env:AIRLOCK_CONFIG_DIR = Join-Path $UnmanagedBackupCase 'config'
    $env:AIRLOCK_AGENT_DIR = Join-Path $UnmanagedBackupCase 'agents'
    $UnmanagedBackupParent = Join-Path $env:AIRLOCK_INSTALL_DIR 'share\airlock'
    New-Item -ItemType Directory -Path $UnmanagedBackupParent -Force | Out-Null
    $UnmanagedBackup = New-ConsoleBackupFixture $UnmanagedBackupParent $ConsoleSite
    $UnmanagedBackupMarker = Join-Path $UnmanagedBackup 'site\.airlock-managed'
    [IO.File]::WriteAllText($UnmanagedBackupMarker, "not managed`n")
    $UnmanagedBackupHash = (Get-FileHash -LiteralPath (Join-Path $UnmanagedBackup 'site\index.html') -Algorithm SHA256).Hash
    $UnmanagedBackupBlocked = $false
    try {
      & (Join-Path $Root 'scripts\install.ps1') *> $null
    } catch {
      $UnmanagedBackupBlocked = $_.Exception.Message -like '*refusing unmanaged Airlock Console backup*'
    }
    if (-not $UnmanagedBackupBlocked -or
        -not (Test-Path -LiteralPath $UnmanagedBackup -PathType Container) -or
        [IO.File]::ReadAllText($UnmanagedBackupMarker) -cne "not managed`n" -or
        (Get-FileHash -LiteralPath (Join-Path $UnmanagedBackup 'site\index.html') -Algorithm SHA256).Hash -ne $UnmanagedBackupHash -or
        (Test-Path -LiteralPath (Join-Path $UnmanagedBackupParent 'console'))) {
      throw 'Windows installer changed or accepted an unmanaged Console backup.'
    }
    if (Test-Path -LiteralPath (Join-Path $env:AIRLOCK_CONFIG_DIR 'managed-bundle.json')) {
      throw 'Windows installer wrote the bundle before refusing an unmanaged Console backup.'
    }

    $UnsafeBackupCase = Join-Path $TempRoot 'unsafe-console-backup'
    $env:AIRLOCK_INSTALL_DIR = Join-Path $UnsafeBackupCase 'bin'
    $env:AIRLOCK_CONFIG_DIR = Join-Path $UnsafeBackupCase 'config'
    $env:AIRLOCK_AGENT_DIR = Join-Path $UnsafeBackupCase 'agents'
    $UnsafeBackupParent = Join-Path $env:AIRLOCK_INSTALL_DIR 'share\airlock'
    $UnsafeBackupOutside = Join-Path $UnsafeBackupCase 'outside'
    New-Item -ItemType Directory -Path $UnsafeBackupParent -Force | Out-Null
    New-Item -ItemType Directory -Path $UnsafeBackupOutside -Force | Out-Null
    Copy-Item -LiteralPath $ConsoleSite -Destination (Join-Path $UnsafeBackupOutside 'site') -Recurse
    $UnsafeBackupHash = (Get-FileHash -LiteralPath (Join-Path $UnsafeBackupOutside 'site\index.html') -Algorithm SHA256).Hash
    $UnsafeBackup = Join-Path $UnsafeBackupParent (
      '.airlock-console-backup-' + [Guid]::NewGuid().ToString('N')
    )
    New-Item -ItemType Junction -Path $UnsafeBackup -Target $UnsafeBackupOutside | Out-Null
    $UnsafeBackupBlocked = $false
    try {
      & (Join-Path $Root 'scripts\install.ps1') *> $null
    } catch {
      $UnsafeBackupBlocked = $_.Exception.Message -like '*refusing unsafe Airlock Console backup*'
    }
    if (-not $UnsafeBackupBlocked -or
        -not (Test-Path -LiteralPath $UnsafeBackup) -or
        (Get-FileHash -LiteralPath (Join-Path $UnsafeBackupOutside 'site\index.html') -Algorithm SHA256).Hash -ne $UnsafeBackupHash -or
        (Test-Path -LiteralPath (Join-Path $UnsafeBackupParent 'console'))) {
      throw 'Windows installer changed or accepted a reparse-point Console backup.'
    }
    if (Test-Path -LiteralPath (Join-Path $env:AIRLOCK_CONFIG_DIR 'managed-bundle.json')) {
      throw 'Windows installer wrote the bundle before refusing an unsafe Console backup.'
    }
  } finally {
    $env:AIRLOCK_INSTALL_DIR = $SavedInstallDir
    $env:AIRLOCK_CONFIG_DIR = $SavedConfigDir
    $env:AIRLOCK_AGENT_DIR = $SavedAgentDir
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

  $ConsoleHelp = Invoke-LauncherProcess $InstalledLauncher @('console', '--help')
  if ($ConsoleHelp.Output -notmatch '(?m)^Usage: airlock console \[--port N\] \[--no-open\] \[--scan\] \[--once\]$') {
    throw "Windows Console help omitted its supported options: $($ConsoleHelp.Output)"
  }
  foreach ($BadConsolePort in @('0', '01', '65536', 'nope')) {
    $BadConsolePortResult = Invoke-LauncherProcess `
      $InstalledLauncher @('console', '--port', $BadConsolePort) $false
    if ($BadConsolePortResult.ExitCode -ne 2 -or
        $BadConsolePortResult.Error -notmatch 'port must be an integer from 1 to 65535') {
      throw "Windows Console accepted malformed port ${BadConsolePort}: $($BadConsolePortResult.Error)"
    }
  }
  $BadConsoleOption = Invoke-LauncherProcess $InstalledLauncher @('console', '--unknown') $false
  if ($BadConsoleOption.ExitCode -ne 2 -or
      $BadConsoleOption.Error -notmatch 'unknown option: --unknown') {
    throw 'Windows Console accepted an unknown option.'
  }

  $ConsoleStub = Join-Path $TempRoot 'airlock-console-stub.py'
  $ConsoleStubSource = @'
import http.server
import json
import os
from pathlib import Path
import sys

record = os.environ.get("AIRLOCK_CONSOLE_STUB_RECORD")
if record:
    with open(record, "w", encoding="utf-8") as handle:
        json.dump(sys.argv[1:], handle)
mode = os.environ.get("AIRLOCK_CONSOLE_STUB_MODE", "serve")
port = int(sys.argv[sys.argv.index("--port") + 1])
if mode == "occupied":
    print(f"airlock-console: console already running at http://127.0.0.1:{port}", file=sys.stderr)
    raise SystemExit(2)
if mode == "occupied-other":
    print("airlock-console: console already running at http://127.0.0.1:4783", file=sys.stderr)
    raise SystemExit(2)
if mode == "starting":
    print("airlock-console: another console is already starting", file=sys.stderr)
    raise SystemExit(2)
if mode == "near-occupied":
    print(f"airlock-console: console already running at http://127.0.0.1:{port}.", file=sys.stderr)
    raise SystemExit(2)
if "--once" in sys.argv:
    print('{"ok":true,"mode":"once"}')
    raise SystemExit(0)

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "OtherServer" if mode == "bad-server" else "AirlockConsole"
    sys_version = ""

    def do_GET(self):
        body = b'{"ok":true}' if mode != "bad-health" else b'{"ok":false}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

server = http.server.HTTPServer(("127.0.0.1", port), Handler)
server.timeout = 5
marker_path = os.environ.get("AIRLOCK_CONSOLE_STUB_MARKER")
pid_path = os.environ.get("AIRLOCK_CONSOLE_STUB_PID_RECORD")
if mode == "lifecycle":
    if not marker_path or not pid_path:
        raise SystemExit("lifecycle paths are missing")
    marker = Path(marker_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "schema_version": 1,
        "url": f"http://127.0.0.1:{port}",
        "pid": os.getpid(),
        "console_id": "1" * 64,
    }), encoding="utf-8")
    Path(pid_path).write_text(str(os.getpid()), encoding="ascii")
try:
    server.handle_request()
finally:
    server.server_close()
    if mode == "lifecycle" and marker_path:
        Path(marker_path).unlink(missing_ok=True)
if mode == "occupied-race":
    print(f"airlock-console: console already running at http://127.0.0.1:{port}", file=sys.stderr)
    raise SystemExit(2)
'@
  [IO.File]::WriteAllText($ConsoleStub, $ConsoleStubSource, (New-Object Text.UTF8Encoding($false)))
  $ConsoleBundleWriter = Join-Path $TempRoot 'write-console-bundle.py'
  $ConsoleBundleWriterSource = @'
import hashlib
import json
import sys
from pathlib import Path

bundle = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
bundle["components"]["bin/airlock_console.py"] = hashlib.sha256(
    Path(sys.argv[3]).read_bytes()
).hexdigest()
Path(sys.argv[2]).write_text(json.dumps(bundle), encoding="utf-8")
'@
  [IO.File]::WriteAllText(
    $ConsoleBundleWriter,
    $ConsoleBundleWriterSource,
    (New-Object Text.UTF8Encoding($false))
  )
  $ConsoleBundle = Join-Path $TempRoot 'console-managed-bundle.json'
  & $RealPython $ConsoleBundleWriter $env:AIRLOCK_MANAGED_BUNDLE_FILE $ConsoleBundle $ConsoleStub
  if ($LASTEXITCODE -ne 0) { throw 'Windows test could not write a Console bundle fixture.' }
  $ConsoleTestSite = Join-Path $TempRoot 'console-test-site'
  New-Item -ItemType Directory -Path $ConsoleTestSite -Force | Out-Null
  $ConsoleRecord = Join-Path $TempRoot 'console-args.json'
  $ConsoleEnvironment = @{
    AIRLOCK_CONSOLE_HELPER = $ConsoleStub
    AIRLOCK_CONSOLE_SITE = $ConsoleTestSite
    AIRLOCK_CONSOLE_STUB_RECORD = $ConsoleRecord
    AIRLOCK_MANAGED_BUNDLE_FILE = $ConsoleBundle
  }
  $ConsoleOnce = Invoke-LauncherProcess `
    $InstalledLauncher @('console', '--port', '51234', '--scan', '--once') $true $ConsoleEnvironment
  if ($ConsoleOnce.ExitCode -ne 0 -or
      $ConsoleOnce.Output -ne "{`"ok`":true,`"mode`":`"once`"}`n" -or
      $ConsoleOnce.Error) {
    throw "Windows Console --once did not reach its helper (exit $($ConsoleOnce.ExitCode), stderr: $($ConsoleOnce.Error)): $($ConsoleOnce.Output)"
  }
  $ConsoleArguments = [string[]](Get-Content -LiteralPath $ConsoleRecord -Raw | ConvertFrom-Json)
  $ExpectedConsoleArguments = @(
    '--port', '51234', '--site', $ConsoleTestSite,
    '--access-helper', (Join-Path $InstallDir 'airlock-access.py'),
    '--tools-helper', (Join-Path $InstallDir 'airlock_console_tools.py'),
    '--scan', '--once'
  )
  if (Compare-Object $ExpectedConsoleArguments $ConsoleArguments -SyncWindow 0) {
    throw "Windows Console changed its helper arguments: $($ConsoleArguments -join ' ')"
  }

  $ConsolePort = Get-FreeLoopbackPort
  $ConsoleServeEnvironment = $ConsoleEnvironment.Clone()
  $ConsoleServeEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'serve'
  $ConsoleServe = Invoke-LauncherProcess `
    $InstalledLauncher @('console', "--port=$ConsolePort", '--no-open') $true $ConsoleServeEnvironment
  if ($ConsoleServe.Output -ne "Airlock Console: http://127.0.0.1:$ConsolePort`n") {
    throw "Windows Console did not print its exact loopback URL: $($ConsoleServe.Output)"
  }

  # Windows PowerShell cannot generate a reliable Ctrl+C event in every CI
  # host. Exercise the same normal child-cleanup path with a controlled helper
  # and prove the parent neither force-stops it nor leaves its marker or port.
  $ConsoleLifecycleRoot = Join-Path $TempRoot 'console-lifecycle'
  $ConsoleLifecycleMarker = Join-Path $ConsoleLifecycleRoot 'console-address.json'
  $ConsoleLifecyclePidRecord = Join-Path $ConsoleLifecycleRoot 'helper.pid'
  $ConsoleLifecycleStopCapture = Join-Path $ConsoleLifecycleRoot 'forced-stop.txt'
  $ConsoleLifecycleHarness = Join-Path $TempRoot 'console-lifecycle-harness.ps1'
  $EscapedLifecycleStopCapture = $ConsoleLifecycleStopCapture.Replace("'", "''")
  $EscapedLifecycleLauncher = $InstalledLauncher.Replace("'", "''")
  $ConsoleLifecycleHarnessSource = @"
function Stop-Process {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = `$true)][int]`$Id,
    [switch]`$Force
  )
  [IO.File]::AppendAllText(
    '$EscapedLifecycleStopCapture',
    "`$Id|`$Force`n",
    (New-Object Text.UTF8Encoding(`$false))
  )
  Microsoft.PowerShell.Management\Stop-Process @PSBoundParameters
}
& '$EscapedLifecycleLauncher' @args
exit `$LASTEXITCODE
"@
  [IO.File]::WriteAllText(
    $ConsoleLifecycleHarness,
    $ConsoleLifecycleHarnessSource,
    (New-Object Text.UTF8Encoding($false))
  )
  $ConsoleLifecyclePort = Get-FreeLoopbackPort
  $ConsoleLifecycleEnvironment = $ConsoleEnvironment.Clone()
  $ConsoleLifecycleEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'lifecycle'
  $ConsoleLifecycleEnvironment['AIRLOCK_CONSOLE_STUB_MARKER'] = $ConsoleLifecycleMarker
  $ConsoleLifecycleEnvironment['AIRLOCK_CONSOLE_STUB_PID_RECORD'] = $ConsoleLifecyclePidRecord
  $ConsoleLifecycle = Invoke-LauncherProcess `
    $ConsoleLifecycleHarness `
    @('console', '--port', [string]$ConsoleLifecyclePort, '--no-open') `
    $true $ConsoleLifecycleEnvironment
  if ($ConsoleLifecycle.Output -ne "Airlock Console: http://127.0.0.1:$ConsoleLifecyclePort`n" -or
      $ConsoleLifecycle.Error) {
    throw "Windows Console lifecycle helper returned unexpected output: $($ConsoleLifecycle.Output)$($ConsoleLifecycle.Error)"
  }
  if (-not (Test-Path -LiteralPath $ConsoleLifecyclePidRecord -PathType Leaf)) {
    throw 'Windows Console lifecycle helper did not record its child process.'
  }
  $ConsoleLifecyclePid = [int]([IO.File]::ReadAllText($ConsoleLifecyclePidRecord))
  if (Get-Process -Id $ConsoleLifecyclePid -ErrorAction SilentlyContinue) {
    throw "Windows Console lifecycle helper process $ConsoleLifecyclePid remained running."
  }
  if (Test-Path -LiteralPath $ConsoleLifecycleMarker) {
    throw 'Windows Console normal shutdown left console-address.json behind.'
  }
  if (Test-Path -LiteralPath $ConsoleLifecycleStopCapture) {
    throw 'Windows Console parent force-stopped a helper that was exiting normally.'
  }
  $ConsoleLifecycleListener = [Net.Sockets.TcpListener]::new(
    [Net.IPAddress]::Loopback,
    $ConsoleLifecyclePort
  )
  try {
    $ConsoleLifecycleListener.Start()
  } finally {
    $ConsoleLifecycleListener.Stop()
  }
  $ConsoleBrowserCapture = Join-Path $TempRoot 'console-browser.json'
  $ConsoleBrowserHarness = Join-Path $TempRoot 'console-browser-harness.ps1'
  $EscapedBrowserCapture = $ConsoleBrowserCapture.Replace("'", "''")
  $EscapedInstalledLauncher = $InstalledLauncher.Replace("'", "''")
  $ConsoleBrowserHarnessSource = @"
function Start-Process {
  [CmdletBinding()]
  param(
    [Parameter(Position = 0, Mandatory = `$true)][string]`$FilePath,
    [object[]]`$ArgumentList,
    [switch]`$NoNewWindow,
    [switch]`$PassThru,
    [string]`$RedirectStandardError
  )
  if (`$FilePath -like 'http://127.0.0.1:*') {
    `$capture = [ordered]@{
      file_path = `$FilePath
      argument_list_supplied = `$PSBoundParameters.ContainsKey('ArgumentList')
    }
    [IO.File]::WriteAllText(
      '$EscapedBrowserCapture',
      (ConvertTo-Json -InputObject `$capture -Compress),
      (New-Object Text.UTF8Encoding(`$false))
    )
    return
  }
  Microsoft.PowerShell.Management\Start-Process @PSBoundParameters
}
& '$EscapedInstalledLauncher' @args
exit `$LASTEXITCODE
"@
  [IO.File]::WriteAllText(
    $ConsoleBrowserHarness,
    $ConsoleBrowserHarnessSource,
    (New-Object Text.UTF8Encoding($false))
  )
  $ConsoleBrowserPort = Get-FreeLoopbackPort
  $ConsoleBrowserEnvironment = $ConsoleEnvironment.Clone()
  $ConsoleBrowserEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'serve'
  $ConsoleBrowser = Invoke-LauncherProcess `
    $ConsoleBrowserHarness @('console', '--port', [string]$ConsoleBrowserPort) $true $ConsoleBrowserEnvironment
  if ($ConsoleBrowser.Output -ne "Airlock Console: http://127.0.0.1:$ConsoleBrowserPort`n" -or
      $ConsoleBrowser.Error) {
    throw "Windows Console browser launch returned unexpected output: $($ConsoleBrowser.Output)$($ConsoleBrowser.Error)"
  }
  $ConsoleBrowserInvocation = Get-Content -LiteralPath $ConsoleBrowserCapture -Raw | ConvertFrom-Json
  if ($ConsoleBrowserInvocation.file_path -ne "http://127.0.0.1:$ConsoleBrowserPort" -or
      $ConsoleBrowserInvocation.argument_list_supplied) {
    throw 'Windows Console did not pass one exact loopback URL to Start-Process.'
  }
  Remove-Item -LiteralPath $ConsoleBrowserCapture -Force
  $ConsoleNoOpenPort = Get-FreeLoopbackPort
  $ConsoleNoOpen = Invoke-LauncherProcess `
    $ConsoleBrowserHarness @('console', '--port', [string]$ConsoleNoOpenPort, '--no-open') $true $ConsoleBrowserEnvironment
  if ($ConsoleNoOpen.Output -ne "Airlock Console: http://127.0.0.1:$ConsoleNoOpenPort`n" -or
      $ConsoleNoOpen.Error -or (Test-Path -LiteralPath $ConsoleBrowserCapture)) {
    throw 'Windows Console --no-open invoked the browser opener or returned unexpected output.'
  }
  $OccupiedPort = Get-FreeLoopbackPort
  $OccupiedEnvironment = $ConsoleEnvironment.Clone()
  $OccupiedEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'occupied'
  $OccupiedConsole = Invoke-LauncherProcess `
    $InstalledLauncher @('console', '--port', [string]$OccupiedPort, '--no-open') $true $OccupiedEnvironment
  if ($OccupiedConsole.Output -ne "Airlock Console: http://127.0.0.1:$OccupiedPort`n" -or
      $OccupiedConsole.Error) {
    throw "Windows Console did not accept its helper's exact occupied-port result: $($OccupiedConsole.Output)$($OccupiedConsole.Error)"
  }
  $OccupiedOtherEnvironment = $ConsoleEnvironment.Clone()
  $OccupiedOtherEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'occupied-other'
  $OccupiedOtherConsole = Invoke-LauncherProcess `
    $InstalledLauncher @('console', '--port', '51238', '--no-open') $true $OccupiedOtherEnvironment
  if ($OccupiedOtherConsole.Output -ne "Airlock Console: http://127.0.0.1:4783`n" -or
      $OccupiedOtherConsole.Error) {
    throw "Windows Console did not reopen the validated existing Console URL: $($OccupiedOtherConsole.Output)$($OccupiedOtherConsole.Error)"
  }
  $OccupiedRacePort = Get-FreeLoopbackPort
  $OccupiedRaceEnvironment = $ConsoleEnvironment.Clone()
  $OccupiedRaceEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'occupied-race'
  $OccupiedRaceConsole = Invoke-LauncherProcess `
    $InstalledLauncher @('console', '--port', [string]$OccupiedRacePort, '--no-open') $true $OccupiedRaceEnvironment
  if ($OccupiedRaceConsole.Output -ne "Airlock Console: http://127.0.0.1:$OccupiedRacePort`n" -or
      $OccupiedRaceConsole.Error) {
    throw "Windows Console lost the occupied-port race: $($OccupiedRaceConsole.Output)$($OccupiedRaceConsole.Error)"
  }
  $StartingEnvironment = $ConsoleEnvironment.Clone()
  $StartingEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'starting'
  $StartingConsole = Invoke-LauncherProcess `
    $InstalledLauncher @('console', '--port', '51239', '--no-open') $false $StartingEnvironment
  if ($StartingConsole.ExitCode -ne 2 -or
      $StartingConsole.Error -notmatch 'another console is already starting') {
    throw "Windows Console treated a starting lock as an existing console: $($StartingConsole.Error)"
  }
  $NearOccupiedPort = Get-FreeLoopbackPort
  $NearOccupiedEnvironment = $ConsoleEnvironment.Clone()
  $NearOccupiedEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'near-occupied'
  $NearOccupiedConsole = Invoke-LauncherProcess `
    $InstalledLauncher @('console', '--port', [string]$NearOccupiedPort, '--no-open') $false $NearOccupiedEnvironment
  if ($NearOccupiedConsole.ExitCode -ne 2 -or
      $NearOccupiedConsole.Error -notmatch 'console already running at http://127\.0\.0\.1:.*\.$') {
    throw "Windows Console accepted or changed a near-match occupied-port error: $($NearOccupiedConsole.Error)"
  }
  $BadHealthPort = Get-FreeLoopbackPort
  $BadHealthEnvironment = $ConsoleEnvironment.Clone()
  $BadHealthEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'bad-health'
  $BadHealthConsole = Invoke-LauncherProcess `
    $InstalledLauncher @('console', '--port', [string]$BadHealthPort, '--no-open') $false $BadHealthEnvironment
  if ($BadHealthConsole.ExitCode -ne 1 -or
      $BadHealthConsole.Error -notmatch 'helper exited before its loopback health check passed') {
    throw "Windows Console accepted a health response whose ok value was false: $($BadHealthConsole.Error)"
  }
  $BadServerPort = Get-FreeLoopbackPort
  $BadServerEnvironment = $ConsoleEnvironment.Clone()
  $BadServerEnvironment['AIRLOCK_CONSOLE_STUB_MODE'] = 'bad-server'
  $BadServerConsole = Invoke-LauncherProcess `
    $InstalledLauncher @('console', '--port', [string]$BadServerPort, '--no-open') $false $BadServerEnvironment
  if ($BadServerConsole.ExitCode -ne 1 -or
      $BadServerConsole.Error -notmatch 'helper exited before its loopback health check passed') {
    throw "Windows Console accepted a health response from the wrong server: $($BadServerConsole.Error)"
  }

  $NoRouterMessage = 'no Airlock router is running for this terminal'
  $NoRouterStatus = Invoke-LauncherProcess $InstalledLauncher @('status')
  if ($NoRouterStatus.Output -ne "$NoRouterMessage`n" -or $NoRouterStatus.Error) {
    throw "Windows status without a router returned unexpected output: $($NoRouterStatus.Output)$($NoRouterStatus.Error)"
  }
  $InvalidStatus = Invoke-LauncherProcess $InstalledLauncher @('status', '--json') $false
  if ($InvalidStatus.ExitCode -ne 2 -or
      $InvalidStatus.Error -notmatch 'airlock status: this command does not accept arguments') {
    throw 'Windows status accepted an unexpected argument.'
  }

  $StatusStub = Join-Path $TempRoot 'router-status-stub.py'
  $StatusPortFile = Join-Path $TempRoot 'router-status-port.txt'
  $StatusStubSource = @'
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading

payload = {
    "profile": "hybrid-openai-root",
    "root_model": "gpt-5.6-sol",
    "root_provider": "openai",
    "events": [
        {
            "timestamp": "2026-08-26T10:00:01Z",
            "kind": "session_model_pinned",
            "model": "gpt-5.6-sol",
            "provider": "openai",
        },
        {
            "timestamp": "2026-08-26T10:00:02Z",
            "kind": "rate_limit_failover_attempted",
            "from_model": "gpt-5.6-sol",
            "to_model": "gpt-5.6-terra",
        },
        {
            "timestamp": "2026-08-26T10:00:03Z",
            "kind": "rate_limit_failover_succeeded",
            "from_model": "gpt-5.6-sol",
            "to_model": "gpt-5.6-terra",
        },
        {
            "timestamp": "2026-08-26T10:00:04Z",
            "kind": "rate_limit_cooldown_skipped",
            "model": "gpt-5.6-terra",
        },
        {
            "timestamp": "2026-08-26T10:00:05Z",
            "kind": "openrouter_effort_clamped",
            "model": "stealth/ox-alpha",
            "requested": "max",
            "forwarded": "high",
        },
        {
            "timestamp": "2026-08-26T10:00:06Z",
            "kind": "sanitized_error_substituted",
            "provider": "openrouter",
            "model": "stealth/ox-alpha",
            "message": "SHOULD_NOT_PRINT",
        },
        {
            "timestamp": "2026-08-26T10:00:07Z",
            "kind": "rate_limit_chain_exhausted",
            "model": "gpt-5.6-sol",
            "models_considered": 2,
        },
        {
            "timestamp": "2026-08-26T10:00:08Z",
            "kind": "openrouter_server_tools_stripped",
            "model": "stealth/ox-alpha",
            "removed_count": 2,
        },
        {
            "timestamp": "2026-08-26T10:00:09Z",
            "kind": "future_action_v2",
            "message": "SHOULD_NOT_PRINT",
            "credential": "SENTINEL_STATUS_SECRET",
        },
    ],
}
body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/diagnostics":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.server.served += 1
        if self.server.served >= 2:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.served = 0
Path(sys.argv[1]).write_text(str(server.server_port), encoding="ascii")
server.serve_forever()
server.server_close()
'@
  [IO.File]::WriteAllText(
    $StatusStub,
    $StatusStubSource,
    (New-Object Text.UTF8Encoding($false))
  )
  $StatusStubInfo = New-Object Diagnostics.ProcessStartInfo
  $StatusStubInfo.FileName = $RealPython
  $StatusStubInfo.Arguments = '"' + $StatusStub + '" "' + $StatusPortFile + '"'
  $StatusStubInfo.UseShellExecute = $false
  $StatusStubInfo.CreateNoWindow = $true
  $StatusStubProcess = [Diagnostics.Process]::Start($StatusStubInfo)
  $StatusPort = $null
  for ($attempt = 0; $attempt -lt 100; $attempt++) {
    if (Test-Path -LiteralPath $StatusPortFile -PathType Leaf) {
      $candidatePort = [IO.File]::ReadAllText($StatusPortFile).Trim()
      if ($candidatePort -match '^[1-9][0-9]{0,4}$') {
        $StatusPort = $candidatePort
        break
      }
    }
    if ($StatusStubProcess.HasExited) {
      throw "Windows status stub exited before publishing its port: $($StatusStubProcess.ExitCode)"
    }
    Start-Sleep -Milliseconds 50
  }
  if (-not $StatusPort) { throw 'Windows status stub did not publish its port.' }
  $StatusRouterUrl = "http://127.0.0.1:$StatusPort"
  $LiveStatus = Invoke-LauncherProcess $InstalledLauncher @('status') $true @{
    AIRLOCK_SESSION_ROUTER_URL = $StatusRouterUrl
  }
  $ExpectedStatusOutput = (@(
    'Airlock router: hybrid-openai-root profile; root gpt-5.6-sol (openai)',
    '10:00:01Z - Pinned gpt-5.6-sol (openai) as the session root.',
    '10:00:02Z - gpt-5.6-sol hit a rate limit; trying gpt-5.6-terra.',
    '10:00:03Z - gpt-5.6-sol hit a rate limit; continued on gpt-5.6-terra.',
    '10:00:04Z - Skipped gpt-5.6-terra because its rate-limit cooldown is active.',
    '10:00:05Z - Clamped OpenRouter effort for stealth/ox-alpha from max to high.',
    '10:00:06Z - Replaced the openrouter error for stealth/ox-alpha with a safe local message.',
    '10:00:07Z - The failover chain for gpt-5.6-sol exhausted 2 models.',
    '10:00:08Z - Removed 2 unsupported OpenRouter server tools for stealth/ox-alpha.',
    '10:00:09Z - Router action: future_action_v2.'
  ) -join "`n") + "`n"
  if ($LiveStatus.Output -ne $ExpectedStatusOutput -or $LiveStatus.Error) {
    throw "Windows live status returned unexpected output: $($LiveStatus.Output)$($LiveStatus.Error)"
  }
  if ($LiveStatus.Output -match 'SHOULD_NOT_PRINT|SENTINEL_STATUS_SECRET') {
    throw 'Windows status exposed an arbitrary diagnostics payload field.'
  }

  $InstalledRouterNotice = Join-Path $ConfigDir 'plugins\airlock\scripts\router-session-end.py'
  $PreviousRouterUrl = [Environment]::GetEnvironmentVariable(
    'AIRLOCK_SESSION_ROUTER_URL', 'Process'
  )
  try {
    [Environment]::SetEnvironmentVariable(
      'AIRLOCK_SESSION_ROUTER_URL', $StatusRouterUrl, 'Process'
    )
    $HookOutput = ((& $RealPython $InstalledRouterNotice | Out-String).Replace("`r", ''))
    $HookExit = $LASTEXITCODE
  } finally {
    [Environment]::SetEnvironmentVariable(
      'AIRLOCK_SESSION_ROUTER_URL', $PreviousRouterUrl, 'Process'
    )
  }
  if ($HookExit -ne 0 -or -not $HookOutput.Trim()) {
    throw "Windows router SessionEnd notice failed: $HookOutput"
  }
  try {
    $HookNotice = $HookOutput | ConvertFrom-Json
  } catch {
    throw "Windows router SessionEnd notice did not emit JSON: $HookOutput"
  }
  $HookPropertyNames = @($HookNotice.PSObject.Properties.Name)
  $HookLines = @($HookNotice.systemMessage -split "`n")
  if ($HookPropertyNames.Count -ne 1 -or
      $HookPropertyNames[0] -ne 'systemMessage' -or
      $HookLines.Count -gt 6 -or
      $HookLines[-1] -ne '10:00:09Z - Router action: future_action_v2.' -or
      $HookNotice.systemMessage -match 'SHOULD_NOT_PRINT|SENTINEL_STATUS_SECRET') {
    throw "Windows router SessionEnd notice was unsafe or too long: $HookOutput"
  }
  if (-not $StatusStubProcess.WaitForExit(5000)) {
    throw 'Windows status stub did not stop after its two loopback requests.'
  }

  $StoppedStatus = Invoke-LauncherProcess $InstalledLauncher @('status') $true @{
    AIRLOCK_SESSION_ROUTER_URL = $StatusRouterUrl
  }
  if ($StoppedStatus.Output -ne "$NoRouterMessage`n" -or $StoppedStatus.Error) {
    throw "Windows status did not fail friendly after the router stopped: $($StoppedStatus.Output)$($StoppedStatus.Error)"
  }
  try {
    [Environment]::SetEnvironmentVariable(
      'AIRLOCK_SESSION_ROUTER_URL', $StatusRouterUrl, 'Process'
    )
    $SilentHookOutput = ((& $RealPython $InstalledRouterNotice | Out-String).Replace("`r", ''))
    $SilentHookExit = $LASTEXITCODE
  } finally {
    [Environment]::SetEnvironmentVariable(
      'AIRLOCK_SESSION_ROUTER_URL', $PreviousRouterUrl, 'Process'
    )
  }
  if ($SilentHookExit -ne 0 -or $SilentHookOutput) {
    throw "Windows router SessionEnd notice was not silent on failure: $SilentHookOutput"
  }

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
    'Hybrid root aliases: auto, sonnet, astra, sol, terra, luna, opus, fable, haiku, grok, composer'
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

  # Registry management is delegated without exposing an endpoint or upstream
  # identity in launcher output. This mock has no registry or network access.
  $OpenModelHelperStub = Join-Path $TempRoot 'openmodel-helper-stub.py'
  [IO.File]::WriteAllText(
    $OpenModelHelperStub,
    "import os, sys`nfrom pathlib import Path`ninput_path = os.environ.get('AIRLOCK_TEST_HELPER_STDIN')`nif input_path:`n    Path(input_path).write_bytes(sys.stdin.buffer.read())`nprint('MOCK_OPENMODEL=' + '|'.join(sys.argv[1:]))`nsys.exit(int(os.environ.get('AIRLOCK_TEST_HELPER_EXIT', '0')))`n",
    (New-Object Text.UTF8Encoding($false))
  )
  $OpenModelRegistry = Join-Path (Join-Path $TempRoot 'openmodel-private') 'openmodel-registry.json'
  $OpenModelList = Invoke-LauncherProcess $InstalledLauncher @('open-model', 'list') $true @{
    AIRLOCK_OPENMODEL_HELPER = $OpenModelHelperStub
    AIRLOCK_OPENMODEL_REGISTRY_FILE = $OpenModelRegistry
  }
  $ExpectedOpenModelDispatch = "MOCK_OPENMODEL=--registry|$OpenModelRegistry|list"
  if (-not $OpenModelList.Output.Contains($ExpectedOpenModelDispatch)) {
    throw "Windows open-model command was not dispatched safely: $($OpenModelList.Output)"
  }
  $FailedOpenModelCheck = Invoke-LauncherProcess $InstalledLauncher @('open-model', 'check', 'local-coder') $false @{
    AIRLOCK_OPENMODEL_HELPER = $OpenModelHelperStub
    AIRLOCK_OPENMODEL_REGISTRY_FILE = $OpenModelRegistry
    AIRLOCK_TEST_HELPER_EXIT = '25'
  }
  if ($FailedOpenModelCheck.ExitCode -ne 25) {
    throw "Windows open-model helper exit status was lost: $($FailedOpenModelCheck.ExitCode)"
  }

  $OpenModelStdinCapture = Join-Path $TempRoot 'openmodel-helper.stdin'
  $PrivateEndpointInput = '{"base_url":"http://127.0.0.1:18098/v1"}'
  $EndpointAdd = Invoke-LauncherProcess $InstalledLauncher @(
    'open-model', 'endpoint', 'add', 'stdin-server', '--stdin',
    '--max-concurrency', '1'
  ) $true @{
    AIRLOCK_OPENMODEL_HELPER = $OpenModelHelperStub
    AIRLOCK_OPENMODEL_REGISTRY_FILE = $OpenModelRegistry
    AIRLOCK_TEST_HELPER_STDIN = $OpenModelStdinCapture
  } $PrivateEndpointInput
  $ExpectedEndpointArgs = "MOCK_OPENMODEL=--registry|$OpenModelRegistry|endpoint|add|stdin-server|--stdin|--max-concurrency|1"
  if (-not $EndpointAdd.Output.Contains($ExpectedEndpointArgs)) {
    throw "Windows endpoint add changed safe helper argv: $($EndpointAdd.Output)"
  }
  if ([IO.File]::ReadAllText($OpenModelStdinCapture) -ne $PrivateEndpointInput) {
    throw 'Windows endpoint add did not preserve bounded private stdin.'
  }
  if (($EndpointAdd.Output + $EndpointAdd.Error) -match '127\.0\.0\.1') {
    throw 'Windows endpoint add exposed its private URL in output.'
  }

  $PrivateRouteInput = '{"upstream_model":"private/synthetic-stdin-model","accepted_response_models":["private/synthetic-stdin-model"]}'
  $RouteAdd = Invoke-LauncherProcess $InstalledLauncher @(
    'open-model', 'add', 'stdin-route', 'stdin-server', '--stdin',
    '--context-window', '32768', '--max-output-tokens', '4096',
    '--no-streaming', '--tools', 'none', '--no-worker'
  ) $true @{
    AIRLOCK_OPENMODEL_HELPER = $OpenModelHelperStub
    AIRLOCK_OPENMODEL_REGISTRY_FILE = $OpenModelRegistry
    AIRLOCK_TEST_HELPER_STDIN = $OpenModelStdinCapture
  } $PrivateRouteInput
  if (($RouteAdd.Output + $RouteAdd.Error) -match 'private/synthetic-stdin-model') {
    throw 'Windows route add exposed its private identity in helper argv or output.'
  }
  if ([IO.File]::ReadAllText($OpenModelStdinCapture) -ne $PrivateRouteInput) {
    throw 'Windows route add did not preserve bounded private stdin.'
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
openmodel = {
    "route": "local-coder",
    "model": "openmodel/local-coder",
}
if args and args[0] == "bundle-check":
    expected_adapter = os.environ.get("AIRLOCK_TEST_OPENMODEL_ADAPTER_COMPONENT")
    if expected_adapter:
        components = [
            args[index + 1]
            for index, argument in enumerate(args[:-1])
            if argument == "--component"
        ]
        if f"bin/airlock_openmodel_adapter.py={expected_adapter}" not in components:
            raise SystemExit(26)
    raise SystemExit(0)
if args and args[0] == "fast-transition-create":
    fast_marker = os.environ.get("AIRLOCK_TEST_FAST_CREATE_MARKER")
    if fast_marker:
        Path(fast_marker).write_text("called", encoding="utf-8")
    print("fast-transition-0123456789abcdef0123456789abcdef.json\t" + "a" * 64)
    raise SystemExit(0)
if args and args[0] == "fast-transition-cleanup":
    raise SystemExit(0)
if args == ["openrouter-routes"]:
    print(json.dumps([route], separators=(",", ":")))
    raise SystemExit(0)
if args == ["openmodel-routes"]:
    print(json.dumps([openmodel], separators=(",", ":")))
    raise SystemExit(0)
if len(args) == 2 and args[0] == "openrouter-resolve" and args[1] == route["route"]:
    print(json.dumps(route, separators=(",", ":")))
    raise SystemExit(0)
if len(args) == 2 and args[0] == "openmodel-resolve" and args[1] == openmodel["route"]:
    print(json.dumps(openmodel, separators=(",", ":")))
    raise SystemExit(0)
if args and args[0].startswith("openmodel-"):
    print("unknown or disabled open-model route", file=sys.stderr)
else:
    print("unknown or disabled OpenRouter route", file=sys.stderr)
raise SystemExit(2)
'@
  [IO.File]::WriteAllText(
    $OprAccessStub,
    $OprAccessStubSource,
    (New-Object Text.UTF8Encoding($false))
  )
  $AdapterComponentSentinel = Join-Path $TempRoot 'adapter-component-sentinel.py'
  $BundleAdapterCheck = Invoke-LauncherProcess $InstalledLauncher @('bundle') $true @{
    AIRLOCK_ACCESS_HELPER = $OprAccessStub
    AIRLOCK_OPENMODEL_ADAPTER_HELPER = $AdapterComponentSentinel
    AIRLOCK_TEST_OPENMODEL_ADAPTER_COMPONENT = $AdapterComponentSentinel
  }
  if ($BundleAdapterCheck.Output -notmatch '(?m)^Managed bundle is current and complete\.$') {
    throw "Windows bundle check did not verify the open-model adapter component: $($BundleAdapterCheck.Output)"
  }
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
    $ExpectedLaunchWorkdir = [IO.Path]::GetFullPath((Get-Location).Path)
    if ([string]$OprRequest.workdir -cne [string]$ExpectedLaunchWorkdir) {
      throw "Windows OPR launch lost its working directory: $($OprMatch.Groups[1].Value)"
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

    # Open-model roots use their explicit namespace regardless of the saved
    # profile. Capture the request before the bridge can start its loopback
    # router, which keeps this assertion entirely offline. Local roots must not
    # arm Fast handoff or include parent-binding metadata in the request.
    $OpenModelFastMarker = Join-Path $TempRoot 'openmodel-fast-create.marker'
    Remove-Item -LiteralPath $OpenModelFastMarker -Force -ErrorAction SilentlyContinue
    foreach ($SavedProfile in @('hybrid', 'grok')) {
      $OmCapture = Invoke-LauncherProcess $InstalledLauncher `
        @('om', 'local-coder', '-r') $true @{
          AIRLOCK_ACCESS_HELPER = $OprAccessStub
          AIRLOCK_DEFAULT_PROFILE = $SavedProfile
          AIRLOCK_TEST_FAST_CREATE_MARKER = $OpenModelFastMarker
        }
      $OmMatch = [regex]::Match($OmCapture.Output, '(?m)^OPR_REQUEST=(.+)$')
      if (-not $OmMatch.Success) {
        throw "Windows OM launch did not reach the protected bridge under ${SavedProfile}: $($OmCapture.Output)"
      }
      $OmRequest = $OmMatch.Groups[1].Value | ConvertFrom-Json
      if ($OmRequest.profile -ne 'openmodel-pure' -or
          $OmRequest.openmodel_root_route -ne 'local-coder' -or
          @($OmRequest.args)[-1] -ne '-r') {
        throw "Windows OM launch lost its exact local route under ${SavedProfile}: $($OmMatch.Groups[1].Value)"
      }
      foreach ($ForbiddenField in @(
        'proxy_url', 'root_model', 'root_name', 'openrouter_root_route',
        'fast_transition_launcher_pid', 'fast_transition_cwd'
      )) {
        if ($OmRequest.PSObject.Properties.Name -contains $ForbiddenField) {
          throw "Windows OM request trusted forbidden field ${ForbiddenField}."
        }
      }
    }
    foreach ($Override in @('--model=openmodel/local-coder', '--model', '-m')) {
      $BlockedOm = Invoke-LauncherProcess $InstalledLauncher `
        @('om', 'local-coder', $Override, 'blocked') $false @{
          AIRLOCK_ACCESS_HELPER = $OprAccessStub
        }
      if ($BlockedOm.ExitCode -ne 2 -or
          $BlockedOm.Error -notmatch 'selected by exact registry route') {
        throw "Windows OM launch accepted model override ${Override}."
      }
    }
    $UnknownOm = Invoke-LauncherProcess $InstalledLauncher `
      @('om', 'missing-route', '-p', 'test') $false @{ AIRLOCK_ACCESS_HELPER = $OprAccessStub }
    if ($UnknownOm.ExitCode -ne 2 -or
        $UnknownOm.Error -notmatch 'unknown or disabled open-model route') {
      throw 'Windows OM launch accepted an unknown route.'
    }
    $BareOm = Invoke-LauncherProcess $InstalledLauncher @('om') $false @{
      AIRLOCK_ACCESS_HELPER = $OprAccessStub
    }
    if ($BareOm.ExitCode -ne 2 -or
        $BareOm.Error -notmatch 'route is required outside an interactive terminal') {
      throw 'Windows noninteractive OM launch accepted a missing route.'
    }

    $HybridOmCapture = Invoke-LauncherProcess $InstalledLauncher `
      @('hybrid', 'om:local-coder', '-r', '--model', 'openmodel/local-coder') $true @{
        AIRLOCK_ACCESS_HELPER = $OprAccessStub
        AIRLOCK_TEST_FAST_CREATE_MARKER = $OpenModelFastMarker
      }
    $HybridOmMatch = [regex]::Match($HybridOmCapture.Output, '(?m)^OPR_REQUEST=(.+)$')
    if (-not $HybridOmMatch.Success) {
      throw "Windows hybrid om: launch did not reach the protected bridge: $($HybridOmCapture.Output)"
    }
    $HybridOmRequest = $HybridOmMatch.Groups[1].Value | ConvertFrom-Json
    if ($HybridOmRequest.profile -ne 'hybrid-openmodel-root' -or
        $HybridOmRequest.openmodel_root_route -ne 'local-coder' -or
        -not $HybridOmRequest.proxy_url -or
        @($HybridOmRequest.args)[-2] -ne '--model' -or
        @($HybridOmRequest.args)[-1] -ne 'openmodel/local-coder') {
      throw "Windows hybrid om: launch changed the selected local route: $($HybridOmMatch.Groups[1].Value)"
    }
    foreach ($ForbiddenField in @(
      'root_model', 'root_name', 'openrouter_root_route',
      'fast_transition_launcher_pid', 'fast_transition_cwd'
    )) {
      if ($HybridOmRequest.PSObject.Properties.Name -contains $ForbiddenField) {
        throw "Windows hybrid OM request trusted forbidden field ${ForbiddenField}."
      }
    }
    if (Test-Path -LiteralPath $OpenModelFastMarker) {
      throw 'Windows local roots created Fast handoff state.'
    }
    $MismatchedHybridOm = Invoke-LauncherProcess $InstalledLauncher `
      @('hybrid', 'om:local-coder', '--model', 'openmodel/other', '-p', 'test') $false @{
        AIRLOCK_ACCESS_HELPER = $OprAccessStub
      }
    if ($MismatchedHybridOm.ExitCode -ne 2 -or
        $MismatchedHybridOm.Error -notmatch 'forwarded --model disagrees with the selected local open-model root route') {
      throw 'Windows hybrid om: root accepted a mismatched model override.'
    }
    $MismatchedShortHybridOm = Invoke-LauncherProcess $InstalledLauncher `
      @('hybrid', 'om:local-coder', '-m', 'gpt-5.6-sol', '-p', 'test') $false @{
        AIRLOCK_ACCESS_HELPER = $OprAccessStub
      }
    if ($MismatchedShortHybridOm.ExitCode -ne 2 -or
        $MismatchedShortHybridOm.Error -notmatch 'forwarded --model disagrees with the selected local open-model root route') {
      throw 'Windows hybrid om: root accepted a mismatched -m selector.'
    }
    $MismatchedShortEqualsHybridOm = Invoke-LauncherProcess $InstalledLauncher `
      @('hybrid', 'om:local-coder', '-m=gpt-5.6-sol', '-p', 'test') $false @{
        AIRLOCK_ACCESS_HELPER = $OprAccessStub
      }
    if ($MismatchedShortEqualsHybridOm.ExitCode -ne 2 -or
        $MismatchedShortEqualsHybridOm.Error -notmatch 'forwarded --model disagrees with the selected local open-model root route') {
      throw 'Windows hybrid om: root accepted a mismatched -m= selector.'
    }
    $DuplicateHybridOm = Invoke-LauncherProcess $InstalledLauncher `
      @(
        'hybrid', 'om:local-coder', '--model', 'openmodel/local-coder',
        '-m', 'openmodel/local-coder', '-p', 'test'
      ) $false @{ AIRLOCK_ACCESS_HELPER = $OprAccessStub }
    if ($DuplicateHybridOm.ExitCode -ne 2 -or
        $DuplicateHybridOm.Error -notmatch '--model or -m may be provided only once') {
      throw 'Windows hybrid om: root accepted duplicate model selectors.'
    }
    $DuplicateLongHybridOm = Invoke-LauncherProcess $InstalledLauncher `
      @(
        'hybrid', 'om:local-coder', '--model=openmodel/local-coder',
        '--model=openmodel/local-coder', '-p', 'test'
      ) $false @{ AIRLOCK_ACCESS_HELPER = $OprAccessStub }
    if ($DuplicateLongHybridOm.ExitCode -ne 2 -or
        $DuplicateLongHybridOm.Error -notmatch '--model or -m may be provided only once') {
      throw 'Windows hybrid om: root accepted duplicate long model selectors.'
    }
    $UnknownHybridOm = Invoke-LauncherProcess $InstalledLauncher `
      @('hybrid', 'om:missing-route', '-p', 'test') $false @{ AIRLOCK_ACCESS_HELPER = $OprAccessStub }
    if ($UnknownHybridOm.ExitCode -ne 2 -or
        $UnknownHybridOm.Error -notmatch 'not an enabled local open-model route') {
      throw 'Windows hybrid om: root accepted an unknown local route.'
    }
  } finally {
    [IO.File]::WriteAllBytes($InstalledBridge, $InstalledBridgeBytes)
  }

  # Run the real Windows bridge for a worker-disabled local route. This proves
  # that the protected request is resolved again by the bridge, that the
  # route's declared context replaces the generic saved fallback, and that a
  # pure root does not require a generated named Agent.
  $BridgeOpenModelDirectory = Join-Path $TempRoot 'bridge-openmodel-private'
  $BridgeOpenModelRegistry = Join-Path $BridgeOpenModelDirectory 'openmodel-registry.json'
  $ProtectBridgeRegistryDirectory = Join-Path $TempRoot 'protect-bridge-registry-directory.py'
  [IO.File]::WriteAllText(
    $ProtectBridgeRegistryDirectory,
    @'
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
import airlock_policy

directory = Path(sys.argv[2])
directory.mkdir(parents=True, exist_ok=True)
airlock_policy.protect_private_path(directory)
'@,
    (New-Object Text.UTF8Encoding($false))
  )
  & $RealPython $ProtectBridgeRegistryDirectory $InstallDir $BridgeOpenModelDirectory
  if ($LASTEXITCODE -ne 0) { throw 'Windows bridge fixture could not protect its registry directory.' }
  $InstalledOpenModelHelper = Join-Path $InstallDir 'airlock_openmodel.py'
  '{"base_url":"http://127.0.0.1:18096/v1"}' |
    & $RealPython $InstalledOpenModelHelper --registry $BridgeOpenModelRegistry `
      endpoint add local-bridge --stdin --max-concurrency 1 *> $null
  if ($LASTEXITCODE -ne 0) { throw 'Windows bridge fixture could not add its local endpoint.' }
  '{"upstream_model":"private/synthetic-bridge-model","accepted_response_models":["private/synthetic-bridge-model"]}' |
    & $RealPython $InstalledOpenModelHelper --registry $BridgeOpenModelRegistry `
      add quiet-route local-bridge --stdin `
      --context-window 64000 --max-output-tokens 4096 `
      --no-streaming --tools none --no-worker *> $null
  if ($LASTEXITCODE -ne 0) { throw 'Windows bridge fixture could not add its worker-disabled route.' }

  # The bridge accepts only its managed sibling router. Replace that temporary
  # installed copy with a bounded stub so this launcher test does not need to
  # keep a real router daemon alive, then restore it before later assertions.
  $InstalledBridgeOpenModelRouter = Join-Path $InstallDir 'airlock-router.py'
  $InstalledBridgeOpenModelRouterBytes = [IO.File]::ReadAllBytes($InstalledBridgeOpenModelRouter)
  [IO.File]::WriteAllText(
    $InstalledBridgeOpenModelRouter,
    @'
import os
import sys

if len(sys.argv) >= 2 and sys.argv[1] == "watch":
    raise SystemExit(0)
if len(sys.argv) < 2 or sys.argv[1] != "start":
    raise SystemExit(71)
print("http://127.0.0.1:28476")
print(os.getpid())
'@,
    (New-Object Text.UTF8Encoding($false))
  )
  $BridgeOpenModelBundle = Join-Path $TempRoot 'bridge-openmodel-bundle.json'
  $BundleRewriteHelper = Join-Path $TempRoot 'rewrite-openmodel-bundle.py'
  [IO.File]::WriteAllText(
    $BundleRewriteHelper,
    @'
import hashlib
import json
from pathlib import Path
import sys

source, output, router = map(Path, sys.argv[1:])
bundle = json.loads(source.read_text(encoding="utf-8"))
bundle["components"]["bin/airlock-router.py"] = hashlib.sha256(router.read_bytes()).hexdigest()
output.write_text(json.dumps(bundle), encoding="utf-8")
'@,
    (New-Object Text.UTF8Encoding($false))
  )
  & $RealPython $BundleRewriteHelper (Join-Path $ConfigDir 'managed-bundle.json') `
    $BridgeOpenModelBundle $InstalledBridgeOpenModelRouter
  if ($LASTEXITCODE -ne 0) { throw 'Windows bridge fixture could not rewrite its router bundle hash.' }
  $BridgeOpenModelEnvironment = @{
    AIRLOCK_OPENMODEL_REGISTRY_FILE = $BridgeOpenModelRegistry
    AIRLOCK_MANAGED_BUNDLE_FILE = $BridgeOpenModelBundle
  }
  $BridgeOpenModelPure = Invoke-LauncherProcess $InstalledLauncher `
    @('om', 'quiet-route', '-p', 'test') $true $BridgeOpenModelEnvironment
  foreach ($Expected in @(
    'ACTIVE_PROFILE=openmodel-pure',
    'ROOT_MODEL=openmodel/quiet-route',
    'COMPACT_WINDOW=unset',
    'MAX_CONTEXT=64000',
    'ALWAYS_EFFORT=unset',
    'CUSTOM_CAPS=unset'
  )) {
    if ($BridgeOpenModelPure.Output -notmatch "(?m)^$([regex]::Escape($Expected))$") {
      throw "Windows worker-disabled local root missed ${Expected}: $($BridgeOpenModelPure.Output)"
    }
  }
  if ($BridgeOpenModelPure.Output -notmatch '(?m)^ARG=--agents\nARG=\{\}$') {
    throw "Windows worker-disabled local root did not render an empty Agent object: $($BridgeOpenModelPure.Output)"
  }
  $BridgeOpenModelAuto = Invoke-LauncherProcess $InstalledLauncher `
    @('om', 'quiet-route', '-p', 'test') $true ($BridgeOpenModelEnvironment + @{
      AIRLOCK_CONTEXT_WINDOW = 'auto'
    })
  if ($BridgeOpenModelAuto.Output -notmatch '(?m)^COMPACT_WINDOW=unset$' -or
      $BridgeOpenModelAuto.Output -notmatch '(?m)^MAX_CONTEXT=64000$') {
    throw "Windows local root discarded AIRLOCK_CONTEXT_WINDOW=auto: $($BridgeOpenModelAuto.Output)"
  }
  $BridgeOpenModelInherited = Invoke-LauncherProcess $InstalledLauncher `
    @('om', 'quiet-route', '-p', 'test') $true ($BridgeOpenModelEnvironment + @{
      CLAUDE_CODE_AUTO_COMPACT_WINDOW = '450000'
    })
  if ($BridgeOpenModelInherited.Output -notmatch '(?m)^COMPACT_WINDOW=450000$' -or
      $BridgeOpenModelInherited.Output -notmatch '(?m)^MAX_CONTEXT=64000$') {
    throw "Windows local root discarded an inherited compact-window override: $($BridgeOpenModelInherited.Output)"
  }
  $BridgeOpenModelHybrid = Invoke-LauncherProcess $InstalledLauncher `
    @('hybrid', 'om:quiet-route', '-p', 'test') $true $BridgeOpenModelEnvironment
  foreach ($Expected in @(
    'ACTIVE_PROFILE=hybrid-openmodel-root',
    'ROOT_MODEL=openmodel/quiet-route',
    'COMPACT_WINDOW=unset',
    'MAX_CONTEXT=64000',
    'ALWAYS_EFFORT=unset',
    'CUSTOM_CAPS=unset',
    'FABLE_CAPS=effort,xhigh_effort,max_effort'
  )) {
    if ($BridgeOpenModelHybrid.Output -notmatch "(?m)^$([regex]::Escape($Expected))$") {
      throw "Windows hybrid local root missed ${Expected}: $($BridgeOpenModelHybrid.Output)"
    }
  }
  [IO.File]::WriteAllBytes($InstalledBridgeOpenModelRouter, $InstalledBridgeOpenModelRouterBytes)

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
  if ($HybridLaunch.Output -notmatch '(?m)^SETTINGS_MCP_SERVERS=airlock-console-tools$' -or
      $HybridLaunch.Output -notmatch '(?m)^MCP_SERVERS=airlock-console-tools$' -or
      $HybridLaunch.Output -match 'airlock-web-tools') {
    throw "Anthropic-rooted Windows session did not receive Console-only MCP config: $($HybridLaunch.Output)"
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
  if ($GrokLaunch.Output -notmatch '(?m)^SETTINGS_MCP_SERVERS=airlock-console-tools,airlock-web-tools$' -or
      $GrokLaunch.Output -notmatch '(?m)^MCP_SERVERS=airlock-console-tools,airlock-web-tools$') {
    throw "Windows Grok session did not receive one combined managed MCP config: $($GrokLaunch.Output)"
  }
  $WebToolsOff = Invoke-LauncherProcess $InstalledLauncher @('grok', '-p', 'test') $true @{
    AIRLOCK_WEB_TOOLS = 'off'
  }
  if ($WebToolsOff.Output -notmatch '(?m)^SETTINGS_MCP_SERVERS=airlock-console-tools$' -or
      $WebToolsOff.Output -notmatch '(?m)^MCP_SERVERS=airlock-console-tools$' -or
      $WebToolsOff.Output -match 'airlock-web-tools') {
    throw "AIRLOCK_WEB_TOOLS=off changed or removed Windows Console tools: $($WebToolsOff.Output)"
  }
  $ConsoleToolsOff = Invoke-LauncherProcess $InstalledLauncher @('grok', '-p', 'test') $true @{
    AIRLOCK_CONSOLE_TOOLS = 'off'
  }
  if ($ConsoleToolsOff.Output -notmatch '(?m)^SETTINGS_MCP_SERVERS=airlock-web-tools$' -or
      $ConsoleToolsOff.Output -notmatch '(?m)^MCP_SERVERS=airlock-web-tools$' -or
      $ConsoleToolsOff.Output -match 'airlock-console-tools') {
    throw "AIRLOCK_CONSOLE_TOOLS=off did not omit only Windows Console tools: $($ConsoleToolsOff.Output)"
  }
  $BothToolsOff = Invoke-LauncherProcess $InstalledLauncher @('grok', '-p', 'test') $true @{
    AIRLOCK_WEB_TOOLS = 'off'
    AIRLOCK_CONSOLE_TOOLS = 'off'
  }
  if ($BothToolsOff.Output -notmatch '(?m)^SETTINGS_MCP_SERVERS=$' -or
      $BothToolsOff.Output -match '(?m)^MCP_SERVERS=') {
    throw "Disabled Windows managed tools still produced an MCP config: $($BothToolsOff.Output)"
  }
  $ConsoleToolsUppercase = Invoke-LauncherProcess $InstalledLauncher @('grok', '-p', 'test') $true @{
    AIRLOCK_CONSOLE_TOOLS = 'OFF'
  }
  if ($ConsoleToolsUppercase.Output -notmatch '(?m)^MCP_SERVERS=airlock-console-tools,airlock-web-tools$') {
    throw "Windows Console tools switch accepted a value other than literal off: $($ConsoleToolsUppercase.Output)"
  }
  if ($GrokLaunch.Output -notmatch '(?m)^COMPACT_WINDOW=400000$' -or
      $GrokLaunch.Output -notmatch '(?m)^MAX_CONTEXT=500000$') {
    throw "Grok 4.6 lost its documented window declaration: $($GrokLaunch.Output)"
  }
  # Astra is off in the saved pool, so an explicit Astra root must enable its
  # own route and declare the documented 922000-token input ceiling, matching
  # the POSIX launcher. Other roots keep Astra off.
  $AstraHybrid = Invoke-LauncherProcess $InstalledLauncher @('hybrid', 'astra', '-p', 'test') $true @{}
  if ($AstraHybrid.Output -notmatch '(?m)^ACTIVE_PROFILE=hybrid-openai-root$' -or
      $AstraHybrid.Output -notmatch '(?m)^ROOT_MODEL=gpt-6-astra$' -or
      $AstraHybrid.Output -notmatch 'airlock-astra' -or
      $AstraHybrid.Output -notmatch 'airlock-sol' -or
      $AstraHybrid.Output -notmatch '(?m)^COMPACT_WINDOW=736000$' -or
      $AstraHybrid.Output -notmatch '(?m)^MAX_CONTEXT=922000$') {
    throw "Windows hybrid Astra root did not enable Astra with its documented window: $($AstraHybrid.Output)"
  }
  $AstraPure = Invoke-LauncherProcess $InstalledLauncher @('astra', '-p', 'test') $true @{}
  if ($AstraPure.Output -notmatch '(?m)^ACTIVE_PROFILE=openai-pure$' -or
      $AstraPure.Output -notmatch '(?m)^ROOT_MODEL=gpt-6-astra$' -or
      $AstraPure.Output -notmatch '(?m)^DEFAULT_OPUS=gpt-6-astra$' -or
      $AstraPure.Output -notmatch '(?m)^COMPACT_WINDOW=736000$' -or
      $AstraPure.Output -notmatch '(?m)^MAX_CONTEXT=922000$') {
    throw "Windows OpenAI-only Astra root did not route Astra with its documented window: $($AstraPure.Output)"
  }
  $SolHybrid = Invoke-LauncherProcess $InstalledLauncher @('hybrid', 'sol', '-p', 'test') $true @{}
  if ($SolHybrid.Output -match 'gpt-6-astra') {
    throw "A Windows Sol root enabled Astra without being asked: $($SolHybrid.Output)"
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
  # Airlock runs one Grok flagship, so the older ID stays accepted as an alias
  # of the shipped route instead of becoming a second route.
  $GrokLegacy = Invoke-LauncherProcess $InstalledLauncher @('grok', '--model=grok-4.5', '-p', 'test')
  if ($GrokLegacy.Output -notmatch '(?m)^MODEL=grok-4\.6$') {
    throw "Legacy Grok ID did not alias the shipped flagship: $($GrokLegacy.Output)"
  }
  # An unrecognized bare argument passes through to Claude Code, matching the
  # POSIX launcher; only an explicit --model names a route and can be rejected.
  $BadGrok = Invoke-LauncherProcess $InstalledLauncher @('grok', '--model=bogus', '-p', 'test') $false
  if ($BadGrok.Error -notmatch 'unsupported Grok model') {
    throw "Unknown Grok model was not rejected clearly: $($BadGrok.Error)"
  }
  # A declared model extends the launcher without code changes: with grok-4.7
  # enabled in models.json, the same request resolves to that exact ID. The
  # declared ID has to be one Airlock does not ship, or the launcher's own
  # case matches first and the declaration path is never exercised.
  $DeclaredModels = Join-Path ([IO.Path]::GetTempPath()) ("airlock-models-{0}.json" -f [Guid]::NewGuid().ToString('N'))
  @'
{"schema_version": 1, "models": [{
  "id": "grok-4.7", "provider": "grok", "effort_ceiling": "xhigh",
  "context_window": null, "cost": "premium", "enabled": true
}]}
'@ | Set-Content -LiteralPath $DeclaredModels -Encoding Ascii
  $env:AIRLOCK_MODELS_FILE = $DeclaredModels
  try {
    $Declared = Invoke-LauncherProcess $InstalledLauncher @('grok', '--model=grok-4.7', '-p', 'test')
    if ($Declared.Output -notmatch '(?m)^MODEL=grok-4\.7$') {
      throw "Declared grok-4.7 model was not honored: $($Declared.Output)"
    }
  } finally {
    Remove-Item Env:\AIRLOCK_MODELS_FILE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $DeclaredModels -Force -ErrorAction SilentlyContinue
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
  # Out-String wraps long paths at the host width on Windows PowerShell 5.1.
  # Match the whitespace-normalized output, as the updater check above does.
  if ($DoctorCompact -notmatch 'PASS Airlock Console helper: .*?airlock_console\.py' -or
      $DoctorCompact -notmatch 'PASS Airlock Console tools helper: .*?airlock_console_tools\.py' -or
      $DoctorCompact -notmatch 'PASS Airlock Console MCP wrapper: .*?airlock_console_mcp\.py' -or
      $DoctorCompact -notmatch ('PASS Airlock Console site: .*?' + [regex]::Escape('share\airlock\console')) -or
      $DoctorCompact -notmatch '(PASS Airlock Console health: http://127\.0\.0\.1:4783/healthz|INFO Airlock Console is not running at 127\.0\.0\.1:4783)') {
    throw "Windows doctor did not report the Console installation safely: $DoctorOutput"
  }
  if ($DoctorOutput -notmatch '(?m)^INFO  OpenRouter registry is not configured: ' -or
      $DoctorOutput -notmatch '(?m)^INFO  Open-model registry is not configured: ' -or
      $DoctorOutput -notmatch '(?m)^PASS  OpenRouter credential backend is available: windows-dpapi$' -or
      $DoctorOutput -notmatch '(?m)^INFO  Doctor does not read the OpenRouter credential;') {
    throw "Windows doctor did not report safe OpenRouter or open-model state: $DoctorOutput"
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

  # Doctor must keep all local registry data offline and print only sanitized
  # counts and declared route labels. The helper output is synthetic so this
  # test never starts or contacts an inference server.
  $DoctorOpenModelStub = Join-Path $TempRoot 'doctor-openmodel-stub.py'
  [IO.File]::WriteAllText(
    $DoctorOpenModelStub,
    "import os`nstate = os.environ.get('AIRLOCK_TEST_OPENMODEL_DOCTOR_STATE', 'valid')`nprint('STATE=' + state)`nprint('ENDPOINT_COUNT=2')`nprint('ACTIVE_ENDPOINT_COUNT=1')`nprint('ROUTE_COUNT=3')`nprint('ACTIVE_ROUTE_COUNT=2')`nif state == 'valid':`n print('ENDPOINT=local-a`tenabled	loopback')`n print('ROUTE=local-coder	enabled	declared')`n",
    (New-Object Text.UTF8Encoding($false))
  )
  $env:AIRLOCK_OPENMODEL_HELPER = $DoctorOpenModelStub
  $env:AIRLOCK_OPENMODEL_REGISTRY_FILE = $OpenModelRegistry
  Remove-Item -LiteralPath 'Env:AIRLOCK_TEST_OPENMODEL_DOCTOR_STATE' -ErrorAction SilentlyContinue
  try {
    $ValidOpenModelDoctor = ((& (Join-Path $Root 'scripts\doctor.ps1') *>&1 | Out-String).Replace("`r", ''))
    $ExpectedOpenModelDoctor = (
      'PASS  Open-model registry is valid (1/2 endpoint(s) active, 2/3 route(s) active): ' + $OpenModelRegistry
    ) -replace '\s', ''
    $CompactValidOpenModelDoctor = $ValidOpenModelDoctor -replace '\s', ''
    if ($CompactValidOpenModelDoctor -notmatch [regex]::Escape($ExpectedOpenModelDoctor) -or
        $ValidOpenModelDoctor -notmatch '(?m)^INFO  Open-model endpoint: local-a \(enabled, loopback\)$' -or
        $ValidOpenModelDoctor -notmatch '(?m)^INFO  Open-model route: airlock-om-local-coder \(enabled, declared\)$' -or
        $ValidOpenModelDoctor -notmatch 'Doctor did not contact a local inference server') {
      throw "Windows doctor did not report the sanitized valid open-model registry: $ValidOpenModelDoctor"
    }
    $env:AIRLOCK_TEST_OPENMODEL_DOCTOR_STATE = 'invalid'
    $InvalidOpenModelDoctor = ((& (Join-Path $Root 'scripts\doctor.ps1') *>&1 | Out-String).Replace("`r", ''))
    $ExpectedInvalidOpenModelDoctor = (
      'FAIL  Open-model registry is invalid: ' + $OpenModelRegistry
    ) -replace '\s', ''
    if (($InvalidOpenModelDoctor -replace '\s', '') -notmatch [regex]::Escape($ExpectedInvalidOpenModelDoctor)) {
      throw "Windows doctor did not reject the invalid open-model registry safely: $InvalidOpenModelDoctor"
    }
  } finally {
    Remove-Item -LiteralPath 'Env:AIRLOCK_OPENMODEL_HELPER' -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Env:AIRLOCK_OPENMODEL_REGISTRY_FILE' -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Env:AIRLOCK_TEST_OPENMODEL_DOCTOR_STATE' -ErrorAction SilentlyContinue
  }

  Write-Stub 'claude.cmd' "@if `"%1`"==`"--version`" echo Claude Code test`r`n@if `"%1 %2`"==`"auth status`" exit /b 1`r`n@exit /b 0"
  $SignedOutOutput = ((& (Join-Path $Root 'scripts\doctor.ps1') *>&1 | Out-String).Replace("`r", ''))
  if ($LASTEXITCODE -eq 0) { throw 'Windows doctor ignored an unhealthy proxy with Claude signed out.' }
  if ($SignedOutOutput -notmatch '(?m)^INFO  Claude login was not detected; run: claude auth login$' -or
      $SignedOutOutput -notmatch '(?m)^INFO  OpenAI-only sessions can still work, but hybrid and Claude routes need this login\.$') {
    throw "Windows doctor did not explain the signed-out Claude behavior: $SignedOutOutput"
  }
} finally {
  if ($null -ne $StatusStubProcess -and -not $StatusStubProcess.HasExited) {
    try { $StatusStubProcess.Kill() } catch {}
  }
  $env:PATH = $OldPath
  $env:AIRLOCK_INSTALL_DIR = $OldInstall
  $env:AIRLOCK_CONFIG_DIR = $OldConfig
  $env:AIRLOCK_AGENT_DIR = $OldAgent
  Remove-Item -LiteralPath 'Env:AIRLOCK_PROXY_URL' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:AIRLOCK_TEST_PROXY_LOG' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:AIRLOCK_OPENROUTER_MODELS_HELPER' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:AIRLOCK_OPENROUTER_AUTH_HELPER' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:AIRLOCK_OPENMODEL_HELPER' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:AIRLOCK_OPENMODEL_REGISTRY_FILE' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:AIRLOCK_TEST_OPENMODEL_DOCTOR_STATE' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'All native Windows tests passed.'
# The last check above runs doctor.ps1 and expects it to fail, which leaves
# $LASTEXITCODE at 1. Exit explicitly so a caller that reads $LASTEXITCODE
# instead of the -File exit code still sees a pass.
exit 0
