Set-StrictMode -Version Latest

$EventsModule = Join-Path $PSScriptRoot "GitLoopy.Events.psm1"
Import-Module $EventsModule -Force
$TuiModule = Join-Path $PSScriptRoot "GitLoopy.Tui.psm1"
Import-Module $TuiModule -Force
$Script:ReleaseVersionPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "../../VERSION")
)

function Get-GitLoopyReleaseVersion {
    [CmdletBinding()]
    param(
        [string]$Path = $Script:ReleaseVersionPath
    )

    try {
        $Content = [Text.UTF8Encoding]::new($false, $true).GetString(
            [IO.File]::ReadAllBytes($Path)
        )
    }
    catch {
        throw [IO.InvalidDataException]::new(
            "cannot read Release version authority ${Path}: $($_.Exception.Message)",
            $_.Exception
        )
    }

    $Value = if ($Content.EndsWith("`r`n", [StringComparison]::Ordinal)) {
        $Content.Substring(0, $Content.Length - 2)
    }
    elseif ($Content.EndsWith("`n", [StringComparison]::Ordinal)) {
        $Content.Substring(0, $Content.Length - 1)
    }
    else {
        $Content
    }
    $Identifier = "(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    $Pattern = (
        "\A(?:0|[1-9][0-9]*)\." +
        "(?:0|[1-9][0-9]*)\." +
        "(?:0|[1-9][0-9]*)" +
        "(?:-$Identifier(?:\.$Identifier)*)?" +
        "(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\z"
    )
    if (-not [regex]::IsMatch(
        $Value,
        $Pattern,
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
    )) {
        throw [IO.InvalidDataException]::new(
            "Release version authority $Path must contain exactly one " +
                "Semantic Versioning value"
        )
    }
    return $Value
}

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

function Get-GitLoopyMonotonicSeconds {
    [CmdletBinding()]
    param()

    return (
        [Diagnostics.Stopwatch]::GetTimestamp() /
        [double][Diagnostics.Stopwatch]::Frequency
    )
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

# The config home (`<config-home>/git-loopy` is the global scope) resolved the
# same way every scope-aware lookup resolves it: $XDG_CONFIG_HOME, else the home
# directory's `.config`. Returns $null when no home is resolvable.
function Get-GitLoopyConfigHome {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Environment
    )

    $Xdg = Get-GitLoopyEnvironmentValue $Environment "XDG_CONFIG_HOME"
    if (-not [string]::IsNullOrWhiteSpace($Xdg)) {
        return $Xdg
    }

    $HomePath = Get-GitLoopyEnvironmentValue $Environment "HOME"
    if ([string]::IsNullOrWhiteSpace($HomePath)) {
        $HomePath = Get-GitLoopyEnvironmentValue $Environment "USERPROFILE"
    }
    if ([string]::IsNullOrWhiteSpace($HomePath)) {
        $HomePath = [Environment]::GetFolderPath(
            [Environment+SpecialFolder]::UserProfile
        )
    }
    if ([string]::IsNullOrWhiteSpace($HomePath)) {
        return $null
    }
    return (Join-Path $HomePath ".config")
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
    # Tri-state interactive request: "on", "off", or empty for "no flag given".
    $InteractiveFlag = ""
    $CliTools = [Collections.Generic.List[string]]::new()
    $CliSkills = [Collections.Generic.List[string]]::new()
    $SkillPolicyFlagsSeen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )

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

        if ($Token -ceq "--interactive") {
            $InteractiveFlag = "on"
            continue
        }
        if ($Token -ceq "--no-interactive") {
            $InteractiveFlag = "off"
            continue
        }

        $ValueOptions = @(
            "--model",
            "--reasoning-effort",
            "--issue-source",
            "--max-nmt-strikes",
            "--deny-tool",
            "--deny-skill",
            "--enable-skill",
            "--disable-skill",
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
                # Recognised as a closed-world Skill-policy overlay, never as an
                # unknown option and never as a legacy denial: the value is
                # consumed and discarded so preflight can fail closed on the
                # surface (contract §17.6) instead of the parser reporting a
                # usage error or the overlay being silently applied.
                "--enable-skill" {
                    [void]$SkillPolicyFlagsSeen.Add($Option)
                }
                "--disable-skill" {
                    [void]$SkillPolicyFlagsSeen.Add($Option)
                }
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
        SkillPolicyFlags = [string[]]@(
            $SkillPolicyFlagsSeen | Sort-Object -CaseSensitive
        )
        SendTimeoutSeconds = $SendTimeoutSeconds
        InteractiveFlag = $InteractiveFlag
        ShowHelp = $ShowHelp
    }
}

# Decode a TOML *quoted* key's escapes into the characters `tomllib` would
# resolve them to.
#
# `tomllib` reads `"enabled\u005fskills"` as `enabled_skills`, so a Config the
# Python Orchestrator honours would slip through this port's detector unnoticed
# if the raw spelling were compared. Unlike the shell port — which cannot use
# `printf %b` on the Bash 4.0/4.1 it supports and therefore materialises only the
# ASCII range — .NET can resolve any scalar, so this decodes the full range
# rather than dropping characters it cannot build. An escape it cannot resolve
# decodes to nothing, which can only ever *widen* the abort.
function ConvertFrom-GitLoopyTomlKey {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$Raw
    )

    $Builder = [Text.StringBuilder]::new()
    $Index = 0
    while ($Index -lt $Raw.Length) {
        $Char = $Raw[$Index]
        if ($Char -cne [char]0x5C -or $Index + 1 -ge $Raw.Length) {
            [void]$Builder.Append($Char)
            $Index++
            continue
        }

        $Width = switch -CaseSensitive ([string]$Raw[$Index + 1]) {
            "u" { 4 }
            "U" { 8 }
            default { 0 }
        }
        if ($Width -eq 0 -or $Index + 2 + $Width -gt $Raw.Length) {
            # Every other escape stands for one literal character, and a
            # truncated one has no scalar to resolve.
            [void]$Builder.Append($Raw[$Index + 1])
            $Index += 2
            continue
        }

        $Hex = $Raw.Substring($Index + 2, $Width)
        $Index += 2 + $Width
        $Scalar = 0
        if (
            -not [int]::TryParse(
                $Hex,
                [Globalization.NumberStyles]::HexNumber,
                [Globalization.CultureInfo]::InvariantCulture,
                [ref]$Scalar
            )
        ) {
            continue
        }
        if (
            $Scalar -lt 0 -or
            $Scalar -gt 0x10FFFF -or
            ($Scalar -ge 0xD800 -and $Scalar -le 0xDFFF)
        ) {
            continue
        }
        [void]$Builder.Append([char]::ConvertFromUtf32($Scalar))
    }
    return $Builder.ToString()
}

