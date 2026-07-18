Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$ConformanceDir = Join-Path (Split-Path -Parent $PortDir) "conformance"
$ModulePath = Join-Path $PortDir "GitLoopy.Orchestrator.psm1"

Import-Module $ModulePath -Force

function Assert-True {
    param(
        [Parameter(Mandatory)]
        [bool]$Condition,
        [Parameter(Mandatory)]
        [string]$Description
    )

    if (-not $Condition) {
        throw "FAIL: $Description"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        [object]$Expected,
        [AllowNull()]
        [object]$Actual,
        [Parameter(Mandatory)]
        [string]$Description
    )

    if ($Expected -is [string] -and $Actual -is [string]) {
        if ($Expected -cne $Actual) {
            throw "FAIL: $Description`nexpected: $Expected`nactual:   $Actual"
        }
        return
    }
    if ($Expected -ne $Actual) {
        throw "FAIL: $Description`nexpected: $Expected`nactual:   $Actual"
    }
}

$Discriminator = Get-Content `
    -LiteralPath (Join-Path $ConformanceDir "discriminator.json") `
    -Raw |
    ConvertFrom-Json -AsHashtable
foreach ($Case in $Discriminator["cases"]) {
    $Actual = Test-GitLoopyAfkReady -Body $Case["body"]
    Assert-Equal $Case["eligible"] $Actual "discriminator fixture: $($Case["id"])"
}

$ExitCodes = Get-Content `
    -LiteralPath (Join-Path $ConformanceDir "exit-codes.json") `
    -Raw |
    ConvertFrom-Json -AsHashtable
foreach ($Case in $ExitCodes["cases"]) {
    $Actual = Get-GitLoopyExitCode -Reason $Case["reason"]
    Assert-Equal $Case["exit_code"] $Actual "exit-code fixture: $($Case["id"])"
}

$EmptyEnvironment = [ordered]@{}
$Defaults = Resolve-GitLoopyConfig `
    -Arguments @() `
    -Environment $EmptyEnvironment
Assert-Equal 0 $Defaults.MaxIterations "default iteration cap"
Assert-Equal "claude-opus-4.8" $Defaults.Model "default model"
Assert-Equal "max" $Defaults.ReasoningEffort "default reasoning effort"
Assert-Equal "github" $Defaults.IssueSource "default issue source"
Assert-Equal 3 $Defaults.MaxNmtStrikes "default Strike threshold"
Assert-Equal 7200.0 $Defaults.SendTimeoutSeconds "default send timeout"
Assert-Equal 0 $Defaults.DenyTools.Count "default tool denylist"
Assert-Equal 0 $Defaults.DenySkills.Count "default skill denylist"

$Environment = [ordered]@{
    GIT_LOOPY_MODEL = "env-model"
    GIT_LOOPY_REASONING_EFFORT = "low"
    GIT_LOOPY_ISSUE_SOURCE = "github"
    GIT_LOOPY_MAX_NMT_STRIKES = "7"
    GIT_LOOPY_DENY_TOOLS = "env-tool,shared-tool"
    GIT_LOOPY_DENY_SKILLS = "env-skill"
    GIT_LOOPY_SEND_TIMEOUT_SECONDS = "90"
}
$Resolved = Resolve-GitLoopyConfig `
    -Arguments @(
        "2",
        "--model", "cli-model",
        "--reasoning-effort", "xhigh",
        "--issue-source", "prds",
        "--max-nmt-strikes", "5",
        "--deny-tool", "cli-tool",
        "--deny-tool", "shared-tool",
        "--deny-skill", "cli-skill",
        "--send-timeout-seconds", "45"
    ) `
    -Environment $Environment

Assert-Equal 2 $Resolved.MaxIterations "CLI iteration cap"
Assert-Equal "cli-model" $Resolved.Model "CLI model precedence"
Assert-Equal "xhigh" $Resolved.ReasoningEffort "CLI effort precedence"
Assert-Equal "prds" $Resolved.IssueSource "CLI source precedence"
Assert-Equal 5 $Resolved.MaxNmtStrikes "CLI Strike precedence"
Assert-Equal 45.0 $Resolved.SendTimeoutSeconds "CLI timeout precedence"
Assert-Equal (
    "cli-tool,shared-tool,env-tool"
) ([string]::Join(",", $Resolved.DenyTools)) "tool denylists are unioned and stable"
Assert-Equal (
    "cli-skill,env-skill"
) ([string]::Join(",", $Resolved.DenySkills)) "skill denylists are unioned and stable"

