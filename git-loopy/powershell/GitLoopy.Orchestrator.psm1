Set-StrictMode -Version Latest

$EventsModule = Join-Path $PSScriptRoot "GitLoopy.Events.psm1"
Import-Module $EventsModule -Force

function New-GitLoopyParseException {
    param(
        [Parameter(Mandatory)]
        [string]$Message
    )

    return [System.Management.Automation.ParseException]::new(
        "git-loopy: $Message"
    )
}

function Get-GitLoopyEnvironment {
    [CmdletBinding()]
    param()

    $Environment = [ordered]@{}
    foreach ($Item in Get-ChildItem Env:) {
        $Environment[$Item.Name] = $Item.Value
    }
    return $Environment
}

function Get-GitLoopyEnvironmentValue {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Environment,
        [Parameter(Mandatory)]
        [string]$Name
    )

    if ($Environment.Contains($Name)) {
        return [string]$Environment[$Name]
    }
    return $null
}

function Add-GitLoopyUniqueValue {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[string]]$Values,
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [Collections.Generic.HashSet[string]]$Seen,
        [AllowNull()]
        [string]$Value
    )

    $Trimmed = if ($null -eq $Value) { "" } else { $Value.Trim() }
    if ($Trimmed.Length -gt 0 -and $Seen.Add($Trimmed)) {
        $Values.Add($Trimmed)
    }
}

function Resolve-GitLoopyConfig {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$Arguments,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Environment
    )

    $Model = Get-GitLoopyEnvironmentValue $Environment "GIT_LOOPY_MODEL"
    $ModelExplicit = -not [string]::IsNullOrWhiteSpace($Model)
    if (-not $ModelExplicit) {
        $Model = "claude-opus-4.8"
    }
    $ReasoningEffort = Get-GitLoopyEnvironmentValue `
        $Environment `
        "GIT_LOOPY_REASONING_EFFORT"
    $EffortExplicit = -not [string]::IsNullOrWhiteSpace($ReasoningEffort)
    if (-not $EffortExplicit) {
        $ReasoningEffort = $null
    }
    $IssueSource = Get-GitLoopyEnvironmentValue `
        $Environment `
        "GIT_LOOPY_ISSUE_SOURCE"
    if ([string]::IsNullOrWhiteSpace($IssueSource)) {
        $IssueSource = "github"
    }
    $MaxNmtStrikesText = Get-GitLoopyEnvironmentValue `
        $Environment `
        "GIT_LOOPY_MAX_NMT_STRIKES"
    if ([string]::IsNullOrWhiteSpace($MaxNmtStrikesText)) {
        $MaxNmtStrikesText = "3"
    }
    $SendTimeoutText = Get-GitLoopyEnvironmentValue `
        $Environment `
        "GIT_LOOPY_SEND_TIMEOUT_SECONDS"
    if ([string]::IsNullOrWhiteSpace($SendTimeoutText)) {
        $SendTimeoutText = "7200"
    }
    $EnvironmentTools = Get-GitLoopyEnvironmentValue `
        $Environment `
        "GIT_LOOPY_DENY_TOOLS"
    $EnvironmentSkills = Get-GitLoopyEnvironmentValue `
        $Environment `
        "GIT_LOOPY_DENY_SKILLS"

    $MaxIterationsText = "0"
    $PositionalSeen = $false
    $ShowHelp = $false
    $CliTools = [Collections.Generic.List[string]]::new()
    $CliSkills = [Collections.Generic.List[string]]::new()

    for ($Index = 0; $Index -lt $Arguments.Count; $Index++) {
        $Token = $Arguments[$Index]
        $Option = $Token
        $InlineValue = $null
        $EqualsIndex = $Token.IndexOf("=", [StringComparison]::Ordinal)
        if ($EqualsIndex -gt 0) {
            $Option = $Token.Substring(0, $EqualsIndex)
            $InlineValue = $Token.Substring($EqualsIndex + 1)
        }

        if ($Option -cin @("-h", "--help")) {
            $ShowHelp = $true
            continue
        }

        $ValueOptions = @(
            "--model",
            "--reasoning-effort",
            "--issue-source",
            "--max-nmt-strikes",
            "--deny-tool",
            "--deny-skill",
            "--send-timeout-seconds"
        )
        if ($Option -cin $ValueOptions) {
            $Value = $InlineValue
            if ($null -eq $Value) {
                $Index++
                if ($Index -ge $Arguments.Count) {
                    throw (New-GitLoopyParseException "$Option requires a value")
                }
                $Value = $Arguments[$Index]
                if ($Value.StartsWith("-", [StringComparison]::Ordinal)) {
                    throw (New-GitLoopyParseException "$Option requires a value")
                }
            }
            if ([string]::IsNullOrWhiteSpace($Value)) {
                throw (New-GitLoopyParseException "$Option requires a value")
            }

            switch -CaseSensitive ($Option) {
                "--model" {
                    $Model = $Value
                    $ModelExplicit = $true
                }
                "--reasoning-effort" {
                    $ReasoningEffort = $Value
                    $EffortExplicit = $true
                }
                "--issue-source" { $IssueSource = $Value }
                "--max-nmt-strikes" { $MaxNmtStrikesText = $Value }
                "--deny-tool" { $CliTools.Add($Value) }
                "--deny-skill" { $CliSkills.Add($Value) }
                "--send-timeout-seconds" { $SendTimeoutText = $Value }
            }
            continue
        }

        if ($Token -ceq "--") {
            for ($Index++; $Index -lt $Arguments.Count; $Index++) {
                if ($PositionalSeen) {
                    throw (
                        New-GitLoopyParseException `
                            "only one iteration cap is accepted"
                    )
                }
                $MaxIterationsText = $Arguments[$Index]
                $PositionalSeen = $true
            }
            break
        }
        if ($Token.StartsWith("-", [StringComparison]::Ordinal)) {
            throw (New-GitLoopyParseException "unknown option: $Token")
        }
        if ($PositionalSeen) {
            throw (
                New-GitLoopyParseException "only one iteration cap is accepted"
            )
        }
        $MaxIterationsText = $Token
        $PositionalSeen = $true
    }

    $Model = $Model.Trim()
    if ($Model.Length -eq 0) {
        throw (New-GitLoopyParseException "model must not be empty")
    }
    $SuffixEffort = $null
    if ($Model -cmatch "^(.+)-(none|minimal|low|medium|high|xhigh|max)$") {
        $Model = $Matches[1]
        $SuffixEffort = $Matches[2]
    }
    if (-not $EffortExplicit) {
        if ($null -ne $SuffixEffort) {
            $ReasoningEffort = $SuffixEffort
        }
        elseif (-not $ModelExplicit) {
            $ReasoningEffort = "max"
        }
        else {
            $ReasoningEffort = $null
        }
    }
    if ($null -ne $ReasoningEffort) {
        $ReasoningEffort = $ReasoningEffort.ToLowerInvariant()
        if ($ReasoningEffort -cnotin @(
            "none", "minimal", "low", "medium", "high", "xhigh", "max"
        )) {
            throw (
                New-GitLoopyParseException `
                    "invalid reasoning effort: $ReasoningEffort"
            )
        }
    }
    $IssueSource = $IssueSource.ToLowerInvariant()
    if ($IssueSource -cnotin @("github", "prds")) {
        throw (
            New-GitLoopyParseException `
                "issue source must be github or prds"
        )
    }

    [int]$MaxIterations = 0
    if (
        -not [int]::TryParse(
            $MaxIterationsText,
            [Globalization.NumberStyles]::None,
            [Globalization.CultureInfo]::InvariantCulture,
            [ref]$MaxIterations
        ) -or
        $MaxIterations -lt 0
    ) {
        throw (
            New-GitLoopyParseException `
                "iteration cap must be a non-negative integer"
        )
    }

    [int]$MaxNmtStrikes = 0
    if (
        -not [int]::TryParse(
            $MaxNmtStrikesText,
            [Globalization.NumberStyles]::None,
            [Globalization.CultureInfo]::InvariantCulture,
            [ref]$MaxNmtStrikes
        ) -or
        $MaxNmtStrikes -lt 1
    ) {
        throw (
            New-GitLoopyParseException `
                "max NMT strikes must be a positive integer"
        )
    }

    [double]$SendTimeoutSeconds = 0
    if (
        -not [double]::TryParse(
            $SendTimeoutText,
            [Globalization.NumberStyles]::AllowDecimalPoint,
            [Globalization.CultureInfo]::InvariantCulture,
            [ref]$SendTimeoutSeconds
        ) -or
        -not [double]::IsFinite($SendTimeoutSeconds) -or
        $SendTimeoutSeconds -le 0
    ) {
        throw (
            New-GitLoopyParseException `
                "send timeout must be a positive number"
        )
    }

    $DenyTools = [Collections.Generic.List[string]]::new()
    $SeenTools = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Value in $CliTools) {
        Add-GitLoopyUniqueValue $DenyTools $SeenTools $Value
    }
    foreach ($Value in @($EnvironmentTools -split ",")) {
        Add-GitLoopyUniqueValue $DenyTools $SeenTools $Value
    }

    $DenySkills = [Collections.Generic.List[string]]::new()
    $SeenSkills = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Value in $CliSkills) {
        Add-GitLoopyUniqueValue $DenySkills $SeenSkills $Value
    }
    foreach ($Value in @($EnvironmentSkills -split ",")) {
        Add-GitLoopyUniqueValue $DenySkills $SeenSkills $Value
    }

    return [pscustomobject]@{
        PSTypeName = "GitLoopy.RunConfig"
        MaxIterations = $MaxIterations
        Model = $Model
        ReasoningEffort = $ReasoningEffort
        IssueSource = $IssueSource
        MaxNmtStrikes = $MaxNmtStrikes
        DenyTools = [string[]]$DenyTools.ToArray()
        DenySkills = [string[]]$DenySkills.ToArray()
        SendTimeoutSeconds = $SendTimeoutSeconds
        ShowHelp = $ShowHelp
    }
}

