Set-StrictMode -Version Latest

$script:EventTypes = [ordered]@{
    WRAPPER_RUN_START = "wrapper.run.start"
    WRAPPER_RUN_END = "wrapper.run.end"
    WRAPPER_ISSUE_ACTIVATED = "wrapper.issue.activated"
    WRAPPER_SKILL_POLICY_RESOLVED = "wrapper.skill_policy.resolved"
    WRAPPER_ITERATION_START = "wrapper.iteration.start"
    WRAPPER_ITERATION_END = "wrapper.iteration.end"
    WRAPPER_AFK_READY_COLLECTED = "wrapper.afk_ready.collected"
    WRAPPER_POOL_EXCLUDED = "wrapper.pool.excluded"
    # The two halves of one **Pickup** walk (#397). This port admits every
    # candidate -- it has no refusal to make, because the only admission the
    # Wrapper contract names is §14's Routed pair and this port implements
    # none -- so it binds and never skips. The skip literal is carried for
    # vocabulary parity and produced the day this port gains something to
    # refuse.
    WRAPPER_PICKUP_BOUND = "wrapper.pickup.bound"
    WRAPPER_PICKUP_SKIPPED = "wrapper.pickup.skipped"
    WRAPPER_CHECKPOINT_RECORDED = "wrapper.checkpoint.recorded"
    WRAPPER_COMMIT_RECORDED = "wrapper.commit.recorded"
    WRAPPER_PUSH_RECORDED = "wrapper.push.recorded"
    WRAPPER_AUTO_CLOSE = "wrapper.auto_close"
    WRAPPER_PR_ADVANCED = "wrapper.pr.advanced"
    WRAPPER_STRIKE = "wrapper.strike"
    WRAPPER_ASK_USER_ATTEMPTED = "wrapper.ask_user.attempted"
    # Run-scoped record of a **Dashboard fault** (ADR-0024). Only an
    # Orchestrator that hosts a Dashboard can emit it; this port hosts none, so
    # it carries the literal for vocabulary parity and never produces the Event.
    WRAPPER_DASHBOARD_FAULT = "wrapper.dashboard.fault"
    WRAPPER_CONTINUATION_RECONCILED = "wrapper.continuation.reconciled"
    WRAPPER_CONTINUATION_DISPATCH_STARTED = "wrapper.continuation_dispatch.started"
    WRAPPER_CONTINUATION_DISPATCH_ENDED = "wrapper.continuation_dispatch.ended"
    WRAPPER_CONTINUATION_STOPPED = "wrapper.continuation.stopped"
    WRAPPER_POOL_REFRESHED = "wrapper.pool.refreshed"
    WRAPPER_CONTRIBUTION_START = "wrapper.contribution.start"
    WRAPPER_CONTRIBUTION_WORK_FINISHED = "wrapper.contribution.work_finished"
    WRAPPER_INTEGRATION_PARKED = "wrapper.integration.parked"
    WRAPPER_INTEGRATION_ADMITTED = "wrapper.integration.admitted"
    WRAPPER_INTEGRATION_STARTED = "wrapper.integration.started"
    WRAPPER_INTEGRATION_BRANCH_OBSERVED = "wrapper.integration.branch_observed"
    WRAPPER_INTEGRATION_RECOVERY_STARTED = "wrapper.integration.recovery_started"
    WRAPPER_INTEGRATION_PUBLISHED = "wrapper.integration.published"
    WRAPPER_CONTRIBUTION_END = "wrapper.contribution.end"
    WRAPPER_CONCURRENCY_CHANGED = "wrapper.concurrency.changed"
    WRAPPER_SERIAL_REQUESTED = "wrapper.serial.requested"
    WRAPPER_PIPELINE_QUIESCENT = "wrapper.pipeline.quiescent"
    WRAPPER_ROLLING_REFILL_TURN = "wrapper.rolling.refill_turn"
    WRAPPER_PARALLEL_SERIAL_FALLBACK = "wrapper.parallel.serial_fallback"
    # Calibration records (contract 1.16). Declared so this table stays the
    # whole event vocabulary; never emitted here, because Calibration and the
    # measured tier it serves are Python-only (contract 14.1).
    CALIBRATION_TRIAL_START = "calibration.trial.start"
    CALIBRATION_TRIAL_END = "calibration.trial.end"
    AGENT_OUTPUT = "agent.output"
    SESSION_CREATED = "session.created"
    SESSION_IDLE = "session.idle"
    SESSION_DELETED = "session.deleted"
    ASSISTANT_MESSAGE = "assistant.message"
    ASSISTANT_REASONING = "assistant.reasoning"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    TOOL_PERMISSION_REQUESTED = "tool.permission_requested"
    TOOL_PERMISSION_DENIED = "tool.permission_denied"
    USAGE_TOKENS = "usage.tokens"
    USAGE_CONTEXT_WINDOW = "usage.context_window"
}
$script:EventSchemaVersion = 1
$script:InsightCapabilities = [ordered]@{
    agent_output = $true
    structured_agent_events = $false
    token_usage = $false
    context_window = $false
    skill_consultation = $false
    cost = $false
}
# What *this Run* obtained, as opposed to what this distribution can observe.
# The six above are fixed per binary; a run-scoped capability can differ between
# two Runs of one binary, so it is composed onto the wire manifest rather than
# frozen beside them (#334, ADR-0026, Wrapper contract 12).
#
# `rate_card` is the only one today. This port subscribes to no SDK event stream
# and reads no model listing, so it resolves no **Rate card** on any Run — the
# answer happens to be the same every time, but it is declared here because a
# **Dashboard** reading this stream must be able to tell "this Orchestrator
# cannot report a rate" from "this Run's prices failed to load", and an omitted
# key collapses those two facts into one unknown.
$script:RunScopedInsightCapabilities = [ordered]@{
    rate_card = $false
}
# The card that travels beside the declaration. A port declaring the capability
# `false` publishes an explicit `null`, never an omitted key and never an empty
# object: an empty card would be a record nothing can be audited against.
$script:RateCard = $null
# What this port can *schedule*, as opposed to what it can observe. The
# PowerShell Orchestrator has no rolling scheduler, no **Integration** stage,
# and no **Lane**, so every parallel capability is false. Declaring it is what
# turns "unimplemented" from a silent serial Run into something an operator can
# read off `wrapper.run.start` (ADR-0020, Wrapper contract 12).
$script:ParallelCapabilities = [ordered]@{
    parallel_mode = $false
    rolling_dispatch = $false
    integration_backlog = $false
    adaptive_lane_limit = $false
    contribution_events = $false
}

