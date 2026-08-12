# Serial fixed-frontier Dispatch in the PowerShell Orchestrator (#266).
#
# §9 already decides *whether* one Action may be dispatched: `reconcile` returns at
# most one Dispatch authorization or exactly one typed stop, and the shared
# automation fixtures gate that decision through the real native entrypoint. What
# this suite pins is the half that comes before it --- the preflight in which a Run
# turns a resolved §10 authority into the one fixed Performer posture it will
# dispatch with, and refuses, before any Dispatch, an authority this distribution
# cannot honour.
#
# The authority is resolved through the real `git-loopy.ps1 continuation
# resolve-authority` boundary rather than hand-built, so the chain runs operator
# configuration -> native command -> preflight with no hand-asserted link.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$Entrypoint = Join-Path $PortDir "git-loopy.ps1"
Import-Module (Join-Path $PortDir "GitLoopy.Continuation.psm1") -Force
$Pwsh = (
    Get-Command pwsh -CommandType Application |
        Select-Object -First 1
).Source

function Assert-True {
    param(
        [Parameter(Mandatory)][bool]$Condition,
        [Parameter(Mandatory)][string]$Description
    )
    if (-not $Condition) {
        throw "FAIL: $Description"
    }
}

function Resolve-Authority {
    <#
    .SYNOPSIS
        One operator-declared source, resolved through the real native command.
    #>
    param(
        [Collections.IDictionary]$Ceilings = $null,
        [object]$Actor = "runner"
    )

    $Declared = [ordered]@{
        repositories = @("octo/example")
        targets = @()
        action_kinds = @()
        instruction_modes = @()
        effect_scopes = @("tracker-read")
    }
    if ($null -ne $Ceilings) {
        foreach ($Entry in $Ceilings.GetEnumerator()) {
            $Declared[[string]$Entry.Key] = @($Entry.Value)
        }
    }
    $Source = [ordered]@{
        source = "project"
        mode = "execute-frontier"
        trusted_producers = @("planner")
        ceilings = $Declared
    }
    if ($null -ne $Actor) {
        $Source["actor"] = [string]$Actor
    }
    $Request = [ordered]@{
        continuation_contract_version = "1.3"
        record_format = 1
        sources = @($Source)
    } | ConvertTo-Json -Compress -Depth 20

    $StdoutPath = [IO.Path]::GetTempFileName()
    try {
        $Request | & $Pwsh -NoLogo -NoProfile -File $Entrypoint `
            continuation resolve-authority > $StdoutPath
        $ExitCode = $LASTEXITCODE
        $Payload = Get-Content -LiteralPath $StdoutPath -Raw |
            ConvertFrom-Json -AsHashtable -DateKind String
        Assert-True ($ExitCode -eq 0) (
            "resolve-authority answered execute-frontier " +
            "(exit ${ExitCode}: $($Payload | ConvertTo-Json -Compress -Depth 20))"
        )
        return $Payload["result"]
    }
    finally {
        Remove-Item -LiteralPath $StdoutPath -Force -ErrorAction SilentlyContinue
    }
}

function Get-Refusal {
    <#
    .SYNOPSIS
        The message of the capability refusal the preflight raised, or `$null`.
    .DESCRIPTION
        The refusal type is matched by name rather than by `[type]`, because
        `Import-Module` publishes a module's functions and not its classes. The
        name still matters: it is the type the native command maps to the
        `unsupported_operation` error code, so a preflight that failed some other
        way must not read here as a clean refusal.
    #>
    param([Parameter(Mandatory)][scriptblock]$Action)

    try {
        & $Action | Out-Null
    }
    catch {
        $Exception = $_.Exception
        while ($null -ne $Exception -and
            $Exception.GetType().Name -cne "GitLoopyContinuationCapabilityUnsupported") {
            $Exception = $Exception.InnerException
        }
        if ($null -eq $Exception) { return $null }
        return [string]$Exception.Message
    }
    return $null
}

# --- The mode this distribution now serves -----------------------------------

# A distribution that implemented the mode without advertising it would be
# unreachable: `resolve-authority` consults the manifest and would refuse the very
# Run this ticket exists to make possible.
$Verdict = Get-GitLoopyContinuationVerification `
    -Manifest (Get-GitLoopyCapabilityManifest) -Name "execute-frontier"
Assert-True ([bool]$Verdict["satisfied"]) (
    "this distribution satisfies the execute-frontier capability profile"
)
Assert-True (@($Verdict["unsatisfied_requirements"]).Count -eq 0) (
    "the execute-frontier verdict leaves no requirement unsatisfied"
)

# Serial Dispatch is not a step towards concurrency a reader may extrapolate from:
# general concurrency needs issue-backed `parallel-safe` plus Prerequisite, Target
# and effect-scope checks no family member performs. The manifest is where that
# stays visible.
$Optional = (Get-GitLoopyCapabilityManifest)["optional_capabilities"]
Assert-True ($Optional["fixed_frontier_authorization"] -eq $true) (
    "fixed-frontier authorization is advertised beside the mode"
)
Assert-True ($Optional["concurrent_dispatch"] -eq $false) (
    "concurrent Dispatch stays unsupported beside a serial frontier"
)
Assert-True (
    @($Verdict["unsupported_optional_capabilities"]) -contains "concurrent_dispatch"
) "the execute-frontier verdict still names concurrent Dispatch unsupported"

# The mode the manifest advertises is the mode an operator's configuration
# resolves to, through the real entrypoint.
$Authority = Resolve-Authority
Assert-True ([string]$Authority["mode"] -ceq "execute-frontier") (
    "a Run resolves execute-frontier from configuration"
)
Assert-True ([bool]$Authority["participates"]) (
    "an execute-frontier authority participates"
)

# --- Preflight: the Run declares what it is, and refuses what it cannot serve ---

# A closed-world posture: one identity, and only the Instruction mode it handles.
# The Orchestrator drives a noninteractive Copilot session, so a `skill`
# Instruction is something it can genuinely execute. `command` and `manual` are
# not, and §9 reads silence as universal competence --- so the claim is made
# explicitly and narrowly rather than left to be inferred.
$Plan = New-GitLoopyFrontierPlan -Authority $Authority
Assert-True ([string]$Plan["performer"]["id"] -ceq "runner") (
    "the Performer speaks as the configured actor"
)
Assert-True (
    (@($Plan["performer"]["posture"]["instruction_modes"]) -join ",") -ceq "skill"
) "the Performer claims only the Instruction mode this distribution handles"
Assert-True ([bool]$Plan["performer"]["posture"]["noninteractive"]) (
    "the Performer is declared noninteractive"
)
Assert-True ((@($Plan["repositories"]) -join ",") -ceq "octo/example") (
    "the plan carries the repositories the authority allows"
)
Assert-True ((@($Plan["trusted_producers"]) -join ",") -ceq "planner") (
    "the plan carries the authority's trusted Producers"
)
Assert-True ((@($Plan["effect_kinds"]) -join ",") -ceq "tracker-read") (
    "the plan carries the authority's effect scopes"
)

# Dispatch evidence is bound to the actor that writes it, so there must be one.
# §10 makes `actor` optional because report mode never writes; an execute-frontier
# Run does --- `record-dispatch-result` requires the authenticated actor to be the
# Performer the record names. A Run that discovered that at the moment it had to
# record a safety-case violation would lose the one record a human needs.
$Refusal = Get-Refusal { New-GitLoopyFrontierPlan -Authority (Resolve-Authority -Actor $null) }
Assert-True ($null -ne $Refusal) "an execute-frontier Run without an actor refuses to start"
Assert-True ($Refusal.Contains("actor")) "the actor refusal names the actor"

# §10's ceiling intersects the closed world; it never adds a handler to it.
$Narrowed = New-GitLoopyFrontierPlan -Authority (
    Resolve-Authority -Ceilings ([ordered]@{ instruction_modes = @("skill", "command") })
)
Assert-True (
    (@($Narrowed["performer"]["posture"]["instruction_modes"]) -join ",") -ceq "skill"
) "an Instruction-mode ceiling narrows the posture it cannot widen"

# A Performer with no handler left cannot dispatch anything, and says so once.
$Refusal = Get-Refusal {
    New-GitLoopyFrontierPlan -Authority (
        Resolve-Authority -Ceilings ([ordered]@{ instruction_modes = @("command") })
    )
}
Assert-True ($null -ne $Refusal) (
    "a ceiling that excludes every handled Instruction mode refuses to start"
)
Assert-True ($Refusal.Contains("instruction_modes")) (
    "the Instruction-mode refusal names the ceiling axis"
)

# An unenforceable cap is refused, never accepted and quietly ignored. §9 derives
# eligibility from coverage, grants and Performer posture; it has no input for an
# Action-kind or Target cap, so this distribution cannot honour one. An operator
# who capped kinds and got a Run that dispatched every kind would have been told
# their authority was narrower than it was.
foreach ($Axis in @("action_kinds", "targets")) {
    $Narrower = @{
        action_kinds = @("Implement ticket")
        targets = @("issue")
    }[$Axis]
    $Refusal = Get-Refusal {
        New-GitLoopyFrontierPlan -Authority (
            Resolve-Authority -Ceilings ([ordered]@{ $Axis = $Narrower })
        )
    }
    Assert-True ($null -ne $Refusal) (
        "a $Axis ceiling this distribution cannot apply refuses to start"
    )
    Assert-True ($Refusal.Contains($Axis)) "the $Axis refusal names the ceiling axis"
}

# A Run that is not in execute-frontier mode has no frontier to plan. Refusing
# here keeps the preflight from manufacturing a Dispatch posture for a `report`
# authority that was never granted execution.
$ReportAuthority = $Authority | ConvertTo-Json -Compress -Depth 20 |
    ConvertFrom-Json -AsHashtable -DateKind String
$ReportAuthority["mode"] = "report"
$Refusal = Get-Refusal { New-GitLoopyFrontierPlan -Authority $ReportAuthority }
Assert-True ($null -ne $Refusal) "a report-mode authority cannot plan a frontier"
Assert-True ($Refusal.Contains("execute-frontier")) (
    "the mode refusal names the mode a frontier plan requires"
)

# A ceiling axis the authority never declared is absent, not a cap of nothing.
# The native `resolve-authority` result always carries all five axes, but the
# preflight accepts any resolved authority, and reading an absent axis as a
# one-element list would refuse a Run whose operator capped nothing at all.
$Sparse = $Authority | ConvertTo-Json -Compress -Depth 20 |
    ConvertFrom-Json -AsHashtable -DateKind String
foreach ($Axis in @("action_kinds", "targets", "instruction_modes", "effect_scopes")) {
    $Sparse["ceilings"].Remove($Axis)
}
$SparsePlan = New-GitLoopyFrontierPlan -Authority $Sparse
Assert-True (
    (@($SparsePlan["performer"]["posture"]["instruction_modes"]) -join ",") -ceq "skill"
) "an absent Instruction-mode ceiling caps nothing"
Assert-True (@($SparsePlan["effect_kinds"]).Count -eq 0) (
    "an absent effect-scope ceiling is an empty list, not a list holding nothing"
)

# `-eq` compares arrays element-wise in PowerShell, so a requirement that merely
# asked "does this equal `$true`" would accept a flag whose value is a list that
# happens to contain `$true`. A manifest that malformed is not serving the mode.
foreach ($Malformed in @(
    @{ Section = "continuation_modes"; Key = "execute-frontier" },
    @{ Section = "optional_capabilities"; Key = "fixed_frontier_authorization" }
)) {
    $Broken = (Get-GitLoopyCapabilityManifest) |
        ConvertTo-Json -Compress -Depth 20 |
        ConvertFrom-Json -AsHashtable -DateKind String
    $Broken[$Malformed.Section][$Malformed.Key] = @($true, $false)
    $BrokenVerdict = Get-GitLoopyContinuationVerification `
        -Manifest $Broken -Name "execute-frontier"
    Assert-True (-not [bool]$BrokenVerdict["satisfied"]) (
        "a list-valued $($Malformed.Key) does not satisfy the execute-frontier profile"
    )
}


# --- An unadvertised mode fails closed (#267) --------------------------------
#
# Before the family-wide rollout gate this was a Conformance scenario, driven by
# pointing it at whichever member had not yet implemented `execute-frontier`.
# Every member advertises it now, so no distribution can play that part and the
# fixture cannot ask the question at all. The refusal is a property of the native
# command rather than of a member's backlog, so it is pinned family-locally
# instead --- against a manifest doctored to withhold the mode, exactly as the
# Python Runner's `test_a_mode_this_distribution_does_not_advertise_fails_closed`
# and the shell suite's counterpart do. `Get-GitLoopyCapabilityManifest` hands
# back the live nested sections, so withdrawing the one advertisement leaves the
# rest of the manifest the real one: nothing here answers from a stub.
$Modes = (Get-GitLoopyCapabilityManifest)["continuation_modes"]
Assert-True ($Modes["execute-frontier"] -eq $true) (
    "this distribution advertises execute-frontier before it is withdrawn"
)

$UnadvertisedRequest = [ordered]@{
    continuation_contract_version = "1.3"
    record_format = 1
    sources = @(
        [ordered]@{
            source = "global"
            mode = "execute-frontier"
            trusted_producers = @("planner")
            ceilings = [ordered]@{
                repositories = @("octo/example")
                targets = @("issue")
                action_kinds = @("Implement ticket")
                instruction_modes = @("skill")
                effect_scopes = @("tracker-read")
            }
        }
    )
}
$RequestPath = Join-Path ([IO.Path]::GetTempPath()) (
    "git-loopy-unadvertised-$([Guid]::NewGuid().ToString('n')).json"
)
$UnadvertisedRequest | ConvertTo-Json -Compress -Depth 20 |
    Set-Content -LiteralPath $RequestPath -Encoding utf8 -NoNewline

$SavedOut = [Console]::Out
$SavedError = [Console]::Error
$CapturedOut = [IO.StringWriter]::new()
$CapturedError = [IO.StringWriter]::new()
try {
    $Modes["execute-frontier"] = $false
    [Console]::SetOut($CapturedOut)
    [Console]::SetError($CapturedError)
    $ExitCode = Invoke-GitLoopyContinuationMain -Arguments @(
        "resolve-authority", "--input", $RequestPath
    )
}
finally {
    [Console]::SetOut($SavedOut)
    [Console]::SetError($SavedError)
    $Modes["execute-frontier"] = $true
    Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
}

Assert-True ($ExitCode -eq 1) (
    "resolve-authority exits 1 for a mode this distribution does not advertise"
)
$Refused = $CapturedOut.ToString() | ConvertFrom-Json -AsHashtable -DateKind String
Assert-True (-not [bool]$Refused["ok"]) "an unadvertised mode is refused"
Assert-True ([string]$Refused["operation"] -ceq "resolve-authority") (
    "the refusal names the operation it refused"
)
Assert-True ([string]$Refused["error"]["code"] -ceq "unsupported_operation") (
    "an unadvertised mode is an unsupported operation, not an invalid request"
)
Assert-True (
    [string]$Refused["error"]["message"] -ceq (
        "continuation mode execute-frontier is not supported by this distribution"
    )
) "the refusal names the mode this distribution does not advertise"
Assert-True ($CapturedError.ToString().Contains("execute-frontier")) (
    "the refusal names the mode on stderr"
)

# The withdrawal is undone, so nothing after this point reads a doctored manifest.
Assert-True (
    (Get-GitLoopyCapabilityManifest)["continuation_modes"]["execute-frontier"] -eq $true
) "the advertisement is restored"

[Console]::Out.WriteLine("PowerShell Continuation frontier: ok")