$InvalidArgumentSets = @(
    @("not-a-number"),
    @("-1"),
    @("--issue-source", "nowhere"),
    @("--max-nmt-strikes", "0"),
    @("--reasoning-effort", "impossible"),
    @("--send-timeout-seconds", "0"),
    @("--unknown")
)
foreach ($InvalidArguments in $InvalidArgumentSets) {
    $Rejected = $false
    try {
        Resolve-GitLoopyConfig `
            -Arguments $InvalidArguments `
            -Environment $EmptyEnvironment | Out-Null
    }
    catch [System.Management.Automation.ParseException] {
        $Rejected = $true
    }
    Assert-True $Rejected (
        "malformed invocation was accepted: " +
        [string]::Join(" ", $InvalidArguments)
    )
}

$TempDir = Join-Path ([IO.Path]::GetTempPath()) (
    "git-loopy-prompt-$([guid]::NewGuid())"
)
$Repo = Join-Path $TempDir "repo"
$GlobalHome = Join-Path $TempDir "global"
$PackagedPrompt = Join-Path $TempDir "packaged/PROMPT.md"
[IO.Directory]::CreateDirectory((Join-Path $Repo "git-loopy")) | Out-Null
[IO.Directory]::CreateDirectory((Join-Path $GlobalHome "git-loopy")) | Out-Null
[IO.Directory]::CreateDirectory((Split-Path -Parent $PackagedPrompt)) | Out-Null

try {
    [IO.File]::WriteAllText($PackagedPrompt, "packaged`n")
    $GlobalPrompt = Join-Path $GlobalHome "git-loopy/PROMPT.md"
    [IO.File]::WriteAllText($GlobalPrompt, "global`n")
    $PromptEnvironment = [ordered]@{
        XDG_CONFIG_HOME = $GlobalHome
    }

    Assert-Equal $GlobalPrompt (
        Resolve-GitLoopyPrompt `
            -RepoRoot $Repo `
            -PackagedPrompt $PackagedPrompt `
            -Environment $PromptEnvironment
    ) "global prompt overrides packaged prompt"

    $ProjectPrompt = Join-Path $Repo "git-loopy/PROMPT.md"
    [IO.File]::WriteAllText($ProjectPrompt, "project`n")
    $ResolvedProjectPrompt = Resolve-GitLoopyPrompt `
        -RepoRoot $Repo `
        -PackagedPrompt $PackagedPrompt `
        -Environment $PromptEnvironment
    Assert-Equal "project" (
        [IO.File]::ReadAllText($ResolvedProjectPrompt).Trim()
    ) "project prompt overrides global prompt"
    Assert-True (
        $ResolvedProjectPrompt.StartsWith(
            (Join-Path $Repo "git-loopy"),
            [StringComparison]::OrdinalIgnoreCase
        )
    ) "project prompt did not resolve from project scope"

    [IO.File]::Delete($ProjectPrompt)
    [IO.File]::Delete($GlobalPrompt)
    Assert-Equal $PackagedPrompt (
        Resolve-GitLoopyPrompt `
            -RepoRoot $Repo `
            -PackagedPrompt $PackagedPrompt `
            -Environment $PromptEnvironment
    ) "packaged prompt is the final fallback"

    [IO.File]::Delete($PackagedPrompt)
    Assert-True (
        $null -eq (
            Resolve-GitLoopyPrompt `
                -RepoRoot $Repo `
                -PackagedPrompt $PackagedPrompt `
                -Environment $PromptEnvironment
        )
    ) "prompt resolution succeeded with every scope absent"
}
finally {
    if ([IO.Directory]::Exists($TempDir)) {
        [IO.Directory]::Delete($TempDir, $true)
    }
}

Write-Output "PowerShell Orchestrator conformance: ok"