# Conservative detection of an `enabled_skills` key in one `config.toml`.
#
# The PowerShell port has no TOML parser, and this decision only ever *widens
# the abort*: a false positive costs an operator one diagnostic, while a false
# negative runs an Iteration on a wider capability set than they configured
# (contract §17.6). So any assignment of the key counts, including one nested
# under a table that the Python resolver would ignore. Anchoring the match to
# the start of the trimmed line is what keeps a commented example — including
# the comment-only banner `write_config` generates — from reading as a policy.
#
# A quoted key is decoded before comparison, so a Config the Python Orchestrator
# honours cannot run wide here on spelling alone.
function Test-GitLoopyConfigDeclaresEnabledSkills {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if (-not [IO.File]::Exists($Path)) {
        return $false
    }
    try {
        $Lines = [IO.File]::ReadAllLines($Path)
    }
    catch [IO.IOException] {
        return $false
    }
    catch [UnauthorizedAccessException] {
        return $false
    }

    foreach ($Line in $Lines) {
        $Trimmed = $Line.Trim()
        if ($Trimmed -cmatch '^enabled_skills\s*=') {
            return $true
        }
        $Quoted = [regex]::Match(
            $Trimmed,
            '^(?:"(?<basic>[^"]*)"|''(?<literal>[^'']*)'')\s*='
        )
        if (-not $Quoted.Success) {
            continue
        }
        # Decoding a literal (single-quoted) key too is harmless: TOML gives it
        # no escapes, and the only possible effect is widening the abort.
        $Raw = $Quoted.Groups["literal"].Value
        if ($Quoted.Groups["basic"].Success) {
            $Raw = $Quoted.Groups["basic"].Value
        }
        if ((ConvertFrom-GitLoopyTomlKey -Raw $Raw) -ceq "enabled_skills") {
            return $true
        }
    }
    return $false
}

# Every closed-world Skill-policy surface this Run carries that the PowerShell
# port cannot honour, in the canonical order of the family fixture's
# `native_transition.policy_surfaces`. An empty result means nothing unsupported
# was configured; legacy deny-only inputs are never a surface (contract §17.2).
function Get-GitLoopySkillPolicySurfaces {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Environment,
        [AllowNull()]
        [string]$RepoRoot
    )

    $Surfaces = [Collections.Generic.List[string]]::new()

    # Presence, not content: an explicit empty replacement is a real policy.
    if ($Environment.Contains("GIT_LOOPY_ENABLED_SKILLS")) {
        $Surfaces.Add("GIT_LOOPY_ENABLED_SKILLS")
    }

    # Canonical order, not command-line order, so two Runs that configure the
    # same overlays always produce the same diagnostic.
    foreach ($Flag in @("--enable-skill", "--disable-skill")) {
        if ($Flag -cin @($Config.SkillPolicyFlags)) {
            $Surfaces.Add($Flag)
        }
    }

    $ConfigPaths = [Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($RepoRoot)) {
        $ConfigPaths.Add((Join-Path $RepoRoot "git-loopy/config.toml"))
    }
    $ConfigHome = Get-GitLoopyConfigHome -Environment $Environment
    if (-not [string]::IsNullOrWhiteSpace($ConfigHome)) {
        $ConfigPaths.Add((Join-Path $ConfigHome "git-loopy/config.toml"))
    }
    foreach ($Path in $ConfigPaths) {
        if (Test-GitLoopyConfigDeclaresEnabledSkills -Path $Path) {
            $Surfaces.Add("enabled_skills")
            break
        }
    }

    return [string[]]$Surfaces.ToArray()
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

function Get-GitLoopyIterationRollup {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$IterationStartedMonotonic,
        [Parameter(Mandatory)]
        [object]$FinishedMonotonic,
        [AllowNull()]
        [object]$ActiveIssue,
        [AllowNull()]
        [string]$ActiveStartedAt,
        [object]$ActiveStartedMonotonic = 0,
        [AllowNull()]
        [string]$FirstStartedAt,
        [object]$FirstStartedMonotonic = 0,
        [object]$PreviousCumulativeActiveSeconds = 0,
        [AllowNull()]
        [string]$ActiveClosedAt,
        [AllowNull()]
        [object]$ActiveClosedMonotonic,
        [int]$Commits = 0,
        [int]$AutoClosures = 0,
        [int]$PrAdvances = 0,
        [int]$Strikes = 0,
        [string]$TerminalOutcome
    )

    $Duration = $FinishedMonotonic - $IterationStartedMonotonic
    if ($Duration -lt 0) {
        $Duration = 0
    }

    $Outcome = "no_progress"
    $Issues = [object[]]@()
    if ($null -ne $ActiveIssue) {
        $EndedMonotonic = $FinishedMonotonic
        $Status = "no-progress"
        if (-not [string]::IsNullOrEmpty($ActiveClosedAt)) {
            $EndedMonotonic = $ActiveClosedMonotonic
            $Status = "closed"
        }
        elseif ($TerminalOutcome -cin @("aborted", "gone")) {
            $Status = $TerminalOutcome
        }
        elseif ($Commits -gt 0 -or $PrAdvances -gt 0) {
            $Status = "advanced"
        }

        $ActiveSeconds = $EndedMonotonic - $ActiveStartedMonotonic
        if ($ActiveSeconds -lt 0) {
            $ActiveSeconds = 0
        }
        $CumulativeActiveSeconds = (
            $PreviousCumulativeActiveSeconds + $ActiveSeconds
        )
        $ClosedAt = $null
        $IssueElapsedSeconds = $null
        if (-not [string]::IsNullOrEmpty($ActiveClosedAt)) {
            $ClosedAt = $ActiveClosedAt
            $IssueElapsedSeconds = (
                $ActiveClosedMonotonic - $FirstStartedMonotonic
            )
            if ($IssueElapsedSeconds -lt 0) {
                $IssueElapsedSeconds = 0
            }
        }
        $Issues = [object[]]@(
            [ordered]@{
                issue = $ActiveIssue
                status = $Status
                first_started_at = $FirstStartedAt
                closed_at = $ClosedAt
                issue_elapsed_seconds = $IssueElapsedSeconds
                active_seconds = $ActiveSeconds
                cumulative_active_seconds = $CumulativeActiveSeconds
                consumption = [ordered]@{
                    model = $null
                    tokens_in = $null
                    tokens_out = $null
                }
                cost_usd = $null
                peak_context_window = $null
            }
        )
        $Outcome = if ($Status -ceq "no-progress") {
            "no_progress"
        }
        else {
            $Status
        }
    }

    return [ordered]@{
        outcome = $Outcome
        duration_seconds = $Duration
        summary = [ordered]@{
            model = $null
            tokens_in = $null
            tokens_out = $null
            observed_tokens = $null
            cost_usd = $null
            tool_count = $null
            skill_call_count = $null
            skills_consulted = $null
            commits = $Commits
            auto_closures = $AutoClosures
            pr_advances = $PrAdvances
            strikes = $Strikes
            peak_context_window = $null
        }
        issues = $Issues
    }
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

    $ConfigHome = Get-GitLoopyConfigHome -Environment $Environment
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