$script:EnvelopeKeys = @("ts", "run_id", "iter", "type")
$script:CrockfordAlphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
$script:RunIdPattern = "^[0-9A-HJKMNP-TV-Z]{26}$"
$script:RedactedSecret = "<redacted-secret>"
$script:SecretPatterns = @(
    [regex]::new("ghp_[A-Za-z0-9]{36,}"),
    [regex]::new("gho_[A-Za-z0-9]{36,}"),
    [regex]::new(
        "eyJ[A-Za-z0-9_-]{17,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"
    ),
    [regex]::new("AKIA[0-9A-Z]{16}")
)

function Get-GitLoopyEventTypes {
    [CmdletBinding()]
    param()

    $Copy = [ordered]@{}
    foreach ($Name in $script:EventTypes.Keys) {
        $Copy[$Name] = $script:EventTypes[$Name]
    }
    return $Copy
}

function Get-GitLoopyEventSchemaVersion {
    [CmdletBinding()]
    param()

    return $script:EventSchemaVersion
}

function Get-GitLoopyInsightCapabilities {
    [CmdletBinding()]
    param()

    $Copy = [ordered]@{}
    foreach ($Name in $script:InsightCapabilities.Keys) {
        $Copy[$Name] = $script:InsightCapabilities[$Name]
    }
    return $Copy
}

