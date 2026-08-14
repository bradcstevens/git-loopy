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
    # Wrapper contract §3.1 — the reason is drawn through the same production
    # seam as membership, so the two cannot drift.
    $ActualReason = Get-GitLoopyAfkReadyExclusion -Body $Case["body"]
    Assert-Equal `
        $Case["exclusion_reason"] `
        $ActualReason `
        "discriminator exclusion reason: $($Case["id"])"
}
foreach ($Reason in @($Discriminator["exclusion_reasons"])) {
    if (@($Discriminator["cases"] | Where-Object {
            $_["exclusion_reason"] -ceq $Reason
        }).Count -eq 0) {
        throw "FAIL: no discriminator case covers exclusion reason $Reason"
    }
}

# Wrapper contract §3.2 — the total order over eligible issues (#391, ADR-0032).
# Driven through `Get-GitLoopyIssueOrder` itself: an adapter that reproduced the
# comparison would stay green while the Orchestrator ordered a Pool differently,
# which is the drift the shared fixture exists to catch.
$IssueOrdering = ConvertFrom-GitLoopyJsonText -Text (
    Get-Content -LiteralPath (Join-Path $ConformanceDir "issue-ordering.json") -Raw
)

Assert-Equal `
    $IssueOrdering["priority_label"] `
    (Get-GitLoopyPriorityLabel) `
    "issue-ordering: the port reads the Priority label the fixture names"

$AcceptedYears = Get-GitLoopyAcceptedYearRange
Assert-Equal `
    $IssueOrdering["accepted_year_range"]["min"] `
    $AcceptedYears["min"] `
    "issue-ordering: the port accepts the oldest year the fixture declares"
Assert-Equal `
    $IssueOrdering["accepted_year_range"]["max"] `
    $AcceptedYears["max"] `
    "issue-ordering: the port accepts the newest year the fixture declares"

foreach ($Case in $IssueOrdering["cases"]) {
    $CaseId = $Case["id"]
    $Result = Get-GitLoopyIssueOrder -Candidates @($Case["issues"])
    $Expected = $Case["expected"]

    Assert-Equal `
        (@($Expected["order"]) -join ",") `
        (@($Result["order"]) -join ",") `
        "issue-ordering order: $CaseId"

    $ActualHead = if (@($Result["order"]).Count -gt 0) {
        @($Result["order"])[0]
    }
    else {
        $null
    }
    Assert-Equal $Expected["selected"] $ActualHead "issue-ordering head: $CaseId"

    $ExpectedUndated = @($Expected["undated"] | ForEach-Object {
            "$($_["issue"]):$($_["defect"])"
        }) -join ","
    $ActualUndated = @($Result["undated"] | ForEach-Object {
            "$($_["issue"]):$($_["defect"])"
        }) -join ","
    Assert-Equal $ExpectedUndated $ActualUndated "issue-ordering undated: $CaseId"
}

foreach ($Defect in @($IssueOrdering["timestamp_defects"])) {
    $Covered = @($IssueOrdering["cases"] | Where-Object {
            @($_["expected"]["undated"] | Where-Object {
                    $_["defect"] -ceq $Defect
                }).Count -gt 0
        }).Count
    if ($Covered -eq 0) {
        throw "FAIL: no issue-ordering case covers timestamp defect $Defect"
    }
}

# The reason this port reads its JSON through System.Text.Json: `ConvertFrom-Json`
# dates values §3.2 calls malformed, and a member that inherited its reader's
# tolerances would order a Pool the rest of the family orders differently.
foreach ($Widened in @(
        "2026-01-01T00:00:00",
        "2026-01-01T00:00:00+0100",
        "2026-01-01T24:00:00Z",
        "2026-01-01T00:00:00+24:00")) {
    $Json = "{""created_at"": ""$Widened""}"
    Assert-True `
        (($Json | ConvertFrom-Json -AsHashtable)["created_at"] -isnot [string]) `
        "issue-ordering: ConvertFrom-Json dates $Widened, which is why it is not used"
    Assert-True `
        ((ConvertFrom-GitLoopyJsonText -Text $Json)["created_at"] -is [string]) `
        "issue-ordering: the date-safe read keeps $Widened a string"
    Assert-Equal `
        "malformed" `
        (Get-GitLoopyTimestampDefect -CreatedAt $Widened) `
        "issue-ordering: $Widened is malformed, as it is for every other member"
}

