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

$CloseReferences = Get-Content `
    -LiteralPath (Join-Path $ConformanceDir "close-references.json") `
    -Raw |
    ConvertFrom-Json -AsHashtable
Assert-Equal (
    $CloseReferences["reference_regex"]
) (Get-GitLoopyCloseKeywordPattern) "close-keyword regex matches the shared reference"
foreach ($Case in $CloseReferences["cases"]) {
    $Messages = $Case["commit_messages"]
    $Pool = @()
    foreach ($Number in @($Case["issue_pool"])) {
        $Pool += [ordered]@{ ref = [int]$Number; kind = "issue" }
    }
    foreach ($Number in @($Case["pr_pool"])) {
        $Pool += [ordered]@{ ref = [int]$Number; kind = "pr" }
    }
    $Extracted = Get-GitLoopyCloseReferences -Messages $Messages
    $Actionable = Get-GitLoopyActionableCloseReferences `
        -Messages $Messages `
        -Pool $Pool
    Assert-Equal (
        [string]::Join(",", @($Case["extracted_refs"]))
    ) (
        [string]::Join(",", @($Extracted))
    ) "close-references extract fixture: $($Case["id"])"
    Assert-Equal (
        [string]::Join(",", @($Case["actionable_refs"]))
    ) (
        [string]::Join(",", @($Actionable))
    ) "close-references actionable fixture: $($Case["id"])"
}

$ProgressStrikes = Get-Content `
    -LiteralPath (Join-Path $ConformanceDir "progress-strikes.json") `
    -Raw |
    ConvertFrom-Json -AsHashtable
foreach ($Case in $ProgressStrikes["cases"]) {
    [int]$Strikes = 0
    $Outcome = "running"
    $StepIndex = 0
    foreach ($Step in $Case["steps"]) {
        $StepIndex += 1
        $Signals = $Step["signals"]
        $Expected = $Step["expected"]
        $Progress = Test-GitLoopyIterationProgress `
            -Commits $Signals["commits_in_iter"] `
            -AutoClosures $Signals["auto_closures_in_iter"] `
            -Checkpoints $Signals["checkpoints_in_iter"] `
            -PrAdvances $Signals["pr_advances_in_iter"] `
            -SawNmt ([bool]$Signals["saw_nmt_sentinel"])
        $State = Step-GitLoopyStrikeState `
            -MaxStrikes $Case["max_strikes"] `
            -Strikes $Strikes `
            -Outcome $Outcome `
            -Commits $Signals["commits_in_iter"] `
            -AutoClosures $Signals["auto_closures_in_iter"] `
            -Checkpoints $Signals["checkpoints_in_iter"] `
            -PrAdvances $Signals["pr_advances_in_iter"] `
            -SawNmt ([bool]$Signals["saw_nmt_sentinel"])
        $Strikes = $State.Strikes
        $Outcome = $State.Outcome
        Assert-Equal ([bool]$Expected["progress"]) $Progress (
            "progress-strikes fixture: $($Case["id"]) step $StepIndex (progress)"
        )
        Assert-Equal $Expected["strikes"] $Strikes (
            "progress-strikes fixture: $($Case["id"]) step $StepIndex (strikes)"
        )
        Assert-Equal $Expected["outcome"] $Outcome (
            "progress-strikes fixture: $($Case["id"]) step $StepIndex (outcome)"
        )
    }
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

$Suffixed = Resolve-GitLoopyConfig `
    -Arguments @() `
    -Environment ([ordered]@{
        GIT_LOOPY_MODEL = "claude-opus-4.7-xhigh"
    })
Assert-Equal "claude-opus-4.7" $Suffixed.Model "suffixed model base id"
Assert-Equal "xhigh" $Suffixed.ReasoningEffort "model suffix effort"

$OverriddenSuffix = Resolve-GitLoopyConfig `
    -Arguments @() `
    -Environment ([ordered]@{
        GIT_LOOPY_MODEL = "claude-opus-4.7-xhigh"
        GIT_LOOPY_REASONING_EFFORT = "medium"
    })
Assert-Equal (
    "claude-opus-4.7"
) $OverriddenSuffix.Model "overridden suffix base id"
Assert-Equal (
    "medium"
) $OverriddenSuffix.ReasoningEffort "explicit effort overrides model suffix"

$OmittedEffort = Resolve-GitLoopyConfig `
    -Arguments @() `
    -Environment ([ordered]@{
        GIT_LOOPY_MODEL = "claude-sonnet-4.6"
    })
Assert-Equal $null (
    $OmittedEffort.ReasoningEffort
) "non-default model leaves effort omitted"

$InvalidArgumentSets = @(
    @("not-a-number"),
    @("-1"),
    @("--issue-source", "nowhere"),
    @("--max-nmt-strikes", "0"),
    @("--reasoning-effort", "impossible"),
    @("--reasoning-effort="),
    @("--send-timeout-seconds", "0"),
    @("--model", "--help"),
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

# --- Send-timeout watchdog (Wrapper contract §4 real-exit-status + §6) ---------
# `Invoke-GitLoopyBoundedTurn` bounds one agent turn by the resolved send timeout
# using pwsh built-ins only (an inner pwsh under a child Process, no jq/timeout(1)
# dependency). A turn that overruns the bound is force-terminated at ~the bound
# and reported as a failed turn (exit 124) — landing no agent commit, so §6 counts
# the Iteration no-progress; a turn that finishes within the bound returns its own
# real exit status. The turn command is the running pwsh so the assertions hold
# identically on Linux, macOS, and Windows.
$PwshExe = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName

# Capture the helper's own stderr (the timeout warning) without disturbing the
# child process's inherited fd 2. [Console]::SetError only reroutes in-process
# [Console]::Error writes, which is exactly where the warning is emitted.
function Invoke-CapturedBoundedTurn {
    param(
        [double]$TimeoutSeconds,
        [string]$Command,
        [string[]]$Argv
    )

    $OriginalError = [Console]::Error
    $Capture = [IO.StringWriter]::new()
    [Console]::SetError($Capture)
    try {
        $Code = Invoke-GitLoopyBoundedTurn `
            -TimeoutSeconds $TimeoutSeconds `
            -Command $Command `
            -Argv $Argv
    }
    finally {
        [Console]::SetError($OriginalError)
    }
    return [pscustomobject]@{ Code = $Code; Stderr = $Capture.ToString() }
}

$OverrunWatch = [Diagnostics.Stopwatch]::StartNew()
$Overrun = Invoke-CapturedBoundedTurn `
    -TimeoutSeconds 1 `
    -Command $PwshExe `
    -Argv @("-NoLogo", "-NoProfile", "-Command", "Start-Sleep -Seconds 30")
$OverrunWatch.Stop()
Assert-Equal 124 $Overrun.Code (
    "an overrunning turn is reported with the timeout exit code"
)
Assert-True (
    $OverrunWatch.Elapsed.TotalSeconds -lt 20
) "an overrunning turn was not bounded (took $($OverrunWatch.Elapsed.TotalSeconds)s, bound 1s)"
Assert-True (
    $Overrun.Stderr.Contains(
        "exceeded the 1s send timeout",
        [StringComparison]::Ordinal
    )
) "an overrunning turn did not warn that the bound fired"

$WithinBound = Invoke-CapturedBoundedTurn `
    -TimeoutSeconds 30 `
    -Command $PwshExe `
    -Argv @("-NoLogo", "-NoProfile", "-Command", "exit 7")
Assert-Equal 7 $WithinBound.Code (
    "a within-bound turn preserves its real exit status (contract §4)"
)

Write-Output "PowerShell Orchestrator conformance: ok"