function Get-GitLoopyRunScopedInsightCapabilities {
    [CmdletBinding()]
    param()

    $Copy = [ordered]@{}
    foreach ($Name in $script:RunScopedInsightCapabilities.Keys) {
        $Copy[$Name] = $script:RunScopedInsightCapabilities[$Name]
    }
    return $Copy
}

function Get-GitLoopyRunInsightCapabilities {
    [CmdletBinding()]
    param()

    # The Run-start Insight manifest as it goes on the wire: the frozen
    # per-distribution capabilities, then this Run's own run-scoped answers. The
    # run-scoped keys come last so the six a **Dashboard** must find keep the
    # order the family contract lists them in.
    $Manifest = Get-GitLoopyInsightCapabilities
    foreach ($Name in $script:RunScopedInsightCapabilities.Keys) {
        $Manifest[$Name] = $script:RunScopedInsightCapabilities[$Name]
    }
    return $Manifest
}

function Get-GitLoopyRateCard {
    [CmdletBinding()]
    param()

    return $script:RateCard
}

function Get-GitLoopyParallelCapabilities {
    [CmdletBinding()]
    param()

    $Copy = [ordered]@{}
    foreach ($Name in $script:ParallelCapabilities.Keys) {
        $Copy[$Name] = $script:ParallelCapabilities[$Name]
    }
    return $Copy
}