# Fail closed on any closed-world Skill-policy surface this port cannot honour
# (contract §17.6). Runs before the issue-tracker, dependency, and GitHub checks
# — and therefore before source collection and before Copilot exists — so a
# configured policy can never be silently widened into an Iteration.
function Assert-GitLoopySkillPolicySupported {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Environment,
        [AllowNull()]
        [string]$RepoRoot
    )

    $Surfaces = @(
        Get-GitLoopySkillPolicySurfaces `
            -Config $Config `
            -Environment $Environment `
            -RepoRoot $RepoRoot
    )
    if ($Surfaces.Count -eq 0) {
        return $true
    }

    [Console]::Error.WriteLine(
        "git-loopy: the PowerShell Orchestrator does not yet support the " +
        "closed-world Skill policy."
    )
    foreach ($Surface in $Surfaces) {
        [Console]::Error.WriteLine(
            "git-loopy: unsupported Skill-policy surface: $Surface"
        )
    }
    [Console]::Error.WriteLine(
        "git-loopy: run the Python Orchestrator, or wait for a PowerShell " +
        "release with native config parity."
    )
    [Console]::Error.WriteLine(
        "git-loopy: legacy --deny-skill / GIT_LOOPY_DENY_SKILLS invocations " +
        "continue to run unchanged."
    )
    return $false
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

    if (
        -not (
            Assert-GitLoopySkillPolicySupported `
                -Config $Config `
                -Environment $Environment `
                -RepoRoot $RepoRoot
        )
    ) {
        return $null
    }

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
        # `gh issue list --state open` is a snapshot; this per-issue view is the
        # live read and therefore the authoritative source state. An issue the
        # source already reports CLOSED is finished work, not AFK-ready work:
        # record the source closure as lifecycle evidence and keep the issue out
        # of the Pool so the agent is never handed an already-closed issue and
        # the closing-keyword backstop never issues a duplicate `gh issue close`.
        if ([string]$Full["state"] -ceq "CLOSED") {
            $script:GitLoopySourceClosedRefs.Add([string]$Number)
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

function Test-GitLoopyPoolContainsRef {
    param(
        [AllowEmptyCollection()]
        [object[]]$Pool,
        [Parameter(Mandatory)]
        [string]$Ref
    )

    foreach ($Item in @($Pool)) {
        $Candidate = if ($Item.Contains("number")) {
            [string]$Item["number"]
        }
        else {
            [string]$Item["ref"]
        }
        if ($Candidate -ceq $Ref) {
            return $true
        }
    }
    return $false
}

$script:GitLoopyActiveRef = $null
$script:GitLoopyIterationStartedAt = $null
$script:GitLoopyIterationStartedMonotonic = 0
$script:GitLoopyActiveStartedAt = $null
$script:GitLoopyActiveStartedMonotonic = 0
$script:GitLoopyActiveClosedAt = $null
$script:GitLoopyActiveClosedMonotonic = $null
$script:GitLoopyIssueFirstStartedAt = @{}
$script:GitLoopyIssueFirstStartedMonotonic = @{}
$script:GitLoopyIssueCumulativeActiveSeconds = @{}
$script:GitLoopyWarnedMarkerRefs = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::Ordinal
)
# Refs this Iteration observed as already closed at the source (first-encounter
# order). An agent may perform the required source closure itself, so a CLOSED
# source state is authoritative lifecycle evidence the Orchestrator did not
# produce — never a wrapper auto-closure.
$script:GitLoopySourceClosedRefs = [Collections.Generic.List[string]]::new()

function Reset-GitLoopyIterationLifecycleState {
    [CmdletBinding()]
    param()

    # Begin a fresh Run's lifecycle accounting. Per-issue first activation and
    # cumulative Active time deliberately outlive a single Iteration, so they are
    # Run-scoped and only a new Run may clear them.
    $script:GitLoopyActiveRef = $null
    $script:GitLoopyIterationStartedAt = $null
    $script:GitLoopyIterationStartedMonotonic = 0
    $script:GitLoopyActiveStartedAt = $null
    $script:GitLoopyActiveStartedMonotonic = 0
    $script:GitLoopyActiveClosedAt = $null
    $script:GitLoopyActiveClosedMonotonic = $null
    $script:GitLoopyIssueFirstStartedAt = @{}
    $script:GitLoopyIssueFirstStartedMonotonic = @{}
    $script:GitLoopyIssueCumulativeActiveSeconds = @{}
    $script:GitLoopyWarnedMarkerRefs.Clear()
    $script:GitLoopySourceClosedRefs.Clear()
}