# A caller that read its JSON the coercing way must not quietly get a different
# order; the pre-dated value is refused rather than rescued.
Assert-Equal `
    "malformed" `
    (Get-GitLoopyTimestampDefect -CreatedAt ([datetime]"2026-01-01T00:00:00Z")) `
    "issue-ordering: an already-dated created_at is refused, not rescued"

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

# The auto-close backstop (§5) and the Checkpoint active-ref inference (§7) share
# one Pool-close-ref assembly (#114): { ref, kind = "issue" } descriptors from the
# Pool crossed with the closing keywords in this Iteration's commits.
$PacCommits = @(
    [ordered]@{ sha = "a1"; subject = "feat: thing"; body = "Closes #41" },
    [ordered]@{ sha = "b2"; subject = "chore: noise"; body = "" }
)
$PacActionable = Get-GitLoopyPoolActionableCloseReferences `
    -Pool @([ordered]@{ number = 41 }, [ordered]@{ number = 77 }) `
    -Commits $PacCommits
Assert-Equal "41" (
    [string]::Join(",", @($PacActionable))
) "pool-actionable-close-refs: in-Pool close-ref is actionable"
$PacOutOfPool = Get-GitLoopyPoolActionableCloseReferences `
    -Pool @([ordered]@{ number = 41 }) `
    -Commits @([ordered]@{ sha = "c3"; subject = "fix: other"; body = "Fixes #999" })
Assert-Equal 0 (
    @($PacOutOfPool).Count
) "pool-actionable-close-refs: out-of-Pool ref excluded"
$PacEmptyPool = Get-GitLoopyPoolActionableCloseReferences `
    -Pool @() `
    -Commits $PacCommits
Assert-Equal 0 (
    @($PacEmptyPool).Count
) "pool-actionable-close-refs: empty Pool yields nothing"

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

$CheckpointMessages = Get-Content `
    -LiteralPath (Join-Path $ConformanceDir "checkpoint-messages.json") `
    -Raw |
    ConvertFrom-Json -AsHashtable
foreach ($Case in $CheckpointMessages["author_cases"]) {
    # Translate the JSON active_ref to the seam's string argument: null -> ""
    # (unattributed), an int -> its digits (issue-number attribution), and a
    # digit-bearing string ref passes through verbatim. Mirrors the shell
    # adapter's `if .active_ref == null then "" else (.active_ref | tostring)`.
    $ActiveRefValue = $Case["active_ref"]
    $ActiveRef = if ($null -eq $ActiveRefValue) {
        ""
    }
    else {
        [string]$ActiveRefValue
    }
    $Message = Get-GitLoopyCheckpointMessage -ActiveRef $ActiveRef
    $CloseRefs = Get-GitLoopyCloseReferences -Messages $Message
    Assert-Equal (
        $Case["expected_message"]
    ) $Message "checkpoint-messages author fixture: $($Case["id"])"
    Assert-Equal 0 (
        @($CloseRefs).Count
    ) "checkpoint-messages author fixture: $($Case["id"]) (no close refs)"
    Assert-True (
        Test-GitLoopyCheckpointMessage -Message $Message
    ) "checkpoint-messages author fixture: $($Case["id"]) (is checkpoint)"
    Assert-True (
        -not $Message.Contains("#")
    ) "checkpoint-messages author fixture: $($Case["id"]) contains '#'"
}
foreach ($Case in $CheckpointMessages["detection_cases"]) {
    $Actual = Test-GitLoopyCheckpointMessage -Message $Case["message"]
    Assert-Equal (
        [bool]$Case["is_checkpoint"]
    ) $Actual "checkpoint-messages detection fixture: $($Case["id"])"
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
Assert-Equal "" $Defaults.InteractiveFlag "no interactive flag by default"

# The live interface is a tri-state at the flag layer: "on", "off", and "no flag
# given". Collapsing the third into a boolean would lose the only thing that
# lets `GIT_LOOPY_INTERACTIVE` and TTY detection still have a say.
foreach ($Case in @(
    @{
        Id = "--interactive requests the live interface"
        Arguments = @("--interactive")
        Expected = "on"
    },
    @{
        Id = "--no-interactive refuses it"
        Arguments = @("--no-interactive")
        Expected = "off"
    },
    @{
        Id = "the last interactive flag wins"
        Arguments = @("--interactive", "--no-interactive")
        Expected = "off"
    },
    @{
        Id = "the last interactive flag wins in the other order"
        Arguments = @("--no-interactive", "--interactive")
        Expected = "on"
    }
)) {
    $InteractiveConfig = Resolve-GitLoopyConfig `
        -Arguments $Case["Arguments"] `
        -Environment $EmptyEnvironment
    Assert-Equal $Case["Expected"] $InteractiveConfig.InteractiveFlag (
        "interactive flag: $($Case["Id"])"
    )
}

# The flags take no value, so a bare cap after one is still the cap and not a
# swallowed argument.
$InteractiveWithCap = Resolve-GitLoopyConfig `
    -Arguments @("--interactive", "4") `
    -Environment $EmptyEnvironment
Assert-Equal 4 $InteractiveWithCap.MaxIterations (
    "--interactive consumes no value"
)

$Usage = Get-GitLoopyUsage
foreach ($Flag in @("--interactive", "--no-interactive")) {
    Assert-True (
        $Usage.Contains($Flag, [StringComparison]::Ordinal)
    ) "usage documents $Flag"
}

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

# --- Closed-world Skill policy (Wrapper contract §17.6) ------------------------
# This port has no native `enabled_skills` support yet, so every policy surface
# the family contract names must be *detected*, not ignored. The fixture's
# `native_transition` block is the input — the surface names and their canonical
# order are read from it rather than restated here, so a fifth surface added to
# the contract fails this port instead of silently widening a Run's capability
# set.
$SkillPolicyFixture = Get-Content `
    -LiteralPath (Join-Path $ConformanceDir "skill-policy.json") `
    -Raw |
    ConvertFrom-Json -AsHashtable

Assert-True (
    @($SkillPolicyFixture["native_transition"]["fail_closed"]) -ccontains "powershell"
) "skill-policy fixture: powershell is a fail-closed port"
Assert-True (
    -not (
        @($SkillPolicyFixture["native_transition"]["implemented"]) -ccontains "powershell"
    )
) "skill-policy fixture: powershell does not implement the policy natively"

$PolicySurfaces = [string]::Join(
    ",",
    @($SkillPolicyFixture["native_transition"]["policy_surfaces"])
)

$PolicyTemp = Join-Path ([IO.Path]::GetTempPath()) (
    "git-loopy-skill-policy-$([guid]::NewGuid())"
)
$PolicyRepo = Join-Path $PolicyTemp "repo"
$PolicyGlobal = Join-Path $PolicyTemp "global"
$PolicyProjectConfig = Join-Path $PolicyRepo "git-loopy/config.toml"
$PolicyGlobalConfig = Join-Path $PolicyGlobal "git-loopy/config.toml"
[IO.Directory]::CreateDirectory((Join-Path $PolicyRepo "git-loopy")) | Out-Null
[IO.Directory]::CreateDirectory((Join-Path $PolicyGlobal "git-loopy")) | Out-Null

function Get-PolicySurfaceLine {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$Arguments,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Environment
    )

    $Config = Resolve-GitLoopyConfig `
        -Arguments $Arguments `
        -Environment $Environment
    return [string]::Join(
        ",",
        @(
            Get-GitLoopySkillPolicySurfaces `
                -Config $Config `
                -Environment $Environment `
                -RepoRoot $PolicyRepo
        )
    )
}