function Get-GitLoopyIsoTimestamp {
    [CmdletBinding()]
    param(
        [DateTimeOffset]$Timestamp = [DateTimeOffset]::UtcNow
    )

    return $Timestamp.ToUniversalTime().ToString(
        "yyyy-MM-dd'T'HH:mm:ss.fff'Z'",
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function New-GitLoopyRunId {
    [CmdletBinding()]
    param(
        [Nullable[long]]$TimeMilliseconds
    )

    [long]$Value = if ($null -ne $TimeMilliseconds) {
        $TimeMilliseconds
    }
    else {
        [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    }
    if ($Value -lt 0 -or $Value -ge 281474976710656) {
        throw "Run-id timestamp must be an unsigned 48-bit millisecond value."
    }

    $TimestampChars = [char[]]::new(10)
    for ($Index = 9; $Index -ge 0; $Index--) {
        $TimestampChars[$Index] = $script:CrockfordAlphabet[
            [int]($Value -band 31)
        ]
        $Value = $Value -shr 5
    }

    $RandomBytes = [byte[]]::new(10)
    [Security.Cryptography.RandomNumberGenerator]::Fill($RandomBytes)
    $RandomPart = [Text.StringBuilder]::new(16)
    [int]$Buffer = 0
    [int]$Bits = 0
    foreach ($Byte in $RandomBytes) {
        $Buffer = ($Buffer -shl 8) -bor [int]$Byte
        $Bits += 8
        while ($Bits -ge 5) {
            $Bits -= 5
            $AlphabetIndex = ($Buffer -shr $Bits) -band 31
            [void]$RandomPart.Append($script:CrockfordAlphabet[$AlphabetIndex])
            if ($Bits -eq 0) {
                $Buffer = 0
            }
            else {
                $Buffer = $Buffer -band ((1 -shl $Bits) - 1)
            }
        }
    }

    return [string]::new($TimestampChars) + $RandomPart.ToString()
}

function New-GitLoopyEventContext {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot,
        [string]$RunId,
        [DateTimeOffset]$StartedAt = [DateTimeOffset]::UtcNow
    )

    if ([string]::IsNullOrWhiteSpace($RunId)) {
        $RunId = New-GitLoopyRunId `
            -TimeMilliseconds $StartedAt.ToUnixTimeMilliseconds()
    }
    if ($RunId -cnotmatch $script:RunIdPattern) {
        throw "Run id must be a 26-character Crockford-base32 ULID."
    }

    $UtcStartedAt = $StartedAt.ToUniversalTime()
    $FilenameTimestamp = $UtcStartedAt.ToString(
        "yyyy-MM-dd'T'HH-mm-ss'Z'",
        [Globalization.CultureInfo]::InvariantCulture
    )
    $Root = [IO.Path]::GetFullPath($RepoRoot)
    $ReplayPath = Join-Path $Root (
        ".git-loopy/logs/$FilenameTimestamp-$RunId.jsonl"
    )

    return [pscustomobject]@{
        PSTypeName = "GitLoopy.EventContext"
        RunId = $RunId
        StartedAt = Get-GitLoopyIsoTimestamp -Timestamp $UtcStartedAt
        ReplayPath = $ReplayPath
    }
}

function New-GitLoopyEvent {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [string]$Type,
        [AllowNull()]
        [Nullable[int]]$Iteration,
        [Collections.IDictionary]$Payload = [ordered]@{},
        [Nullable[DateTimeOffset]]$Timestamp
    )

    if ($null -ne $Iteration -and $Iteration -lt 1) {
        throw "Iteration must be null or a positive integer."
    }
    foreach ($Key in $script:EnvelopeKeys) {
        if ($Payload.Contains($Key)) {
            throw "Event payload cannot contain envelope key '$Key'."
        }
    }

    $EventTimestamp = if ($null -ne $Timestamp) {
        Get-GitLoopyIsoTimestamp -Timestamp $Timestamp
    }
    else {
        Get-GitLoopyIsoTimestamp
    }
    $Event = [ordered]@{
        ts = $EventTimestamp
        run_id = $Context.RunId
        iter = if ($null -ne $Iteration) { $Iteration } else { $null }
        type = $Type
    }

    [string[]]$PayloadKeys = @($Payload.Keys | ForEach-Object { [string]$_ })
    [Array]::Sort($PayloadKeys, [StringComparer]::Ordinal)
    foreach ($Key in $PayloadKeys) {
        $Event[$Key] = $Payload[$Key]
    }
    return $Event
}

function ConvertTo-GitLoopyJsonValue {
    param(
        [AllowNull()]
        [object]$Value
    )

    if ($null -eq $Value) {
        return "null"
    }
    if ($Value -is [DateTimeOffset]) {
        $Timestamp = Get-GitLoopyIsoTimestamp -Timestamp $Value
        return ConvertTo-Json -InputObject $Timestamp -Compress
    }
    if ($Value -is [DateTime]) {
        $DateTimeOffset = if ($Value.Kind -eq [DateTimeKind]::Unspecified) {
            [DateTimeOffset]::new(
                [DateTime]::SpecifyKind($Value, [DateTimeKind]::Utc)
            )
        }
        else {
            [DateTimeOffset]::new($Value)
        }
        $Timestamp = Get-GitLoopyIsoTimestamp -Timestamp $DateTimeOffset
        return ConvertTo-Json -InputObject $Timestamp -Compress
    }
    if ($Value -is [Collections.IDictionary]) {
        $Parts = [Collections.Generic.List[string]]::new()
        foreach ($Key in $Value.Keys) {
            $EncodedKey = ConvertTo-Json -InputObject ([string]$Key) -Compress
            $EncodedValue = ConvertTo-GitLoopyJsonValue -Value $Value[$Key]
            $Parts.Add("$EncodedKey`: $EncodedValue")
        }
        return "{" + [string]::Join(", ", $Parts) + "}"
    }
    if (
        $Value -is [Collections.IEnumerable] -and
        $Value -isnot [string]
    ) {
        $Parts = [Collections.Generic.List[string]]::new()
        foreach ($Item in $Value) {
            $Parts.Add((ConvertTo-GitLoopyJsonValue -Value $Item))
        }
        return "[" + [string]::Join(", ", $Parts) + "]"
    }
    if ($Value -is [pscustomobject]) {
        $Properties = [ordered]@{}
        foreach ($Property in $Value.PSObject.Properties) {
            $Properties[$Property.Name] = $Property.Value
        }
        return ConvertTo-GitLoopyJsonValue -Value $Properties
    }
    return ConvertTo-Json -InputObject $Value -Compress -Depth 100
}

function Protect-GitLoopyJson {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Json
    )

    $Scrubbed = $Json
    foreach ($Pattern in $script:SecretPatterns) {
        $Scrubbed = $Pattern.Replace($Scrubbed, $script:RedactedSecret)
    }
    return $Scrubbed
}