function Test-GitLoopyAfkReady {
    [CmdletBinding()]
    param(
        [AllowEmptyString()]
        [string]$Body
    )

    return (
        $Body -cmatch "(?m)^## What to build" -and
        $Body -cmatch "(?m)^## Acceptance criteria"
    )
}

function Get-GitLoopyExitCode {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Reason
    )

    switch -CaseSensitive ($Reason) {
        "empty_pool" { return 0 }
        "iteration_cap" { return 0 }
        "stuck" { return 1 }
        "preflight_failed" { return 1 }
        "usage_error" { return 2 }
        default { throw "Unknown Run exit reason: $Reason" }
    }
}

# GitHub closing-keyword regex — kept byte-identical to the Conformance suite's
# reference_regex and the Python reference so the whole Runner family shares one
# close-keyword oracle. .NET honours the embedded (?i) and matches \s (including
# \r and Unicode line separators) the same way Python's re does.
$script:GitLoopyCloseKeywordPattern =
    '(?i)(close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)'

function Get-GitLoopyCloseKeywordPattern {
    [CmdletBinding()]
    param()
    return $script:GitLoopyCloseKeywordPattern
}

function Get-GitLoopyCloseReferences {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [string]$Messages
    )

    $Refs = [Collections.Generic.List[int]]::new()
    $Seen = [Collections.Generic.HashSet[int]]::new()
    if ([string]::IsNullOrEmpty($Messages)) {
        return , $Refs.ToArray()
    }
    # Match line-by-line, splitting on LF only, so a newline is a hard boundary
    # while \r and Unicode line separators stay inline whitespace — matching the
    # Python reference `extract_close_refs`.
    foreach ($Line in $Messages.Split([char]10)) {
        foreach (
            $Match in [regex]::Matches($Line, $script:GitLoopyCloseKeywordPattern)
        ) {
            [int]$Number = 0
            if (
                [int]::TryParse($Match.Groups[2].Value, [ref]$Number) -and
                $Seen.Add($Number)
            ) {
                $Refs.Add($Number)
            }
        }
    }
    return , $Refs.ToArray()
}

function Get-GitLoopyActionableCloseReferences {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [string]$Messages,
        [AllowNull()]
        [object[]]$Pool
    )

    # First-seen close refs restricted to *issues* in the current Pool. Pull
    # requests and non-integer refs are excluded, preserving the Wrapper
    # contract's issues-only closure boundary.
    $IssueRefs = [Collections.Generic.HashSet[int]]::new()
    foreach ($Descriptor in @($Pool)) {
        if ($Descriptor -isnot [Collections.IDictionary]) {
            continue
        }
        if ([string]$Descriptor["kind"] -cne "issue") {
            continue
        }
        $RefValue = $Descriptor["ref"]
        if ($RefValue -isnot [int] -and $RefValue -isnot [long]) {
            continue
        }
        [void]$IssueRefs.Add([int]$RefValue)
    }
    $Actionable = [Collections.Generic.List[int]]::new()
    foreach ($Ref in (Get-GitLoopyCloseReferences -Messages $Messages)) {
        if ($IssueRefs.Contains($Ref)) {
            $Actionable.Add($Ref)
        }
    }
    return , $Actionable.ToArray()
}

function Test-GitLoopyIterationProgress {
    [CmdletBinding()]
    param(
        [int]$Commits,
        [int]$AutoClosures,
        [int]$Checkpoints,
        [int]$PrAdvances,
        [bool]$SawNmt
    )

    # Progress is true only for an agent commit, an auto-closure, or a PR head
    # advance. Runner Checkpoints and the legacy no-more-tasks sentinel are
    # informational and never progress.
    return ($Commits -gt 0) -or ($AutoClosures -gt 0) -or ($PrAdvances -gt 0)
}