function ConvertTo-GitLoopyLifecycleInstant {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [object]$Value
    )

    # Normalize one inbound lifecycle timestamp to the contract's RFC3339 UTC
    # form. The accumulator turns an Event's activation instant into a *durable
    # string fact* that every later Iteration end republishes, so this is the
    # seam that owns the format guarantee -- not each producer.
    #
    # A normalized lifecycle Event may legitimately carry that instant as a
    # decoded date value rather than a string; a JSON reader hands back
    # `DateTime`, an Orchestrator hands back `DateTimeOffset`, and the native Run
    # loop hands back an already-formatted string. Casting a date value straight
    # to string yields .NET's general pattern (`05/16/2026 00:00:10`), which is
    # not RFC3339 in any culture, so the cast has to be a deliberate conversion.
    #
    # Returns `$null` for anything that is not a resolvable instant. An
    # activation without one is not a valid activation: binding it would publish
    # a contract-violating timestamp, so the caller leaves the Iteration unbound
    # exactly as it does for a missing one, and the Run continues.
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [DateTimeOffset]) {
        return Get-GitLoopyIsoTimestamp -Timestamp $Value
    }
    if ($Value -is [DateTime]) {
        # An unspecified Kind comes from a reader that dropped the zone rather
        # than from a producer asserting local time. Every lifecycle timestamp
        # the contract defines is UTC, so read it as the UTC instant it was.
        $Instant = if ($Value.Kind -eq [DateTimeKind]::Unspecified) {
            [DateTimeOffset]::new(
                [DateTime]::SpecifyKind($Value, [DateTimeKind]::Utc)
            )
        }
        else {
            [DateTimeOffset]::new($Value)
        }
        return Get-GitLoopyIsoTimestamp -Timestamp $Instant
    }

    $Text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }
    $Parsed = [DateTimeOffset]::MinValue
    $Styles = (
        [Globalization.DateTimeStyles]::AdjustToUniversal -bor
        [Globalization.DateTimeStyles]::AssumeUniversal
    )
    # Parse invariantly and explicitly. PowerShell's implicit string-to-date
    # coercion would resolve against the ambient culture's calendar, which turns
    # a durable UTC fact into a Hijri or Buddhist year on a perfectly ordinary
    # operator machine.
    if (
        -not [DateTimeOffset]::TryParse(
            $Text,
            [Globalization.CultureInfo]::InvariantCulture,
            $Styles,
            [ref]$Parsed
        )
    ) {
        return $null
    }
    return Get-GitLoopyIsoTimestamp -Timestamp $Parsed
}

