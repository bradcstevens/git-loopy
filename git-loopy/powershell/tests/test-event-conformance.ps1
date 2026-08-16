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
$DashboardFixturePath = Join-Path (
    Split-Path -Parent $PortDir
) "conformance/dashboard-insights.json"
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

# #334: the run-scoped Rate-card capability (ADR-0026, Wrapper contract 12).
# This port reads no model listing, so it resolves no **Rate card** on any Run
# and declares the capability false with an explicit `null` card beside it.
# Pinned as a *relationship* between the fixture and this port's production
# values rather than as a second copy of the literal, so the two cannot end up
# agreeing only with themselves.
$RunScoped = $Fixture["insight_capabilities"]["run_scoped"]
$ActualRunScoped = Get-GitLoopyRunScopedInsightCapabilities
Assert-Equal (
    (@($RunScoped["names"]) | Sort-Object) -join ","
) ((@($ActualRunScoped.Keys) | Sort-Object) -join ",") (
    "run-scoped Insight capability names"
)
foreach ($Name in $ActualRunScoped.Keys) {
    # A JSON Boolean, not merely something that compares equal to one:
    # PowerShell's loose equality makes `0 -eq $false` true, so without the type
    # check a fabricated numeric zero would read as a truthful "unavailable".
    Assert-True ($ActualRunScoped[$Name] -is [bool]) (
        "run-scoped Insight capability $Name is a boolean"
    )
    Assert-Equal $false $ActualRunScoped[$Name] (
        "run-scoped Insight capability $Name"
    )
}
Assert-True (
    @($RunScoped["declared_by"]) -contains "powershell"
) "PowerShell declares the run-scoped Insight capabilities"
Assert-True (
    @($RunScoped["never_resolved_by"]) -contains "powershell"
) "PowerShell resolves no Rate card on any Run"
# A false declaration publishes an explicit `null`, never an empty card: an
# empty card is a record nothing can be audited against.
Assert-True ($null -eq (Get-GitLoopyRateCard)) "PowerShell publishes no Rate card"
# The wire manifest is exactly the frozen per-distribution keys plus the
# run-scoped ones -- a port may not smuggle a run-scoped answer into the frozen
# manifest, nor drop one on its way to `wrapper.run.start`.
$ExpectedWireKeys = @($Fixture["insight_capabilities"]["names"]) +
    @($RunScoped["names"])
Assert-Equal (
    ($ExpectedWireKeys | Sort-Object) -join ","
) ((@((Get-GitLoopyRunInsightCapabilities).Keys) | Sort-Object) -join ",") (
    "Run-start Insight manifest keys"
)

# #311 AC3: Parallel mode is declared, never inferred from silence. This port
# has no rolling scheduler, so `parallel_mode` is false -- and a port that
# cannot fill a second Lane cannot honour refill, backlog, adaptation, or the
# contribution stream either, so the whole manifest is false with it.
$ExpectedParallel = $Fixture["parallel_capabilities"]["orchestrators"]["powershell"]
$ActualParallel = Get-GitLoopyParallelCapabilities
Assert-Equal $ExpectedParallel.Count $ActualParallel.Count (
    "parallel capability count"
)
Assert-Equal (
    ($Fixture["parallel_capabilities"]["names"] -join ",")
) (($ActualParallel.Keys -join ",")) "parallel capability names and order"
foreach ($Name in $ExpectedParallel.Keys) {
    Assert-True $ActualParallel.Contains($Name) "missing parallel capability $Name"
    Assert-Equal $ExpectedParallel[$Name] $ActualParallel[$Name] (
        "parallel capability $Name"
    )
    Assert-True ($ActualParallel[$Name] -is [bool]) (
        "parallel capability $Name must be boolean"
    )
}
if (-not $ActualParallel["parallel_mode"]) {
    foreach ($Name in $ActualParallel.Keys) {
        Assert-True (-not $ActualParallel[$Name]) (
            "parallel_mode is false so $Name cannot be advertised"
        )
    }
}