function Step-GitLoopyStrikeState {
    [CmdletBinding()]
    param(
        [int]$MaxStrikes,
        [int]$Strikes,
        [string]$Outcome,
        [int]$Commits,
        [int]$AutoClosures,
        [int]$Checkpoints,
        [int]$PrAdvances,
        [bool]$SawNmt
    )

    # Advance the NMT Strike machine by one Iteration. Progress resets strikes;
    # a no-progress Iteration adds one and, on reaching the threshold, flips the
    # outcome to `aborted` and freezes there.
    if ($Outcome -ceq "aborted") {
        return [pscustomobject]@{ Strikes = $Strikes; Outcome = $Outcome }
    }
    $MadeProgress = Test-GitLoopyIterationProgress `
        -Commits $Commits `
        -AutoClosures $AutoClosures `
        -Checkpoints $Checkpoints `
        -PrAdvances $PrAdvances `
        -SawNmt $SawNmt
    if ($MadeProgress) {
        return [pscustomobject]@{ Strikes = 0; Outcome = $Outcome }
    }
    $Strikes += 1
    if ($Strikes -ge $MaxStrikes) {
        $Outcome = "aborted"
    }
    return [pscustomobject]@{ Strikes = $Strikes; Outcome = $Outcome }
}

# Runner Checkpoint message contract (ADR-0004), kept in lockstep with the
# Python reference `checkpoint_message` / `CHECKPOINT_TRAILER_KEY` and the shell
# port. The trailer key tags a runner-authored Checkpoint so it is
# distinguishable from an agent commit and excluded from Strike progress; its
# value is the active issue ref (or `unattributed`) — deliberately NOT `#N`, so a
# Checkpoint never opens a GitHub cross-reference. The body is byte-identical to
# the reference so the whole family authors the same close-keyword-free message.
$script:GitLoopyCheckpointTrailerKey = "GitLoopy-Checkpoint"
$script:GitLoopyCheckpointBody = (
    "Runner-authored Checkpoint (ADR-0004): staged the worktree the agent left",
    "uncommitted so the next iteration starts on a clean tree and the work can",
    "reach the remote. Not an agent commit; excluded from Strike progress."
) -join "`n"

function Test-GitLoopyCheckpointMessage {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [string]$Message
    )

    # Recognize the runner Checkpoint trailer (`GitLoopy-Checkpoint:`), tolerant
    # of surrounding whitespace and case, so a Checkpoint is excluded from Strike
    # progress even before this port authors one. Mirrors the Python reference.
    if ([string]::IsNullOrEmpty($Message)) {
        return $false
    }
    $Prefix = ($script:GitLoopyCheckpointTrailerKey + ":").ToLowerInvariant()
    foreach ($Line in [regex]::Split($Message, "\r\n|\r|\n")) {
        if ($Line.Trim().ToLowerInvariant().StartsWith($Prefix)) {
            return $true
        }
    }
    return $false
}

function Get-GitLoopyCheckpointMessage {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [string]$ActiveRef
    )

    # Build a runner Checkpoint commit message (ADR-0004) attributed to
    # $ActiveRef — an issue number, a PRDs/PR string ref, or empty for an
    # unattributed Checkpoint. The message is guaranteed close-keyword-free (its
    # subject/body never match the close-keyword pattern) and carries the
    # `GitLoopy-Checkpoint:` trailer, mirroring the Python reference and the shell
    # port byte-for-byte.
    if ([string]::IsNullOrEmpty($ActiveRef)) {
        $Subject = "Checkpoint: capture uncommitted work-in-progress"
        $Attribution = "unattributed"
    }
    elseif ($ActiveRef -match '^[0-9]+$') {
        $Subject = "Checkpoint: capture work-in-progress for issue $ActiveRef"
        $Attribution = $ActiveRef
    }
    else {
        $Subject = "Checkpoint: capture work-in-progress for $ActiveRef"
        $Attribution = $ActiveRef
    }
    $Trailer = "$($script:GitLoopyCheckpointTrailerKey): $Attribution"
    return "$Subject`n`n$($script:GitLoopyCheckpointBody)`n`n$Trailer"
}

function Resolve-GitLoopyPrompt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot,
        [Parameter(Mandatory)]
        [string]$PackagedPrompt,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Environment
    )

    $Candidates = [Collections.Generic.List[string]]::new()
    $Candidates.Add((Join-Path $RepoRoot "git-loopy/prompt.md"))
    $Candidates.Add((Join-Path $RepoRoot "git-loopy/PROMPT.md"))

    $Xdg = Get-GitLoopyEnvironmentValue $Environment "XDG_CONFIG_HOME"
    if (-not [string]::IsNullOrWhiteSpace($Xdg)) {
        $ConfigHome = $Xdg
    }
    else {
        $HomePath = Get-GitLoopyEnvironmentValue $Environment "HOME"
        if ([string]::IsNullOrWhiteSpace($HomePath)) {
            $HomePath = Get-GitLoopyEnvironmentValue `
                $Environment `
                "USERPROFILE"
        }
        if ([string]::IsNullOrWhiteSpace($HomePath)) {
            $HomePath = [Environment]::GetFolderPath(
                [Environment+SpecialFolder]::UserProfile
            )
        }
        if (-not [string]::IsNullOrWhiteSpace($HomePath)) {
            $ConfigHome = Join-Path $HomePath ".config"
        }
        else {
            $ConfigHome = $null
        }
    }
    if ($null -ne $ConfigHome) {
        $Candidates.Add((Join-Path $ConfigHome "git-loopy/PROMPT.md"))
    }
    $Candidates.Add($PackagedPrompt)

    foreach ($Candidate in $Candidates) {
        if ([IO.File]::Exists($Candidate)) {
            return [IO.Path]::GetFullPath($Candidate)
        }
    }
    return $null
}

function Test-GitLoopyCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    return $null -ne (
        Get-Command `
            -Name $Name `
            -CommandType Application, ExternalScript `
            -ErrorAction SilentlyContinue |
            Select-Object -First 1
    )
}

function Invoke-GitLoopyPreflight {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [string]$PackagedPrompt,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Environment
    )

    if (-not (Test-GitLoopyCommand "git")) {
        [Console]::Error.WriteLine("git-loopy: git is required on PATH.")
        return $null
    }
    $RepoOutput = @(& git rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or $RepoOutput.Count -eq 0) {
        [Console]::Error.WriteLine(
            "git-loopy: run from inside a git repository."
        )
        return $null
    }
    $RepoRoot = [IO.Path]::GetFullPath([string]$RepoOutput[-1])

    $TrackerPath = Join-Path $RepoRoot "docs/agents/issue-tracker.md"
    if (-not [IO.File]::Exists($TrackerPath)) {
        [Console]::Error.WriteLine(
            "git-loopy: issue tracking is not configured. " +
            "Run /setup-agent-skills interactively, then retry."
        )
        return $null
    }
    if (-not (Test-GitLoopyCommand "copilot")) {
        [Console]::Error.WriteLine(
            "git-loopy: copilot is required on PATH."
        )
        return $null
    }

    if ($Config.IssueSource -ceq "github") {
        if (-not (Test-GitLoopyCommand "gh")) {
            [Console]::Error.WriteLine(
                "git-loopy: gh is required for the GitHub issue source."
            )
            return $null
        }
        & gh auth status *> $null
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine(
                "git-loopy: gh is not authenticated. " +
                "Run 'gh auth login', then retry."
            )
            return $null
        }
        & gh repo view --json owner,name,defaultBranchRef *> $null
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine(
                "git-loopy: gh could not resolve this GitHub repository."
            )
            return $null
        }
    }

    $PromptPath = Resolve-GitLoopyPrompt `
        -RepoRoot $RepoRoot `
        -PackagedPrompt $PackagedPrompt `
        -Environment $Environment
    if ($null -eq $PromptPath) {
        [Console]::Error.WriteLine(
            "git-loopy: PROMPT.md was not found in project, global, " +
            "or packaged scope."
        )
        return $null
    }

    return [pscustomobject]@{
        PSTypeName = "GitLoopy.PreflightContext"
        RepoRoot = $RepoRoot
        PromptPath = $PromptPath
    }
}

function ConvertFrom-GitLoopyExternalJson {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [object[]]$Output,
        [Parameter(Mandatory)]
        [string]$Description
    )

    $Raw = [string]::Join([Environment]::NewLine, $Output)
    try {
        return $Raw | ConvertFrom-Json -AsHashtable -NoEnumerate
    }
    catch {
        [Console]::Error.WriteLine(
            "git-loopy: $Description returned malformed JSON."
        )
        return $null
    }
}

