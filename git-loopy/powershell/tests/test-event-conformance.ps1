Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$FixturePath = Join-Path (Split-Path -Parent $PortDir) "conformance/event-schema.json"
$ReleaseFixturePath = Join-Path (
    Split-Path -Parent $PortDir
) "conformance/release-version.json"
$ModulePath = Join-Path $PortDir "GitLoopy.Events.psm1"
$OrchestratorModulePath = Join-Path $PortDir "GitLoopy.Orchestrator.psm1"

Import-Module $OrchestratorModulePath -Force
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

$Fixture = Get-Content -LiteralPath $FixturePath -Raw |
    ConvertFrom-Json -AsHashtable
$ReleaseFixture = Get-Content -LiteralPath $ReleaseFixturePath -Raw |
    ConvertFrom-Json -AsHashtable
$ExpectedTypes = $Fixture["event_types"]
$ActualTypes = Get-GitLoopyEventTypes

Assert-Equal $ExpectedTypes.Count $ActualTypes.Count "event type count"
foreach ($Name in $ExpectedTypes.Keys) {
    Assert-True $ActualTypes.Contains($Name) "missing event type $Name"
    Assert-Equal $ExpectedTypes[$Name] $ActualTypes[$Name] "event type $Name"
}
$ExpectedCapabilities = $Fixture["insight_capabilities"]["orchestrators"]["powershell"]
$ActualCapabilities = Get-GitLoopyInsightCapabilities
Assert-Equal $Fixture["schema_version"] (
    Get-GitLoopyEventSchemaVersion
) "Event-schema version"
Assert-Equal $ExpectedCapabilities.Count $ActualCapabilities.Count (
    "Insight capability count"
)
foreach ($Name in $ExpectedCapabilities.Keys) {
    Assert-True $ActualCapabilities.Contains($Name) "missing Insight capability $Name"
    Assert-Equal $ExpectedCapabilities[$Name] $ActualCapabilities[$Name] (
        "Insight capability $Name"
    )
}
$RunStartCase = @(
    $Fixture["serialization_cases"] |
        Where-Object { $_["id"] -ceq "run-start-insight-capabilities" }
)
Assert-Equal 1 $RunStartCase.Count "Run-start serialization case count"
Assert-Equal (
    $ReleaseFixture["expected_release_version"]
) $RunStartCase[0]["event"]["release_version"] (
    "Run-start Event Release version"
)

foreach ($Case in $Fixture["serialization_cases"]) {
    $Actual = ConvertTo-GitLoopyJsonLine -Event $Case["event"]
    Assert-Equal $Case["jsonl"] $Actual "serialization fixture: $($Case["id"])"
}

function Add-RollupEnvelope {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Rollup
    )

    # The rollup is an Iteration-end payload, not a whole Event. Wrap it in one
    # fixed envelope so the comparison exercises the contract's envelope-first
    # key order and nested value serialization.
    $Event = [ordered]@{
        ts = "2026-05-16T00:00:00.000Z"
        run_id = "01HXR0000000000000000000AA"
        iter = 1
        type = "wrapper.iteration.end"
    }
    foreach ($Key in $Rollup.Keys) {
        $Event[$Key] = $Rollup[$Key]
    }
    return $Event
}

$script:NumericTypes = @(
    [int], [long], [double], [decimal], [single], [short], [byte]
)

function Test-IsNumber {
    param(
        [AllowNull()]
        [object]$Value
    )

    if ($null -eq $Value -or $Value -is [bool]) {
        return $false
    }
    return $Value.GetType() -in $script:NumericTypes
}