# The manifest is a claim about this port's own code, so read the code. A
# distribution declaring `contribution_events: false` must name no producer for
# the Lane-contribution lifecycle literals.
if (-not $ActualParallel["contribution_events"]) {
    $ModuleSource = (
        Get-ChildItem -LiteralPath $PortDir -Filter "*.psm1" |
            ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw }
    ) -join "`n"
    foreach ($Literal in $Fixture["contribution_identity"]["lifecycle_types"]) {
        $Key = @(
            $Fixture["event_types"].GetEnumerator() |
                Where-Object { $_.Value -ceq $Literal }
        )[0].Key
        Assert-True (
            -not ($ModuleSource -match [regex]::Escape("EventTypes[`"$Key`"]"))
        ) "contribution_events is declared false but $Key has a producer"
    }
}

# A refusal an operator can act on, instead of a Lane cap accepted and ignored.
$CapturedError = [IO.StringWriter]::new()
$OriginalError = [Console]::Error
[Console]::SetError($CapturedError)
try {
    $Refusal = Assert-GitLoopyParallelSupported -Environment @{
        GIT_LOOPY_MAX_PARALLEL = "3"
    }
    foreach ($Accepted in @($null, "", "0", "1", "00", "01")) {
        Assert-True (
            Assert-GitLoopyParallelSupported -Environment @{
                GIT_LOOPY_MAX_PARALLEL = $Accepted
            }
        ) "a serial Lane cap of '$Accepted' must be accepted"
    }
    # A cap this port cannot honour stays refused however it is written, and a
    # value it cannot even read is a rejection rather than a silent zero.
    foreach ($Refused in @("2", "08", "010", "18446744073709551617", "-1", "1.5", "two")) {
        Assert-True (
            -not (
                Assert-GitLoopyParallelSupported -Environment @{
                    GIT_LOOPY_MAX_PARALLEL = $Refused
                }
            )
        ) "a Lane cap of '$Refused' must not be accepted"
    }
    # A missing key is an unset cap, not a strict-mode failure.
    Assert-True (
        Assert-GitLoopyParallelSupported -Environment @{}
    ) "an absent GIT_LOOPY_MAX_PARALLEL must be accepted"
} finally {
    [Console]::SetError($OriginalError)
}
Assert-True (-not $Refusal) "a Lane cap above 1 must be refused, not accepted"
$RefusalText = $CapturedError.ToString()
Assert-True ($RefusalText -match "parallel_mode") (
    "refusal must name the unsupported capability"
)
Assert-True ($RefusalText -match "PowerShell") (
    "refusal must name the distribution that cannot honour the request"
)
Assert-True ($RefusalText -match "GIT_LOOPY_MAX_PARALLEL") (
    "refusal must name the setting the operator can change"
)

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

# #311 AC2: the rolling Event stream, driven through this port's own
# serializer. This port schedules no Lane, but it is still a family member that
# has to read and write the same bytes -- a drifted literal or a re-sorted
# payload key would make its replay logs unreadable to every other member the
# day Parallel mode does arrive here.
$RollingRecords = 0
foreach ($Case in $Fixture["rolling_stream_cases"]) {
    if ($Case["distributions"] -notcontains "powershell") {
        continue
    }
    Assert-Equal $Case["events"].Count $Case["jsonl"].Count (
        "rolling stream $($Case["id"]) pins one line per record"
    )
    for ($Index = 0; $Index -lt $Case["events"].Count; $Index++) {
        $Actual = ConvertTo-GitLoopyJsonLine -Event $Case["events"][$Index]
        Assert-Equal $Case["jsonl"][$Index] $Actual (
            "rolling stream: $($Case["id"]) record $Index"
        )
        $RollingRecords++
    }
}
Assert-True ($RollingRecords -gt 0) (
    "no rolling stream case names the powershell distribution"
)

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

function New-RollupFromInput {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$InputFacts
    )

    # One fixture `input` object driven through the real rollup seam.
    $FirstStartedAt = $InputFacts["active_started_at"]
    if ($InputFacts.Contains("first_started_at")) {
        $FirstStartedAt = $InputFacts["first_started_at"]
    }
    $FirstStartedMonotonic = $InputFacts["active_started_monotonic"]
    if ($InputFacts.Contains("first_started_monotonic")) {
        $FirstStartedMonotonic = $InputFacts["first_started_monotonic"]
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
            Get-GitLoopyIsoTimestamp -Timestamp $FirstStartedAt
        )
        FirstStartedMonotonic = $FirstStartedMonotonic
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
    return Get-GitLoopyIterationRollup @RollupArguments
}

function ConvertTo-CanonicalJson {
    param(
        [AllowNull()]
        [object]$Value
    )

    # Key-order-independent, integer/double-independent structural rendering.
    # Rollup key order is already pinned by `normalized_rollup_cases`; the
    # Dashboard seam pins semantic content, and the shared fixture arrives
    # through `ConvertFrom-Json` whose key order is not a contract.
    if ($null -eq $Value) { return "null" }
    if ($Value -is [bool]) {
        if ($Value) { return "true" }
        return "false"
    }
    if ($Value -is [string]) {
        return ConvertTo-Json -InputObject $Value -Compress
    }
    if ($Value -is [DateTime] -or $Value -is [DateTimeOffset]) {
        # `ConvertFrom-Json` decodes the fixture's RFC3339 literals into date
        # values. Normalize only the decoded side: an actual timestamp that the
        # producer emitted as a culture-dependent string stays a string here and
        # still fails the comparison.
        return ConvertTo-Json -InputObject (
            Get-GitLoopyIsoTimestamp -Timestamp $Value
        ) -Compress
    }
    if ($Value -is [Collections.IDictionary]) {
        $Parts = @(
            foreach ($Key in @($Value.Keys | Sort-Object -CaseSensitive)) {
                (ConvertTo-Json -InputObject ([string]$Key) -Compress) +
                    ":" + (ConvertTo-CanonicalJson -Value $Value[$Key])
            }
        )
        return "{" + [string]::Join(",", $Parts) + "}"
    }
    if ($Value -is [Collections.IEnumerable]) {
        $Parts = @(
            foreach ($Item in $Value) { ConvertTo-CanonicalJson -Value $Item }
        )
        return "[" + [string]::Join(",", $Parts) + "]"
    }
    return [string]([double]$Value)
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
    # rollup field, and `routing` governs the Routing resolution on
    # `wrapper.pickup.bound`, so both lists are deliberately empty — stating
    # that explicitly is what makes the key equality meaningful.
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
            "cost",
            "routing"
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

function Assert-NoBillingFigure {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Rollup,
        [Parameter(Mandatory)]
        [string]$CaseId
    )

    # #334 AC3: billing telemetry arrives on the SDK event stream, which this
    # port does not subscribe to, so it emits no Credits, premium-request or
    # cache-split figure at all. Those keys are *omitted* rather than nulled —
    # that omission is what makes them additive for the reference Orchestrator —
    # so their absence at any depth is the assertion. A fabricated 0 here would
    # say the Iteration was free rather than that this port cannot see what it
    # cost, and the fixture comparison alone cannot catch a coordinated edit
    # that adds the key to both production and `expected`.
    $Forbidden = @("credits", "premium_requests", "cache_read", "cache_write")
    $Pending = [Collections.Generic.Queue[object]]::new()
    $Pending.Enqueue($Rollup)
    while ($Pending.Count -gt 0) {
        $Node = $Pending.Dequeue()
        if ($Node -is [Collections.IDictionary]) {
            foreach ($Key in @($Node.Keys)) {
                Assert-True (
                    -not ($Forbidden -ccontains [string]$Key)
                ) (
                    "this port must emit no billing figure it cannot " +
                    "observe ('$Key'): $CaseId"
                )
                $Pending.Enqueue($Node[$Key])
            }
            continue
        }
        if ($Node -is [Collections.IEnumerable] -and $Node -isnot [string]) {
            foreach ($Item in $Node) { $Pending.Enqueue($Item) }
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
            Assert-NoBillingFigure -Rollup $Rollup -CaseId $Case["id"]
        }
        continue
    }
    $Actual = New-RollupFromInput -InputFacts $InputFacts
    Assert-Equal (
        ConvertTo-GitLoopyJsonLine -Event (
            Add-RollupEnvelope -Rollup $Case["expected"]
        )
    ) (
        ConvertTo-GitLoopyJsonLine -Event (Add-RollupEnvelope -Rollup $Actual)
    ) "normalized rollup fixture: $($Case["id"])"
    Assert-CapabilityDerivedTelemetry -Rollup $Actual -CaseId $Case["id"]
    Assert-ObservedFact -Rollup $Actual -CaseId $Case["id"]
    Assert-NoBillingFigure -Rollup $Actual -CaseId $Case["id"]
}

# The renderer-neutral Dashboard seam is only anti-drift if the native trace it
# pins is one this port can actually emit. Every native Dashboard case declares
# the producer input behind each `wrapper.iteration.end`, and the real rollup
# seam must reproduce that Event's rollup payload exactly.
$DashboardFixture = Get-Content -LiteralPath $DashboardFixturePath -Raw |
    ConvertFrom-Json -AsHashtable
$DashboardRollups = 0
$NativeRunStarts = 0
foreach ($Case in $DashboardFixture["cases"]) {
    if (-not $Case.Contains("producer_rollups")) { continue }
    $DeclaresThisPort = @(
        $Case["producer_rollups"] |
            Where-Object { @($_["distributions"]) -ccontains "powershell" }
    ).Count -gt 0
    if ($DeclaresThisPort) {
        # The same demand one Event earlier: a native case's
        # `wrapper.run.start` must declare what this port really declares.
        # Without this the Dashboard fixture could pin a native trace whose
        # capability manifest no port emits, and every consumer would agree
        # with a producer that does not exist.
        $RunStart = $Case["events"][0]
        Assert-Equal "wrapper.run.start" $RunStart["type"] (
            "native Dashboard case opens on a Run start: $($Case["id"])"
        )
        Assert-Equal (
            ConvertTo-CanonicalJson -Value (Get-GitLoopyRunInsightCapabilities)
        ) (
            ConvertTo-CanonicalJson -Value $RunStart["insight_capabilities"]
        ) (
            "native Dashboard Run start declares a manifest this port " +
            "cannot emit: $($Case["id"])"
        )
        Assert-True (
            $RunStart.Contains("rate_card")
        ) "native Dashboard Run start publishes a Rate-card key: $($Case["id"])"
        Assert-True (
            (Get-GitLoopyRateCard) -eq $RunStart["rate_card"]
        ) "native Dashboard Run start publishes this port's Rate card: $($Case["id"])"
        $NativeRunStarts++
    }
    foreach ($Rollup in $Case["producer_rollups"]) {
        if (-not (@($Rollup["distributions"]) -ccontains "powershell")) {
            continue
        }
        $EventIndex = $Rollup["event_index"]
        $FixtureEvent = $Case["events"][$EventIndex]
        $Expected = [ordered]@{}
        foreach ($Key in @("outcome", "duration_seconds", "summary", "issues")) {
            $Expected[$Key] = $FixtureEvent[$Key]
        }
        $Actual = New-RollupFromInput -InputFacts $Rollup["input"]
        # The complete rollup, not a projection of it: an extra field the
        # producer grew would otherwise be filtered away before comparison.
        Assert-Equal (
            ConvertTo-CanonicalJson -Value $Expected
        ) (
            ConvertTo-CanonicalJson -Value $Actual
        ) "dashboard producer rollup: $($Case["id"]) event $EventIndex"
        $DashboardRollups++
    }
}
Assert-True (
    $DashboardRollups -gt 0
) "no native Dashboard case declares a PowerShell producer rollup"
Assert-True (
    $NativeRunStarts -gt 0
) "no native Dashboard case pins a PowerShell Run start"

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