# `gh` emits comment timestamps as canonical UTC ISO-8601 strings
# (YYYY-MM-DDTHH:MM:SSZ). `ConvertFrom-Json` coerces those into [datetime]
# values, whose default string form is the host's locale ("03/01/2026 ..."),
# which would drift the assembled prompt away from the shell and Python ports
# (both keep the raw string). Re-render any coerced value back to the canonical
# UTC string so every port assembles byte-identical comment context.
function ConvertTo-GitLoopyCommentTimestamp {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [object]$Value
    )

    if ($null -eq $Value) {
        return ""
    }
    $Format = "yyyy-MM-ddTHH:mm:ssZ"
    $Invariant = [Globalization.CultureInfo]::InvariantCulture
    if ($Value -is [datetime]) {
        $Instant = [datetime]$Value
        if ($Instant.Kind -eq [DateTimeKind]::Unspecified) {
            $Instant = [datetime]::SpecifyKind($Instant, [DateTimeKind]::Utc)
        }
        return $Instant.ToUniversalTime().ToString($Format, $Invariant)
    }
    if ($Value -is [datetimeoffset]) {
        return ([datetimeoffset]$Value).ToUniversalTime().ToString(
            $Format, $Invariant
        )
    }
    return [string]$Value
}

function Get-GitLoopyGitHubPool {
    [CmdletBinding()]
    param()

    $ListOutput = @(
        & gh issue list `
            --state open `
            --label ready-for-agent `
            --limit 100 `
            --json number,title,body,labels,state,url 2>$null
    )
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            "git-loopy: gh issue list failed; treating this Pool as empty."
        )
        return
    }
    $Candidates = ConvertFrom-GitLoopyExternalJson `
        -Output $ListOutput `
        -Description "gh issue list"
    if ($null -eq $Candidates -or $Candidates -isnot [Collections.IList]) {
        if ($null -ne $Candidates) {
            [Console]::Error.WriteLine(
                "git-loopy: gh issue list did not return a JSON array."
            )
        }
        return
    }

    foreach ($Candidate in $Candidates) {
        $Body = if ($null -eq $Candidate["body"]) {
            ""
        }
        else {
            [string]$Candidate["body"]
        }
        if (-not (Test-GitLoopyAfkReady -Body $Body)) {
            continue
        }
        [int]$Number = 0
        if (-not [int]::TryParse([string]$Candidate["number"], [ref]$Number)) {
            [Console]::Error.WriteLine(
                "git-loopy: skipping issue with a malformed number."
            )
            continue
        }

        $ViewOutput = @(
            & gh issue view $Number `
                --json number,title,body,labels,state,url,comments 2>$null
        )
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine(
                "git-loopy: gh issue view #$Number failed; " +
                "skipping this Iteration."
            )
            continue
        }
        $Full = ConvertFrom-GitLoopyExternalJson `
            -Output $ViewOutput `
            -Description "gh issue view #$Number"
        if ($null -eq $Full -or $Full -isnot [Collections.IDictionary]) {
            continue
        }
        $FullBody = if ($null -eq $Full["body"]) {
            ""
        }
        else {
            [string]$Full["body"]
        }
        if (-not (Test-GitLoopyAfkReady -Body $FullBody)) {
            continue
        }

        $Labels = @(
            foreach ($Label in @($Full["labels"])) {
                if ($Label -is [Collections.IDictionary]) {
                    [string]$Label["name"]
                }
                else {
                    [string]$Label
                }
            }
        )
        $Comments = @(
            foreach ($Comment in @($Full["comments"])) {
                $Author = $Comment["author"]
                if ($Author -is [Collections.IDictionary]) {
                    $Author = $Author["login"]
                }
                [ordered]@{
                    author = [string]$Author
                    body = [string]$Comment["body"]
                    created_at = ConvertTo-GitLoopyCommentTimestamp -Value (
                        $Comment["createdAt"] ?? $Comment["created_at"]
                    )
                }
            }
        )
        [ordered]@{
            number = $Number
            title = [string]$Full["title"]
            body = $FullBody
            labels = [string[]]$Labels
            state = [string]$Full["state"]
            url = [string]$Full["url"]
            comments = [object[]]$Comments
        }
    }
}

function Get-GitLoopyPrdsPool {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot
    )

    $PrdsDir = Join-Path $RepoRoot "prds"
    if (-not [IO.Directory]::Exists($PrdsDir)) {
        return
    }
    $PrdsItem = Get-Item -LiteralPath $PrdsDir -Force
    if ($PrdsItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        [Console]::Error.WriteLine(
            "git-loopy: linked prds root is not allowed: $PrdsDir"
        )
        return
    }

    $Items = [Collections.Generic.SortedDictionary[string, object]]::new(
        [StringComparer]::Ordinal
    )
    [string[]]$FeatureNames = @(
        Get-ChildItem -LiteralPath $PrdsDir -Directory |
            Where-Object {
                $_.Name -cne "done" -and
                -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
            } |
            ForEach-Object { $_.Name }
    )
    [Array]::Sort($FeatureNames, [StringComparer]::Ordinal)
    foreach ($FeatureName in $FeatureNames) {
        $FeaturePath = Join-Path $PrdsDir $FeatureName
        [string[]]$FileNames = @(
            Get-ChildItem -LiteralPath $FeaturePath -File |
                Where-Object {
                    $_.Name -cmatch "^\d+-.*\.md$" -and
                    -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
                } |
                ForEach-Object { $_.Name }
        )
        [Array]::Sort($FileNames, [StringComparer]::Ordinal)
        foreach ($FileName in $FileNames) {
            $FilePath = Join-Path $FeaturePath $FileName
            try {
                $Body = [IO.File]::ReadAllText($FilePath)
            }
            catch {
                [Console]::Error.WriteLine(
                    "git-loopy: could not read $FilePath; skipping."
                )
                continue
            }
            if (-not (Test-GitLoopyAfkReady -Body $Body)) {
                continue
            }
            $Ref = [IO.Path]::GetRelativePath(
                $RepoRoot,
                $FilePath
            ).Replace("\", "/")
            $Items.Add($Ref, [ordered]@{
                ref = $Ref
                title = $Ref
                body = $Body
            })
        }
    }
    foreach ($Item in $Items.Values) {
        $Item
    }
}

function Get-GitLoopyPool {
    param(
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [string]$RepoRoot
    )

    if ($Config.IssueSource -ceq "github") {
        return @(Get-GitLoopyGitHubPool)
    }
    return @(Get-GitLoopyPrdsPool -RepoRoot $RepoRoot)
}

function ConvertFrom-GitLoopyLogOutput {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [string[]]$Lines
    )

    $Commits = [Collections.Generic.List[object]]::new()
    if ($null -eq $Lines -or $Lines.Count -eq 0) {
        return , $Commits.ToArray()
    }

    $RecordSeparator = [char]0x1e
    $UnitSeparator = [char]0x1f
    $Raw = ($Lines -join "`n")
    foreach ($Record in ($Raw -split ([regex]::Escape($RecordSeparator)))) {
        $Trimmed = $Record.TrimStart("`n", "`r")
        if ([string]::IsNullOrEmpty($Trimmed)) {
            continue
        }
        $Fields = $Trimmed -split ([regex]::Escape($UnitSeparator)), 4
        while ($Fields.Count -lt 4) {
            $Fields += ""
        }
        $Commits.Add([ordered]@{
            sha = $Fields[0]
            subject = $Fields[1]
            date = $Fields[2]
            body = $Fields[3].TrimEnd("`n", "`r")
        })
    }

    return , $Commits.ToArray()
}

function Get-GitLoopyHeadSha {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot
    )

    $Output = @(& git -C $RepoRoot rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or $Output.Count -eq 0) {
        return $null
    }
    return [string]$Output[-1]
}