function Update-GitLoopyIterationLifecycle {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$LifecycleEvent,
        [Parameter(Mandatory)]
        [object]$ObservedMonotonic
    )

    # Fold one normalized lifecycle Event into the Iteration rollup accumulator.
    # This is the whole Orchestrator-owned seam for Iteration and issue *timing*:
    # `wrapper.iteration.start` opens a fresh Iteration and `wrapper.issue.activated`
    # binds its Active issue exactly once. Per-issue facts that outlive one
    # Iteration — first activation and cumulative Active time — deliberately
    # survive the reset so a repeated issue accrues only the time it was actually
    # Active and never the inactive gap between Iterations.
    #
    # Durations come from the caller's monotonic reading, never from the UTC
    # timestamps: those are durable lifecycle facts that a wall-clock adjustment
    # may move backwards.
    switch ([string]$LifecycleEvent["type"]) {
        "wrapper.iteration.start" {
            $script:GitLoopyIterationStartedMonotonic = $ObservedMonotonic
            $script:GitLoopyActiveRef = $null
            $script:GitLoopyActiveStartedAt = $null
            $script:GitLoopyActiveStartedMonotonic = 0
            $script:GitLoopyActiveClosedAt = $null
            $script:GitLoopyActiveClosedMonotonic = $null
            return
        }
        "wrapper.issue.activated" {
            # The first valid activation binds immutably; a conflicting later
            # marker is diagnosed by the caller and never rebinds.
            if ($null -ne $script:GitLoopyActiveRef) {
                return
            }
            $Issue = $LifecycleEvent["issue"]
            $ActivatedAt = ConvertTo-GitLoopyLifecycleInstant `
                -Value $LifecycleEvent["activated_at"]
            if ($null -eq $Issue -or $null -eq $ActivatedAt) {
                return
            }
            $Ref = [string]$Issue
            $script:GitLoopyActiveRef = $Ref
            $script:GitLoopyActiveStartedAt = $ActivatedAt
            # A Working marker declares the issue as the agent starts on it, so
            # its Active time begins at the marker. Every fallback binding is
            # recognized only after the fact, so it is attributed retroactively
            # to the Iteration start and keeps the pre-marker work visible.
            $script:GitLoopyActiveStartedMonotonic = if (
                [string]$LifecycleEvent["binding_source"] -ceq "working_marker"
            ) {
                $ObservedMonotonic
            }
            else {
                $script:GitLoopyIterationStartedMonotonic
            }
            if (-not $script:GitLoopyIssueFirstStartedAt.ContainsKey($Ref)) {
                $script:GitLoopyIssueFirstStartedAt[$Ref] = (
                    $script:GitLoopyActiveStartedAt
                )
                $script:GitLoopyIssueFirstStartedMonotonic[$Ref] = (
                    $script:GitLoopyActiveStartedMonotonic
                )
            }
            return
        }
    }
}

function Publish-GitLoopyActiveBinding {
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [int]$Iteration,
        [Parameter(Mandatory)]
        [string]$Ref,
        [Parameter(Mandatory)]
        [string]$Source,
        [Parameter(Mandatory)]
        [DateTimeOffset]$ObservedAt
    )

    if ($null -ne $script:GitLoopyActiveRef) {
        return $false
    }
    $ActivatedAt = Get-GitLoopyIsoTimestamp -Timestamp $ObservedAt
    Update-GitLoopyIterationLifecycle `
        -LifecycleEvent ([ordered]@{
            type = $EventTypes["WRAPPER_ISSUE_ACTIVATED"]
            issue = $Ref
            activated_at = $ActivatedAt
            binding_source = $Source
        }) `
        -ObservedMonotonic (Get-GitLoopyMonotonicSeconds)
    $Issue = if ($Ref -match '^[0-9]+$') { [int]$Ref } else { $Ref }
    Write-GitLoopyEvent `
        -Context $Context `
        -Type $EventTypes["WRAPPER_ISSUE_ACTIVATED"] `
        -Iteration $Iteration `
        -Payload ([ordered]@{
            activated_at = $ActivatedAt
            binding_source = $Source
            issue = $Issue
        }) `
        -Timestamp $ObservedAt
    return $true
}

function Register-GitLoopySourceClosure {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [int]$Iteration,
        [Parameter(Mandatory)]
        [string]$Ref
    )

    # Record one authoritative source closure the Orchestrator did not perform —
    # the agent (or a human) closed the issue at the source itself. It binds the
    # Iteration's Active issue and stamps the closure instant, so the normalized
    # rollup reports the `closed` Status with its closure-only facts, but it is
    # not a wrapper auto-closure: no `wrapper.auto_close` Event, no closure
    # count, and no duplicate `gh issue close`.
    Publish-GitLoopyActiveBinding `
        -Context $Context `
        -EventTypes $EventTypes `
        -Iteration $Iteration `
        -Ref $Ref `
        -Source "closure" `
        -ObservedAt $script:GitLoopyIterationStartedAt | Out-Null
    if ([string]$script:GitLoopyActiveRef -cne $Ref) {
        return
    }
    if (-not [string]::IsNullOrEmpty($script:GitLoopyActiveClosedAt)) {
        return
    }
    $script:GitLoopyActiveClosedAt = Get-GitLoopyIsoTimestamp `
        -Timestamp ([DateTimeOffset]::UtcNow)
    $script:GitLoopyActiveClosedMonotonic = Get-GitLoopyMonotonicSeconds
}

function Register-GitLoopyObservedSourceClosures {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [int]$Iteration
    )

    # Replay the closures this Iteration's Pool read observed at the source, in
    # first-encounter order. Deliberately called after the turn (or on the
    # empty-Pool path, where no turn runs) so a Working marker still wins the
    # Active binding; an already-bound Iteration keeps its binding and only a
    # closure of the bound issue stamps the closure instant.
    foreach ($Ref in @($script:GitLoopySourceClosedRefs)) {
        Register-GitLoopySourceClosure `
            -Context $Context `
            -EventTypes $EventTypes `
            -Iteration $Iteration `
            -Ref $Ref
    }
}

function Get-GitLoopyCurrentIterationRollup {
    param(
        [Parameter(Mandatory)]
        [object]$FinishedMonotonic,
        [int]$Commits = 0,
        [int]$AutoClosures = 0,
        [int]$PrAdvances = 0,
        [int]$Strikes = 0,
        [string]$TerminalOutcome
    )

    $Arguments = @{
        IterationStartedMonotonic = $script:GitLoopyIterationStartedMonotonic
        FinishedMonotonic = $FinishedMonotonic
        ActiveIssue = $null
        Commits = $Commits
        AutoClosures = $AutoClosures
        PrAdvances = $PrAdvances
        Strikes = $Strikes
        TerminalOutcome = $TerminalOutcome
    }
    if ($null -ne $script:GitLoopyActiveRef) {
        $Ref = [string]$script:GitLoopyActiveRef
        $Arguments["ActiveIssue"] = if ($Ref -match '^[0-9]+$') {
            [int]$Ref
        }
        else {
            $Ref
        }
        $Arguments["ActiveStartedAt"] = $script:GitLoopyActiveStartedAt
        $Arguments["ActiveStartedMonotonic"] = (
            $script:GitLoopyActiveStartedMonotonic
        )
        $Arguments["FirstStartedAt"] = $script:GitLoopyIssueFirstStartedAt[$Ref]
        $Arguments["FirstStartedMonotonic"] = (
            $script:GitLoopyIssueFirstStartedMonotonic[$Ref]
        )
        $Arguments["PreviousCumulativeActiveSeconds"] = if (
            $script:GitLoopyIssueCumulativeActiveSeconds.ContainsKey($Ref)
        ) {
            $script:GitLoopyIssueCumulativeActiveSeconds[$Ref]
        }
        else {
            0
        }
        $Arguments["ActiveClosedAt"] = $script:GitLoopyActiveClosedAt
        $Arguments["ActiveClosedMonotonic"] = $script:GitLoopyActiveClosedMonotonic
    }

    $Rollup = Get-GitLoopyIterationRollup @Arguments
    if ($null -ne $script:GitLoopyActiveRef -and $Rollup["issues"].Count -eq 1) {
        $script:GitLoopyIssueCumulativeActiveSeconds[
            [string]$script:GitLoopyActiveRef
        ] = $Rollup["issues"][0]["cumulative_active_seconds"]
    }
    return $Rollup
}

function Set-GitLoopyActiveBinding {
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [int]$Iteration,
        [AllowEmptyCollection()]
        [object[]]$Pool,
        [Parameter(Mandatory)]
        [string]$Ref,
        [Parameter(Mandatory)]
        [string]$Source,
        [Parameter(Mandatory)]
        [DateTimeOffset]$ObservedAt
    )

    if ($null -ne $script:GitLoopyActiveRef) {
        if (
            $Source -ceq "working_marker" -and
            [string]$script:GitLoopyActiveRef -cne $Ref -and
            $script:GitLoopyWarnedMarkerRefs.Add($Ref)
        ) {
            [Console]::Error.WriteLine(
                "git-loopy: conflicting Active-issue marker for #$Ref ignored; " +
                "Iteration is already bound to #$($script:GitLoopyActiveRef)"
            )
        }
        return $false
    }
    if (
        $Source -ceq "working_marker" -and
        -not (Test-GitLoopyPoolContainsRef -Pool $Pool -Ref $Ref)
    ) {
        if ($script:GitLoopyWarnedMarkerRefs.Add($Ref)) {
            [Console]::Error.WriteLine(
                "git-loopy: Active-issue marker for #$Ref ignored; " +
                "issue is not in the current Pool"
            )
        }
        return $false
    }
    return Publish-GitLoopyActiveBinding `
        -Context $Context `
        -EventTypes $EventTypes `
        -Iteration $Iteration `
        -Ref $Ref `
        -Source $Source `
        -ObservedAt $ObservedAt
}

function Write-GitLoopyAgentOutput {
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [int]$Iteration,
        [AllowEmptyCollection()]
        [object[]]$Pool,
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$Line
    )

    $ObservedAt = [DateTimeOffset]::UtcNow
    [Console]::Error.WriteLine($Line)
    $MarkerPattern = [regex]::new(
        '<\s*working\s+issue\s*=\s*"?\#?(?<issue>[0-9]+)"?\s*>',
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    foreach ($Match in $MarkerPattern.Matches($Line)) {
        $MarkerRef = [string]$Match.Groups["issue"].Value
        [int]$MarkerIssue = 0
        if (
            [int]::TryParse(
                $MarkerRef,
                [Globalization.NumberStyles]::None,
                [Globalization.CultureInfo]::InvariantCulture,
                [ref]$MarkerIssue
            )
        ) {
            $MarkerRef = $MarkerIssue.ToString(
                [Globalization.CultureInfo]::InvariantCulture
            )
        }
        Set-GitLoopyActiveBinding `
            -Context $Context `
            -EventTypes $EventTypes `
            -Iteration $Iteration `
            -Pool $Pool `
            -Ref $MarkerRef `
            -Source "working_marker" `
            -ObservedAt $ObservedAt | Out-Null
    }
    Write-GitLoopyEvent `
        -Context $Context `
        -Type $EventTypes["AGENT_OUTPUT"] `
        -Iteration $Iteration `
        -Payload ([ordered]@{
            kind = "unclassified"
            text = $Line
        }) `
        -Timestamp $ObservedAt
}

function Invoke-GitLoopyBoundedTurn {
    # Run one already-assembled agent turn ("& $Command @Argv") with native CLI
    # stdout represented as unclassified Events while remaining visible on
    # stderr, bounded by a wall-clock send timeout. The turn runs inside
    # an inner pwsh launched as a child Process: the inner `& $Command` keeps
    # PowerShell's cross-platform command resolution (a `.cmd` shim on Windows, a
    # shebang script on Unix — neither of which a raw Process could launch by
    # name), while the outer Process gives a bounded WaitForExit and a
    # tree-killing Kill so a hung agent can never hang the Iteration. Returns the
    # turn's real exit status; a turn that overruns the bound is force-terminated
    # (Process.Kill sends SIGKILL on Unix / TerminateProcess on Windows, so even
    # a signal-trapping agent is reclaimed) and reported as exit 124 (GNU
    # timeout's convention) — a failed, no-progress turn that lands no agent
    # commit, so §6 Strike accounting counts it accordingly. Uses only pwsh
    # built-ins (no jq, no timeout(1), no new dependency).
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [double]$TimeoutSeconds,
        [Parameter(Mandatory)]
        [string]$Command,
        [AllowEmptyCollection()]
        [string[]]$Argv = @(),
        [AllowNull()]
        [psobject]$Context,
        [AllowNull()]
        [Collections.IDictionary]$EventTypes,
        [AllowNull()]
        [Nullable[int]]$Iteration,
        [AllowEmptyCollection()]
        [object[]]$Pool = @()
    )

    # Embed $Command/$Argv as single-quoted PowerShell literals (every `'`
    # doubled) so arbitrary prompt content — newlines, quotes, `$(...)`, unicode —
    # travels to the inner pwsh verbatim with no shell quoting and no injection.
    $CommandLiteral = "'" + ($Command -replace "'", "''") + "'"
    $ArgvLiterals = foreach ($Value in $Argv) {
        "'" + ($Value -replace "'", "''") + "'"
    }
    $ArgvArray = if ($ArgvLiterals) {
        "@(" + ($ArgvLiterals -join ",") + ")"
    } else {
        "@()"
    }

    # The inner pwsh preserves the turn's real exit status
    # ($PSNativeCommandUseErrorActionPreference = $false keeps a
    # non-zero copilot exit a captured status, not a thrown error), and reports a
    # launch failure as 126 — matching the prior in-process invocation exactly.
    $InnerScript = @"
