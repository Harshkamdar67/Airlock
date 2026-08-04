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

$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("airlock-windows-test-{0}" -f [Guid]::NewGuid().ToString('N'))
$StubDir = Join-Path $TempRoot 'stubs'
$InstallDir = Join-Path $TempRoot 'bin'
$ConfigDir = Join-Path $TempRoot 'config'
$AgentDir = Join-Path $TempRoot 'agents'
New-Item -ItemType Directory -Path $StubDir -Force | Out-Null

function Write-Stub([string]$Name, [string]$Body = '@exit /b 0') {
  [IO.File]::WriteAllText(
    (Join-Path $StubDir $Name),
    "@echo off`r`n$Body`r`n",
    (New-Object Text.ASCIIEncoding)
  )
}

Write-Stub 'git.cmd'
Write-Stub 'bash.cmd'
Write-Stub 'claude.cmd' "@if `"%1`"==`"--version`" echo Claude Code test`r`n@exit /b 0"
Write-Stub 'claude-code-proxy.cmd' "@if `"%1`"==`"--version`" echo Proxy test`r`n@exit /b 0"
Write-Stub 'python.cmd'

$OldPath = $env:PATH
$OldInstall = $env:AIRLOCK_INSTALL_DIR
$OldConfig = $env:AIRLOCK_CONFIG_DIR
$OldAgent = $env:AIRLOCK_AGENT_DIR
try {
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

  $env:PATH = "$InstallDir;$StubDir;$OldPath"
  $env:AIRLOCK_PROXY_URL = 'http://127.0.0.1:1'
  & (Join-Path $Root 'scripts\doctor.ps1') *> $null
  if ($LASTEXITCODE -eq 0) { throw 'Windows doctor ignored an unhealthy proxy.' }
} finally {
  $env:PATH = $OldPath
  $env:AIRLOCK_INSTALL_DIR = $OldInstall
  $env:AIRLOCK_CONFIG_DIR = $OldConfig
  $env:AIRLOCK_AGENT_DIR = $OldAgent
  Remove-Item -LiteralPath 'Env:AIRLOCK_PROXY_URL' -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'All native Windows tests passed.'
# The last check above runs doctor.ps1 and expects it to fail, which leaves
# $LASTEXITCODE at 1. Exit explicitly so a caller that reads $LASTEXITCODE
# instead of the -File exit code still sees a pass.
exit 0