function Get-GitLoopyCommitsInRange {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot,
        [Parameter(Mandatory)]
        [string]$Pre,
        [Parameter(Mandatory)]
        [string]$Head
    )

    if ($Pre -ceq $Head) {
        return @()
    }
    $Lines = @(
        & git -C $RepoRoot log `
            --format="%H%x1f%s%x1f%ad%x1f%b%x1e" --date=short "$Pre..$Head" 2>$null
    )
    return (ConvertFrom-GitLoopyLogOutput -Lines $Lines)
}

function Test-GitLoopyWorktreeDirty {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot
    )

    # Report whether the worktree carries any uncommitted tracked change OR any
    # untracked, non-ignored file — the ADR-0004 Checkpoint trigger. A single
    # `git status --porcelain` reports both (modified/staged tracked entries plus
    # `??` untracked ones) while honouring `.gitignore`, so it is the shell/Python
    # equivalent of `is_dirty` OR `has_untracked`. A git failure (e.g. not a
    # repository) reports "not dirty" so the caller skips the Checkpoint rather
    # than aborting.
    $Output = @(& git -C $RepoRoot status --porcelain 2>$null)
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    foreach ($Line in $Output) {
        if (-not [string]::IsNullOrEmpty($Line)) {
            return $true
        }
    }
    return $false
}

function Invoke-GitLoopyStageAll {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot
    )

    # Stage every change (`git add -A`, honouring `.gitignore`); the user's git
    # config stays the single source of truth (no `--force`, no excludes
    # override). Returns whether the staging succeeded.
    & git -C $RepoRoot add -A 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-GitLoopyCommit {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot,
        [Parameter(Mandatory)]
        [string]$Message
    )

    # Commit the staged index with $Message and return the new HEAD SHA. A plain
    # `git commit -m` keeps the user's identity/hooks/signing config
    # authoritative. An empty index (nothing staged) exits non-zero, which the
    # caller treats as a skipped Checkpoint rather than an abort ($null return).
    & git -C $RepoRoot commit -m $Message 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return Get-GitLoopyHeadSha -RepoRoot $RepoRoot
}

function Invoke-GitLoopyPush {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot
    )

    # Push the current branch to its configured upstream. A bare `git push` (no
    # ref args, no `--force`) keeps `push.default`, the branch's upstream tracking
    # ref, and credential helpers authoritative. Returns whether the push
    # succeeded; a missing upstream, an unreachable/missing remote, an auth
    # failure, or a non-fast-forward rejection all report failure without
    # throwing, and the caller treats every failure as non-fatal.
    & git -C $RepoRoot push 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Get-GitLoopyRecentCommitsBlock {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot
    )

    $Lines = @(
        & git -C $RepoRoot log `
            -n5 --format="%H%x1f%s%x1f%ad%x1f%b%x1e" --date=short 2>$null
    )
    $Commits = ConvertFrom-GitLoopyLogOutput -Lines $Lines
    if ($Commits.Count -eq 0) {
        return "No commits found"
    }
    $Parts = foreach ($Commit in $Commits) {
        if ([string]::IsNullOrEmpty([string]$Commit.body)) {
            $Message = [string]$Commit.subject
        }
        else {
            $Message = "$([string]$Commit.subject)`n$([string]$Commit.body)"
        }
        "$([string]$Commit.sha)`n$([string]$Commit.date)`n$Message---"
    }
    return ($Parts -join "`n")
}

function Format-GitLoopyPoolBlocks {
    [CmdletBinding()]
    param(
        [AllowEmptyCollection()]
        [object[]]$Pool
    )

    $Blocks = foreach ($Item in $Pool) {
        if ($Item.Contains("number")) {
            $Labels = (@($Item["labels"]) -join ", ")
            $Header = "=== Issue #$($Item["number"]): " +
                "$([string]$Item["title"]) [labels: $Labels] ==="
            $Body = [string]$Item["body"]
            $Recent = @(
                @($Item["comments"]) |
                    Sort-Object -Property { [string]$_["created_at"] } -Descending |
                    Select-Object -First 5
            )
            if ($Recent.Count -eq 0) {
                "$Header`n$Body"
            }
            else {
                $CommentLines = foreach ($Comment in $Recent) {
                    "[$([string]$Comment["created_at"]) " +
                        "@$([string]$Comment["author"])] $([string]$Comment["body"])"
                }
                "$Header`n$Body`n`n" +
                    "--- Recent comments (newest first, up to 5) ---`n" +
                    ($CommentLines -join "`n`n")
            }
        }
        else {
            "=== $([string]$Item["ref"]) ===`n$([string]$Item["body"])"
        }
    }
    return (@($Blocks) -join "`n`n")
}

function Build-GitLoopyPrompt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot,
        [AllowEmptyCollection()]
        [object[]]$Pool,
        [Parameter(Mandatory)]
        [string]$PromptPath
    )

    $CommitsBlock = Get-GitLoopyRecentCommitsBlock -RepoRoot $RepoRoot
    $IssuesBlock = Format-GitLoopyPoolBlocks -Pool $Pool
    $PromptText = [IO.File]::ReadAllText($PromptPath)
    return "Previous commits: $CommitsBlock Issues: $IssuesBlock $PromptText"
}

function Invoke-GitLoopyAgentTurn {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [string]$Prompt
    )

    $Argv = [Collections.Generic.List[string]]::new()
    $Argv.Add("--yolo")
    $Argv.Add("-p")
    $Argv.Add($Prompt)
    $Argv.Add("--model")
    $Argv.Add([string]$Config.Model)
    $Argv.Add("--no-color")
    if (-not [string]::IsNullOrEmpty([string]$Config.ReasoningEffort)) {
        $Argv.Add("--reasoning-effort")
        $Argv.Add([string]$Config.ReasoningEffort)
    }
    foreach ($Tool in @($Config.DenyTools)) {
        $Argv.Add("--deny-tool")
        $Argv.Add([string]$Tool)
    }
    foreach ($Skill in @($Config.DenySkills)) {
        $Argv.Add("--deny-tool")
        $Argv.Add("skill($([string]$Skill))")
    }

    # Stream the agent's own output to stderr so stdout stays the JSONL Event
    # stream; capture Copilot's real exit status, not a pipeline's (contract §4).
    try {
        & copilot @Argv |
            ForEach-Object { [Console]::Error.WriteLine([string]$_) }
        return $LASTEXITCODE
    }
    catch {
        [Console]::Error.WriteLine(
            "git-loopy: copilot turn could not launch: $($_.Exception.Message)"
        )
        return 126
    }
}

# The first Pool issue this Iteration actually closed (OPEN -> closed), in
# encounter order — the equivalent of the Python reference's `completions[0].ref`
# and the strongest Checkpoint-attribution signal, so `Get-GitLoopyActiveRef`
# consults it first. $null when nothing closed this Iteration.
$script:GitLoopyFirstClosedRef = $null