`$ErrorActionPreference = 'Stop'
`$PSNativeCommandUseErrorActionPreference = `$false
`$Argv = $ArgvArray
try {
    & $CommandLiteral @Argv
}
catch {
    [Console]::Error.WriteLine("git-loopy: copilot turn could not launch: `$(`$_.Exception.Message)")
    exit 126
}
exit `$LASTEXITCODE
"@
    $Encoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($InnerScript)
    )

    # Launch the current pwsh (resolved without [Environment]::ProcessPath, which
    # is .NET 6+/pwsh 7.2+, to keep the port's pwsh 7.0 floor) with the encoded
    # turn. UseShellExecute = $false lets the inner pwsh inherit our stdout (the
    # Event stream, which receives only the parent Orchestrator's records) and
    # stderr (where native CLI diagnostics remain visible).
    $PwshPath = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $PwshPath
    foreach ($Flag in @("-NoLogo", "-NoProfile", "-EncodedCommand", $Encoded)) {
        $StartInfo.ArgumentList.Add($Flag)
    }
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardOutput = $true

    $Process = [Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    try {
        $null = $Process.Start()
    }
    catch {
        [Console]::Error.WriteLine(
            "git-loopy: copilot turn could not launch: $($_.Exception.Message)"
        )
        return 126
    }

    $Timer = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        $RemainingMs = [int][Math]::Ceiling(
            ($TimeoutSeconds - $Timer.Elapsed.TotalSeconds) * 1000
        )
        if ($RemainingMs -le 0) {
            break
        }
        $ReadTask = $Process.StandardOutput.ReadLineAsync()
        $Completed = [Threading.Tasks.Task]::WhenAny(
            $ReadTask,
            [Threading.Tasks.Task]::Delay($RemainingMs)
        ).GetAwaiter().GetResult()
        if ($Completed -ne $ReadTask) {
            break
        }
        $Line = $ReadTask.GetAwaiter().GetResult()
        if ($null -eq $Line) {
            if ($Process.WaitForExit($RemainingMs)) {
                $Process.WaitForExit()
                return $Process.ExitCode
            }
            break
        }
        if ($null -ne $Context -and $null -ne $EventTypes -and $null -ne $Iteration) {
            Write-GitLoopyAgentOutput `
                -Context $Context `
                -EventTypes $EventTypes `
                -Iteration $Iteration `
                -Pool $Pool `
                -Line $Line
        }
        else {
            [Console]::Error.WriteLine($Line)
        }
    }

    # The turn overran its bound: reclaim the whole tree (inner pwsh + copilot)
    # and report the timeout exit code so the Iteration proceeds as no-progress.
    [Console]::Error.WriteLine(
        "git-loopy: copilot turn exceeded the ${TimeoutSeconds}s send timeout; terminated."
    )
    $Process.Kill($true)
    $Process.WaitForExit()
    return 124
}