function Get-GovernedMeasurement {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Rollup
    )

    # Every normalized measurement each Insight capability governs. The keys are
    # asserted below to equal the production manifest's keys exactly, so a new
    # capability cannot arrive uncovered and a typo cannot quietly govern
    # nothing. `agent_output` governs the `agent.output` Event rather than a
    # rollup field, so its list is deliberately empty — stating that explicitly
    # is what makes the key equality meaningful.
    #
    # Built through an explicit list rather than array literals so a null is
    # recorded as a governed measurement instead of vanishing: a dropped null
    # would leave the "all must be null" check below vacuously satisfied.
    $Summary = $Rollup["summary"]
    $Governed = [ordered]@{}
    foreach (
        $Name in @(
            "agent_output",
            "structured_agent_events",
            "token_usage",
            "context_window",
            "skill_consultation",
            "cost"
        )
    ) {
        $Governed[$Name] = [Collections.Generic.List[object]]::new()
    }

    $Governed["structured_agent_events"].Add($Summary["tool_count"])
    foreach ($Name in @("model", "tokens_in", "tokens_out", "observed_tokens")) {
        $Governed["token_usage"].Add($Summary[$Name])
    }
    $Governed["context_window"].Add($Summary["peak_context_window"])
    foreach ($Name in @("skill_call_count", "skills_consulted")) {
        $Governed["skill_consultation"].Add($Summary[$Name])
    }
    $Governed["cost"].Add($Summary["cost_usd"])

    foreach ($Issue in @($Rollup["issues"])) {
        foreach ($Name in @("model", "tokens_in", "tokens_out")) {
            $Governed["token_usage"].Add($Issue["consumption"][$Name])
        }
        $Governed["context_window"].Add($Issue["peak_context_window"])
        $Governed["cost"].Add($Issue["cost_usd"])
    }
    return $Governed
}

function Assert-CapabilityDerivedTelemetry {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Rollup,
        [Parameter(Mandatory)]
        [string]$CaseId
    )

    # An unavailable measurement must be unavailable *because* this port
    # declares it so. The fixture comparison pins the nulls as literals, which
    # cannot tell a deliberately unknown measurement apart from one that was
    # simply forgotten — a coordinated edit that fabricates a value in
    # production and updates the fixture's `expected` to match passes it. So
    # derive the demand from the production Insight capability manifest
    # instead. A capability this port declares false MUST send every
    # measurement it governs as null; contract 12's value semantics forbid
    # reporting an unavailable counter as 0 or an unavailable collection as an
    # empty list. Flip a capability and this asks for the opposite.
    $Capabilities = Get-GitLoopyInsightCapabilities
    $Governed = Get-GovernedMeasurement -Rollup $Rollup

    [string[]]$CapabilityKeys = @(
        $Capabilities.Keys | ForEach-Object { [string]$_ }
    )
    [string[]]$GovernedKeys = @($Governed.Keys | ForEach-Object { [string]$_ })
    [Array]::Sort($CapabilityKeys, [StringComparer]::Ordinal)
    [Array]::Sort($GovernedKeys, [StringComparer]::Ordinal)
    Assert-Equal (
        [string]::Join(",", $CapabilityKeys)
    ) (
        [string]::Join(",", $GovernedKeys)
    ) "every Insight capability governs a declared measurement set: $CaseId"

    $DeclaredFalse = @(
        $Capabilities.Keys | Where-Object { -not $Capabilities[$_] }
    )
    Assert-True (
        $DeclaredFalse.Count -gt 0
    ) "this port must declare at least one unavailable capability: $CaseId"

    foreach ($Name in $DeclaredFalse) {
        foreach ($Value in $Governed[$Name]) {
            Assert-True (
                $null -eq $Value
            ) (
                "unavailable telemetry must stay null for declared-false " +
                "capability '$Name': $CaseId"
            )
        }
    }
}

function Assert-ObservedFact {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Rollup,
        [Parameter(Mandatory)]
        [string]$CaseId
    )

    # The complement: a fact this port really can observe is never nulled away
    # in the name of honesty. Without this, nulling the whole rollup would
    # satisfy the capability-derived guard above.
    Assert-True (
        $Rollup["outcome"] -is [string] -and $Rollup["outcome"].Length -gt 0
    ) "observed Iteration outcome must stay observed: $CaseId"
    Assert-True (
        Test-IsNumber $Rollup["duration_seconds"]
    ) "observed Iteration duration must stay observed: $CaseId"
    foreach ($Name in @("commits", "auto_closures", "pr_advances", "strikes")) {
        Assert-True (
            Test-IsNumber $Rollup["summary"][$Name]
        ) "observed accounting fact $Name must stay observed: $CaseId"
    }
    foreach ($Issue in @($Rollup["issues"])) {
        Assert-True (
            $null -ne $Issue["issue"]
        ) "an Active issue's identity must stay observed: $CaseId"
        Assert-True (
            $Issue["status"] -is [string] -and $Issue["status"].Length -gt 0
        ) "an Active issue's status must stay observed: $CaseId"
        Assert-True (
            $Issue["first_started_at"] -is [string]
        ) "an Active issue's first activation must stay observed: $CaseId"
        foreach ($Name in @("active_seconds", "cumulative_active_seconds")) {
            Assert-True (
                Test-IsNumber $Issue[$Name]
            ) "an Active issue's $Name must stay observed: $CaseId"
        }
    }
}