function Invoke-GitLoopyCloseOneIssue {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [int]$Iteration,
        [Parameter(Mandatory)]
        [int]$Issue,
        [AllowEmptyCollection()]
        [object[]]$Commits
    )

    # Re-verify one Pool issue is still OPEN and close it via `gh issue close`,
    # attributing every new commit that referenced it. Emits one
    # wrapper.auto_close on success. A gh failure or an already-CLOSED issue
    # warns/skips without aborting. Returns $true iff the issue was closed.
    $RefShas = [Collections.Generic.List[string]]::new()
    foreach ($Commit in @($Commits)) {
        $Body = [string]$Commit["body"]
        $Subject = [string]$Commit["subject"]
        $Message = if ([string]::IsNullOrEmpty($Body)) {
            $Subject
        }
        else {
            "$Subject`n$Body"
        }
        if ((Get-GitLoopyCloseReferences -Messages $Message) -contains $Issue) {
            $RefShas.Add([string]$Commit["sha"])
        }
    }
    if ($RefShas.Count -eq 0) {
        return $false
    }

    $ViewOutput = @(
        & gh issue view $Issue --json number,state,url 2>$null
    )
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            "git-loopy: gh issue view #$Issue during auto-close failed; " +
            "issue remains open."
        )
        return $false
    }
    $View = ConvertFrom-GitLoopyExternalJson `
        -Output $ViewOutput `
        -Description "gh issue view #$Issue"
    if (
        $null -eq $View -or
        $View -isnot [Collections.IDictionary] -or
        [string]$View["state"] -cne "OPEN"
    ) {
        return $false
    }

    $ShasText = $RefShas -join " "
    $Comment = @(
        "Implemented in $ShasText."
        ""
        "Closed by the git-loopy loop because the agent did not run " +
        "``gh issue close`` itself this iteration (commit messages did " +
        "reference ``Closes #$Issue``)."
        ""
        "If this closure looks wrong, reopen with ``gh issue reopen $Issue`` " +
        "— the loop will not re-close it without a new commit that " +
        "references it."
    ) -join "`n"
    & gh issue close $Issue --comment $Comment 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            "git-loopy: gh issue close #$Issue failed; issue remains open."
        )
        return $false
    }

    Write-GitLoopyEvent `
        -Context $Context `
        -Type $EventTypes["WRAPPER_AUTO_CLOSE"] `
        -Iteration $Iteration `
        -Payload ([ordered]@{
            issue = $Issue
            sha = $RefShas[0]
            shas = [string[]]$RefShas.ToArray()
        })
    if ($null -eq $script:GitLoopyFirstClosedRef) {
        $script:GitLoopyFirstClosedRef = $Issue
    }
    return $true
}

function Invoke-GitLoopyAutoClose {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [int]$Iteration,
        [AllowEmptyCollection()]
        [object[]]$Pool,
        [AllowEmptyCollection()]
        [object[]]$Commits
    )

    # Close finished Pool *issues* referenced by closing keywords in this
    # Iteration's new commits. Only the GitHub source auto-closes (the PRDs agent
    # owns its own archival). Repeated references collapse to at most one closure
    # via the first-encounter dedup. Returns the number of issues closed.
    $script:GitLoopyFirstClosedRef = $null
    if ($Config.IssueSource -cne "github") {
        return 0
    }

    $Descriptors = @(
        foreach ($Item in @($Pool)) {
            if ($Item -is [Collections.IDictionary] -and $Item.Contains("number")) {
                [ordered]@{ ref = [int]$Item["number"]; kind = "issue" }
            }
        }
    )
    $Concatenated = @(
        foreach ($Commit in @($Commits)) {
            $Body = [string]$Commit["body"]
            $Subject = [string]$Commit["subject"]
            if ([string]::IsNullOrEmpty($Body)) { $Subject } else { "$Subject`n$Body" }
        }
    ) -join "`n"

    $Closures = 0
    $Actionable = Get-GitLoopyActionableCloseReferences `
        -Messages $Concatenated `
        -Pool $Descriptors
    foreach ($Issue in $Actionable) {
        $Closed = Invoke-GitLoopyCloseOneIssue `
            -Context $Context `
            -EventTypes $EventTypes `
            -Iteration $Iteration `
            -Issue $Issue `
            -Commits $Commits
        if ($Closed) {
            $Closures += 1
        }
    }
    return $Closures
}

function Get-GitLoopyActiveRef {
    [CmdletBinding()]
    param(
        [AllowEmptyCollection()]
        [object[]]$Pool,
        [AllowEmptyCollection()]
        [object[]]$Commits
    )

    # Best-effort attribution of the Iteration's Active issue for a Checkpoint,
    # mirroring the Python reference `_infer_active_ref` and the shell port. In
    # priority order: the first Pool issue this Iteration actually auto-closed (the
    # strongest signal of what was worked, `completions[0].ref` in the reference);
    # then an actionable Pool-issue close-ref named in this Iteration's agent
    # commits (the agent named the issue it worked, even if the closure did not
    # fire); then a single-member Pool (the only candidate); else nothing
    # (unattributed). Returns the ref (an issue number or a PRDs/PR string) or
    # $null.
    if ($null -ne $script:GitLoopyFirstClosedRef) {
        return [string]$script:GitLoopyFirstClosedRef
    }
    $Descriptors = @(
        foreach ($Item in @($Pool)) {
            if ($Item -is [Collections.IDictionary] -and $Item.Contains("number")) {
                [ordered]@{ ref = [int]$Item["number"]; kind = "issue" }
            }
        }
    )
    $Concatenated = @(
        foreach ($Commit in @($Commits)) {
            $Body = [string]$Commit["body"]
            $Subject = [string]$Commit["subject"]
            if ([string]::IsNullOrEmpty($Body)) { $Subject } else { "$Subject`n$Body" }
        }
    ) -join "`n"
    $Actionable = Get-GitLoopyActionableCloseReferences `
        -Messages $Concatenated `
        -Pool $Descriptors
    if (@($Actionable).Count -gt 0) {
        return [string]@($Actionable)[0]
    }
    if (@($Pool).Count -eq 1) {
        $Only = @($Pool)[0]
        if ($Only.Contains("number")) {
            return [string]$Only["number"]
        }
        return [string]$Only["ref"]
    }
    return $null
}

