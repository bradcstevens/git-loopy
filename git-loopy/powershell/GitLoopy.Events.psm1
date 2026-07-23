Set-StrictMode -Version Latest

$script:EventTypes = [ordered]@{
    WRAPPER_RUN_START = "wrapper.run.start"
    WRAPPER_RUN_END = "wrapper.run.end"
    WRAPPER_ISSUE_ACTIVATED = "wrapper.issue.activated"
    WRAPPER_SKILL_POLICY_RESOLVED = "wrapper.skill_policy.resolved"
    WRAPPER_ITERATION_START = "wrapper.iteration.start"
    WRAPPER_ITERATION_END = "wrapper.iteration.end"
    WRAPPER_AFK_READY_COLLECTED = "wrapper.afk_ready.collected"
    WRAPPER_CHECKPOINT_RECORDED = "wrapper.checkpoint.recorded"
    WRAPPER_COMMIT_RECORDED = "wrapper.commit.recorded"
    WRAPPER_PUSH_RECORDED = "wrapper.push.recorded"
    WRAPPER_AUTO_CLOSE = "wrapper.auto_close"
    WRAPPER_PR_ADVANCED = "wrapper.pr.advanced"
    WRAPPER_STRIKE = "wrapper.strike"
    WRAPPER_ASK_USER_ATTEMPTED = "wrapper.ask_user.attempted"
    WRAPPER_CONTINUATION_RECONCILED = "wrapper.continuation.reconciled"
    WRAPPER_CONTINUATION_DISPATCH_STARTED = "wrapper.continuation_dispatch.started"
    WRAPPER_CONTINUATION_DISPATCH_ENDED = "wrapper.continuation_dispatch.ended"
    WRAPPER_CONTINUATION_STOPPED = "wrapper.continuation.stopped"
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

function Write-GitLoopyEvent {
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

    $Event = New-GitLoopyEvent @PSBoundParameters
    $Line = ConvertTo-GitLoopyJsonLine -Event $Event
    $ReplayDirectory = Split-Path -Parent $Context.ReplayPath
    [IO.Directory]::CreateDirectory($ReplayDirectory) | Out-Null
    [IO.File]::AppendAllText(
        $Context.ReplayPath,
        $Line,
        [Text.UTF8Encoding]::new($false)
    )
    [Console]::Out.Write($Line)
}

Export-ModuleMember -Function @(
    "Get-GitLoopyEventTypes",
    "Get-GitLoopyEventSchemaVersion",
    "Get-GitLoopyInsightCapabilities",
    "Get-GitLoopyIsoTimestamp",
    "New-GitLoopyRunId",
    "New-GitLoopyEventContext",
    "New-GitLoopyEvent",
    "Protect-GitLoopyJson",
    "ConvertTo-GitLoopyJsonLine",
    "Write-GitLoopyEvent"
)