foreach (
    $Case in @(
        $Fixture["normalized_rollup_cases"] |
            Where-Object { $_["orchestrator"] -ceq "powershell" }
    )
) {
    $InputFacts = $Case["input"]
    if ($InputFacts.Contains("iterations")) {
        # A multi-Iteration case drives the stateful lifecycle accumulator — the
        # same production seam the Run loop uses — so per-issue cumulative Active
        # time, retroactive fallback binding, and first-activation identity are
        # pinned across Iterations rather than only inside one rollup call.
        # Each case is one Run, so it starts from the Run loop's own reset seam:
        # otherwise a later case inherits an earlier case's per-issue history and
        # the fixture would pin whatever order the cases happen to appear in.
        Reset-GitLoopyIterationLifecycleState
        $Actual = [object[]]@(
            foreach ($IterationFacts in $InputFacts["iterations"]) {
                foreach ($FixtureEvent in $IterationFacts["events"]) {
                    $Observed = $FixtureEvent["observed_monotonic"]
                    $LifecycleEvent = [ordered]@{}
                    foreach ($Key in $FixtureEvent.Keys) {
                        if ($Key -ceq "observed_monotonic") { continue }
                        $LifecycleEvent[$Key] = $FixtureEvent[$Key]
                    }
                    Update-GitLoopyIterationLifecycle `
                        -LifecycleEvent $LifecycleEvent `
                        -ObservedMonotonic $Observed
                }
                $Finish = $IterationFacts["finish"]
                $FinishArguments = @{
                    FinishedMonotonic = $Finish["finished_monotonic"]
                    Strikes = $Finish["strikes"]
                }
                foreach ($Name in @("commits", "auto_closures", "pr_advances")) {
                    if ($Finish.Contains($Name)) {
                        $Parameter = switch ($Name) {
                            "commits" { "Commits" }
                            "auto_closures" { "AutoClosures" }
                            "pr_advances" { "PrAdvances" }
                        }
                        $FinishArguments[$Parameter] = $Finish[$Name]
                    }
                }
                if ($Finish.Contains("terminal_outcome")) {
                    $FinishArguments["TerminalOutcome"] = $Finish["terminal_outcome"]
                }
                Get-GitLoopyCurrentIterationRollup @FinishArguments
            }
        )
        $ExpectedLines = @(
            $Case["expected"] | ForEach-Object {
                ConvertTo-GitLoopyJsonLine -Event (Add-RollupEnvelope -Rollup $_)
            }
        )
        $ActualLines = @(
            $Actual | ForEach-Object {
                ConvertTo-GitLoopyJsonLine -Event (Add-RollupEnvelope -Rollup $_)
            }
        )
        Assert-Equal (
            [string]::Join("", $ExpectedLines)
        ) (
            [string]::Join("", $ActualLines)
        ) "normalized rollup fixture: $($Case["id"])"
        foreach ($Rollup in $Actual) {
            Assert-CapabilityDerivedTelemetry `
                -Rollup $Rollup `
                -CaseId $Case["id"]
            Assert-ObservedFact -Rollup $Rollup -CaseId $Case["id"]
        }
        continue
    }
    $RollupArguments = @{
        IterationStartedMonotonic = $InputFacts["iteration_started_monotonic"]
        FinishedMonotonic = $InputFacts["finished_monotonic"]
        ActiveIssue = $InputFacts["active_issue"]
        ActiveStartedAt = (
            Get-GitLoopyIsoTimestamp -Timestamp $InputFacts["active_started_at"]
        )
        ActiveStartedMonotonic = $InputFacts["active_started_monotonic"]
        FirstStartedAt = (
            Get-GitLoopyIsoTimestamp -Timestamp $InputFacts["first_started_at"]
        )
        FirstStartedMonotonic = $InputFacts["first_started_monotonic"]
        PreviousCumulativeActiveSeconds = (
            $InputFacts["previous_cumulative_active_seconds"]
        )
        Commits = $InputFacts["commits"]
        AutoClosures = $InputFacts["auto_closures"]
        PrAdvances = $InputFacts["pr_advances"]
        Strikes = $InputFacts["strikes"]
    }
    if ($InputFacts.Contains("active_closed_at")) {
        $RollupArguments["ActiveClosedAt"] = Get-GitLoopyIsoTimestamp `
            -Timestamp $InputFacts["active_closed_at"]
        $RollupArguments["ActiveClosedMonotonic"] = (
            $InputFacts["active_closed_monotonic"]
        )
    }
    if ($InputFacts.Contains("terminal_outcome")) {
        $RollupArguments["TerminalOutcome"] = $InputFacts["terminal_outcome"]
    }
    $Actual = Get-GitLoopyIterationRollup @RollupArguments
    Assert-Equal (
        ConvertTo-GitLoopyJsonLine -Event (
            Add-RollupEnvelope -Rollup $Case["expected"]
        )
    ) (
        ConvertTo-GitLoopyJsonLine -Event (Add-RollupEnvelope -Rollup $Actual)
    ) "normalized rollup fixture: $($Case["id"])"
    Assert-CapabilityDerivedTelemetry -Rollup $Actual -CaseId $Case["id"]
    Assert-ObservedFact -Rollup $Actual -CaseId $Case["id"]
}

$GeneratedRunId = New-GitLoopyRunId
Assert-True (
    $GeneratedRunId -cmatch "^[0-9A-HJKMNP-TV-Z]{26}$"
) "generated run id is not a 26-character Crockford ULID"
Assert-True (
    (New-GitLoopyRunId -TimeMilliseconds 0).StartsWith("0000000000")
) "run id does not encode its millisecond timestamp as a ULID prefix"

$GeneratedTimestamp = Get-GitLoopyIsoTimestamp
Assert-True (
    $GeneratedTimestamp -cmatch "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
) "generated timestamp is not UTC ISO-8601 with millisecond precision"

$TempDir = Join-Path ([IO.Path]::GetTempPath()) "git-loopy-pwsh-$([guid]::NewGuid())"
[IO.Directory]::CreateDirectory($TempDir) | Out-Null

try {
    $FixedRunId = "01HXR0000000000000000000AA"
    $FixedStartedAt = [DateTimeOffset]::Parse(
        "2026-05-16T00:00:00.123Z",
        [Globalization.CultureInfo]::InvariantCulture
    )
    $Context = New-GitLoopyEventContext `
        -RepoRoot $TempDir `
        -RunId $FixedRunId `
        -StartedAt $FixedStartedAt

    $ExpectedReplay = Join-Path $TempDir (
        ".git-loopy/logs/2026-05-16T00-00-00Z-$FixedRunId.jsonl"
    )
    Assert-Equal $ExpectedReplay $Context.ReplayPath "contract replay path"
    Assert-True (
        -not [IO.File]::Exists($Context.ReplayPath)
    ) "event context created the replay file before the first record"

    $GhpSecret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    $GhoSecret = "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    $JwtSecret = "eyJ" + ("A" * 17) + "." + ("B" * 20) + "." + ("C" * 20)
    $AwsSecret = "AKIAABCDEFGHIJKLMNOP"
    $Payload = [ordered]@{
        content = "token=$GhpSecret"
        nested = [ordered]@{
            gho = $GhoSecret
            jwt = $JwtSecret
            aws = $AwsSecret
        }
        zeta = 2
        alpha = 1
    }

    $OriginalOut = [Console]::Out
    $StreamBuffer = [IO.StringWriter]::new(
        [Globalization.CultureInfo]::InvariantCulture
    )
    try {
        [Console]::SetOut($StreamBuffer)
        Write-GitLoopyEvent `
            -Context $Context `
            -Type "assistant.message" `
            -Iteration 1 `
            -Payload $Payload `
            -Timestamp ([DateTimeOffset]::Parse("2026-05-16T00:00:01.456Z"))
        Write-GitLoopyEvent `
            -Context $Context `
            -Type "wrapper.run.end" `
            -Payload ([ordered]@{ reason = "complete" }) `
            -Timestamp ([DateTimeOffset]::Parse("2026-05-16T00:00:02.789Z"))
    }
    finally {
        [Console]::SetOut($OriginalOut)
    }

    $Stream = $StreamBuffer.ToString()
    $Replay = [IO.File]::ReadAllText($Context.ReplayPath)
    Assert-Equal $Stream $Replay "stream and replay parity"
    foreach ($Secret in @($GhpSecret, $GhoSecret, $JwtSecret, $AwsSecret)) {
        Assert-True (
            -not $Stream.Contains($Secret)
        ) "stream leaked a known secret shape"
    }
    Assert-True (
        $Stream.Contains("<redacted-secret>")
    ) "stream did not contain the redaction sentinel"
    Assert-True (
        -not $Replay.Contains("`r")
    ) "replay must use platform-independent LF line endings"

    $Records = @(
        $Replay.Split("`n", [StringSplitOptions]::RemoveEmptyEntries) |
            ForEach-Object { $_ | ConvertFrom-Json -AsHashtable }
    )
    Assert-Equal 2 $Records.Count "replay record count"
    Assert-Equal $FixedRunId $Records[0]["run_id"] "record run id"
    Assert-Equal 1 $Records[0]["iter"] "Iteration value"
    Assert-Equal "assistant.message" $Records[0]["type"] "record type"
    Assert-Equal (
        "token=<redacted-secret>"
    ) $Records[0]["content"] "top-level secret redaction"
    foreach ($Name in @("gho", "jwt", "aws")) {
        Assert-Equal (
            "<redacted-secret>"
        ) $Records[0]["nested"][$Name] "nested $Name secret redaction"
    }
    Assert-True ($null -eq $Records[1]["iter"]) "run-scope Iteration must be null"

    $RejectedMalformedRunId = $false
    try {
        New-GitLoopyEventContext `
            -RepoRoot $TempDir `
            -RunId "not-a-run-id" `
            -StartedAt $FixedStartedAt | Out-Null
    }
    catch {
        $RejectedMalformedRunId = $true
    }
    Assert-True $RejectedMalformedRunId "malformed explicit run id was accepted"

    # The live-sink seam (PRD #173). The replay log is written unconditionally
    # and first, so it stays the authoritative record no matter what the live
    # destination does; this indirection only decides who *else* sees the same
    # bytes. `GitLoopy.Tui.psm1` points it at the TUI helper's stdin for an
    # interactive Run and points it back at stdout — permanently — the moment
    # that child stops reading.
    $SinkContext = New-GitLoopyEventContext `
        -RepoRoot (Join-Path $TempDir "sink") `
        -RunId "01HXR0000000000000000000AB" `
        -StartedAt $FixedStartedAt
    $Delivered = [Collections.Generic.List[string]]::new()
    $SinkOriginalOut = [Console]::Out
    $SinkBuffer = [IO.StringWriter]::new(
        [Globalization.CultureInfo]::InvariantCulture
    )
    try {
        [Console]::SetOut($SinkBuffer)
        Set-GitLoopyLiveSink -Sink { param($Line) $Delivered.Add($Line) }
        Write-GitLoopyEvent `
            -Context $SinkContext `
            -Type "wrapper.run.start" `
            -Timestamp ([DateTimeOffset]::Parse("2026-05-16T00:00:03.000Z"))
        Set-GitLoopyLiveSink -Sink $null
        Write-GitLoopyEvent `
            -Context $SinkContext `
            -Type "wrapper.run.end" `
            -Payload ([ordered]@{ reason = "complete" }) `
            -Timestamp ([DateTimeOffset]::Parse("2026-05-16T00:00:04.000Z"))
    }
    finally {
        Set-GitLoopyLiveSink -Sink $null
        [Console]::SetOut($SinkOriginalOut)
    }

    $SinkReplay = [IO.File]::ReadAllText($SinkContext.ReplayPath)
    Assert-Equal 1 $Delivered.Count "an installed sink receives exactly the live copy"
    Assert-True (
        $SinkReplay.StartsWith($Delivered[0], [StringComparison]::Ordinal)
    ) "the sink and the replay log carry the same bytes"
    Assert-True (
        -not $SinkBuffer.ToString().Contains(
            '"wrapper.run.start"', [StringComparison]::Ordinal
        )
    ) "an installed sink is the live destination, not a second one"
    Assert-True (
        $SinkBuffer.ToString().Contains(
            '"wrapper.run.end"', [StringComparison]::Ordinal
        )
    ) "clearing the sink restores stdout as the live destination"
    Assert-Equal (
        $SinkReplay
    ) ($Delivered[0] + $SinkBuffer.ToString()) "replay parity across a sink change"
}
finally {
    if ([IO.Directory]::Exists($TempDir)) {
        [IO.Directory]::Delete($TempDir, $true)
    }
}

Write-Output "PowerShell Event-schema conformance: ok"