function Invoke-GitLoopyMaybeCheckpoint {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [string]$RepoRoot,
        [int]$Iteration,
        [AllowEmptyCollection()]
        [object[]]$Pool,
        [AllowEmptyCollection()]
        [object[]]$Commits
    )

    # ADR-0004 durability net, first half. If the worktree carries any
    # uncommitted or untracked change, stage it all and capture it in exactly one
    # close-keyword-free Checkpoint attributed to the Active issue, then emit
    # `wrapper.checkpoint.recorded` ({issue, sha}). Runs AFTER the agent-commit
    # accounting and BEFORE the Strike decision, so the Checkpoint is structurally
    # excluded from both the commit tally (it is never a `wrapper.commit.recorded`)
    # and Strike progress. Returns the new SHA, or $null when the tree was clean
    # or the Checkpoint could not be made. Every failure warns and continues, so a
    # clean tree, a non-repo, and a local-only repo all complete normally.
    if (-not (Test-GitLoopyWorktreeDirty -RepoRoot $RepoRoot)) {
        return $null
    }
    $ActiveRef = Get-GitLoopyActiveRef -Pool $Pool -Commits $Commits
    $Message = Get-GitLoopyCheckpointMessage -ActiveRef $ActiveRef
    if (-not (Invoke-GitLoopyStageAll -RepoRoot $RepoRoot)) {
        [Console]::Error.WriteLine(
            "git-loopy: checkpoint staging failed; continuing without it."
        )
        return $null
    }
    $Sha = Invoke-GitLoopyCommit -RepoRoot $RepoRoot -Message $Message
    if ($null -eq $Sha) {
        [Console]::Error.WriteLine(
            "git-loopy: checkpoint commit failed; continuing without it."
        )
        return $null
    }
    $IssueValue = if ([string]::IsNullOrEmpty($ActiveRef)) {
        $null
    }
    elseif ($ActiveRef -match '^[0-9]+$') {
        [int]$ActiveRef
    }
    else {
        $ActiveRef
    }
    Write-GitLoopyEvent `
        -Context $Context `
        -Type $EventTypes["WRAPPER_CHECKPOINT_RECORDED"] `
        -Iteration $Iteration `
        -Payload ([ordered]@{
            issue = $IssueValue
            sha = $Sha
        })
    return $Sha
}

function Invoke-GitLoopyMaybePush {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [string]$RepoRoot,
        [int]$Iteration,
        [int]$NewCommitCount,
        [AllowNull()]
        [string]$CheckpointSha
    )

    # ADR-0004 durability net, second half. Whenever this Iteration produced any
    # new local commit — an agent commit and/or the Checkpoint just authored —
    # push the current branch to its configured upstream and emit
    # `wrapper.push.recorded` on success. A missing upstream, an
    # unreachable/missing remote, an auth failure, or a non-fast-forward rejection
    # warns but never aborts (a local-only repo completes normally) and — like a
    # failed Checkpoint — emits no event, so replay records only pushes that
    # actually landed. An Iteration with no new local commit skips the push.
    if ($NewCommitCount -eq 0 -and [string]::IsNullOrEmpty($CheckpointSha)) {
        return
    }
    if (-not (Invoke-GitLoopyPush -RepoRoot $RepoRoot)) {
        [Console]::Error.WriteLine(
            "git-loopy: auto-push failed; continuing (work stays local)."
        )
        return
    }
    Write-GitLoopyEvent `
        -Context $Context `
        -Type $EventTypes["WRAPPER_PUSH_RECORDED"] `
        -Iteration $Iteration
}

function Set-GitLoopyGitignoreEntry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot
    )

    # Idempotently keep `.git-loopy/` in the repo's `.gitignore` so the runner's
    # own replay/summary artefacts never trip the Checkpoint dirty-check or get
    # swept into a Checkpoint by `git add -A`. Mirrors the Python reference
    # `ensure_gitignore_entry` and the shell port: a no-op when `.gitignore` is
    # absent (downstream projects own their conventions — we never create it) or
    # already carries a `.git-loopy/` / `.git-loopy` line; otherwise appends one
    # line, adding a leading newline when the file does not already end in one.
    $Gitignore = Join-Path $RepoRoot ".gitignore"
    if (-not [IO.File]::Exists($Gitignore)) {
        return
    }
    $Content = [IO.File]::ReadAllText($Gitignore)
    foreach ($Line in [regex]::Split($Content, "\r\n|\r|\n")) {
        $Trimmed = $Line.Trim()
        if ($Trimmed -ceq ".git-loopy/" -or $Trimmed -ceq ".git-loopy") {
            return
        }
    }
    if ($Content.Length -gt 0 -and -not $Content.EndsWith("`n")) {
        [IO.File]::AppendAllText($Gitignore, "`n")
    }
    [IO.File]::AppendAllText($Gitignore, ".git-loopy/`n")
}