function ConvertTo-GitLoopyJsonLine {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Event
    )

    foreach ($Key in $script:EnvelopeKeys) {
        if (-not $Event.Contains($Key)) {
            throw "Event must contain envelope key '$Key'."
        }
    }

    $Ordered = [ordered]@{}
    foreach ($Key in $script:EnvelopeKeys) {
        $Ordered[$Key] = $Event[$Key]
    }
    [string[]]$PayloadKeys = @(
        $Event.Keys |
            ForEach-Object { [string]$_ } |
            Where-Object { $_ -cnotin $script:EnvelopeKeys }
    )
    [Array]::Sort($PayloadKeys, [StringComparer]::Ordinal)
    foreach ($Key in $PayloadKeys) {
        $Ordered[$Key] = $Event[$Key]
    }

    $Json = ConvertTo-GitLoopyJsonValue -Value $Ordered
    $Scrubbed = Protect-GitLoopyJson -Json $Json
    return $Scrubbed + "`n"
}

# Where a serialized Event goes *live*. `$null` means stdout, which is the only
# destination this port had before the shared TUI helper and remains the one it
# falls back to. The replay log is written unconditionally and first, so it stays
# the authoritative record no matter what the live destination does; this
# indirection only decides who else sees the same bytes.
$script:LiveSink = $null

function Set-GitLoopyLiveSink {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [scriptblock]$Sink
    )

    $script:LiveSink = $Sink
}

function Write-GitLoopyEvent {    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [psobject]$Context,
        [Parameter(Mandatory)]
        [string]$Type,
        [AllowNull()]
        [Nullable[int]]$Iteration,
        [Collections.IDictionary]$Payload = [ordered]@{},
        [Nullable[DateTimeOffset]]$Timestamp
    )

    $Event = New-GitLoopyEvent @PSBoundParameters
    $Line = ConvertTo-GitLoopyJsonLine -Event $Event
    $ReplayDirectory = Split-Path -Parent $Context.ReplayPath
    [IO.Directory]::CreateDirectory($ReplayDirectory) | Out-Null
    [IO.File]::AppendAllText(
        $Context.ReplayPath,
        $Line,
        [Text.UTF8Encoding]::new($false)
    )
    if ($null -eq $script:LiveSink) {
        [Console]::Out.Write($Line)
        return
    }
    & $script:LiveSink $Line
}

Export-ModuleMember -Function @(
    "Get-GitLoopyEventTypes",
    "Get-GitLoopyEventSchemaVersion",
    "Get-GitLoopyInsightCapabilities",
    "Get-GitLoopyRunScopedInsightCapabilities",
    "Get-GitLoopyRunInsightCapabilities",
    "Get-GitLoopyRateCard",
    "Get-GitLoopyParallelCapabilities",
    "Get-GitLoopyIsoTimestamp",
    "New-GitLoopyRunId",
    "New-GitLoopyEventContext",
    "New-GitLoopyEvent",
    "Protect-GitLoopyJson",
    "ConvertTo-GitLoopyJsonLine",
    "Set-GitLoopyLiveSink",
    "Write-GitLoopyEvent"
)