try {
    # Every surface at once: the detector must name all four, in fixture order.
    [IO.File]::WriteAllText($PolicyProjectConfig, "enabled_skills = [`"tdd`"]`n")
    Assert-Equal $PolicySurfaces (
        Get-PolicySurfaceLine `
            -Arguments @("--enable-skill", "tdd", "--disable-skill", "prototype") `
            -Environment ([ordered]@{
                XDG_CONFIG_HOME = $PolicyGlobal
                GIT_LOOPY_ENABLED_SKILLS = "tdd"
            })
    ) "skill-policy: every unsupported surface is detected in fixture order"
    [IO.File]::Delete($PolicyProjectConfig)

    # An explicit empty replacement is a real policy, so presence — not content —
    # is what the detector reads.
    Assert-Equal "GIT_LOOPY_ENABLED_SKILLS" (
        Get-PolicySurfaceLine `
            -Arguments @() `
            -Environment ([ordered]@{
                XDG_CONFIG_HOME = $PolicyGlobal
                GIT_LOOPY_ENABLED_SKILLS = ""
            })
    ) "skill-policy: an explicit empty environment replacement is a surface"

    # The global scope is a standard Config location too.
    [IO.File]::WriteAllText($PolicyGlobalConfig, "enabled_skills = []`n")
    Assert-Equal "enabled_skills" (
        Get-PolicySurfaceLine `
            -Arguments @() `
            -Environment ([ordered]@{ XDG_CONFIG_HOME = $PolicyGlobal })
    ) "skill-policy: a global Config key is detected"
    [IO.File]::Delete($PolicyGlobalConfig)

    # A Config that predates the key stays runnable: the generated banner is
    # comment-only, and a commented example is not a configured policy.
    [IO.File]::WriteAllText(
        $PolicyProjectConfig,
        "# enabled_skills = [`"tdd`"]`nmodel = `"claude-opus-4.8`"`n"
    )
    Assert-Equal "" (
        Get-PolicySurfaceLine `
            -Arguments @() `
            -Environment ([ordered]@{ XDG_CONFIG_HOME = $PolicyGlobal })
    ) "skill-policy: a commented key is not a configured policy"

    # A TOML quoted key may spell the same name with escapes, and `tomllib`
    # resolves `"enabled\u005fskills"` to `enabled_skills`. Detection must too, or
    # a Config the Python Orchestrator honours would run wide here.
    foreach ($Spelling in @(
        '"enabled\u005fskills"',
        '"enabled\U0000005Fskills"',
        '"\u0065nabled\u005fskills"'
    )) {
        [IO.File]::WriteAllText($PolicyProjectConfig, "$Spelling = [`"tdd`"]`n")
        Assert-Equal "enabled_skills" (
            Get-PolicySurfaceLine `
                -Arguments @() `
                -Environment ([ordered]@{ XDG_CONFIG_HOME = $PolicyGlobal })
        ) "skill-policy: an escaped TOML quoted key is detected ($Spelling)"
    }

    # Decoding must not smear one key into another: a deprecated legacy guard
    # spelled with the same escape is still not a closed-world surface.
    [IO.File]::WriteAllText(
        $PolicyProjectConfig,
        "`"deny\u005fskills`" = [`"tdd`"]`n"
    )
    Assert-Equal "" (
        Get-PolicySurfaceLine `
            -Arguments @() `
            -Environment ([ordered]@{ XDG_CONFIG_HOME = $PolicyGlobal })
    ) "skill-policy: an escaped unrelated key is not a policy"
    [IO.File]::Delete($PolicyProjectConfig)

    # Legacy deny-only invocations are explicitly *not* a closed-world surface
    # (contract §17.2 keeps them as deprecated final guards), so they must
    # resolve and run unchanged.
    $LegacyEnvironment = [ordered]@{
        XDG_CONFIG_HOME = $PolicyGlobal
        GIT_LOOPY_DENY_SKILLS = "legacy-skill"
    }
    $LegacyArguments = @("--deny-skill", "flag-skill", "--deny-tool", "flag-tool")
    Assert-Equal "" (
        Get-PolicySurfaceLine `
            -Arguments $LegacyArguments `
            -Environment $LegacyEnvironment
    ) "skill-policy: legacy deny-only inputs are not an unsupported surface"
    Assert-Equal "flag-skill legacy-skill" (
        [string]::Join(
            " ",
            @(
                (
                    Resolve-GitLoopyConfig `
                        -Arguments $LegacyArguments `
                        -Environment $LegacyEnvironment
                ).DenySkills
            )
        )
    ) "skill-policy: legacy Skill denials still resolve unchanged"

    # Recognition is not application: the port records the surface and applies
    # nothing, so a Run that aborts never carried an overlay into the denylists.
    $OverlayEnvironment = [ordered]@{ XDG_CONFIG_HOME = $PolicyGlobal }
    Assert-Equal "--disable-skill" (
        Get-PolicySurfaceLine `
            -Arguments @("--disable-skill=prototype") `
            -Environment $OverlayEnvironment
    ) "skill-policy: the =VALUE overlay form is recognised"
    Assert-Equal 0 (
        @(
            (
                Resolve-GitLoopyConfig `
                    -Arguments @("--disable-skill=prototype") `
                    -Environment $OverlayEnvironment
            ).DenySkills
        ).Count
    ) "skill-policy: a recognised overlay is never applied as a legacy denial"
}
finally {
    if ([IO.Directory]::Exists($PolicyTemp)) {
        [IO.Directory]::Delete($PolicyTemp, $true)
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