function Invoke-GitLoopyDiscovery {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [psobject]$Preflight
    )

    $Context = New-GitLoopyEventContext -RepoRoot $Preflight.RepoRoot
    $EventTypes = Get-GitLoopyEventTypes
    Set-GitLoopyGitignoreEntry -RepoRoot $Preflight.RepoRoot
    Write-GitLoopyEvent `
        -Context $Context `
        -Type $EventTypes["WRAPPER_RUN_START"] `
        -Payload ([ordered]@{
            deny_skills = [string[]]$Config.DenySkills
            deny_tools = [string[]]$Config.DenyTools
            issue_source = $Config.IssueSource
            max_iterations = $Config.MaxIterations
            max_nmt_strikes = $Config.MaxNmtStrikes
            model = $Config.Model
            prompt_path = $Preflight.PromptPath
            reasoning_effort = $Config.ReasoningEffort
            send_timeout_seconds = $Config.SendTimeoutSeconds
        })

    [int]$Iteration = 0
    [int]$IterationsRun = 0
    $Outcome = "iteration_cap"
    [int]$Strikes = 0
    $StrikeOutcome = "running"
    while ($true) {
        $NextIteration = $Iteration + 1
        if (
            $Config.MaxIterations -ne 0 -and
            $NextIteration -gt $Config.MaxIterations
        ) {
            $Outcome = "iteration_cap"
            break
        }
        $Iteration = $NextIteration

        Write-GitLoopyEvent `
            -Context $Context `
            -Type $EventTypes["WRAPPER_ITERATION_START"] `
            -Iteration $Iteration

        $Pool = @(Get-GitLoopyPool `
            -Config $Config `
            -RepoRoot $Preflight.RepoRoot)
        $Refs = @(
            foreach ($Item in $Pool) {
                if ($Item.Contains("number")) {
                    $Item["number"]
                }
                else {
                    $Item["ref"]
                }
            }
        )
        Write-GitLoopyEvent `
            -Context $Context `
            -Type $EventTypes["WRAPPER_AFK_READY_COLLECTED"] `
            -Iteration $Iteration `
            -Payload ([ordered]@{ issues = [object[]]$Refs })

        if ($Pool.Count -eq 0) {
            Write-GitLoopyEvent `
                -Context $Context `
                -Type $EventTypes["WRAPPER_ITERATION_END"] `
                -Iteration $Iteration
            $IterationsRun = $Iteration
            $Outcome = "empty_pool"
            break
        }

        # Assemble the same minimum context as the Python reference (last-5
        # commits + the AFK-ready Pool blocks + the resolved shared prompt) and
        # run exactly one streamed Copilot turn. The agent's own output goes to
        # stderr so stdout stays the JSONL Event stream; the turn's real exit
        # status is preserved and a non-zero turn warns without failing the Run.
        $Prompt = Build-GitLoopyPrompt `
            -RepoRoot $Preflight.RepoRoot `
            -Pool $Pool `
            -PromptPath $Preflight.PromptPath
        $PreSha = Get-GitLoopyHeadSha -RepoRoot $Preflight.RepoRoot
        $AgentStatus = Invoke-GitLoopyAgentTurn -Config $Config -Prompt $Prompt
        if ($AgentStatus -ne 0) {
            [Console]::Error.WriteLine(
                "git-loopy: copilot turn exited with status $AgentStatus; continuing."
            )
        }

        $NewCommits = @()
        if ($null -ne $PreSha) {
            $HeadSha = Get-GitLoopyHeadSha -RepoRoot $Preflight.RepoRoot
            if ($null -eq $HeadSha) {
                $HeadSha = $PreSha
            }
            $NewCommits = @(
                Get-GitLoopyCommitsInRange `
                    -RepoRoot $Preflight.RepoRoot `
                    -Pre $PreSha `
                    -Head $HeadSha
            )
        }

        # Split the boundary commits into agent commits and recognized runner
        # Checkpoints. Only agent commits are recorded as contract commit events
        # (newest-first) and count toward Strike progress; a Checkpoint is
        # excluded even before this port authors one.
        [int]$AgentCommits = 0
        [int]$CheckpointCommits = 0
        foreach ($Commit in $NewCommits) {
            $Body = [string]$Commit["body"]
            $Subject = [string]$Commit["subject"]
            $Message = if ([string]::IsNullOrEmpty($Body)) {
                $Subject
            }
            else {
                "$Subject`n$Body"
            }
            if (Test-GitLoopyCheckpointMessage -Message $Message) {
                $CheckpointCommits += 1
                continue
            }
            $AgentCommits += 1
            Write-GitLoopyEvent `
                -Context $Context `
                -Type $EventTypes["WRAPPER_COMMIT_RECORDED"] `
                -Iteration $Iteration `
                -Payload ([ordered]@{
                    date = [string]$Commit["date"]
                    sha = [string]$Commit["sha"]
                    subject = [string]$Commit["subject"]
                })
        }

        # Auto-close finished Pool issues from the new commit messages, then
        # decide progress and advance the Strike machine. Progress (an agent
        # commit or a wrapper closure) resets the Strike count; consecutive
        # no-progress Iterations accumulate Strikes and the threshold ends the
        # Run as stuck.
        $AutoClosures = Invoke-GitLoopyAutoClose `
            -Context $Context `
            -EventTypes $EventTypes `
            -Config $Config `
            -Iteration $Iteration `
            -Pool $Pool `
            -Commits $NewCommits

        # Runner Checkpoint + auto-push (ADR-0004). Capture any dirty / untracked
        # work-in-progress in one close-keyword-free Checkpoint attributed to the
        # Active issue, then push the branch whenever this Iteration produced any
        # new local commit (an agent commit and/or the Checkpoint just made). Both
        # run AFTER the agent-commit accounting and BEFORE the Strike decision, so
        # the Checkpoint is excluded from the commit tally and Strike progress;
        # both are non-fatal so a local-only repo still completes.
        $CheckpointSha = Invoke-GitLoopyMaybeCheckpoint `
            -Context $Context `
            -EventTypes $EventTypes `
            -RepoRoot $Preflight.RepoRoot `
            -Iteration $Iteration `
            -Pool $Pool `
            -Commits $NewCommits
        Invoke-GitLoopyMaybePush `
            -Context $Context `
            -EventTypes $EventTypes `
            -RepoRoot $Preflight.RepoRoot `
            -Iteration $Iteration `
            -NewCommitCount $NewCommits.Count `
            -CheckpointSha $CheckpointSha

        $Progress = Test-GitLoopyIterationProgress `
            -Commits $AgentCommits `
            -AutoClosures $AutoClosures `
            -Checkpoints $CheckpointCommits `
            -PrAdvances 0 `
            -SawNmt $false
        $StrikeState = Step-GitLoopyStrikeState `
            -MaxStrikes $Config.MaxNmtStrikes `
            -Strikes $Strikes `
            -Outcome $StrikeOutcome `
            -Commits $AgentCommits `
            -AutoClosures $AutoClosures `
            -Checkpoints $CheckpointCommits `
            -PrAdvances 0 `
            -SawNmt $false
        $Strikes = $StrikeState.Strikes
        $StrikeOutcome = $StrikeState.Outcome
        if ($StrikeOutcome -ceq "aborted" -or -not $Progress) {
            $StrikeEventOutcome = if ($StrikeOutcome -ceq "aborted") {
                "abort"
            }
            else {
                "warn"
            }
            Write-GitLoopyEvent `
                -Context $Context `
                -Type $EventTypes["WRAPPER_STRIKE"] `
                -Iteration $Iteration `
                -Payload ([ordered]@{
                    max_strikes = $Config.MaxNmtStrikes
                    outcome = $StrikeEventOutcome
                    strikes = $Strikes
                })
        }

        Write-GitLoopyEvent `
            -Context $Context `
            -Type $EventTypes["WRAPPER_ITERATION_END"] `
            -Iteration $Iteration
        $IterationsRun = $Iteration
        if ($StrikeOutcome -ceq "aborted") {
            $Outcome = "stuck"
            break
        }
    }

    Write-GitLoopyEvent `
        -Context $Context `
        -Type $EventTypes["WRAPPER_RUN_END"] `
        -Payload ([ordered]@{
            iterations_run = $IterationsRun
            outcome = $Outcome
        })

    if ($Outcome -ceq "empty_pool") {
        return Get-GitLoopyExitCode -Reason "empty_pool"
    }
    if ($Outcome -ceq "stuck") {
        return Get-GitLoopyExitCode -Reason "stuck"
    }
    return Get-GitLoopyExitCode -Reason "iteration_cap"
}

function Get-GitLoopyUsage {
    [CmdletBinding()]
    param()

    return @"
Usage: git-loopy.ps1 [<max-iterations>] [options]

Options:
  --model ID
  --reasoning-effort none|minimal|low|medium|high|xhigh|max
  --issue-source github|prds
  --max-nmt-strikes N
  --deny-tool TOOL              Repeatable; unioned with GIT_LOOPY_DENY_TOOLS.
  --deny-skill SKILL            Repeatable; unioned with GIT_LOOPY_DENY_SKILLS.
  --send-timeout-seconds N
  -h, --help
"@
}

function Invoke-GitLoopyMain {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$Arguments,
        [Parameter(Mandatory)]
        [string]$PackagedPrompt,
        [Collections.IDictionary]$Environment = (Get-GitLoopyEnvironment)
    )

    $Config = Resolve-GitLoopyConfig `
        -Arguments $Arguments `
        -Environment $Environment
    if ($Config.ShowHelp) {
        [Console]::Out.WriteLine((Get-GitLoopyUsage))
        return 0
    }
    $Preflight = Invoke-GitLoopyPreflight `
        -Config $Config `
        -PackagedPrompt $PackagedPrompt `
        -Environment $Environment
    if ($null -eq $Preflight) {
        return Get-GitLoopyExitCode -Reason "preflight_failed"
    }
    return Invoke-GitLoopyDiscovery `
        -Config $Config `
        -Preflight $Preflight
}

Export-ModuleMember -Function @(
    "Get-GitLoopyEnvironment",
    "Resolve-GitLoopyConfig",
    "Test-GitLoopyAfkReady",
    "Get-GitLoopyExitCode",
    "Get-GitLoopyCloseKeywordPattern",
    "Get-GitLoopyCloseReferences",
    "Get-GitLoopyActionableCloseReferences",
    "Test-GitLoopyIterationProgress",
    "Step-GitLoopyStrikeState",
    "Test-GitLoopyCheckpointMessage",
    "Get-GitLoopyCheckpointMessage",
    "Test-GitLoopyWorktreeDirty",
    "Get-GitLoopyActiveRef",
    "Resolve-GitLoopyPrompt",
    "Invoke-GitLoopyPreflight",
    "Get-GitLoopyGitHubPool",
    "Get-GitLoopyPrdsPool",
    "Get-GitLoopyPool",
    "Invoke-GitLoopyDiscovery",
    "Get-GitLoopyUsage",
    "Invoke-GitLoopyMain"
)