function Invoke-GitLoopyAgentTurn {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [string]$Prompt,
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [int]$Iteration,
        [AllowEmptyCollection()]
        [object[]]$Pool
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
    # stream, and bound the turn by the resolved send timeout. The helper
    # preserves Copilot's real exit status (contract §4), or terminates and fails
    # a turn that overruns the bound so a hung agent never hangs the Iteration.
    return Invoke-GitLoopyBoundedTurn `
        -TimeoutSeconds ([double]$Config.SendTimeoutSeconds) `
        -Command "copilot" `
        -Argv @($Argv) `
        -Context $Context `
        -EventTypes $EventTypes `
        -Iteration $Iteration `
        -Pool $Pool
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
        $View -isnot [Collections.IDictionary]
    ) {
        return $false
    }
    if ([string]$View["state"] -cne "OPEN") {
        # The agent may have performed the required source closure itself before
        # the Orchestrator reached this backstop. That CLOSED state is still
        # authoritative lifecycle evidence, but it is not an auto-closure: record
        # it and skip the duplicate `gh issue close`.
        if ([string]$View["state"] -ceq "CLOSED") {
            Register-GitLoopySourceClosure `
                -Context $Context `
                -EventTypes $EventTypes `
                -Iteration $Iteration `
                -Ref ([string]$Issue)
        }
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

    if ($null -eq $script:GitLoopyFirstClosedRef) {
        $script:GitLoopyFirstClosedRef = $Issue
    }
    Set-GitLoopyActiveBinding `
        -Context $Context `
        -EventTypes $EventTypes `
        -Iteration $Iteration `
        -Pool @() `
        -Ref ([string]$Issue) `
        -Source "closure" `
        -ObservedAt $script:GitLoopyIterationStartedAt | Out-Null
    $ClosedAt = [DateTimeOffset]::UtcNow
    $ClosedMonotonic = Get-GitLoopyMonotonicSeconds
    if ([string]$script:GitLoopyActiveRef -ceq [string]$Issue) {
        $script:GitLoopyActiveClosedAt = Get-GitLoopyIsoTimestamp `
            -Timestamp $ClosedAt
        $script:GitLoopyActiveClosedMonotonic = $ClosedMonotonic
    }
    Write-GitLoopyEvent `
        -Context $Context `
        -Type $EventTypes["WRAPPER_AUTO_CLOSE"] `
        -Iteration $Iteration `
        -Payload ([ordered]@{
            issue = $Issue
            sha = $RefShas[0]
            shas = [string[]]$RefShas.ToArray()
        }) `
        -Timestamp $ClosedAt
    return $true
}

function Get-GitLoopyPoolActionableCloseReferences {
    [CmdletBinding()]
    param(
        [AllowEmptyCollection()]
        [object[]]$Pool,
        [AllowEmptyCollection()]
        [object[]]$Commits
    )

    # Assemble the actionable Pool-*issue* close-refs named in this Iteration's new
    # commits: the { ref, kind = "issue" } Pool descriptors crossed with the closing
    # keywords in the concatenated commit subjects/bodies. Shared by the auto-close
    # backstop (§5) and the Checkpoint active-ref inference (§7) so both derive the
    # identical first-encounter-ordered close-ref set from one assembly (the two
    # paths must never disagree about which Pool issues this Iteration referenced).
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
    return , $Actionable
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

    $Closures = 0
    $Actionable = Get-GitLoopyPoolActionableCloseReferences -Pool $Pool -Commits $Commits
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
    # priority order: the immutable published binding; then the first Pool issue
    # this Iteration actually auto-closed (the
    # strongest signal of what was worked, `completions[0].ref` in the reference);
    # then an actionable Pool-issue close-ref named in this Iteration's agent
    # commits (the agent named the issue it worked, even if the closure did not
    # fire); then a single-member Pool (the only candidate); else nothing
    # (unattributed). Returns the ref (an issue number or a PRDs/PR string) or
    # $null.
    if ($null -ne $script:GitLoopyActiveRef) {
        return [string]$script:GitLoopyActiveRef
    }
    if ($null -ne $script:GitLoopyFirstClosedRef) {
        return [string]$script:GitLoopyFirstClosedRef
    }
    $Actionable = Get-GitLoopyPoolActionableCloseReferences -Pool $Pool -Commits $Commits
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

function Get-GitLoopyFallbackBinding {
    param(
        [AllowEmptyCollection()]
        [object[]]$Pool,
        [AllowEmptyCollection()]
        [object[]]$Commits
    )

    if ($null -ne $script:GitLoopyFirstClosedRef) {
        return [pscustomobject]@{
            Ref = [string]$script:GitLoopyFirstClosedRef
            Source = "closure"
        }
    }
    $Actionable = @(
        Get-GitLoopyPoolActionableCloseReferences -Pool $Pool -Commits $Commits
    )
    if (
        $Actionable.Count -gt 0 -and
        -not [string]::IsNullOrEmpty([string]$Actionable[0])
    ) {
        return [pscustomobject]@{
            Ref = [string]$Actionable[0]
            Source = "commit"
        }
    }
    if (@($Pool).Count -eq 1) {
        $Only = @($Pool)[0]
        return [pscustomobject]@{
            Ref = if ($Only.Contains("number")) {
                [string]$Only["number"]
            }
            else {
                [string]$Only["ref"]
            }
            Source = "single_member_pool"
        }
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
        [psobject]$Preflight,
        [Collections.IDictionary]$Environment = (Get-GitLoopyEnvironment)
    )

    $ReleaseVersion = Get-GitLoopyReleaseVersion
    $Context = New-GitLoopyEventContext -RepoRoot $Preflight.RepoRoot
    $EventTypes = Get-GitLoopyEventTypes
    Set-GitLoopyGitignoreEntry -RepoRoot $Preflight.RepoRoot
    # Earn the live interface before the first Event exists, so the very first
    # `wrapper.run.start` already goes to its final destination and the helper
    # never has to be handed a partially replayed Run. Teardown is in the
    # `finally` below because a Run that throws still owes the operator its
    # terminal back.
    #
    # The sink is installed here rather than inside the TUI module so exactly one
    # module owns the live destination. It stays installed after a mid-Run
    # fallback and becomes a pass-through to stdout, which is what makes "a Run
    # never respawns the helper" true by construction.
    $Interactive = Start-GitLoopyTuiSession `
        -RepoRoot $Preflight.RepoRoot `
        -Flag $Config.InteractiveFlag `
        -EnvironmentValue (
            Get-GitLoopyEnvironmentValue $Environment "GIT_LOOPY_INTERACTIVE"
        ) `
        -ReleaseVersion $ReleaseVersion `
        -SchemaVersion (Get-GitLoopyEventSchemaVersion)
    if ($Interactive) {
        Set-GitLoopyLiveSink -Sink {
            param($Line)
            Write-GitLoopyTuiLine -Line $Line
        }
    }
    try {
        return Invoke-GitLoopyDiscoveryLoop `
            -Config $Config `
            -Preflight $Preflight `
            -Context $Context `
            -EventTypes $EventTypes `
            -ReleaseVersion $ReleaseVersion
    }
    finally {
        Stop-GitLoopyTuiSession
        Set-GitLoopyLiveSink -Sink $null
    }
}

function Invoke-GitLoopyDiscoveryLoop {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Config,
        [Parameter(Mandatory)]
        [psobject]$Preflight,
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$EventTypes,
        [Parameter(Mandatory)]
        [string]$ReleaseVersion
    )

    Write-GitLoopyEvent `
        -Context $Context `
        -Type $EventTypes["WRAPPER_RUN_START"] `
        -Payload ([ordered]@{
            deny_skills = [string[]]$Config.DenySkills
            deny_tools = [string[]]$Config.DenyTools
            insight_capabilities = Get-GitLoopyInsightCapabilities
            issue_source = $Config.IssueSource
            max_iterations = $Config.MaxIterations
            max_nmt_strikes = $Config.MaxNmtStrikes
            model = $Config.Model
            prompt_path = $Preflight.PromptPath
            release_version = $ReleaseVersion
            reasoning_effort = $Config.ReasoningEffort
            schema_version = Get-GitLoopyEventSchemaVersion
            send_timeout_seconds = $Config.SendTimeoutSeconds
        })

    [int]$Iteration = 0
    [int]$IterationsRun = 0
    $Outcome = "iteration_cap"
    [int]$Strikes = 0
    $StrikeOutcome = "running"
    Reset-GitLoopyIterationLifecycleState
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

        $script:GitLoopyIterationStartedAt = [DateTimeOffset]::UtcNow
        Update-GitLoopyIterationLifecycle `
            -LifecycleEvent ([ordered]@{
                type = $EventTypes["WRAPPER_ITERATION_START"]
                iter = $Iteration
            }) `
            -ObservedMonotonic (Get-GitLoopyMonotonicSeconds)
        $script:GitLoopyWarnedMarkerRefs.Clear()
        $script:GitLoopySourceClosedRefs.Clear()
        Write-GitLoopyEvent `
            -Context $Context `
            -Type $EventTypes["WRAPPER_ITERATION_START"] `
            -Iteration $Iteration `
            -Timestamp $script:GitLoopyIterationStartedAt

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
            # No turn runs, so no Working marker can claim this Iteration: any
            # closure the Pool read observed at the source is its only Active
            # binding and lifecycle evidence.
            Register-GitLoopyObservedSourceClosures `
                -Context $Context `
                -EventTypes $EventTypes `
                -Iteration $Iteration
            $Rollup = Get-GitLoopyCurrentIterationRollup `
                -FinishedMonotonic (Get-GitLoopyMonotonicSeconds) `
                -Strikes $Strikes
            Write-GitLoopyEvent `
                -Context $Context `
                -Type $EventTypes["WRAPPER_ITERATION_END"] `
                -Iteration $Iteration `
                -Payload $Rollup
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
        $AgentStatus = Invoke-GitLoopyAgentTurn `
            -Config $Config `
            -Prompt $Prompt `
            -Context $Context `
            -EventTypes $EventTypes `
            -Iteration $Iteration `
            -Pool $Pool
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
        # Binding priority (CONTEXT.md): a Working marker from the turn wins,
        # then a wrapper closure, then a closure the Pool read observed at the
        # source, then the commit / single-member-Pool fallback.
        Register-GitLoopyObservedSourceClosures `
            -Context $Context `
            -EventTypes $EventTypes `
            -Iteration $Iteration
        if ($null -eq $script:GitLoopyActiveRef) {
            $FallbackBinding = Get-GitLoopyFallbackBinding `
                -Pool $Pool `
                -Commits $NewCommits
            if ($null -ne $FallbackBinding) {
                Publish-GitLoopyActiveBinding `
                    -Context $Context `
                    -EventTypes $EventTypes `
                    -Iteration $Iteration `
                    -Ref $FallbackBinding.Ref `
                    -Source $FallbackBinding.Source `
                    -ObservedAt $script:GitLoopyIterationStartedAt | Out-Null
            }
        }

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

        $TerminalOutcome = if ($StrikeOutcome -ceq "aborted") {
            "aborted"
        }
        else {
            ""
        }
        $Rollup = Get-GitLoopyCurrentIterationRollup `
            -FinishedMonotonic (Get-GitLoopyMonotonicSeconds) `
            -Commits $AgentCommits `
            -AutoClosures $AutoClosures `
            -PrAdvances 0 `
            -Strikes $Strikes `
            -TerminalOutcome $TerminalOutcome
        Write-GitLoopyEvent `
            -Context $Context `
            -Type $EventTypes["WRAPPER_ITERATION_END"] `
            -Iteration $Iteration `
            -Payload $Rollup
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

Commands:
  continuation                    Native Continuation contract commands.

Options:
  --model ID
  --reasoning-effort none|minimal|low|medium|high|xhigh|max
  --issue-source github|prds
  --max-nmt-strikes N
  --deny-tool TOOL              Repeatable; unioned with GIT_LOOPY_DENY_TOOLS.
  --deny-skill SKILL            Repeatable; unioned with GIT_LOOPY_DENY_SKILLS.
  --enable-skill SKILL          Closed-world Skill policy; not yet supported by
                                the PowerShell Orchestrator (fails closed).
  --disable-skill SKILL         Closed-world Skill policy; not yet supported by
                                the PowerShell Orchestrator (fails closed).
  --send-timeout-seconds N
  --interactive                 Drive the shared git-loopy-tui helper when a
                                compatible one is installed.
  --no-interactive              Keep raw JSONL on stdout (CI-safe).
  --version
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

    if ($Arguments.Count -gt 0 -and $Arguments[0] -ceq "--version") {
        if ($Arguments.Count -ne 1) {
            throw (New-GitLoopyParseException "--version accepts no arguments")
        }
        [Console]::Out.WriteLine(
            "git-loopy $(Get-GitLoopyReleaseVersion)"
        )
        return 0
    }

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
        -Preflight $Preflight `
        -Environment $Environment
}

Export-ModuleMember -Function @(
    "Get-GitLoopyReleaseVersion",
    "Get-GitLoopyEnvironment",
    "Get-GitLoopyMonotonicSeconds",
    "Resolve-GitLoopyConfig",
    "Get-GitLoopyConfigHome",
    "Test-GitLoopyConfigDeclaresEnabledSkills",
    "Get-GitLoopySkillPolicySurfaces",
    "Assert-GitLoopySkillPolicySupported",
    "Test-GitLoopyAfkReady",
    "Get-GitLoopyExitCode",
    "Get-GitLoopyCloseKeywordPattern",
    "Get-GitLoopyCloseReferences",
    "Get-GitLoopyActionableCloseReferences",
    "Get-GitLoopyPoolActionableCloseReferences",
    "Test-GitLoopyIterationProgress",
    "Get-GitLoopyIterationRollup",
    "Update-GitLoopyIterationLifecycle",
    "Reset-GitLoopyIterationLifecycleState",
    "Get-GitLoopyCurrentIterationRollup",
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
    "Invoke-GitLoopyBoundedTurn",
    "Get-GitLoopyUsage",
    "Invoke-GitLoopyMain"
)
