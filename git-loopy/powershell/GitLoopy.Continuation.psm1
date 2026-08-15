Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# The manifest carries this clone's Release version, so a Consumer that imports
# this module alone — setup verification, which is not an Orchestrator Run —
# resolves it through the same single authority the Orchestrator uses.
if (-not (Get-Command Get-GitLoopyReleaseVersion -ErrorAction SilentlyContinue)) {
    Import-Module (Join-Path $PSScriptRoot "GitLoopy.Release.psm1")
}

class GitLoopyContinuationRejection : System.Exception {
    GitLoopyContinuationRejection([string]$Message) : base($Message) {}
}

class GitLoopyContinuationRepairRequired : System.Exception {
    GitLoopyContinuationRepairRequired([string]$Message) : base($Message) {}
}

class GitLoopyContinuationCapabilityUnsupported : System.Exception {
    GitLoopyContinuationCapabilityUnsupported([string]$Message) : base($Message) {}
}

class GitLoopyContinuationGitHubException : System.Exception {
    [string]$Context
    [string]$StderrTail

    GitLoopyContinuationGitHubException([string]$Context) : base(
        "GitHub operation failed while $Context"
    ) {
        $this.Context = $Context
        $this.StderrTail = ""
    }

    GitLoopyContinuationGitHubException(
        [string]$Context,
        [string]$StderrTail
    ) : base(
        "GitHub operation failed while $Context"
    ) {
        $this.Context = $Context
        $this.StderrTail = $StderrTail
    }
}

$Script:ContinuationContractVersion = "1.3"
$Script:SupportedContinuationContractVersions = @("1.0", "1.1", "1.2", "1.3")
$Script:SafetyCaseContractVersion = "1.2"
$Script:RecordFormat = 1
$Script:WrapperContractVersion = "1.17"
$Script:EventSchemaVersion = "1.1"

$Script:IndexLabel = "git-loopy-continuation"
$Script:RecordMarker = "<!-- git-loopy-continuation:1 -->"
$Script:MaxInteger = [System.Numerics.BigInteger]::Pow(2, 53) - 1
$Script:MaxDepth = 16
$Script:MaxArrayLength = 256
$Script:MaxStringBytes = 8 * 1024
$Script:MaxRecordBytes = 48 * 1024
$Script:MaxCarrierBodyBytes = 64 * 1024
# `\A`/`\z` rather than `^`/`$`: both jq's Oniguruma and .NET let `$` match
# before a terminal newline, which would accept a digest with a trailing "\n"
# that Python's `fullmatch` rejects.
$Script:DigestPattern = "\A[0-9a-f]{64}\z"
$Script:WritePermissions = @("ADMIN", "MAINTAIN", "WRITE")

$Script:Publications = @("ephemeral", "shared")
$Script:Dispositions = @("continue", "no-guidance", "terminal")
$Script:InteractionClassifications = @("AFK-safe", "HITL-required")
$Script:HumanBoundaryReasons = @(
    "consent-required",
    "credential-required",
    "human-decision",
    "physical-interaction",
    "privilege-expansion",
    "scope-ambiguity",
    "subjective-validation"
)
$Script:AnyInteraction = $Script:InteractionClassifications
$Script:HitlOnly = @("HITL-required")
$Script:ActionKindSchemas = [ordered]@{
    "Address review findings" = $Script:AnyInteraction
    "Authorize operation" = $Script:HitlOnly
    "Chart workstream" = $Script:HitlOnly
    "Close parent" = $Script:AnyInteraction
    "Decompose spec" = $Script:AnyInteraction
    "Implement ticket" = $Script:AnyInteraction
    "Perform manual validation" = $Script:HitlOnly
    "Prototype evidence" = $Script:AnyInteraction
    "Provide information" = $Script:HitlOnly
    "Publish head" = $Script:AnyInteraction
    "Publish spec" = $Script:AnyInteraction
    "Research fact" = $Script:AnyInteraction
    "Resolve conflict" = $Script:AnyInteraction
    "Resolve decision" = $Script:HitlOnly
    "Review and merge PR" = $Script:HitlOnly
    "Review head" = $Script:AnyInteraction
    "Triage item" = $Script:AnyInteraction
}
$Script:ActionKinds = @($Script:ActionKindSchemas.Keys)

$Script:ReferenceFields = [ordered]@{
    "issue" = @("repository", "number")
    "pull-request" = @("repository", "number")
    "issue-comment" = @("repository", "issue", "comment_id")
    "pull-request-review" = @("repository", "pull_request", "review_id")
    "commit" = @("repository", "sha")
    "branch" = @("repository", "name", "sha")
}
$Script:ReferenceKinds = @($Script:ReferenceFields.Keys)

$Script:InteractionEvidenceSchemas = [ordered]@{
    "human-boundary" = [ordered]@{
        classifications = $Script:HitlOnly
        required_fields = @("kind", "reason", "resolution_condition")
        optional_fields = @("advisory_extensions")
        string_fields = @()
        condition_fields = @("resolution_condition")
        bound_fields = [ordered]@{}
        enum_fields = [ordered]@{ reason = $Script:HumanBoundaryReasons }
    }
    "transition-owner-attestation" = [ordered]@{
        classifications = @("AFK-safe")
        required_fields = @("kind", "noninteractive", "owner")
        optional_fields = @("advisory_extensions")
        string_fields = @("owner")
        condition_fields = @()
        bound_fields = [ordered]@{ owner = "completion.transition.owner" }
        enum_fields = [ordered]@{ noninteractive = @($true) }
    }
}
$Script:OutcomeKinds = @("complete", "rejected", "abandoned", "superseded")
$Script:RetirementReasons = @(
    "completed", "lost-basis", "workstream-outcome", "supersession"
)
$Script:NoGuidanceReasons = @("no-successor-created", "ephemeral-only")
$Script:TerminalRemainderRows = 3
# A trustworthy, complete all-state read is what makes terminal completion
# provable. Any one of these diagnostics says the read is not trustworthy, so
# the projection may not claim closed coverage over it.
$Script:CoverageUncertaintyCodes = @(
    "invalid_revision",
    "missing_predecessor",
    "missing_retirement_receipt",
    "mutated_revision",
    "retired_occurrence_resurrected",
    "revision_fork"
)

$Script:ConditionOptionalFields = @("advisory_extensions")
$Script:TargetConditionFields = @("kind", "target")
$Script:ConditionSchemas = [ordered]@{
    "action-completed" = [ordered]@{
        required_fields = @("kind", "action_key")
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @("action_key")
        local_reference_field = "action_key"
        target_kinds = @()
        enum_fields = [ordered]@{}
    }
    "artifact-exists" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = $Script:ReferenceKinds
        enum_fields = [ordered]@{}
    }
    "branch-head-equals" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("branch")
        enum_fields = [ordered]@{}
    }
    "commit-exists" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("commit")
        enum_fields = [ordered]@{}
    }
    "dependency-satisfied" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("issue")
        enum_fields = [ordered]@{}
    }
    "issue-closed" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("issue")
        enum_fields = [ordered]@{}
    }
    "issue-label-present" = [ordered]@{
        required_fields = @("kind", "target", "label")
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @("label")
        local_reference_field = $null
        target_kinds = @("issue")
        enum_fields = [ordered]@{}
    }
    "issue-open" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("issue")
        enum_fields = [ordered]@{}
    }
    "pull-request-closed" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("pull-request")
        enum_fields = [ordered]@{}
    }
    "pull-request-merged" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("pull-request")
        enum_fields = [ordered]@{}
    }
    "pull-request-open" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("pull-request")
        enum_fields = [ordered]@{}
    }
    "pull-request-review-state" = [ordered]@{
        required_fields = @("kind", "target", "state")
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("pull-request-review")
        enum_fields = [ordered]@{
            state = @("approved", "changes-requested", "commented")
        }
    }
    "sub-issues-complete" = [ordered]@{
        required_fields = $Script:TargetConditionFields
        optional_fields = $Script:ConditionOptionalFields
        string_fields = @()
        local_reference_field = $null
        target_kinds = @("issue")
        enum_fields = [ordered]@{}
    }
}
$Script:ConditionKinds = @($Script:ConditionSchemas.Keys)
$Script:EffectKinds = @(
    "external-write",
    "git-read",
    "git-write",
    "network-read",
    "repository-read",
    "repository-write",
    "tracker-read",
    "tracker-write"
)
$Script:RequirementKinds = @(
    "access", "capability", "command", "evaluator", "policy", "skill"
)
$Script:TriggerKinds = $Script:HumanBoundaryReasons
# The typed safety assumptions an AFK safety case may declare. A Transition
# owner states why unattended completion is safe; free prose would let a
# Producer justify anything, so the vocabulary is closed and pinned by the
# shared fixture.
$Script:AssumptionKinds = @(
    "bounded-effect-scope",
    "durable-inputs-fixed",
    "no-human-decision",
    "noninteractive-environment",
    "objective-completion",
    "stable-external-state"
)
# How a Performer may repeat the Instruction after a failed attempt.
# `at-most-once` is the honest answer for an effect that cannot be replayed;
# it is what makes uncertain effect state a recordable Dispatch-evidence class
# rather than an ordinary retry.
$Script:RetryKinds = @("at-most-once", "idempotent", "resumable")
$Script:InstructionModes = @("command", "manual", "skill")

# Operator-configured Continuation authority (§10, #263).
#
# The mode lattice, weakest first. Narrowing is `min` over this order, which is
# what makes authority monotonic: a later configuration source may move a Run
# down the lattice and never up it.
$Script:ContinuationModes = @("off", "report", "execute-frontier")
$Script:ModeRank = [ordered]@{}
for ($Index = 0; $Index -lt $Script:ContinuationModes.Count; $Index++) {
    $Script:ModeRank[$Script:ContinuationModes[$Index]] = $Index
}

# The ordered configuration sources. The order is the narrowing order, so it is
# part of the contract rather than a convenience: "later narrows earlier" has no
# meaning without one.
$Script:AuthoritySources = @("global", "project", "runtime")

# The five positive ceilings, against the closed vocabulary each one draws from.
# `$null` means the vocabulary is open (a repository name is not enumerable).
$Script:CeilingVocabularies = [ordered]@{
    repositories = $null
    targets = @($Script:ReferenceFields.Keys)
    action_kinds = $Script:ActionKinds
    instruction_modes = $Script:InstructionModes
    effect_scopes = $Script:EffectKinds
}

# What each participating mode needs from the tracker Adapter. `off` is absent
# because it needs nothing: it is the claim that Continuation does not run.
$Script:ModeRequiredOperations = [ordered]@{
    report = @("reconcile")
    "execute-frontier" = @("reconcile", "record-dispatch-result")
}
$Script:ShaPattern = "\A[0-9a-f]{40}\z"
# The only two exceptional Dispatch outcomes that become durable evidence.
# Ordinary success and ordinary execution failure stay in the Runner's
# existing artifacts, Events, retry, and Strike paths.
$Script:DispatchEvidenceClasses = @(
    "safety-case-violation", "uncertain-effect-state"
)
$Script:DispatchMarker = "<!-- git-loopy-continuation-dispatch:1 -->"

# Is this comment claiming to be a Continuation record or dispatch evidence?
# Authentication is scoped to *marked* comments: an ordinary human comment on a
# carrier issue is not a record, was never going to become one, and must not cost
# a Producer permission read or answer the mutation question. Testing for the
# marker is a discriminator, not semantic parsing, so the contract's
# authenticate-before-parse ordering holds -- an unmarked comment is never parsed.
function Test-GitLoopyMarkedComment {
    param([Parameter(Mandatory)][Collections.IDictionary]$Comment)

    $Body = [string]$Comment["body"]
    return (
        $Body.Contains($Script:RecordMarker, [StringComparison]::Ordinal) -or
        $Body.Contains($Script:DispatchMarker, [StringComparison]::Ordinal)
    )
}

# The locked Automation stop precedence, strongest first. Exactly one stop is
# returned and the first matching reason wins, so a Run that is both blocked on
# a human boundary and waiting on a Prerequisite reports the human boundary --
# the thing a person can act on -- rather than the more numerous barrier.
$Script:AutomationStopPrecedence = @(
    @("workstreams-terminal", "complete"),
    @("safety-case-violation", "attention-required"),
    @("uncertain-effect-state", "attention-required"),
    @("guidance-fault", "attention-required"),
    @("human-boundary", "expected-boundary"),
    @("grant-missing", "expected-boundary"),
    @("performer-ineligible", "expected-boundary"),
    @("frontier-drained", "expected-boundary"),
    @("awaiting-prerequisites", "expected-boundary")
)
# Which ineligibility reason raises which stop. Reasons absent from this map
# describe an Action the Run was never entitled to consider (report-only,
# out of coverage, already dispatched) and so cannot themselves stop a Run.
$Script:IneligibilityStopReasons = [ordered]@{
    "human-boundary" = "human-boundary"
    "grant-missing" = "grant-missing"
    "performer-ineligible" = "performer-ineligible"
    "not-ready" = "awaiting-prerequisites"
    # An AFK-safe claim with no safety case behind it is a defect in the
    # guidance, not a property of the Performer or the Run's authority.
    "safety-case-absent" = "guidance-fault"
}

$Script:CapabilityManifest = [ordered]@{
    continuation_contract_versions =
        @($Script:SupportedContinuationContractVersions)
    record_formats = @($Script:RecordFormat)
    wrapper_contract_version = $Script:WrapperContractVersion
    event_schema_version = $Script:EventSchemaVersion
    tracker_adapters = [ordered]@{
        github = [ordered]@{
            operations = @(
                "publish", "reconcile", "record-dispatch-result", "repair-index"
            )
        }
    }
    operations = [ordered]@{
        capabilities = $true
        "resolve-authority" = $true
        publish = $true
        reconcile = $true
        "record-dispatch-result" = $true
        "repair-index" = $true
    }
    instruction_handlers = @()
    instruction_modes = @()
    evaluators = @()
    effect_scopes = @()
    optional_capabilities = [ordered]@{
        immutable_producer_revisions = $true
        terminal_rendering = $true
        concurrent_dispatch = $false
        prospective_projection = $true
        fixed_frontier_authorization = $true
    }
    # Contract 1.3. `report` is advertised because §4's mandatory precondition is
    # met: every locked coverage area has a recognized Transition owner. `default`
    # stays `off` -- adoption is the operator's decision, not the distribution's --
    # and `execute-frontier` is advertised because this distribution can now
    # authorize and drain a serial fixed frontier (#266). Serial only: general
    # concurrency is a separate decision, and `concurrent_dispatch` above stays
    # `$false` so no reader can extrapolate one from the other.
    continuation_modes = [ordered]@{
        default = "off"
        off = $true
        report = $true
        "execute-frontier" = $true
    }
}

# --- Setup verification (#257) ----------------------------------------------
#
# Verification is a *Consumer* of the capability manifest, not a Continuation
# operation: contract §1 scopes the contract to Continuation records and their
# derivation, and §4 says the manifest "describes capability only". So the profile
# and its evaluator live beside the manifest they judge, and the native command
# namespace is unchanged.
#
# The distribution running this code is the distribution being verified. Nothing
# resolves an entrypoint and nothing names a family member, which is how setup
# records the operator's selection without committing a host-specific executable
# path or a family-member choice.

# The named requirement sets this distribution is judged against. `foundation`
# is the baseline every distribution must clear; `report` (#263) additionally
# requires the `resolve-authority` operation and the `report` mode to be
# advertised; `execute-frontier` (#266) additionally requires the mode this
# distribution now serves and the optional capability §9's authorization is
# gated by.
$Script:ContinuationProfiles = [ordered]@{
    foundation = [ordered]@{
        requirements = @(
            "contract-version"
            "record-format"
            "tracker-adapter"
            "native-operations"
            "mode-default-off"
        )
        continuation_contract_version = $Script:ContinuationContractVersion
        record_format = $Script:RecordFormat
        tracker_adapter = "github"
        tracker_operations = @(
            "publish", "reconcile", "record-dispatch-result", "repair-index"
        )
        native_operations = @(
            "capabilities", "publish", "reconcile", "record-dispatch-result",
            "repair-index"
        )
        mode_default = "off"
    }
    report = [ordered]@{
        requirements = @(
            "contract-version"
            "record-format"
            "tracker-adapter"
            "native-operations"
            "mode-default-off"
            "mode-report"
        )
        continuation_contract_version = $Script:ContinuationContractVersion
        record_format = $Script:RecordFormat
        tracker_adapter = "github"
        tracker_operations = @(
            "publish", "reconcile", "record-dispatch-result", "repair-index"
        )
        native_operations = @(
            "capabilities", "publish", "reconcile", "record-dispatch-result",
            "repair-index", "resolve-authority"
        )
        mode_default = "off"
        required_modes = @("report")
    }
    # Fixed declaration order, as above. The two execute-frontier requirements
    # come last for the same reason `mode-report` did: a reader comparing the
    # profiles sees exactly what serial Dispatch added over read-only observation.
    "execute-frontier" = [ordered]@{
        requirements = @(
            "contract-version"
            "record-format"
            "tracker-adapter"
            "native-operations"
            "mode-default-off"
            "mode-report"
            "mode-execute-frontier"
            "fixed-frontier"
        )
        continuation_contract_version = $Script:ContinuationContractVersion
        record_format = $Script:RecordFormat
        tracker_adapter = "github"
        tracker_operations = @(
            "publish", "reconcile", "record-dispatch-result", "repair-index"
        )
        native_operations = @(
            "capabilities", "publish", "reconcile", "record-dispatch-result",
            "repair-index", "resolve-authority"
        )
        mode_default = "off"
        # `report` is required beside `execute-frontier` because narrowing is
        # real: an operator whose project table asks for `report` under a global
        # `execute-frontier` gets `report`, and a distribution that advertised
        # only the stronger mode would fail closed on the weaker one it just
        # resolved to.
        required_modes = @("report", "execute-frontier")
        # §9's authorization is gated by this optional capability, so a manifest
        # that advertises the mode without it is advertising a mode with no
        # decision procedure behind it.
        required_optional_capabilities = @("fixed_frontier_authorization")
    }
}

function Get-GitLoopyCapabilityManifest {
    <#
    .SYNOPSIS
        The manifest this distribution advertises, including its Release version.
    .DESCRIPTION
        One seam for both readers: the `capabilities` operation and setup
        verification. A second construction site is a second manifest, and the
        whole point of verifying is that the answer is about what this
        distribution really advertises.
    #>
    [CmdletBinding()]
    param()

    $Manifest = [ordered]@{
        release_version = Get-GitLoopyReleaseVersion
    }
    foreach ($Name in $Script:CapabilityManifest.Keys) {
        $Manifest[$Name] = $Script:CapabilityManifest[$Name]
    }
    return $Manifest
}

function Get-GitLoopyContinuationProfile {
    <#
    .SYNOPSIS
        One named Continuation capability requirement set.
    #>
    [CmdletBinding()]
    param([string]$Name = "foundation")

    if (-not $Script:ContinuationProfiles.Contains($Name)) {
        throw "unknown Continuation capability profile $Name"
    }
    return $Script:ContinuationProfiles[$Name]
}

function Get-GitLoopyContinuationVerification {
    <#
    .SYNOPSIS
        Judge one advertised capability manifest against one named profile.
    .DESCRIPTION
        Unsatisfied requirements come out in the profile's own declaration order,
        and unsupported optional capabilities are sorted: the three family
        manifests declare `optional_capabilities` in three different orders, so an
        unsorted answer would differ between members advertising exactly the same
        capabilities.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Manifest,
        [string]$Name = "foundation"
    )

    $ProfileSpec = Get-GitLoopyContinuationProfile -Name $Name
    $Unsatisfied = [Collections.Generic.List[string]]::new()
    foreach ($Requirement in @($ProfileSpec["requirements"])) {
        if (-not (Test-GitLoopyCapabilityRequirement `
                    -Manifest $Manifest -ProfileSpec $ProfileSpec -Requirement $Requirement)) {
            $Unsatisfied.Add([string]$Requirement)
        }
    }

    $Unsupported = [Collections.Generic.List[string]]::new()
    if ($Manifest.Contains("optional_capabilities") -and
        $Manifest["optional_capabilities"] -is [Collections.IDictionary]) {
        foreach ($Entry in $Manifest["optional_capabilities"].GetEnumerator()) {
            if ($Entry.Value -ne $true) {
                $Unsupported.Add([string]$Entry.Key)
            }
        }
    }

    $ReleaseVersion = ""
    if ($Manifest.Contains("release_version") -and
        $Manifest["release_version"] -is [string]) {
        $ReleaseVersion = [string]$Manifest["release_version"]
    }

    return [ordered]@{
        profile = $Name
        release_version = $ReleaseVersion
        satisfied = ($Unsatisfied.Count -eq 0)
        unsatisfied_requirements = @($Unsatisfied)
        unsupported_optional_capabilities = @(
            $Unsupported | Sort-Object -CaseSensitive
        )
    }
}

function Test-GitLoopyCapabilityRequirement {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Manifest,
        [Parameter(Mandatory)][Collections.IDictionary]$ProfileSpec,
        [Parameter(Mandatory)][string]$Requirement
    )

    switch ($Requirement) {
        "contract-version" {
            return @(Get-GitLoopyManifestValue $Manifest "continuation_contract_versions") -contains
                $ProfileSpec["continuation_contract_version"]
        }
        "record-format" {
            return @(Get-GitLoopyManifestValue $Manifest "record_formats") -contains
                $ProfileSpec["record_format"]
        }
        "tracker-adapter" {
            $Adapters = Get-GitLoopyManifestValue $Manifest "tracker_adapters"
            if ($Adapters -isnot [Collections.IDictionary]) { return $false }
            $Adapter = Get-GitLoopyManifestValue $Adapters ([string]$ProfileSpec["tracker_adapter"])
            if ($Adapter -isnot [Collections.IDictionary]) { return $false }
            $Advertised = @(Get-GitLoopyManifestValue $Adapter "operations")
            foreach ($Operation in @($ProfileSpec["tracker_operations"])) {
                if ($Advertised -notcontains $Operation) { return $false }
            }
            return $true
        }
        "native-operations" {
            $Operations = Get-GitLoopyManifestValue $Manifest "operations"
            if ($Operations -isnot [Collections.IDictionary]) { return $false }
            foreach ($Operation in @($ProfileSpec["native_operations"])) {
                if ((Get-GitLoopyManifestValue $Operations ([string]$Operation)) -ne $true) {
                    return $false
                }
            }
            return $true
        }
        "mode-default-off" {
            $Modes = Get-GitLoopyManifestValue $Manifest "continuation_modes"
            if ($Modes -isnot [Collections.IDictionary]) { return $false }
            $Default = [string]$ProfileSpec["mode_default"]
            return ((Get-GitLoopyManifestValue $Modes "default") -ceq $Default) -and
                ((Get-GitLoopyManifestValue $Modes $Default) -eq $true)
        }
        "mode-report" {
            return (Test-GitLoopyAdvertisedMode -Manifest $Manifest -Mode "report")
        }
        "mode-execute-frontier" {
            return (
                Test-GitLoopyAdvertisedMode -Manifest $Manifest -Mode "execute-frontier"
            )
        }
        "fixed-frontier" {
            # The optional capabilities §9's authorization is gated by. Named per
            # requirement rather than folded into the mode check, so setup can
            # tell an unadvertised mode apart from a mode advertised with no
            # decision procedure behind it.
            $Optional = Get-GitLoopyManifestValue $Manifest "optional_capabilities"
            if ($Optional -isnot [Collections.IDictionary]) { return $false }
            if (-not $ProfileSpec.Contains("required_optional_capabilities")) {
                return $false
            }
            foreach ($Capability in @($ProfileSpec["required_optional_capabilities"])) {
                if (-not (
                        Test-GitLoopyAdvertisedFlag `
                            -Value (Get-GitLoopyManifestValue $Optional ([string]$Capability))
                    )) {
                    return $false
                }
            }
            return $true
        }
        default { return $false }
    }
}

function Test-GitLoopyAdvertisedFlag {
    <#
    .SYNOPSIS
        Is this advertised value the scalar `$true`, and nothing that merely
        compares equal to it?
    .DESCRIPTION
        `-eq` compares a list element-wise and answers with the matching
        elements, so `@($true, $false) -eq $true` is truthy. A manifest whose
        capability flag is a list is malformed, and reading it as support would
        let setup pass a distribution that never claimed the capability at all.
    #>
    [CmdletBinding()]
    param([AllowNull()][object]$Value)

    return ($Value -is [bool]) -and [bool]$Value
}

function Test-GitLoopyAdvertisedMode {
    <#
    .SYNOPSIS
        Does the manifest advertise exactly this one Continuation mode?
    .DESCRIPTION
        One requirement asks about one mode, never about the profile's whole
        `required_modes` set: a profile that requires two modes must be defeatable
        by dropping either one on its own, or a refusal could never name which
        mode the manifest actually stopped serving.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Manifest,
        [Parameter(Mandatory)][string]$Mode
    )

    $Modes = Get-GitLoopyManifestValue $Manifest "continuation_modes"
    if ($Modes -isnot [Collections.IDictionary]) { return $false }
    return (
        Test-GitLoopyAdvertisedFlag -Value (Get-GitLoopyManifestValue $Modes $Mode)
    )
}

function Get-GitLoopyManifestValue {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)][Collections.IDictionary]$Container,
        [Parameter(Mandatory, Position = 1)][string]$Key
    )

    if (-not $Container.Contains($Key)) { return $null }
    return $Container[$Key]
}

function Test-GitLoopyDistributionCapabilities {
    <#
    .SYNOPSIS
        Verify the running distribution and render one operator-facing line.
    .DESCRIPTION
        Returns `$false` when the profile is unsatisfied, so a setup surface can
        fail closed before it writes or installs anything.
    #>
    [CmdletBinding()]
    param([string]$Name = "foundation")

    $Verdict = Get-GitLoopyContinuationVerification `
        -Manifest (Get-GitLoopyCapabilityManifest) -Name $Name

    if (-not $Verdict["satisfied"]) {
        [Console]::Error.WriteLine(
            "git-loopy: this distribution does not satisfy the $Name " +
            "Continuation capability profile " +
            "($(@($Verdict['unsatisfied_requirements']) -join ', ')).")
        return $false
    }

    $ReleaseVersion = [string]$Verdict["release_version"]
    if ([string]::IsNullOrEmpty($ReleaseVersion)) {
        $ReleaseVersion = "unknown"
    }
    $Line = "Verified this distribution's Continuation capabilities " +
        "($Name profile, contract $Script:ContinuationContractVersion, " +
        "release $ReleaseVersion)"
    $Unsupported = @($Verdict["unsupported_optional_capabilities"])
    if ($Unsupported.Count -gt 0) {
        $Line += "; unsupported optional capabilities: $($Unsupported -join ', ')"
    }
    [Console]::Out.WriteLine("$Line.")
    return $true
}

# --- Serial fixed-frontier preflight (#266) ----------------------------------
#
# §9 decides *whether* one Action may be dispatched, and the shared automation
# fixtures gate that decision through the real native entrypoint. This is the
# half that runs before it: one Run turns one resolved §10 authority into the
# single Performer posture it will dispatch with, and refuses an authority this
# distribution cannot honour rather than discovering it mid-flight.
#
# Nothing here widens. Every rule may remove an Instruction mode from
# consideration and none may add one, which is why an unenforceable ceiling is
# refused instead of accepted and quietly ignored.
#
# What is deliberately *not* here: the Run loop that turns each authorization
# into a session and emits the Continuation lifecycle Events. That half lives in
# the reference Orchestrator (#264) for the same reason report mode's does --- the
# Event payloads and outer-Run exits are family contract, and a second family
# inventing them ahead of the reference is exactly the cross-family drift #267
# gates on. This distribution proves its parity where the family contract is
# actually pinned: the native command surface and the shared fixtures.

# The Instruction modes this distribution has a handler for. The Orchestrator
# drives a noninteractive Copilot session, so a canonical Skill is something it
# can genuinely execute; `command` and `manual` are not. §9 reads the posture as
# a closed world, so the claim is made narrowly and explicitly --- silence there
# is read as universal competence.
$Script:HandledInstructionModes = @("skill")

# §10 ceiling axes this distribution cannot enforce. §9 derives eligibility from
# coverage, grants and Performer posture; it has no input for an Action-kind or
# Target cap. An operator who capped kinds and got a Run that dispatched every
# kind would have been told their authority was narrower than it was --- the
# single worst failure mode an authority model has.
$Script:UnenforceableCeilings = @("action_kinds", "targets")

function New-GitLoopyFrontierPlan {
    <#
    .SYNOPSIS
        Turn a resolved §10 authority into the posture this Run will dispatch with.
    .DESCRIPTION
        Fails closed rather than mid-flight. A Run that discovered at the moment
        it had to record a safety-case violation that it had no actor to write as
        would lose the one record a human needs.

        `SatisfiedRequirements` is what the Run can honestly claim it already
        holds, in §9's typed `(kind, name)` vocabulary. It is supplied by the
        caller rather than assumed here, because the only thing this preflight
        knows about the host is what it was told.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Authority,
        [AllowEmptyCollection()][object[]]$SatisfiedRequirements = @()
    )

    $Mode = [string](Get-GitLoopyManifestValue $Authority "mode")
    if ($Mode -cne "execute-frontier") {
        # A `report` or `off` authority was never granted execution, so there is
        # no frontier to plan for it. Manufacturing a posture here would let a
        # narrowed Run dispatch under authority it had already lost.
        throw [GitLoopyContinuationCapabilityUnsupported]::new(
            "a frontier plan requires continuation mode execute-frontier"
        )
    }

    $Ceilings = Get-GitLoopyManifestValue $Authority "ceilings"
    if ($Ceilings -isnot [Collections.IDictionary]) {
        $Ceilings = [ordered]@{}
    }

    $Actor = Get-GitLoopyManifestValue $Authority "actor"
    if ([string]::IsNullOrEmpty([string]$Actor)) {
        # Dispatch evidence is bound to its Performer at both ends:
        # `record-dispatch-result` requires the authenticated actor to be the
        # Performer the record names. §10 makes `actor` optional because report
        # mode never writes; an execute-frontier Run does.
        throw [GitLoopyContinuationCapabilityUnsupported]::new(
            "continuation mode execute-frontier requires a configured actor"
        )
    }

    foreach ($Axis in $Script:UnenforceableCeilings) {
        if ((Get-GitLoopyAuthorityList $Ceilings $Axis).Count -gt 0) {
            throw [GitLoopyContinuationCapabilityUnsupported]::new(
                "continuation ceiling $Axis cannot be enforced by this distribution"
            )
        }
    }

    $Declared = Get-GitLoopyAuthorityList $Ceilings "instruction_modes"
    # A ceiling narrows the closed world; it never adds a handler to it.
    $Modes = @(
        $Script:HandledInstructionModes |
            Where-Object { $Declared.Count -eq 0 -or $Declared -contains $_ }
    )
    if ($Modes.Count -eq 0) {
        throw [GitLoopyContinuationCapabilityUnsupported]::new(
            "continuation ceiling instruction_modes excludes every Instruction " +
            "mode this distribution handles"
        )
    }

    return [ordered]@{
        performer = [ordered]@{
            id = [string]$Actor
            posture = [ordered]@{
                noninteractive = $true
                satisfied_requirements = @(
                    $SatisfiedRequirements |
                        ForEach-Object {
                            [ordered]@{
                                kind = [string]$_["kind"]
                                name = [string]$_["name"]
                            }
                        } |
                        Sort-Object -CaseSensitive -Property `
                            @{ Expression = { $_["kind"] } }, `
                            @{ Expression = { $_["name"] } }
                )
                instruction_modes = $Modes
            }
        }
        repositories = Get-GitLoopyAuthorityList $Ceilings "repositories"
        trusted_producers = Get-GitLoopyAuthorityList $Authority "trusted_producers"
        effect_kinds = Get-GitLoopyAuthorityList $Ceilings "effect_scopes"
    }
}

function Get-GitLoopyAuthorityList {
    <#
    .SYNOPSIS
        One authority or ceiling axis, as a list, with absent read as empty.
    .DESCRIPTION
        `@($null)` is a one-element list holding nothing, so wrapping a missing
        key would turn "this operator capped nothing" into "this operator capped
        one unnameable thing" --- which the preflight would then refuse. Absent
        and empty are the same claim here: no cap.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)][Collections.IDictionary]$Container,
        [Parameter(Mandatory, Position = 1)][string]$Key
    )

    $Value = Get-GitLoopyManifestValue $Container $Key
    if ($null -eq $Value) { return , @() }
    # The leading comma keeps a one-element axis a list: PowerShell unrolls an
    # array returned through the pipeline, and a caller asking a bare string for
    # its `Count` gets nothing at all.
    return , @($Value | Where-Object { $null -ne $_ })
}

function Get-GitLoopyContinuationUsage {
    [CmdletBinding()]
    param()
    return @"
Usage: git-loopy.ps1 continuation <operation> [options]

Operations:
  capabilities
  resolve-authority [--input FILE]
  publish [--input FILE]
  reconcile [--input FILE] [--terminal]
  record-dispatch-result [--input FILE]
  repair-index [--input FILE]
"@
}

function Write-GitLoopyContinuationJson {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Value
    )
    [Console]::Out.Write(
        ($Value | ConvertTo-Json -Compress -Depth 50) + "`n"
    )
}

function Write-GitLoopyContinuationError {
    param(
        [Parameter(Mandatory)]
        [string]$Operation,
        [Parameter(Mandatory)]
        [string]$Code,
        [Parameter(Mandatory)]
        [string]$Message
    )
    Write-GitLoopyContinuationJson ([ordered]@{
        ok = $false
        operation = $Operation
        error = [ordered]@{
            code = $Code
            message = $Message
        }
    })
    [Console]::Error.Write("git-loopy continuation: $Message`n")
    return 1
}

function New-GitLoopyRejection {
    param([Parameter(Mandatory)][string]$Message)
    return [GitLoopyContinuationRejection]::new($Message)
}

function New-GitLoopyRepairRequired {
    param([Parameter(Mandatory)][string]$Message)
    return [GitLoopyContinuationRepairRequired]::new($Message)
}

function Test-GitLoopyRawJsonNesting {
    param(
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][string]$Name
    )
    $Depth = 0
    $InString = $false
    $Escaped = $false
    foreach ($Character in $Text.ToCharArray()) {
        if ($InString) {
            if ($Escaped) {
                $Escaped = $false
            }
            elseif ($Character -eq "\") {
                $Escaped = $true
            }
            elseif ($Character -eq '"') {
                $InString = $false
            }
            continue
        }
        if ($Character -eq '"') {
            $InString = $true
        }
        elseif ($Character -eq "[" -or $Character -eq "{") {
            $Depth++
            if ($Depth -gt $Script:MaxDepth) {
                throw (New-GitLoopyRejection (
                    "$Name exceeds maximum nesting depth $Script:MaxDepth"
                ))
            }
        }
        elseif ($Character -eq "]" -or $Character -eq "}") {
            $Depth = [Math]::Max(0, $Depth - 1)
        }
    }
}

function Test-GitLoopyJsonParsePhase {
    param([Parameter(Mandatory)][Text.Json.JsonElement]$Element)

    if ($Element.ValueKind -eq [Text.Json.JsonValueKind]::Object) {
        $Seen = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Property in $Element.EnumerateObject()) {
            if (-not $Seen.Add($Property.Name)) {
                throw (New-GitLoopyRejection (
                    "request contains duplicate object key: $($Property.Name)"
                ))
            }
            Test-GitLoopyJsonParsePhase $Property.Value
        }
    }
    elseif ($Element.ValueKind -eq [Text.Json.JsonValueKind]::Array) {
        foreach ($Item in $Element.EnumerateArray()) {
            Test-GitLoopyJsonParsePhase $Item
        }
    }
    elseif ($Element.ValueKind -eq [Text.Json.JsonValueKind]::Number) {
        $Raw = $Element.GetRawText()
        if ($Raw.Contains(".") -or $Raw.Contains("e") -or $Raw.Contains("E")) {
            throw (New-GitLoopyRejection (
                "request must not contain floating-point values"
            ))
        }
    }
}

function Test-GitLoopyPortableString {
    param(
        [Parameter(Mandatory)][string]$Value,
        [Parameter(Mandatory)][string]$Name
    )
    if (-not [string]::Equals(
            $Value.Normalize([Text.NormalizationForm]::FormC),
            $Value,
            [StringComparison]::Ordinal
        )) {
        throw (New-GitLoopyRejection "$Name strings must be NFC-normalized")
    }
    if (
        [Text.Encoding]::UTF8.GetByteCount($Value) -gt $Script:MaxStringBytes
    ) {
        throw (New-GitLoopyRejection (
            "$Name string exceeds maximum UTF-8 length $Script:MaxStringBytes"
        ))
    }
}

function Test-GitLoopyPortablePhase {
    param(
        [Parameter(Mandatory)][Text.Json.JsonElement]$Element,
        [Parameter(Mandatory)][string]$Name,
        [int]$Depth = 0
    )

    switch ($Element.ValueKind) {
        ([Text.Json.JsonValueKind]::Number) {
            $Raw = $Element.GetRawText()
            $Integer = [System.Numerics.BigInteger]::Parse($Raw)
            if (
                $Integer -lt (-$Script:MaxInteger) -or
                $Integer -gt $Script:MaxInteger
            ) {
                throw (New-GitLoopyRejection (
                    "$Name integer exceeds interoperable signed 53-bit range"
                ))
            }
        }
        ([Text.Json.JsonValueKind]::String) {
            Test-GitLoopyPortableString $Element.GetString() $Name
        }
        ([Text.Json.JsonValueKind]::Array) {
            $ContainerDepth = $Depth + 1
            if ($ContainerDepth -gt $Script:MaxDepth) {
                throw (New-GitLoopyRejection (
                    "$Name exceeds maximum nesting depth $Script:MaxDepth"
                ))
            }
            if ($Element.GetArrayLength() -gt $Script:MaxArrayLength) {
                throw (New-GitLoopyRejection (
                    "$Name array exceeds maximum length $Script:MaxArrayLength"
                ))
            }
            foreach ($Item in $Element.EnumerateArray()) {
                Test-GitLoopyPortablePhase $Item $Name $ContainerDepth
            }
        }
        ([Text.Json.JsonValueKind]::Object) {
            $ContainerDepth = $Depth + 1
            if ($ContainerDepth -gt $Script:MaxDepth) {
                throw (New-GitLoopyRejection (
                    "$Name exceeds maximum nesting depth $Script:MaxDepth"
                ))
            }
            foreach ($Property in $Element.EnumerateObject()) {
                Test-GitLoopyPortableString $Property.Name $Name
                Test-GitLoopyPortablePhase $Property.Value $Name $ContainerDepth
            }
        }
    }
}

function Read-GitLoopyContinuationRequest {
    param([AllowNull()][object]$InputPath)

    if ($null -ne $InputPath) {
        try {
            $Bytes = [byte[]][IO.File]::ReadAllBytes($InputPath)
        }
        catch {
            throw (New-GitLoopyRejection (
                "could not read request: $($_.Exception.Message)"
            ))
        }
    }
    else {
        $Memory = [IO.MemoryStream]::new()
        try {
            [Console]::OpenStandardInput().CopyTo($Memory)
            $Bytes = [byte[]]$Memory.ToArray()
        }
        finally {
            $Memory.Dispose()
        }
    }

    if (
        $Bytes.Length -ge 3 -and
        $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF
    ) {
        throw (New-GitLoopyRejection "request must be UTF-8 without a BOM")
    }

    $Text = $null
    try {
        $Encoding = [Text.UTF8Encoding]::new($false, $true)
        $Text = $Encoding.GetString($Bytes)
    }
    catch {
        throw (New-GitLoopyRejection "request must be one UTF-8 JSON object")
    }

    Test-GitLoopyRawJsonNesting -Text $Text -Name "request"

    $Document = $null
    try {
        $Document = [Text.Json.JsonDocument]::Parse($Text)
    }
    catch [GitLoopyContinuationRejection] {
        throw
    }
    catch {
        throw (New-GitLoopyRejection "request must be one UTF-8 JSON object")
    }
    try {
        Test-GitLoopyJsonParsePhase $Document.RootElement
        if (
            $Document.RootElement.ValueKind -ne
            [Text.Json.JsonValueKind]::Object
        ) {
            throw (New-GitLoopyRejection (
                "request must be one UTF-8 JSON object"
            ))
        }
        Test-GitLoopyPortablePhase $Document.RootElement "request"
    }
    finally {
        $Document.Dispose()
    }

    $Request = $Text | ConvertFrom-Json -AsHashtable -DateKind String
    if ($Request -isnot [Collections.IDictionary]) {
        throw (New-GitLoopyRejection "request must be one UTF-8 JSON object")
    }
    return $Request
}

function ConvertTo-GitLoopyCanonicalValue {
    param([AllowNull()][object]$Value)

    if ($Value -is [Collections.IDictionary]) {
        $Result = [ordered]@{}
        $Keys = [string[]]@($Value.Keys)
        [Array]::Sort($Keys, [StringComparer]::Ordinal)
        foreach ($Key in $Keys) {
            $Result[$Key] = ConvertTo-GitLoopyCanonicalValue $Value[$Key]
        }
        return $Result
    }
    if ($Value -is [Collections.IList] -and $Value -isnot [string]) {
        $Result = [object[]]::new($Value.Count)
        for ($Index = 0; $Index -lt $Value.Count; $Index++) {
            $Result[$Index] = ConvertTo-GitLoopyCanonicalValue $Value[$Index]
        }
        return , $Result
    }
    return $Value
}

function Convert-GitLoopyJsonEscapesToRawUtf8 {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Json)

    # PowerShell's ConvertTo-Json escapes U+0085, U+2028, and U+2029 as
    # \uXXXX sequences, whereas Python json.dumps(ensure_ascii=False) and jq
    # emit the raw UTF-8 bytes. Rewrite only those escapes so canonical bytes,
    # revision ids, and fingerprints stay identical across every distribution.
    return [Text.RegularExpressions.Regex]::Replace(
        $Json,
        '(\\+)u(0085|2028|2029)',
        {
            param($Match)
            $Slashes = $Match.Groups[1].Value
            if (($Slashes.Length % 2) -eq 0) {
                # An even backslash run leaves the escaped backslashes intact
                # and treats "uXXXX" as literal text, so do not rewrite it.
                return $Match.Value
            }
            $CodePoint = [Convert]::ToInt32($Match.Groups[2].Value, 16)
            return $Slashes.Substring(0, $Slashes.Length - 1) +
                [char]$CodePoint
        },
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
}

function ConvertTo-GitLoopyCanonicalJson {
    param([AllowNull()][object]$Value)

    $Json = ConvertTo-Json `
        -InputObject (ConvertTo-GitLoopyCanonicalValue $Value) `
        -Compress `
        -Depth 50
    return Convert-GitLoopyJsonEscapesToRawUtf8 $Json
}

function Get-GitLoopySha256 {
    param([Parameter(Mandatory)][string]$Value)

    $Bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
    return [Convert]::ToHexString(
        [Security.Cryptography.SHA256]::HashData($Bytes)
    ).ToLowerInvariant()
}

function Copy-GitLoopyWithoutAdvisoryExtensions {
    param([AllowNull()][object]$Value)

    if ($Value -is [Collections.IDictionary]) {
        $Result = [ordered]@{}
        foreach ($Entry in $Value.GetEnumerator()) {
            if ($Entry.Key -cne "advisory_extensions") {
                $Result[$Entry.Key] = Copy-GitLoopyWithoutAdvisoryExtensions `
                    $Entry.Value
            }
        }
        return $Result
    }
    if ($Value -is [Collections.IList] -and $Value -isnot [string]) {
        $Result = [object[]]::new($Value.Count)
        for ($Index = 0; $Index -lt $Value.Count; $Index++) {
            $Result[$Index] = Copy-GitLoopyWithoutAdvisoryExtensions `
                $Value[$Index]
        }
        return , $Result
    }
    return $Value
}

function Get-GitLoopySemanticFingerprint {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Action
    )

    $Effects = [object[]]@()
    $Requirements = [object[]]@()
    $Triggers = [object[]]@()
    if ($Action.Contains("effects")) { $Effects = $Action["effects"] }
    if ($Action.Contains("requirements")) { $Requirements = $Action["requirements"] }
    if ($Action.Contains("triggers")) { $Triggers = $Action["triggers"] }
    $Semantics = [ordered]@{
        instruction = $Action["instruction"]
        prerequisites = $Action["prerequisites"]
        interaction = $Action["interaction"]
        completion_condition = $Action["completion_condition"]
        effects = $Effects
        requirements = $Requirements
        triggers = $Triggers
    }
    # Conditional: a record published under 1.0 or 1.1 has no safety case, and
    # its fingerprint must stay byte-identical across this contract bump.
    if ($Action.Contains("safety_case")) {
        $Semantics["safety_case"] = $Action["safety_case"]
    }
    $Canonical = ConvertTo-GitLoopyCanonicalJson (
        Copy-GitLoopyWithoutAdvisoryExtensions $Semantics
    )
    return Get-GitLoopySha256 $Canonical
}

function Get-GitLoopySemanticFingerprints {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Completion
    )

    $Fingerprints = [ordered]@{}
    if ($Completion.Contains("actions")) {
        foreach ($Action in $Completion["actions"]) {
            $Fingerprints[[string]$Action["key"]] =
                Get-GitLoopySemanticFingerprint $Action
        }
    }
    return $Fingerprints
}

function Test-GitLoopyEnumMember {
    param([AllowNull()][object]$Value, [Parameter(Mandatory)][object[]]$Allowed)

    foreach ($Candidate in $Allowed) {
        if ($Candidate -is [bool]) {
            if ($Value -is [bool] -and $Value -eq $Candidate) { return $true }
        }
        elseif ($Candidate -is [string]) {
            if ($Value -is [string] -and $Value -ceq $Candidate) { return $true }
        }
        elseif ($Value -eq $Candidate) {
            return $true
        }
    }
    return $false
}

function Get-GitLoopyFirstOrdinal {
    param([Parameter(Mandatory)][string[]]$Items)
    $Sorted = [string[]]$Items
    [Array]::Sort($Sorted, [StringComparer]::Ordinal)
    return $Sorted[0]
}

function Assert-GitLoopyObject {
    param([AllowNull()][object]$Value, [Parameter(Mandatory)][string]$Name)
    if ($Value -isnot [Collections.IDictionary]) {
        throw (New-GitLoopyRejection "$Name must be an object")
    }
    return $Value
}

function Assert-GitLoopyString {
    param([AllowNull()][object]$Value, [Parameter(Mandatory)][string]$Name)
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value)) {
        throw (New-GitLoopyRejection "$Name must be a non-empty string")
    }
    return $Value
}

function Assert-GitLoopyPositiveInt {
    param([AllowNull()][object]$Value, [Parameter(Mandatory)][string]$Name)
    if (
        $Value -is [bool] -or
        -not ($Value -is [int] -or $Value -is [long]) -or
        [long]$Value -le 0
    ) {
        throw (New-GitLoopyRejection "$Name must be a positive integer")
    }
    return [long]$Value
}

function Assert-GitLoopyArray {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name,
        [switch]$NonEmpty
    )
    if (
        $Value -isnot [Collections.IList] -or $Value -is [string] -or
        ($NonEmpty -and $Value.Count -eq 0)
    ) {
        $Qualifier = if ($NonEmpty) { "non-empty " } else { "" }
        throw (New-GitLoopyRejection "$Name must be a ${Qualifier}array")
    }
    return , $Value
}

function Assert-GitLoopyFields {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Value,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$Required,
        [string[]]$Optional = @()
    )
    $Present = [Collections.Generic.HashSet[string]]::new(
        [string[]]@($Value.Keys), [StringComparer]::Ordinal
    )
    $Missing = [Collections.Generic.List[string]]::new()
    foreach ($Field in $Required) {
        if (-not $Present.Contains($Field)) { $Missing.Add($Field) }
    }
    if ($Missing.Count -gt 0) {
        throw (New-GitLoopyRejection (
            "$Name is missing required field: " +
            (Get-GitLoopyFirstOrdinal $Missing.ToArray())
        ))
    }
    $Allowed = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Field in $Required) { [void]$Allowed.Add($Field) }
    foreach ($Field in $Optional) { [void]$Allowed.Add($Field) }
    $Unknown = [Collections.Generic.List[string]]::new()
    foreach ($Field in [string[]]@($Value.Keys)) {
        if (-not $Allowed.Contains($Field)) { $Unknown.Add($Field) }
    }
    if ($Unknown.Count -gt 0) {
        throw (New-GitLoopyRejection (
            "$Name contains unknown field: " +
            (Get-GitLoopyFirstOrdinal $Unknown.ToArray())
        ))
    }
    if ($Present.Contains("advisory_extensions")) {
        $null = Assert-GitLoopyObject `
            $Value["advisory_extensions"] "$Name.advisory_extensions"
    }
}

function Get-GitLoopyRepository {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)
    $Repository = Assert-GitLoopyString $Request["repository"] "repository"
    $Parts = $Repository.Split("/")
    if ($Parts.Count -ne 2 -or $Parts[0].Length -eq 0 -or $Parts[1].Length -eq 0) {
        throw (New-GitLoopyRejection "repository must use owner/name form")
    }
    return $Repository
}

function Get-GitLoopyTrustedProducers {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Request,
        [switch]$AllowEmpty
    )
    $Raw = $Request["trusted_producers"]
    if (
        $Raw -isnot [Collections.IList] -or $Raw -is [string] -or
        (-not $AllowEmpty -and $Raw.Count -eq 0)
    ) {
        $Qualifier = if ($AllowEmpty) { "" } else { "non-empty " }
        throw (New-GitLoopyRejection (
            "trusted_producers must be a ${Qualifier}array"
        ))
    }
    $Producers = [Collections.Generic.List[string]]::new()
    $Seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($Item in $Raw) {
        $Producer = Assert-GitLoopyString $Item "trusted_producers item"
        $Producers.Add($Producer)
        [void]$Seen.Add($Producer)
    }
    if ($Seen.Count -ne $Producers.Count) {
        throw (New-GitLoopyRejection (
            "trusted_producers must not contain duplicates"
        ))
    }
    return $Producers
}

function Get-GitLoopyTrustedApps {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

    $Raw = [object[]]@()
    if ($Request.Contains("trusted_apps")) {
        $Raw = $Request["trusted_apps"]
    }
    if ($Raw -isnot [Collections.IList] -or $Raw -is [string]) {
        throw (New-GitLoopyRejection "trusted_apps must be an array")
    }
    $Apps = [Collections.Generic.List[string]]::new()
    $Seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($Item in $Raw) {
        $App = Assert-GitLoopyString $Item "trusted_apps item"
        $Apps.Add($App)
        [void]$Seen.Add($App)
    }
    if ($Seen.Count -ne $Apps.Count) {
        throw (New-GitLoopyRejection "trusted_apps must not contain duplicates")
    }
    return , $Apps
}

function Get-GitLoopyTrustedReattesters {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

    $Raw = [object[]]@()
    if ($Request.Contains("trusted_reattesters")) {
        $Raw = $Request["trusted_reattesters"]
    }
    if ($Raw -isnot [Collections.IList] -or $Raw -is [string]) {
        throw (New-GitLoopyRejection "trusted_reattesters must be an array")
    }
    $Reattesters = [Collections.Generic.List[string]]::new()
    $Seen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Item in $Raw) {
        $Reattester = Assert-GitLoopyString $Item "trusted_reattesters item"
        $Reattesters.Add($Reattester)
        [void]$Seen.Add($Reattester)
    }
    if ($Seen.Count -ne $Reattesters.Count) {
        throw (New-GitLoopyRejection (
            "trusted_reattesters must not contain duplicates"
        ))
    }
    return , $Reattesters
}

function Test-GitLoopyReattestation {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Request,
        [Parameter(Mandatory)][string]$Producer
    )

    if (-not $Request.Contains("reattestation")) {
        return $null
    }
    $Reattestation = Assert-GitLoopyObject `
        $Request["reattestation"] "reattestation"
    Assert-GitLoopyFields `
        -Value $Reattestation `
        -Name "reattestation" `
        -Required @("affected_heads", "authorized_by", "mode")
    $Affected = Assert-GitLoopyArray `
        $Reattestation["affected_heads"] `
        "reattestation.affected_heads" `
        -NonEmpty
    $Seen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($RevisionId in $Affected) {
        if (
            $RevisionId -isnot [string] -or
            $RevisionId -cnotmatch $Script:DigestPattern
        ) {
            throw (New-GitLoopyRejection (
                "reattestation.affected_heads must contain lowercase " +
                "SHA-256 digests"
            ))
        }
        if (-not $Seen.Add($RevisionId)) {
            throw (New-GitLoopyRejection (
                "reattestation.affected_heads must not contain duplicates"
            ))
        }
    }
    $AuthorizedBy = Assert-GitLoopyString `
        $Reattestation["authorized_by"] "reattestation.authorized_by"
    if ($AuthorizedBy -cne $Producer) {
        throw (New-GitLoopyRejection (
            "reattestation.authorized_by must match the authenticated producer"
        ))
    }
    $TrustedReattesters = Get-GitLoopyTrustedReattesters -Request $Request
    if ($AuthorizedBy -cnotin @($TrustedReattesters)) {
        throw (New-GitLoopyRejection (
            "reattestation actor is not separately authorized"
        ))
    }
    if ($Reattestation["mode"] -cnotin @("copy", "replace", "retire")) {
        throw (New-GitLoopyRejection "reattestation.mode is unsupported")
    }
    return $Reattestation
}

function Assert-GitLoopyDurableReference {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Repository,
        [AllowNull()][string[]]$AllowedKinds
    )
    $Reference = Assert-GitLoopyObject $Value $Name
    $Kind = Assert-GitLoopyString $Reference["kind"] "$Name.kind"
    if ($Kind -cnotin $Script:ReferenceKinds) {
        throw (New-GitLoopyRejection "$Name.kind is unsupported")
    }
    if ($null -ne $AllowedKinds -and $Kind -cnotin $AllowedKinds) {
        $Allowed = (Get-GitLoopyOrdinalJoin $AllowedKinds)
        throw (New-GitLoopyRejection "$Name.kind must be one of: $Allowed")
    }
    $Expected = @("kind") + $Script:ReferenceFields[$Kind]
    Assert-GitLoopyFields -Value $Reference -Name $Name -Required $Expected
    if ($Reference["repository"] -cne $Repository) {
        throw (New-GitLoopyRejection "$Name.repository must match repository")
    }
    foreach ($Field in @("number", "issue", "comment_id", "pull_request", "review_id")) {
        if ($Reference.Contains($Field)) {
            $null = Assert-GitLoopyPositiveInt $Reference[$Field] "$Name.$Field"
        }
    }
    if ($Kind -ceq "commit") {
        $Sha = Assert-GitLoopyString $Reference["sha"] "$Name.sha"
        if ($Sha -cnotmatch $Script:ShaPattern) {
            throw (New-GitLoopyRejection (
                "$Name.sha must be a lowercase 40-character SHA"
            ))
        }
    }
    if ($Kind -ceq "branch") {
        $null = Assert-GitLoopyString $Reference["name"] "$Name.name"
        $Sha = Assert-GitLoopyString $Reference["sha"] "$Name.sha"
        if ($Sha -cnotmatch $Script:ShaPattern) {
            throw (New-GitLoopyRejection (
                "$Name.sha must be a lowercase 40-character SHA"
            ))
        }
    }
    return $Reference
}

function Get-GitLoopyOrdinalJoin {
    param([Parameter(Mandatory)][string[]]$Items)
    $Sorted = [string[]]$Items
    [Array]::Sort($Sorted, [StringComparer]::Ordinal)
    return ($Sorted -join ", ")
}

function Test-GitLoopyCondition {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Repository,
        [bool]$AllowLocal = $true
    )
    $Condition = Assert-GitLoopyObject $Value $Name
    $Kind = Assert-GitLoopyString $Condition["kind"] "$Name.kind"
    if ($Kind -cnotin $Script:ConditionKinds) {
        throw (New-GitLoopyRejection "$Name.kind is unsupported")
    }
    $Schema = $Script:ConditionSchemas[$Kind]
    Assert-GitLoopyFields `
        -Value $Condition `
        -Name $Name `
        -Required $Schema["required_fields"] `
        -Optional $Schema["optional_fields"]
    foreach ($Field in $Schema["string_fields"]) {
        $null = Assert-GitLoopyString $Condition[$Field] "$Name.$Field"
    }
    foreach ($Entry in $Schema["enum_fields"].GetEnumerator()) {
        if (-not (Test-GitLoopyEnumMember $Condition[$Entry.Key] $Entry.Value)) {
            throw (New-GitLoopyRejection "$Name.$($Entry.Key) is unsupported")
        }
    }
    $LocalField = $Schema["local_reference_field"]
    if ($null -ne $LocalField) {
        if (-not $AllowLocal) {
            throw (New-GitLoopyRejection "$Name.kind requires a durable subject")
        }
        return [string]$Condition[$LocalField]
    }
    $null = Assert-GitLoopyDurableReference `
        -Value $Condition["target"] `
        -Name "$Name.target" `
        -Repository $Repository `
        -AllowedKinds $Schema["target_kinds"]
    return $null
}

function Get-GitLoopyInteractionClassification {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][string]$TransitionOwner
    )
    $Name = "completion.actions item.interaction"
    $Interaction = Assert-GitLoopyObject $Value $Name
    Assert-GitLoopyFields `
        -Value $Interaction `
        -Name $Name `
        -Required @("classification", "evidence") `
        -Optional @("advisory_extensions")
    $Classification = Assert-GitLoopyString `
        $Interaction["classification"] "$Name.classification"
    if ($Classification -cnotin $Script:InteractionClassifications) {
        throw (New-GitLoopyRejection "$Name.classification is unsupported")
    }
    $EvidenceName = "$Name.evidence"
    $Evidence = Assert-GitLoopyObject $Interaction["evidence"] $EvidenceName
    if (-not $Evidence.Contains("kind")) {
        throw (New-GitLoopyRejection (
            "$EvidenceName is missing required field: kind"
        ))
    }
    $EvidenceKind = Assert-GitLoopyString $Evidence["kind"] "$EvidenceName.kind"
    if ($EvidenceKind -cnotin @($Script:InteractionEvidenceSchemas.Keys)) {
        throw (New-GitLoopyRejection "$EvidenceName.kind is unsupported")
    }
    $Schema = $Script:InteractionEvidenceSchemas[$EvidenceKind]
    Assert-GitLoopyFields `
        -Value $Evidence `
        -Name $EvidenceName `
        -Required $Schema["required_fields"] `
        -Optional $Schema["optional_fields"]
    if ($Classification -cnotin $Schema["classifications"]) {
        throw (New-GitLoopyRejection (
            "$EvidenceName.kind is incompatible with $Classification"
        ))
    }
    foreach ($Field in $Schema["string_fields"]) {
        $null = Assert-GitLoopyString $Evidence[$Field] "$EvidenceName.$Field"
    }
    foreach ($Entry in $Schema["enum_fields"].GetEnumerator()) {
        if (-not (Test-GitLoopyEnumMember $Evidence[$Entry.Key] $Entry.Value)) {
            throw (New-GitLoopyRejection (
                "$EvidenceName.$($Entry.Key) is unsupported"
            ))
        }
    }
    foreach ($Field in $Schema["condition_fields"]) {
        $null = Test-GitLoopyCondition `
            -Value $Evidence[$Field] `
            -Name "$EvidenceName.$Field" `
            -Repository $Repository `
            -AllowLocal $false
    }
    foreach ($Entry in $Schema["bound_fields"].GetEnumerator()) {
        if ($Entry.Value -ceq "completion.transition.owner") {
            $Expected = $TransitionOwner
        }
        else {
            throw "unsupported interaction evidence binding: $($Entry.Value)"
        }
        if ($Evidence[$Entry.Key] -cne $Expected) {
            throw (New-GitLoopyRejection (
                "$EvidenceName.$($Entry.Key) must match $($Entry.Value)"
            ))
        }
    }
    return $Classification
}

function Test-GitLoopyTypedSemantics {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$Kinds,
        [Parameter(Mandatory)][string]$SecondField
    )
    $Entries = Assert-GitLoopyArray $Value $Name
    for ($Index = 0; $Index -lt $Entries.Count; $Index++) {
        $ItemName = "$Name[$Index]"
        $Entry = Assert-GitLoopyObject $Entries[$Index] $ItemName
        Assert-GitLoopyFields `
            -Value $Entry `
            -Name $ItemName `
            -Required @("kind", $SecondField) `
            -Optional @("advisory_extensions")
        $Kind = Assert-GitLoopyString $Entry["kind"] "$ItemName.kind"
        if ($Kind -cnotin $Kinds) {
            throw (New-GitLoopyRejection "$ItemName.kind is unsupported")
        }
        $null = Assert-GitLoopyString $Entry[$SecondField] "$ItemName.$SecondField"
    }
}

function Test-GitLoopyTriggers {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Repository
    )
    $Entries = Assert-GitLoopyArray $Value $Name
    $LocalReferences = [Collections.Generic.List[string]]::new()
    for ($Index = 0; $Index -lt $Entries.Count; $Index++) {
        $ItemName = "$Name[$Index]"
        $Entry = Assert-GitLoopyObject $Entries[$Index] $ItemName
        Assert-GitLoopyFields `
            -Value $Entry `
            -Name $ItemName `
            -Required @("kind", "condition") `
            -Optional @("advisory_extensions")
        $Kind = Assert-GitLoopyString $Entry["kind"] "$ItemName.kind"
        if ($Kind -cnotin $Script:TriggerKinds) {
            throw (New-GitLoopyRejection "$ItemName.kind is unsupported")
        }
        $LocalReference = Test-GitLoopyCondition `
            -Value $Entry["condition"] `
            -Name "$ItemName.condition" `
            -Repository $Repository
        if ($null -ne $LocalReference) {
            $LocalReferences.Add($LocalReference)
        }
    }
    return $LocalReferences
}

function Test-GitLoopySafetyCase {
    <#
        Structurally validate one positive versioned AFK safety case.

        The safety case is the Transition owner's evidence-backed argument that
        every permitted completion path of *this* Action occurrence is
        unattended. It is therefore bound to the Action it justifies: the exact
        Instruction variant, the exact Target, and the exact objective
        completion condition must be restated here, so a later Instruction or
        Target change invalidates the argument instead of silently inheriting
        it.
    #>
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][Collections.IDictionary]$Action
    )

    $Entry = Assert-GitLoopyObject $Value $Name
    Assert-GitLoopyFields `
        -Value $Entry `
        -Name $Name `
        -Required @(
            "version", "instruction", "target", "completion_condition",
            "effects", "assumptions", "requirements", "retry", "triggers"
        ) `
        -Optional @("advisory_extensions")
    $null = Assert-GitLoopyString $Entry["version"] "$Name.version"
    foreach ($Field in @("instruction", "target", "completion_condition")) {
        if (
            (ConvertTo-GitLoopyCanonicalJson $Entry[$Field]) -cne
            (ConvertTo-GitLoopyCanonicalJson $Action[$Field])
        ) {
            throw (New-GitLoopyRejection (
                "$Name.$Field must match the Action it justifies"
            ))
        }
    }
    Test-GitLoopyTypedSemantics `
        -Value $Entry["effects"] `
        -Name "$Name.effects" `
        -Kinds $Script:EffectKinds `
        -SecondField "scope"
    Test-GitLoopyTypedSemantics `
        -Value $Entry["assumptions"] `
        -Name "$Name.assumptions" `
        -Kinds $Script:AssumptionKinds `
        -SecondField "statement"
    Test-GitLoopyTypedSemantics `
        -Value $Entry["requirements"] `
        -Name "$Name.requirements" `
        -Kinds $Script:RequirementKinds `
        -SecondField "name"
    $RetryName = "$Name.retry"
    $Retry = Assert-GitLoopyObject $Entry["retry"] $RetryName
    Assert-GitLoopyFields `
        -Value $Retry `
        -Name $RetryName `
        -Required @("kind") `
        -Optional @("advisory_extensions")
    if ($Retry["kind"] -cnotin $Script:RetryKinds) {
        throw (New-GitLoopyRejection "$RetryName.kind is unsupported")
    }
    return Test-GitLoopyTriggers `
        -Value $Entry["triggers"] `
        -Name "$Name.triggers" `
        -Repository $Repository
}

function Test-GitLoopyAction {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][string]$TransitionOwner,
        [Parameter(Mandatory)][string]$ContractVersion
    )
    $Action = Assert-GitLoopyObject $Value "completion.actions item"
    Assert-GitLoopyFields `
        -Value $Action `
        -Name "completion.actions item" `
        -Required @(
            "key", "summary", "kind", "occurrence", "instruction",
            "target", "basis", "prerequisites", "interaction",
            "completion_condition"
        ) `
        -Optional @(
            "context_references", "effects", "requirements", "safety_case",
            "triggers", "advisory_extensions"
        )
    foreach ($Field in @("key", "summary", "occurrence")) {
        $null = Assert-GitLoopyString $Action[$Field] "completion.actions item.$Field"
    }
    $Kind = Assert-GitLoopyString $Action["kind"] "completion.actions item.kind"
    if ($Kind -cnotin $Script:ActionKinds) {
        throw (New-GitLoopyRejection "completion.actions item.kind is unsupported")
    }
    $Instruction = Assert-GitLoopyObject `
        $Action["instruction"] "completion.actions item.instruction"
    Assert-GitLoopyFields `
        -Value $Instruction `
        -Name "completion.actions item.instruction" `
        -Required @("mode", "value") `
        -Optional @("behavior_version", "variant", "advisory_extensions")
    if ($Instruction["mode"] -cnotin @("skill", "command", "manual")) {
        throw (New-GitLoopyRejection (
            "completion.actions item.instruction.mode is unsupported"
        ))
    }
    $InstructionValue = Assert-GitLoopyString `
        $Instruction["value"] "completion.actions item.instruction.value"
    if ($InstructionValue.Contains("`n") -or $InstructionValue.Contains("`r")) {
        throw (New-GitLoopyRejection (
            "completion.actions item.instruction.value must be one line"
        ))
    }
    if (
        $Instruction["mode"] -ceq "skill" -and
        -not $InstructionValue.StartsWith("/", [StringComparison]::Ordinal)
    ) {
        throw (New-GitLoopyRejection (
            "completion.actions item.instruction.value must name a canonical Skill"
        ))
    }
    foreach ($Field in @("behavior_version", "variant")) {
        if ($Instruction.Contains($Field)) {
            $null = Assert-GitLoopyString `
                $Instruction[$Field] "completion.actions item.instruction.$Field"
        }
    }
    $null = Assert-GitLoopyDurableReference `
        -Value $Action["target"] `
        -Name "completion.actions item.target" `
        -Repository $Repository
    foreach ($Item in (Assert-GitLoopyArray `
                $Action["basis"] "completion.actions item.basis" -NonEmpty)) {
        $null = Assert-GitLoopyDurableReference `
            -Value $Item `
            -Name "completion.actions item.basis item" `
            -Repository $Repository
    }
    $LocalReferences = [Collections.Generic.List[string]]::new()
    foreach ($Prerequisite in (Assert-GitLoopyArray `
                $Action["prerequisites"] "completion.actions item.prerequisites")) {
        $LocalReference = Test-GitLoopyCondition `
            -Value $Prerequisite `
            -Name "completion.actions item.prerequisites item" `
            -Repository $Repository
        if ($null -ne $LocalReference) {
            $LocalReferences.Add($LocalReference)
        }
    }
    $Classification = Get-GitLoopyInteractionClassification `
        -Value $Action["interaction"] `
        -Repository $Repository `
        -TransitionOwner $TransitionOwner
    if ($Instruction["mode"] -ceq "manual" -and $Classification -cne "HITL-required") {
        throw (New-GitLoopyRejection "manual Instructions must be HITL-required")
    }
    if ($Classification -cnotin $Script:ActionKindSchemas[$Kind]) {
        throw (New-GitLoopyRejection "$Kind Actions must be HITL-required")
    }
    $CompletionLocal = Test-GitLoopyCondition `
        -Value $Action["completion_condition"] `
        -Name "completion.actions item.completion_condition" `
        -Repository $Repository
    if ($null -ne $CompletionLocal) {
        $LocalReferences.Add($CompletionLocal)
    }
    if ($Action.Contains("context_references")) {
        foreach ($Reference in (Assert-GitLoopyArray `
                    $Action["context_references"] "completion.actions item.context_references")) {
            $null = Assert-GitLoopyDurableReference `
                -Value $Reference `
                -Name "completion.actions item.context_references item" `
                -Repository $Repository
        }
    }
    if ($Action.Contains("effects")) {
        Test-GitLoopyTypedSemantics `
            -Value $Action["effects"] `
            -Name "completion.actions item.effects" `
            -Kinds $Script:EffectKinds `
            -SecondField "scope"
    }
    if ($Action.Contains("requirements")) {
        Test-GitLoopyTypedSemantics `
            -Value $Action["requirements"] `
            -Name "completion.actions item.requirements" `
            -Kinds $Script:RequirementKinds `
            -SecondField "name"
    }
    if ($Action.Contains("triggers")) {
        $TriggerReferences = Test-GitLoopyTriggers `
            -Value $Action["triggers"] `
            -Name "completion.actions item.triggers" `
            -Repository $Repository
        foreach ($Reference in $TriggerReferences) {
            $LocalReferences.Add($Reference)
        }
    }
    if ($Action.Contains("safety_case")) {
        # A 1.1 reader drops the unknown field and keeps the AFK-safe claim --
        # precisely the half of the pair that authorizes unattended Dispatch.
        # Dropping the justification while keeping the claim is worth a hard
        # rejection, so the record must declare the version that carries it.
        if ($ContractVersion -cne $Script:SafetyCaseContractVersion) {
            throw (New-GitLoopyRejection (
                "a safety case requires Continuation contract version " +
                $Script:SafetyCaseContractVersion
            ))
        }
        if ($Classification -cne "AFK-safe") {
            throw (New-GitLoopyRejection (
                "only AFK-safe Actions may carry a safety case"
            ))
        }
        foreach ($Field in @("effects", "requirements", "triggers")) {
            if ($Action.Contains($Field)) {
                throw (New-GitLoopyRejection (
                    "completion.actions item.safety_case owns $Field; declare it once"
                ))
            }
        }
        $CaseReferences = Test-GitLoopySafetyCase `
            -Value $Action["safety_case"] `
            -Name "completion.actions item.safety_case" `
            -Repository $Repository `
            -Action $Action
        foreach ($Reference in $CaseReferences) {
            $LocalReferences.Add($Reference)
        }
    }
    return [ordered]@{
        Action = $Action
        LocalReferences = $LocalReferences
    }
}

function Test-GitLoopyCompletion {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Request
    )

    Assert-GitLoopyFields `
        -Value $Request `
        -Name "request" `
        -Required @("repository", "trusted_producers", "completion") `
        -Optional @(
            "trusted_apps", "trusted_reattesters", "observation", "parents",
            "reattestation"
        )
    $Repository = Get-GitLoopyRepository $Request
    $TrustedApps = Get-GitLoopyTrustedApps -Request $Request
    $Completion = Assert-GitLoopyObject $Request["completion"] "completion"
    $OptionalCompletionFields = [object[]]@(
        "carrier", "actions", "outcome", "no_guidance", "retirements",
        "advisory_extensions"
    )
    Assert-GitLoopyFields `
        -Value $Completion `
        -Name "completion" `
        -Required @(
            "continuation_contract_version", "record_format", "publication",
            "disposition", "workstream", "transition", "producer"
        ) `
        -Optional $OptionalCompletionFields
    if ($Completion["continuation_contract_version"] -cnotin
        $Script:SupportedContinuationContractVersions) {
        throw (New-GitLoopyRejection "unsupported Continuation contract version")
    }
    if ($Completion["record_format"] -ne $Script:RecordFormat) {
        throw (New-GitLoopyRejection "unsupported Continuation record format")
    }
    $Publication = $Completion["publication"]
    if ($Publication -cnotin $Script:Publications) {
        throw (New-GitLoopyRejection "completion.publication is unsupported")
    }
    $Disposition = $Completion["disposition"]
    if ($Disposition -cnotin $Script:Dispositions) {
        throw (New-GitLoopyRejection "completion.disposition is unsupported")
    }
    $TrustedRaw = $Request["trusted_producers"]
    if ($TrustedRaw -isnot [Collections.IList] -or $TrustedRaw -is [string]) {
        throw (New-GitLoopyRejection "trusted_producers must be an array")
    }
    $Trusted = Get-GitLoopyTrustedProducers `
        -Request $Request `
        -AllowEmpty:($Publication -ceq "ephemeral" -or $TrustedApps.Count -gt 0)
    $Workstream = Assert-GitLoopyObject $Completion["workstream"] "completion.workstream"
    $WorkstreamRequired = if ($Publication -ceq "shared") {
        @("destination", "anchor")
    }
    else {
        @("destination")
    }
    $WorkstreamOptional = if ($Publication -ceq "shared") {
        @("advisory_extensions")
    }
    else {
        @("anchor", "advisory_extensions")
    }
    Assert-GitLoopyFields `
        -Value $Workstream `
        -Name "completion.workstream" `
        -Required $WorkstreamRequired `
        -Optional $WorkstreamOptional
    if ($Workstream.Contains("anchor")) {
        $null = Assert-GitLoopyDurableReference `
            -Value $Workstream["anchor"] `
            -Name "completion.workstream.anchor" `
            -Repository $Repository
    }
    $null = Test-GitLoopyCondition `
        -Value $Workstream["destination"] `
        -Name "completion.workstream.destination" `
        -Repository $Repository `
        -AllowLocal $false
    $Transition = Assert-GitLoopyObject $Completion["transition"] "completion.transition"
    Assert-GitLoopyFields `
        -Value $Transition `
        -Name "completion.transition" `
        -Required @("owner", "evidence") `
        -Optional @("advisory_extensions")
    $TransitionOwner = Assert-GitLoopyString `
        $Transition["owner"] "completion.transition.owner"
    $Evidence = Assert-GitLoopyArray `
        $Transition["evidence"] "completion.transition.evidence"
    if ($Publication -ceq "shared" -and $Evidence.Count -eq 0) {
        throw (New-GitLoopyRejection (
            "completion.transition.evidence must be non-empty"
        ))
    }
    foreach ($Item in $Evidence) {
        $null = Assert-GitLoopyDurableReference `
            -Value $Item `
            -Name "completion.transition.evidence item" `
            -Repository $Repository `
            -AllowedKinds @("issue-comment")
    }
    $Producer = Assert-GitLoopyObject $Completion["producer"] "completion.producer"
    Assert-GitLoopyFields `
        -Value $Producer `
        -Name "completion.producer" `
        -Required @("login", "role") `
        -Optional @("advisory_extensions")
    $Login = Assert-GitLoopyString $Producer["login"] "completion.producer.login"
    if ($Producer["role"] -cne "planning") {
        throw (New-GitLoopyRejection "completion.producer.role must be planning")
    }
    if (
        $Publication -ceq "shared" -and
        $Login -cnotin @($Trusted) -and
        $Login -cnotin @($TrustedApps)
    ) {
        throw (New-GitLoopyRejection "completion producer is not trusted")
    }
    if ($Publication -ceq "shared") {
        $null = Assert-GitLoopyDurableReference `
            -Value $Completion["carrier"] `
            -Name "completion.carrier" `
            -Repository $Repository `
            -AllowedKinds @("issue")
    }
    elseif ($Completion.Contains("carrier")) {
        throw (New-GitLoopyRejection (
            "ephemeral completion must not contain a carrier"
        ))
    }

    $ContentFields = [ordered]@{
        "continue" = "actions"
        "terminal" = "outcome"
        "no-guidance" = "no_guidance"
    }
    $ExpectedContent = $ContentFields[$Disposition]
    $Present = [Collections.Generic.List[string]]::new()
    foreach ($Field in $ContentFields.Values) {
        if ($Completion.Contains($Field)) { $Present.Add($Field) }
    }
    if ($Present.Count -ne 1 -or $Present[0] -cne $ExpectedContent) {
        throw (New-GitLoopyRejection (
            "completion must contain exactly one content branch matching disposition"
        ))
    }

    if ($Disposition -ceq "continue") {
        $Actions = Assert-GitLoopyArray `
            $Completion["actions"] "completion.actions" -NonEmpty
        $Keys = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        $LocalReferences = [Collections.Generic.List[object]]::new()
        foreach ($Item in $Actions) {
            $Validated = Test-GitLoopyAction `
                -Value $Item `
                -Repository $Repository `
                -TransitionOwner $TransitionOwner `
                -ContractVersion ([string]$Completion["continuation_contract_version"])
            $Key = [string]$Validated.Action["key"]
            if ($Keys.Contains($Key)) {
                throw (New-GitLoopyRejection (
                    "completion.actions contains duplicate local key: $Key"
                ))
            }
            [void]$Keys.Add($Key)
            foreach ($Reference in $Validated.LocalReferences) {
                $LocalReferences.Add([ordered]@{ Owner = $Key; Reference = $Reference })
            }
        }
        foreach ($Entry in $LocalReferences) {
            if (-not $Keys.Contains($Entry.Reference)) {
                throw (New-GitLoopyRejection (
                    "completion.actions contains broken local reference: $($Entry.Reference)"
                ))
            }
            if ($Entry.Reference -ceq $Entry.Owner) {
                throw (New-GitLoopyRejection (
                    "completion.actions contains self-reference: $($Entry.Reference)"
                ))
            }
        }
    }
    elseif ($Disposition -ceq "terminal") {
        if ($Publication -cne "shared") {
            throw (New-GitLoopyRejection "terminal completion must be shared")
        }
        $Outcome = Assert-GitLoopyObject $Completion["outcome"] "completion.outcome"
        Assert-GitLoopyFields `
            -Value $Outcome `
            -Name "completion.outcome" `
            -Required @(
                "kind", "destination_satisfied", "effective_at",
                "evidence", "summary"
            ) `
            -Optional @("successor", "advisory_extensions")
        $OutcomeKind = Assert-GitLoopyString $Outcome["kind"] "completion.outcome.kind"
        if ($OutcomeKind -cnotin $Script:OutcomeKinds) {
            throw (New-GitLoopyRejection "completion.outcome.kind is unsupported")
        }
        $DestinationSatisfied = $Outcome["destination_satisfied"]
        if ($DestinationSatisfied -isnot [bool]) {
            throw (New-GitLoopyRejection (
                "completion.outcome.destination_satisfied must be a boolean"
            ))
        }
        if ([bool]$DestinationSatisfied -ne ($OutcomeKind -ceq "complete")) {
            throw (New-GitLoopyRejection (
                "completion.outcome contradicts destination satisfaction"
            ))
        }
        $EffectiveAt = Assert-GitLoopyString `
            $Outcome["effective_at"] "completion.outcome.effective_at"
        $ParsedEffectiveAt = [DateTimeOffset]::MinValue
        $ParsedOk = [DateTimeOffset]::TryParse(
            $EffectiveAt.Replace("Z", "+00:00"),
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::None,
            [ref]$ParsedEffectiveAt
        )
        if (
            -not $EffectiveAt.Contains("T") -or
            -not $EffectiveAt.EndsWith("Z", [StringComparison]::Ordinal) -or
            -not $ParsedOk -or
            $ParsedEffectiveAt.Offset -ne [TimeSpan]::Zero
        ) {
            throw (New-GitLoopyRejection (
                "completion.outcome.effective_at must be an RFC3339 UTC timestamp"
            ))
        }
        $null = Assert-GitLoopyString $Outcome["summary"] "completion.outcome.summary"
        foreach ($Item in (Assert-GitLoopyArray `
                    $Outcome["evidence"] "completion.outcome.evidence" -NonEmpty)) {
            $null = Assert-GitLoopyDurableReference `
                -Value $Item `
                -Name "completion.outcome.evidence item" `
                -Repository $Repository
        }
        if ($OutcomeKind -ceq "superseded") {
            $null = Assert-GitLoopyDurableReference `
                -Value $Outcome["successor"] `
                -Name "completion.outcome.successor" `
                -Repository $Repository
        }
        elseif ($Outcome.Contains("successor")) {
            throw (New-GitLoopyRejection (
                "completion.outcome.successor is valid only for superseded"
            ))
        }
    }
    else {
        $NoGuidance = Assert-GitLoopyObject `
            $Completion["no_guidance"] "completion.no_guidance"
        Assert-GitLoopyFields `
            -Value $NoGuidance `
            -Name "completion.no_guidance" `
            -Required @("reason", "summary", "references") `
            -Optional @("advisory_extensions")
        $Reason = Assert-GitLoopyString `
            $NoGuidance["reason"] "completion.no_guidance.reason"
        if ($Reason -cnotin $Script:NoGuidanceReasons) {
            throw (New-GitLoopyRejection (
                "completion.no_guidance.reason is unsupported"
            ))
        }
        $Combination = "$Publication|$Reason"
        if ($Combination -cne "shared|no-successor-created" -and
            $Combination -cne "ephemeral|ephemeral-only") {
            throw (New-GitLoopyRejection (
                "completion publication contradicts no-guidance reason"
            ))
        }
        $null = Assert-GitLoopyString `
            $NoGuidance["summary"] "completion.no_guidance.summary"
        foreach ($Item in (Assert-GitLoopyArray `
                    $NoGuidance["references"] "completion.no_guidance.references" -NonEmpty)) {
            $null = Assert-GitLoopyDurableReference `
                -Value $Item `
                -Name "completion.no_guidance.references item" `
                -Repository $Repository
        }
    }

    if ($Completion.Contains("retirements")) {
        $SeenReceipts = [Collections.Generic.HashSet[string]]::new()
        $Receipts = Assert-GitLoopyArray `
            $Completion["retirements"] "completion.retirements"
        for ($Index = 0; $Index -lt $Receipts.Count; $Index++) {
            $Name = "completion.retirements[$Index]"
            $Receipt = Assert-GitLoopyObject $Receipts[$Index] $Name
            Assert-GitLoopyFields `
                -Value $Receipt `
                -Name $Name `
                -Required @(
                    "predecessor_revision_id", "action_key", "reason", "evidence"
                ) `
                -Optional @("replacement", "advisory_extensions")
            $Predecessor = Assert-GitLoopyString `
                $Receipt["predecessor_revision_id"] "$Name.predecessor_revision_id"
            if ($Predecessor -cnotmatch $Script:DigestPattern) {
                throw (New-GitLoopyRejection (
                    "$Name.predecessor_revision_id must be a sha256 revision identity"
                ))
            }
            $ActionKey = Assert-GitLoopyString $Receipt["action_key"] "$Name.action_key"
            $Reason = Assert-GitLoopyString $Receipt["reason"] "$Name.reason"
            if ($Reason -cnotin $Script:RetirementReasons) {
                throw (New-GitLoopyRejection "$Name.reason is unsupported")
            }
            foreach ($Item in (Assert-GitLoopyArray `
                        $Receipt["evidence"] "$Name.evidence" -NonEmpty)) {
                $null = Assert-GitLoopyDurableReference `
                    -Value $Item `
                    -Name "$Name.evidence item" `
                    -Repository $Repository
            }
            if ($Receipt.Contains("replacement")) {
                if ($Reason -cne "supersession") {
                    throw (New-GitLoopyRejection (
                        "$Name.replacement is valid only when reason is supersession"
                    ))
                }
                $Replacement = Assert-GitLoopyObject $Receipt["replacement"] "$Name.replacement"
                Assert-GitLoopyFields `
                    -Value $Replacement `
                    -Name "$Name.replacement" `
                    -Required @("workstream_anchor", "kind", "target", "occurrence") `
                    -Optional @("advisory_extensions")
                $null = Assert-GitLoopyDurableReference `
                    -Value $Replacement["workstream_anchor"] `
                    -Name "$Name.replacement.workstream_anchor" `
                    -Repository $Repository
                $ReplacementKind = Assert-GitLoopyString `
                    $Replacement["kind"] "$Name.replacement.kind"
                if ($ReplacementKind -cnotin $Script:ActionKinds) {
                    throw (New-GitLoopyRejection "$Name.replacement.kind is unsupported")
                }
                $null = Assert-GitLoopyDurableReference `
                    -Value $Replacement["target"] `
                    -Name "$Name.replacement.target" `
                    -Repository $Repository
                $null = Assert-GitLoopyString `
                    $Replacement["occurrence"] "$Name.replacement.occurrence"
            }
            elseif ($Reason -ceq "supersession") {
                throw (New-GitLoopyRejection (
                    "$Name with reason supersession must declare a replacement"
                ))
            }
            if (-not $SeenReceipts.Add("$Predecessor`u{0}$ActionKey")) {
                throw (New-GitLoopyRejection (
                    "completion.retirements contains duplicate " +
                    "predecessor_revision_id/action_key pair"
                ))
            }
        }
    }

    $CanonicalCompletion = [Text.Encoding]::UTF8.GetByteCount(
        (ConvertTo-GitLoopyCanonicalJson $Completion)
    )
    if ($CanonicalCompletion -gt $Script:MaxRecordBytes) {
        throw (New-GitLoopyRejection (
            "completion canonical JSON exceeds maximum record length $Script:MaxRecordBytes"
        ))
    }
    return [ordered]@{
        Repository = $Repository
        Trusted = $Trusted
        Completion = $Completion
        Publication = $Publication
    }
}

function New-GitLoopyRecordBody {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Completion,
        [switch]$RevisionProtocol,
        [AllowNull()][object]$Parents,
        [AllowNull()][object]$Reattestation
    )

    $IdentitySource = $Completion
    if (
        $RevisionProtocol -and
        ($Parents.Count -gt 0 -or $null -ne $Reattestation)
    ) {
        $IdentitySource = [ordered]@{
            completion = $Completion
            parents = $Parents
        }
        if ($null -ne $Reattestation) {
            $IdentitySource["reattestation"] = $Reattestation
        }
    }
    $RevisionId = Get-GitLoopySha256 (
        ConvertTo-GitLoopyCanonicalJson $IdentitySource
    )
    $Fingerprints = Get-GitLoopySemanticFingerprints $Completion
    $Record = [ordered]@{
        revision_id = $RevisionId
        semantic_fingerprints = $Fingerprints
    }
    if ($RevisionProtocol) {
        $Record["parents"] = $Parents
    }
    if ($null -ne $Reattestation) {
        $Record["reattestation"] = $Reattestation
    }
    foreach ($Entry in $Completion.GetEnumerator()) {
        $Record[$Entry.Key] = $Entry.Value
    }
    $CanonicalRecord = ConvertTo-GitLoopyCanonicalJson $Record
    if (
        [Text.Encoding]::UTF8.GetByteCount($CanonicalRecord) -gt $Script:MaxRecordBytes
    ) {
        throw (New-GitLoopyRejection (
            "Producer revision exceeds maximum record length $Script:MaxRecordBytes"
        ))
    }
    $Body = "$Script:RecordMarker`n``````json`n$CanonicalRecord`n``````"
    if ([Text.Encoding]::UTF8.GetByteCount($Body) -gt $Script:MaxCarrierBodyBytes) {
        throw (New-GitLoopyRejection (
            "Producer revision exceeds live carrier body limit"
        ))
    }
    return [ordered]@{
        RevisionId = $RevisionId
        Fingerprints = $Fingerprints
        Body = $Body
    }
}

function Invoke-GitLoopyGitHub {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [AllowNull()]
        [object]$InputValue,
        [Parameter(Mandatory)]
        [string]$Context,
        [switch]$NoJson
    )

    try {
        $GitHubCommand = Get-Command `
            gh `
            -CommandType Application `
            -ErrorAction Stop |
            Select-Object -First 1
    }
    catch {
        throw [GitLoopyContinuationGitHubException]::new(
            "locating the GitHub CLI"
        )
    }
    if (
        $null -eq $GitHubCommand -or
        [string]::IsNullOrWhiteSpace($GitHubCommand.Source) -or
        -not [IO.Path]::IsPathFullyQualified($GitHubCommand.Source)
    ) {
        throw [GitLoopyContinuationGitHubException]::new(
            "locating the GitHub CLI"
        )
    }

    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $GitHubCommand.Source
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    # A `gh` that resolves to a `.cmd`/`.bat` shim -- Scoop and npm publish those,
    # and the Windows Conformance harness is one -- is started through `cmd.exe`,
    # which re-parses the command line *after* .NET has escaped it. .NET quotes
    # only arguments holding whitespace or a quote, so the bare `&` in a paginated
    # `gh api ...?state=all&per_page=100&page=1` arrives at cmd as a command
    # separator: the request is truncated at the first `&` and the rest is run as
    # commands. Quoting each argument for that second parse makes cmd read the one
    # literal argument every other platform already receives.
    $Extension = [IO.Path]::GetExtension($GitHubCommand.Source)
    if ($Extension.ToLowerInvariant() -in @(".cmd", ".bat")) {
        $StartInfo.Arguments = (
            $Arguments | ForEach-Object { '"' + ($_ -replace '"', '""') + '"' }
        ) -join " "
    }
    else {
        foreach ($Argument in $Arguments) {
            $StartInfo.ArgumentList.Add($Argument)
        }
    }

    $Process = $null
    try {
        $Process = [Diagnostics.Process]::new()
        $Process.StartInfo = $StartInfo
        if (-not $Process.Start()) {
            throw [GitLoopyContinuationGitHubException]::new($Context)
        }
        if ($null -ne $InputValue) {
            $InputJson = ConvertTo-Json `
                -InputObject $InputValue `
                -Compress `
                -Depth 50
            $Process.StandardInput.Write($InputJson)
        }
        $Process.StandardInput.Close()
        $StdoutTask = $Process.StandardOutput.ReadToEndAsync()
        $StderrTask = $Process.StandardError.ReadToEndAsync()
        $Process.WaitForExit()
        $Stdout = $StdoutTask.GetAwaiter().GetResult()
        $Stderr = $StderrTask.GetAwaiter().GetResult()
        if ($Process.ExitCode -ne 0) {
            throw [GitLoopyContinuationGitHubException]::new(
                $Context,
                $Stderr.Trim()
            )
        }
    }
    catch [GitLoopyContinuationGitHubException] {
        throw
    }
    catch {
        throw [GitLoopyContinuationGitHubException]::new($Context)
    }
    finally {
        if ($null -ne $Process) {
            $Process.Dispose()
        }
    }

    if ($NoJson) {
        return $null
    }
    try {
        $Parsed = $Stdout | ConvertFrom-Json -AsHashtable -NoEnumerate -DateKind String
        if ($Parsed -is [Collections.IList]) {
            return , $Parsed
        }
        return $Parsed
    }
    catch {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding $Context"
        )
    }
}

function Test-GitLoopyObservation {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Request,
        [Parameter(Mandatory)][string]$Repository
    )

    $Observation = Assert-GitLoopyObject $Request["observation"] "observation"
    Assert-GitLoopyFields `
        -Value $Observation `
        -Name "observation" `
        -Required @("heads", "token", "validators")
    $Heads = Assert-GitLoopyArray $Observation["heads"] "observation.heads"
    $Validators = Assert-GitLoopyArray `
        $Observation["validators"] "observation.validators"
    $ParentIds = [Collections.Generic.List[string]]::new()
    foreach ($Item in $Heads) {
        $Head = Assert-GitLoopyObject $Item "observation.heads item"
        Assert-GitLoopyFields `
            -Value $Head `
            -Name "observation.heads item" `
            -Required @(
                "carrier", "producer", "revision_id", "workstream_anchor"
            )
        $null = Assert-GitLoopyPositiveInt `
            $Head["carrier"] "observation.heads item.carrier"
        $null = Assert-GitLoopyString `
            $Head["producer"] "observation.heads item.producer"
        $RevisionId = Assert-GitLoopyString `
            $Head["revision_id"] "observation.heads item.revision_id"
        if ($RevisionId -cnotmatch $Script:DigestPattern) {
            throw (New-GitLoopyRejection (
                "observation.heads item.revision_id must be a lowercase SHA-256 digest"
            ))
        }
        $null = Assert-GitLoopyDurableReference `
            -Value $Head["workstream_anchor"] `
            -Name "observation.heads item.workstream_anchor" `
            -Repository $Repository
        $ParentIds.Add($RevisionId)
    }
    foreach ($Item in $Validators) {
        $Validator = Assert-GitLoopyObject $Item "observation.validators item"
        Assert-GitLoopyFields `
            -Value $Validator `
            -Name "observation.validators item" `
            -Required @("comment_id", "sha256")
        $null = Assert-GitLoopyPositiveInt `
            $Validator["comment_id"] "observation.validators item.comment_id"
        $Digest = Assert-GitLoopyString `
            $Validator["sha256"] "observation.validators item.sha256"
        if ($Digest -cnotmatch $Script:DigestPattern) {
            throw (New-GitLoopyRejection (
                "observation.validators item.sha256 must be a lowercase SHA-256 digest"
            ))
        }
    }
    $UniqueParents = [Collections.Generic.HashSet[string]]::new(
        $ParentIds, [StringComparer]::Ordinal
    )
    if ($UniqueParents.Count -ne $ParentIds.Count) {
        throw (New-GitLoopyRejection "observation.heads must not contain duplicates")
    }
    $ExpectedToken = "sha256:" + (
        Get-GitLoopySha256 (
            ConvertTo-GitLoopyCanonicalJson ([ordered]@{
                repository = $Repository
                heads = $Heads
                validators = $Validators
            })
        )
    )
    if ($Observation["token"] -cne $ExpectedToken) {
        throw (New-GitLoopyRejection (
            "observation token does not match its bound state"
        ))
    }
    $Parents = Assert-GitLoopyArray $Request["parents"] "parents"
    if ($Parents.Count -ne $ParentIds.Count) {
        throw (New-GitLoopyRejection (
            "parents must name the observed heads in order"
        ))
    }
    for ($Index = 0; $Index -lt $Parents.Count; $Index++) {
        if (
            $Parents[$Index] -isnot [string] -or
            $Parents[$Index] -cne $ParentIds[$Index]
        ) {
            throw (New-GitLoopyRejection (
                "parents must name the observed heads in order"
            ))
        }
    }
    return [ordered]@{
        Observation = $Observation
        Parents = [object[]]@($Parents)
    }
}

function Assert-GitLoopyObservedStateCurrent {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Observation,
        [Parameter(Mandatory)][Collections.IDictionary]$Completion,
        [Parameter(Mandatory)][Collections.IList]$Carriers
    )

    $Comments = [ordered]@{}
    foreach ($Carrier in $Carriers) {
        foreach ($Comment in $Carrier["comments"]) {
            $Comments[[string]$Comment["id"]] = $Comment
        }
    }
    foreach ($Validator in $Observation["validators"]) {
        $CommentId = [string]$Validator["comment_id"]
        if (-not $Comments.Contains($CommentId)) {
            throw (New-GitLoopyRepairRequired (
                "observed Producer revision was deleted; repair required"
            ))
        }
        if (
            (Get-GitLoopySha256 ([string]$Comments[$CommentId]["body"])) -cne
            $Validator["sha256"]
        ) {
            throw (New-GitLoopyRepairRequired (
                "observed Producer revision was mutated; repair required"
            ))
        }
    }
    $CarrierNumber = [long]$Completion["carrier"]["number"]
    $Producer = [string]$Completion["producer"]["login"]
    $AnchorJson = ConvertTo-GitLoopyCanonicalJson `
        $Completion["workstream"]["anchor"]
    foreach ($Head in $Observation["heads"]) {
        if (
            [long]$Head["carrier"] -ne $CarrierNumber -or
            $Head["producer"] -cne $Producer -or
            (ConvertTo-GitLoopyCanonicalJson $Head["workstream_anchor"]) -cne
            $AnchorJson
        ) {
            throw (New-GitLoopyRejection (
                "observed heads must belong to the completion Producer lineage"
            ))
        }
        $Matched = $false
        foreach ($Carrier in $Carriers) {
            if ([long]$Carrier["number"] -ne $CarrierNumber) {
                continue
            }
            foreach ($Comment in $Carrier["comments"]) {
                if ($Comment["author"] -cne $Producer) {
                    continue
                }
                try {
                    $Record = Read-GitLoopyRevisionRecord $Comment
                }
                catch [GitLoopyContinuationRejection] {
                    continue
                }
                if (
                    $null -ne $Record -and
                    $Record["revision_id"] -ceq $Head["revision_id"]
                ) {
                    $Matched = $true
                    break
                }
            }
            if ($Matched) {
                break
            }
        }
        if (-not $Matched) {
            throw (New-GitLoopyRepairRequired (
                "observed Producer predecessor is missing or unauthorized; " +
                "repair required"
            ))
        }
    }
}

function Assert-GitLoopyAuthorizedProducer {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Request,
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][string]$Producer
    )

    $Actor = Invoke-GitLoopyGitHub `
        -Arguments @("api", "user") `
        -Context "reading the authenticated GitHub actor"
    if (
        $Actor -isnot [Collections.IDictionary] -or
        $Actor["login"] -isnot [string] -or
        $Actor["type"] -isnot [string]
    ) {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding the authenticated GitHub actor"
        )
    }
    if ($Actor["login"] -cne $Producer) {
        throw (New-GitLoopyRejection (
            "authenticated actor does not match completion producer"
        ))
    }
    if ($Actor["type"] -cin @("Bot", "App")) {
        $TrustedApps = Get-GitLoopyTrustedApps -Request $Request
        if ($Producer -cnotin @($TrustedApps)) {
            throw (New-GitLoopyRejection (
                "authenticated App producer is not allowlisted"
            ))
        }
        return
    }
    $Trusted = Get-GitLoopyTrustedProducers -Request $Request
    if ($Producer -cnotin @($Trusted)) {
        throw (New-GitLoopyRejection (
            "authenticated human producer is not trusted"
        ))
    }
    $Permission = Invoke-GitLoopyGitHub `
        -Arguments @(
            "api",
            "repos/$Repository/collaborators/$Producer/permission"
        ) `
        -Context "reading Producer repository permission"
    if (
        $Permission -isnot [Collections.IDictionary] -or
        $Permission["permission"] -isnot [string]
    ) {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding Producer repository permission"
        )
    }
    if (
        ([string]$Permission["permission"]).ToUpperInvariant() -cnotin
        $Script:WritePermissions
    ) {
        throw (New-GitLoopyRejection (
            "authenticated human producer lacks current write permission"
        ))
    }
}

function Assert-GitLoopyAuthorizedPolicyActor {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Request,
        [Parameter(Mandatory)][string]$Repository
    )

    $Actor = Invoke-GitLoopyGitHub `
        -Arguments @("api", "user") `
        -Context "reading the authenticated GitHub actor"
    if (
        $Actor -isnot [Collections.IDictionary] -or
        $Actor["login"] -isnot [string] -or
        $Actor["type"] -isnot [string]
    ) {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding the authenticated GitHub actor"
        )
    }
    $Login = [string]$Actor["login"]
    if ($Actor["type"] -cin @("Bot", "App")) {
        if ($Login -cnotin @(Get-GitLoopyTrustedApps -Request $Request)) {
            throw (New-GitLoopyRejection (
                "authenticated App actor is not allowlisted"
            ))
        }
        return
    }
    if ($Login -cnotin @(Get-GitLoopyTrustedProducers -Request $Request)) {
        throw (New-GitLoopyRejection (
            "authenticated human actor is not trusted"
        ))
    }
    $Permission = Invoke-GitLoopyGitHub `
        -Arguments @(
            "api",
            "repos/$Repository/collaborators/$Login/permission"
        ) `
        -Context "reading Producer repository permission"
    if (
        $Permission -isnot [Collections.IDictionary] -or
        $Permission["permission"] -isnot [string]
    ) {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding Producer repository permission"
        )
    }
    if (
        ([string]$Permission["permission"]).ToUpperInvariant() -cnotin
        $Script:WritePermissions
    ) {
        throw (New-GitLoopyRejection (
            "authenticated human actor lacks current write permission"
        ))
    }
}

function Get-GitLoopyAllContinuationCarriers {
    param([Parameter(Mandatory)][string]$Repository)

    $Result = [Collections.Generic.List[object]]::new()
    $PageNumber = 1
    while ($true) {
        $Page = Invoke-GitLoopyGitHub `
            -Arguments @(
                "api",
                (
                    "repos/$Repository/issues?state=all&per_page=100" +
                    "&page=$PageNumber"
                )
            ) `
            -Context "discovering all Producer carriers"
        if ($Page -isnot [Collections.IList]) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding all Producer carriers"
            )
        }
        foreach ($Item in $Page) {
            if ($Item -isnot [Collections.IDictionary]) {
                throw [GitLoopyContinuationGitHubException]::new(
                    "decoding all Producer carriers"
                )
            }
            if ($Item.Contains("pull_request")) {
                continue
            }
            if (
                -not (
                    $Item["number"] -is [int] -or
                    $Item["number"] -is [long]
                ) -or
                $Item["state"] -isnot [string] -or
                $Item["html_url"] -isnot [string] -or
                $Item["labels"] -isnot [Collections.IList] -or
                -not (
                    $Item["comments"] -is [int] -or
                    $Item["comments"] -is [long]
                )
            ) {
                throw [GitLoopyContinuationGitHubException]::new(
                    "decoding all Producer carriers"
                )
            }
            $Labels = [Collections.Generic.List[string]]::new()
            foreach ($Label in $Item["labels"]) {
                if (
                    $Label -is [Collections.IDictionary] -and
                    $Label["name"] -is [string]
                ) {
                    $Labels.Add([string]$Label["name"])
                }
            }
            $Comments = [Collections.Generic.List[object]]::new()
            if ([long]$Item["comments"] -gt 0) {
                $CommentPageNumber = 1
                while ($true) {
                    $CommentPage = Invoke-GitLoopyGitHub `
                        -Arguments @(
                            "api",
                            (
                                "repos/$Repository/issues/$($Item["number"])" +
                                "/comments?per_page=100&page=$CommentPageNumber"
                            )
                        ) `
                        -Context "reading Producer carrier comments"
                    if ($CommentPage -isnot [Collections.IList]) {
                        throw [GitLoopyContinuationGitHubException]::new(
                            "decoding Producer carrier comments"
                        )
                    }
                    foreach ($Comment in $CommentPage) {
                        if ($Comment -isnot [Collections.IDictionary]) {
                            throw [GitLoopyContinuationGitHubException]::new(
                                "decoding Producer carrier comments"
                            )
                        }
                        $Author = if (
                            $Comment["user"] -is [Collections.IDictionary]
                        ) {
                            $Comment["user"]
                        }
                        else {
                            $Comment["author"]
                        }
                        $CommentId = if ($Comment.Contains("databaseId")) {
                            $Comment["databaseId"]
                        }
                        else {
                            $Comment["id"]
                        }
                        $CommentUrl = if ($Comment.Contains("url")) {
                            $Comment["url"]
                        }
                        else {
                            $Comment["html_url"]
                        }
                        if (
                            -not (
                                $CommentId -is [int] -or
                                $CommentId -is [long]
                            ) -or
                            $CommentUrl -isnot [string] -or
                            $Comment["body"] -isnot [string] -or
                            $Author -isnot [Collections.IDictionary] -or
                            $Author["login"] -isnot [string]
                        ) {
                            throw [GitLoopyContinuationGitHubException]::new(
                                "decoding Producer carrier comments"
                            )
                        }
                        $CreatedAt = if ($Comment.Contains("createdAt")) {
                            $Comment["createdAt"]
                        }
                        else {
                            $Comment["created_at"]
                        }
                        $UpdatedAt = if ($Comment.Contains("updatedAt")) {
                            $Comment["updatedAt"]
                        }
                        else {
                            $Comment["updated_at"]
                        }
                        $Comments.Add([ordered]@{
                            id = [long]$CommentId
                            url = [string]$CommentUrl
                            body = [string]$Comment["body"]
                            author = [string]$Author["login"]
                            author_type = [string]($Author["type"] ?? "User")
                            created_at = $CreatedAt
                            updated_at = $UpdatedAt
                        })
                    }
                    if ($CommentPage.Count -lt 100) {
                        break
                    }
                    $CommentPageNumber++
                }
            }
            $Result.Add([ordered]@{
                number = [long]$Item["number"]
                state = ([string]$Item["state"]).ToUpperInvariant()
                url = [string]$Item["html_url"]
                labels = $Labels
                comments = $Comments
            })
        }
        if ($Page.Count -lt 100) {
            break
        }
        $PageNumber++
    }
    return , $Result
}

function Get-GitLoopyCommentTaintIdentity {
    param(
        [Parameter(Mandatory)][long]$Carrier,
        [Parameter(Mandatory)][long]$CommentId
    )
    return Get-GitLoopySha256 (
        ConvertTo-GitLoopyCanonicalJson ([ordered]@{
            carrier = $Carrier
            comment_id = $CommentId
            kind = "invalid-producer-comment"
        })
    )
}

function Get-GitLoopyTaintedLineageHeads {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Completion,
        [Parameter(Mandatory)][Collections.IList]$Carriers
    )

    $CarrierNumber = [long]$Completion["carrier"]["number"]
    $Producer = [string]$Completion["producer"]["login"]
    $Anchor = ConvertTo-GitLoopyCanonicalJson $Completion["workstream"]["anchor"]
    $Records = [ordered]@{}
    $Tainted = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Carrier in $Carriers) {
        if ([long]$Carrier["number"] -ne $CarrierNumber) {
            continue
        }
        foreach ($Comment in $Carrier["comments"]) {
            if (
                $Comment["author"] -cne $Producer -or
                -not ([string]$Comment["body"]).Contains(
                    $Script:RecordMarker,
                    [StringComparison]::Ordinal
                )
            ) {
                continue
            }
            try {
                $Record = Read-GitLoopyRevisionRecord $Comment
            }
            catch [GitLoopyContinuationRejection] {
                [void]$Tainted.Add(
                    (Get-GitLoopyCommentTaintIdentity `
                        -Carrier $CarrierNumber `
                        -CommentId ([long]$Comment["id"]))
                )
                continue
            }
            if (
                $null -eq $Record -or
                [string]$Record["producer"]["login"] -cne $Producer -or
                (
                    ConvertTo-GitLoopyCanonicalJson `
                        $Record["workstream"]["anchor"]
                ) -cne $Anchor
            ) {
                continue
            }
            $RevisionId = [string]$Record["revision_id"]
            $Records[$RevisionId] = $Record
            if (
                $null -ne $Comment["created_at"] -and
                $null -ne $Comment["updated_at"] -and
                $Comment["created_at"] -cne $Comment["updated_at"]
            ) {
                [void]$Tainted.Add($RevisionId)
            }
            try {
                $null = Test-GitLoopyCompletion ([ordered]@{
                    repository = $Completion["carrier"]["repository"]
                    trusted_producers = [object[]]@($Producer)
                    completion = Get-GitLoopyRevisionCompletion $Record
                })
            }
            catch [GitLoopyContinuationRejection] {
                [void]$Tainted.Add($RevisionId)
            }
        }
    }
    foreach ($RevisionId in @($Records.Keys)) {
        foreach ($Parent in (Get-GitLoopyRecordParents $Records[$RevisionId])) {
            if (-not $Records.Contains([string]$Parent)) {
                [void]$Tainted.Add([string]$RevisionId)
                break
            }
        }
    }
    $Changed = $true
    while ($Changed) {
        $Changed = $false
        foreach ($RevisionId in @($Records.Keys)) {
            if ($Tainted.Contains([string]$RevisionId)) {
                continue
            }
            foreach ($Parent in (Get-GitLoopyRecordParents $Records[$RevisionId])) {
                if ($Tainted.Contains([string]$Parent)) {
                    [void]$Tainted.Add([string]$RevisionId)
                    $Changed = $true
                    break
                }
            }
        }
    }
    $ReferencedTainted = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($RevisionId in @($Records.Keys)) {
        if (-not $Tainted.Contains([string]$RevisionId)) {
            continue
        }
        foreach ($Parent in (Get-GitLoopyRecordParents $Records[$RevisionId])) {
            if ($Tainted.Contains([string]$Parent)) {
                [void]$ReferencedTainted.Add([string]$Parent)
            }
        }
    }
    return [object[]]@(
        $Tainted |
            Where-Object { -not $ReferencedTainted.Contains($_) } |
            Sort-Object
    )
}

function Invoke-GitLoopyContinuationPublish {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

    $Validated = Test-GitLoopyCompletion $Request
    $Repository = [string]$Validated.Repository
    $Completion = $Validated.Completion
    $Publication = [string]$Validated.Publication
    $Fingerprints = Get-GitLoopySemanticFingerprints $Completion
    $RevisionProtocol = $Request.Contains("observation")
    if (
        -not $RevisionProtocol -and
        (
            $Request.Contains("parents") -or
            $Request.Contains("reattestation")
        )
    ) {
        throw (New-GitLoopyRejection (
            "observation is required when parents or reattestation is present"
        ))
    }
    if (
        $Publication -ceq "ephemeral" -and
        (
            $RevisionProtocol -or
            $Request.Contains("parents") -or
            $Request.Contains("reattestation")
        )
    ) {
        throw (New-GitLoopyRejection (
            "immutable revision fields require shared publication"
        ))
    }

    if ($Publication -ceq "ephemeral") {
        return [ordered]@{
            ok = $true
            operation = "publish"
            receipt = [ordered]@{
                status = "unpublished"
                publication = "ephemeral"
                disposition = $Completion["disposition"]
                semantic_fingerprints = $Fingerprints
            }
        }
    }

    $Carrier = $Completion["carrier"]
    $CarrierNumber = [string]$Carrier["number"]
    $Producer = [string]$Completion["producer"]["login"]
    $Parents = $null
    $Reattestation = $null
    $ProtocolCarriers = $null
    if ($RevisionProtocol) {
        $ValidatedObservation = Test-GitLoopyObservation `
            -Request $Request `
            -Repository $Repository
        $Parents = $ValidatedObservation.Parents
        Assert-GitLoopyAuthorizedProducer `
            -Request $Request `
            -Repository $Repository `
            -Producer $Producer
        $Reattestation = Test-GitLoopyReattestation `
            -Request $Request `
            -Producer $Producer
        $ProtocolCarriers = Get-GitLoopyAllContinuationCarriers `
            -Repository $Repository
        Assert-GitLoopyObservedStateCurrent `
            -Observation $ValidatedObservation.Observation `
            -Completion $Completion `
            -Carriers $ProtocolCarriers
        $TaintedHeads = @(
            Get-GitLoopyTaintedLineageHeads `
                -Completion $Completion `
                -Carriers $ProtocolCarriers
        )
        if ($TaintedHeads.Count -gt 0 -and -not $Request.Contains("reattestation")) {
            throw (New-GitLoopyRepairRequired (
                "tainted Producer lineage requires authorized re-attestation; " +
                "repair required"
            ))
        }
        if ($TaintedHeads.Count -gt 0) {
            $Affected = [Collections.Generic.HashSet[string]]::new(
                [string[]]@($Reattestation["affected_heads"]),
                [StringComparer]::Ordinal
            )
            $Expected = [Collections.Generic.HashSet[string]]::new(
                [string[]]$TaintedHeads,
                [StringComparer]::Ordinal
            )
            if (-not $Affected.SetEquals($Expected)) {
                throw (New-GitLoopyRejection (
                    "reattestation.affected_heads must name every tainted " +
                    "lineage head"
                ))
            }
        }
    }
    $Record = if ($RevisionProtocol) {
        New-GitLoopyRecordBody `
            -Completion $Completion `
            -RevisionProtocol `
            -Parents $Parents `
            -Reattestation $Reattestation
    }
    else {
        New-GitLoopyRecordBody $Completion
    }
    if ($RevisionProtocol) {
        $ProtocolTrustedApps = Get-GitLoopyTrustedApps -Request $Request
        foreach ($ObservedCarrier in $ProtocolCarriers) {
            if ([long]$ObservedCarrier["number"] -ne [long]$CarrierNumber) {
                continue
            }
            foreach ($Comment in $ObservedCarrier["comments"]) {
                if ($Comment["author"] -cne $Producer) {
                    continue
                }
                if (
                    $Comment["author_type"] -cin @("Bot", "App") -and
                    -not $ProtocolTrustedApps.Contains($Producer)
                ) {
                    continue
                }
                try {
                    $Existing = Read-GitLoopyRevisionRecord $Comment
                }
                catch [GitLoopyContinuationRejection] {
                    continue
                }
                if (
                    $null -ne $Existing -and
                    $Existing["revision_id"] -ceq $Record.RevisionId -and
                    $Comment["body"] -ceq $Record.Body
                ) {
                    $Idempotent = [ordered]@{
                        ok = $true
                        operation = "publish"
                        receipt = [ordered]@{
                            status = "idempotent"
                            revision_id = $Record.RevisionId
                            carrier = $Carrier
                            comment = [ordered]@{
                                id = [long]$Comment["id"]
                                url = [string]$Comment["url"]
                            }
                            index_label = $Script:IndexLabel
                            semantic_fingerprints = $Record.Fingerprints
                            parents = $Parents
                        }
                    }
                    if ($null -ne $Reattestation) {
                        $Idempotent.receipt["reattestation"] = $Reattestation
                    }
                    return $Idempotent
                }
            }
        }
    }

    foreach ($EvidenceRef in $Completion["transition"]["evidence"]) {
        $null = Invoke-GitLoopyGitHub `
            -Arguments @(
                "api",
                "repos/$Repository/issues/comments/$($EvidenceRef["comment_id"])"
            ) `
            -Context "reading transition evidence"
    }
    try {
        $null = Invoke-GitLoopyGitHub `
            -Arguments @(
                "label", "create", $Script:IndexLabel, "--repo", $Repository,
                "--color", "5319E7", "--description",
                "Repairable discovery index for git-loopy Continuation records",
                "--force"
            ) `
            -Context "establishing the discovery index label" `
            -NoJson
        $null = Invoke-GitLoopyGitHub `
            -Arguments @(
                "issue", "edit", $CarrierNumber, "--repo", $Repository,
                "--add-label", $Script:IndexLabel
            ) `
            -Context "indexing the Producer carrier" `
            -NoJson
        $Appended = Invoke-GitLoopyGitHub `
            -Arguments @(
                "api", "--method", "POST",
                "repos/$Repository/issues/$CarrierNumber/comments", "--input", "-"
            ) `
            -InputValue ([ordered]@{ body = $Record.Body }) `
            -Context "appending the Producer revision"
        if ($Appended -isnot [Collections.IDictionary]) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding appending the Producer revision"
            )
        }
        $CommentId = $Appended["id"]
        $Committed = Invoke-GitLoopyGitHub `
            -Arguments @(
                "api", "repos/$Repository/issues/comments/$CommentId"
            ) `
            -Context "rereading the Producer revision"
    }
    catch [GitLoopyContinuationGitHubException] {
        $Detail = $_.Exception.StderrTail
        if ([string]::IsNullOrWhiteSpace($Detail)) {
            $Detail = $_.Exception.Message
        }
        throw (New-GitLoopyRepairRequired (
            "publication failed after durable transition: $Detail; " +
            "repair required"
        ))
    }
    if (
        $Appended -isnot [Collections.IDictionary] -or
        $Appended["user"] -isnot [Collections.IDictionary] -or
        $Appended["user"]["login"] -cne $Producer
    ) {
        throw (New-GitLoopyRepairRequired (
            "published Producer revision author does not match completion " +
            "producer; repair required"
        ))
    }
    if (
        $Committed -isnot [Collections.IDictionary] -or
        $Committed["user"] -isnot [Collections.IDictionary] -or
        $Committed["body"] -cne $Record.Body -or
        $Committed["user"]["login"] -cne $Producer
    ) {
        throw (New-GitLoopyRepairRequired (
            "Producer revision reread did not match the append; repair required"
        ))
    }

    $Status = "committed"
    $ConflictingHeads = [object[]]@()
    if ($RevisionProtocol) {
        $CommittedRecord = Read-GitLoopyRevisionRecord $Committed
        $LineageEntries = [Collections.Generic.List[object]]::new()
        $LineageEntries.Add([ordered]@{ record = $CommittedRecord })
        $CommittedLineage = Get-GitLoopyLineageKey `
            -Carrier ([long]$CarrierNumber) `
            -Record $CommittedRecord
        foreach ($ObservedCarrier in $ProtocolCarriers) {
            if ([long]$ObservedCarrier["number"] -ne [long]$CarrierNumber) {
                continue
            }
            foreach ($Comment in $ObservedCarrier["comments"]) {
                if ($Comment["author"] -cne $Producer) {
                    continue
                }
                try {
                    $Existing = Read-GitLoopyRevisionRecord $Comment
                }
                catch [GitLoopyContinuationRejection] {
                    continue
                }
                if (
                    $null -ne $Existing -and
                    (
                        Get-GitLoopyLineageKey `
                            -Carrier ([long]$CarrierNumber) `
                            -Record $Existing
                    ) -ceq $CommittedLineage
                ) {
                    $LineageEntries.Add([ordered]@{ record = $Existing })
                }
            }
        }
        if ($null -ne $Reattestation) {
            $Affected = [Collections.Generic.HashSet[string]]::new(
                [string[]]@($Reattestation["affected_heads"]),
                [StringComparer]::Ordinal
            )
            $LineageEntries = [Collections.Generic.List[object]]@(
                $LineageEntries |
                    Where-Object {
                        -not $Affected.Contains(
                            [string]$_["record"]["revision_id"]
                        )
                    }
            )
        }
        $Referenced = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Entry in $LineageEntries) {
            foreach ($Parent in (Get-GitLoopyRecordParents $Entry["record"])) {
                [void]$Referenced.Add([string]$Parent)
            }
        }
        $Live = @(
            $LineageEntries |
                Where-Object {
                    -not $Referenced.Contains(
                        [string]$_["record"]["revision_id"]
                    )
                }
        )
        $Semantics = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Entry in $Live) {
            [void]$Semantics.Add(
                (Get-GitLoopyRevisionSemantics $Entry["record"])
            )
        }
        if ($Semantics.Count -gt 1) {
            $Status = "conflict"
            $ConflictingHeads = @(
                $Live |
                    ForEach-Object { $_["record"]["revision_id"] } |
                    Sort-Object
            )
        }
    }

    $Receipt = [ordered]@{
        status = $Status
        revision_id = $Record.RevisionId
        carrier = $Carrier
        comment = [ordered]@{
            id = $CommentId
            url = $Committed["html_url"]
        }
        index_label = $Script:IndexLabel
        semantic_fingerprints = $Record.Fingerprints
    }
    if ($RevisionProtocol) {
        $Receipt["parents"] = $Parents
    }
    if ($null -ne $Reattestation) {
        $Receipt["reattestation"] = $Reattestation
    }
    if ($ConflictingHeads.Count -gt 0) {
        $Receipt["conflicting_heads"] = $ConflictingHeads
    }
    return [ordered]@{
        ok = $true
        operation = "publish"
        receipt = $Receipt
    }
}

function Get-GitLoopyRecordFromComment {
    param([Parameter(Mandatory)][Collections.IDictionary]$Comment)

    $Prefix = "$Script:RecordMarker`n``````json`n"
    $Suffix = "`n``````"
    $Body = $Comment["body"]
    if (
        $Body -isnot [string] -or
        -not $Body.StartsWith($Prefix, [StringComparison]::Ordinal) -or
        -not $Body.EndsWith($Suffix, [StringComparison]::Ordinal)
    ) {
        return $null
    }
    $Raw = $Body.Substring(
        $Prefix.Length,
        $Body.Length - $Prefix.Length - $Suffix.Length
    )
    try {
        $Record = $Raw | ConvertFrom-Json -AsHashtable -DateKind String
    }
    catch {
        return $null
    }
    if ($Record -isnot [Collections.IDictionary]) {
        return $null
    }
    $Completion = Get-GitLoopyRevisionCompletion $Record
    $Parents = [object[]]@()
    if ($Record.Contains("parents")) {
        $Parents = [object[]]@($Record["parents"])
    }
    $Reattestation = if ($Record.Contains("reattestation")) {
        $Record["reattestation"]
    }
    else {
        $null
    }
    $IdentitySource = $Completion
    if ($Parents.Count -gt 0 -or $null -ne $Reattestation) {
        $IdentitySource = [ordered]@{
            completion = $Completion
            parents = $Parents
        }
        if ($null -ne $Reattestation) {
            $IdentitySource["reattestation"] = $Reattestation
        }
    }
    $ExpectedRevision = Get-GitLoopySha256 (
        ConvertTo-GitLoopyCanonicalJson $IdentitySource
    )
    if ($Record["revision_id"] -cne $ExpectedRevision) {
        return $null
    }
    $ExpectedFingerprints = ConvertTo-GitLoopyCanonicalJson (
        Get-GitLoopySemanticFingerprints $Completion
    )
    if (
        (ConvertTo-GitLoopyCanonicalJson $Record["semantic_fingerprints"]) -cne
        $ExpectedFingerprints
    ) {
        return $null
    }
    return [ordered]@{
        Record = $Record
        Completion = $Completion
    }
}

function Get-GitLoopyCommentId {
    param([Parameter(Mandatory)][Collections.IDictionary]$Comment)

    foreach ($Key in @("databaseId", "id")) {
        if (
            -not ($Comment[$Key] -is [bool]) -and
            ($Comment[$Key] -is [int] -or $Comment[$Key] -is [long]) -and
            [long]$Comment[$Key] -gt 0
        ) {
            return [long]$Comment[$Key]
        }
    }
    $Match = [regex]::Match(
        [string]$Comment["url"],
        "#issuecomment-(?<id>[0-9]+)$"
    )
    if (-not $Match.Success) {
        return $null
    }
    return [long]$Match.Groups["id"].Value
}

function ConvertTo-GitLoopyIndexedComment {
    <#
        Normalize one comment the discovery index returned.

        `gh issue list --json comments` and the REST comment resource describe
        the same comment with different field names. The rest of the family
        normalizes both shapes before reading, so a record is either readable
        through every distribution or through none of them.
    #>
    param([Parameter(Mandatory)][Collections.IDictionary]$Comment)

    $Author = if ($Comment["author"] -is [Collections.IDictionary]) {
        $Comment["author"]
    }
    else {
        $Comment["user"]
    }
    if (
        $Author -isnot [Collections.IDictionary] -or
        $Author["login"] -isnot [string] -or
        $Comment["body"] -isnot [string]
    ) {
        return $null
    }
    $Url = if ($Comment["url"] -is [string]) {
        [string]$Comment["url"]
    }
    elseif ($Comment["html_url"] -is [string]) {
        [string]$Comment["html_url"]
    }
    else {
        ""
    }
    $Locator = [ordered]@{ url = $Url }
    foreach ($Key in @("databaseId", "id")) {
        if ($Comment.Contains($Key)) { $Locator[$Key] = $Comment[$Key] }
    }
    $CommentId = Get-GitLoopyCommentId $Locator
    if ($null -eq $CommentId) {
        return $null
    }
    return [ordered]@{
        id = $CommentId
        url = $Url
        body = [string]$Comment["body"]
        author = [string]$Author["login"]
        author_type = [string]($Author["type"] ?? "User")
    }
}

function Get-GitLoopyRevisionCompletion {
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    $Completion = [ordered]@{}
    foreach ($Entry in $Record.GetEnumerator()) {
        if (
            $Entry.Key -cnotin @(
                "revision_id", "semantic_fingerprints", "parents",
                "reattestation"
            )
        ) {
            $Completion[$Entry.Key] = $Entry.Value
        }
    }
    return $Completion
}

function Read-GitLoopyRevisionRecord {
    param([Parameter(Mandatory)][Collections.IDictionary]$Comment)

    $Prefix = "$Script:RecordMarker`n``````json`n"
    $Suffix = "`n``````"
    $Body = $Comment["body"]
    if (
        $Body -isnot [string] -or
        -not $Body.StartsWith($Prefix, [StringComparison]::Ordinal) -or
        -not $Body.EndsWith($Suffix, [StringComparison]::Ordinal)
    ) {
        return $null
    }
    $Raw = $Body.Substring(
        $Prefix.Length,
        $Body.Length - $Prefix.Length - $Suffix.Length
    )
    try {
        Test-GitLoopyRawJsonNesting `
            -Text $Raw `
            -Name "Producer revision comment $($Comment["id"])"
        $Document = [Text.Json.JsonDocument]::Parse($Raw)
        try {
            Test-GitLoopyJsonParsePhase $Document.RootElement
            if (
                $Document.RootElement.ValueKind -ne
                [Text.Json.JsonValueKind]::Object
            ) {
                throw (New-GitLoopyRejection (
                    "Producer revision comment $($Comment["id"]) must contain one JSON object"
                ))
            }
            Test-GitLoopyPortablePhase $Document.RootElement "Producer revision"
        }
        finally {
            $Document.Dispose()
        }
        $Record = $Raw |
            ConvertFrom-Json -AsHashtable -DateKind String
    }
    catch [GitLoopyContinuationRejection] {
        throw
    }
    catch {
        throw (New-GitLoopyRejection (
            "Producer revision comment $($Comment["id"]) contains invalid JSON"
        ))
    }
    if ($Record -isnot [Collections.IDictionary]) {
        throw (New-GitLoopyRejection (
            "Producer revision comment $($Comment["id"]) must contain one JSON object"
        ))
    }
    if (
        [Text.Encoding]::UTF8.GetByteCount(
            (ConvertTo-GitLoopyCanonicalJson $Record)
        ) -gt $Script:MaxRecordBytes
    ) {
        throw (New-GitLoopyRejection (
            "Producer revision comment $($Comment["id"]) exceeds maximum record length"
        ))
    }
    $RevisionId = Assert-GitLoopyString $Record["revision_id"] "revision_id"
    $StoredFingerprints = Assert-GitLoopyObject `
        $Record["semantic_fingerprints"] "semantic_fingerprints"
    $Completion = Get-GitLoopyRevisionCompletion $Record
    $Parents = [object[]]@()
    if ($Record.Contains("parents")) {
        $Parents = [object[]]@($Record["parents"])
    }
    $Reattestation = if ($Record.Contains("reattestation")) {
        $Record["reattestation"]
    }
    else {
        $null
    }
    $IdentitySource = $Completion
    if ($Parents.Count -gt 0 -or $null -ne $Reattestation) {
        $IdentitySource = [ordered]@{
            completion = $Completion
            parents = $Parents
        }
        if ($null -ne $Reattestation) {
            $IdentitySource["reattestation"] = $Reattestation
        }
    }
    if (
        $RevisionId -cne (
            Get-GitLoopySha256 (
                ConvertTo-GitLoopyCanonicalJson $IdentitySource
            )
        )
    ) {
        throw (New-GitLoopyRejection (
            "Producer revision comment $($Comment["id"]) has an invalid revision identity"
        ))
    }
    if (
        (ConvertTo-GitLoopyCanonicalJson $StoredFingerprints) -cne
        (
            ConvertTo-GitLoopyCanonicalJson (
                Get-GitLoopySemanticFingerprints $Completion
            )
        )
    ) {
        throw (New-GitLoopyRejection (
            "Producer revision comment $($Comment["id"]) has invalid semantic fingerprints"
        ))
    }
    return $Record
}

function Get-GitLoopyLineageKey {
    param(
        [Parameter(Mandatory)][long]$Carrier,
        [Parameter(Mandatory)][Collections.IDictionary]$Record
    )
    return (
        "$Carrier`0$($Record["producer"]["login"])`0" +
        (ConvertTo-GitLoopyCanonicalJson $Record["workstream"]["anchor"])
    )
}

function Get-GitLoopyRevisionSemantics {
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    $FingerprintEntries = [Collections.Generic.List[object]]::new()
    foreach ($Key in @($Record["semantic_fingerprints"].Keys | Sort-Object)) {
        $FingerprintEntries.Add([object[]]@(
            $Key, $Record["semantic_fingerprints"][$Key]
        ))
    }
    return ConvertTo-GitLoopyCanonicalJson ([ordered]@{
        disposition = $Record["disposition"]
        actions = $FingerprintEntries
        outcome = $Record["outcome"]
        no_guidance = $Record["no_guidance"]
    })
}

function Get-GitLoopyConditionReadPlan {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Condition,
        [Parameter(Mandatory)][string]$Repository
    )

    $Kind = [string]$Condition["kind"]
    $Target = $Condition["target"]
    $TargetKind = [string]$Target["kind"]
    if ($Kind -ceq "issue-label-present") {
        $Number = [long]$Target["number"]
        return [ordered]@{
            key = "issue-labels`0$Repository`0$Number"
            shape = "issue-labels"
            arguments = @(
                "issue", "view", [string]$Number, "--repo", $Repository,
                "--json", "number,labels"
            )
        }
    }
    if ($Kind -ceq "sub-issues-complete") {
        $Number = [long]$Target["number"]
        return [ordered]@{
            key = "issue-sub-issues`0$Repository`0$Number"
            shape = "issue-sub-issues"
            arguments = @(
                "issue", "view", [string]$Number, "--repo", $Repository,
                "--json", "number,subIssuesSummary"
            )
        }
    }
    if ($TargetKind -ceq "issue") {
        $Number = [long]$Target["number"]
        return [ordered]@{
            key = "issue`0$Repository`0$Number"
            shape = "artifact"
            arguments = @(
                "issue", "view", [string]$Number, "--repo", $Repository,
                "--json", "number,state,url"
            )
        }
    }
    if ($TargetKind -ceq "pull-request") {
        $Number = [long]$Target["number"]
        return [ordered]@{
            key = "pull-request`0$Repository`0$Number"
            shape = "artifact"
            arguments = @(
                "pr", "view", [string]$Number, "--repo", $Repository,
                "--json", "number,state,url"
            )
        }
    }
    if ($TargetKind -ceq "commit") {
        $Sha = [string]$Target["sha"]
        return [ordered]@{
            key = "commit`0$Repository`0$Sha"
            shape = "commit"
            arguments = @("api", "repos/$Repository/commits/$Sha")
        }
    }
    if ($TargetKind -ceq "branch") {
        $Name = [string]$Target["name"]
        return [ordered]@{
            key = "branch`0$Repository`0$Name"
            shape = "branch"
            arguments = @("api", "repos/$Repository/git/ref/heads/$Name")
        }
    }
    if ($TargetKind -ceq "issue-comment") {
        $CommentId = [long]$Target["comment_id"]
        return [ordered]@{
            key = "issue-comment`0$Repository`0$CommentId"
            shape = "issue-comment"
            arguments = @(
                "api", "repos/$Repository/issues/comments/$CommentId"
            )
        }
    }
    if ($TargetKind -ceq "pull-request-review") {
        $PullRequest = [long]$Target["pull_request"]
        $ReviewId = [long]$Target["review_id"]
        return [ordered]@{
            key = (
                "pull-request-review`0$Repository`0$PullRequest`0$ReviewId"
            )
            shape = "pull-request-review"
            arguments = @(
                "api",
                "repos/$Repository/pulls/$PullRequest/reviews/$ReviewId"
            )
        }
    }
    throw [InvalidOperationException]::new(
        "unsupported reference target kind: $TargetKind"
    )
}

function Read-GitLoopyConditionFact {
    param([Parameter(Mandatory)][Collections.IDictionary]$Plan)

    $Raw = Invoke-GitLoopyGitHub `
        -Arguments $Plan["arguments"] `
        -Context "reading a Continuation condition Target"
    if ($Raw -isnot [Collections.IDictionary]) {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding a Continuation condition Target"
        )
    }
    $Shape = [string]$Plan["shape"]
    if ($Shape -ceq "artifact") {
        if (
            $Raw["state"] -isnot [string] -or
            -not (
                $Raw["number"] -is [int] -or
                $Raw["number"] -is [long]
            )
        ) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding a Continuation condition Target"
            )
        }
        return [ordered]@{ state = [string]$Raw["state"] }
    }
    if ($Shape -ceq "issue-labels") {
        if (
            -not (
                $Raw["number"] -is [int] -or
                $Raw["number"] -is [long]
            ) -or
            $Raw["labels"] -isnot [Collections.IList]
        ) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding a Continuation condition Target"
            )
        }
        $Labels = [Collections.Generic.List[string]]::new()
        foreach ($Label in $Raw["labels"]) {
            if (
                $Label -is [Collections.IDictionary] -and
                $Label["name"] -is [string]
            ) {
                $Labels.Add([string]$Label["name"])
            }
        }
        return [ordered]@{ labels = [object[]]@($Labels) }
    }
    if ($Shape -ceq "issue-sub-issues") {
        if (
            -not (
                $Raw["number"] -is [int] -or
                $Raw["number"] -is [long]
            )
        ) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding a Continuation condition Target"
            )
        }
        $Summary = $Raw["subIssuesSummary"]
        if ($null -eq $Summary) {
            $Summary = [ordered]@{}
        }
        if ($Summary -isnot [Collections.IDictionary]) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding a Continuation condition Target"
            )
        }
        $Total = if ($Summary.Contains("total")) {
            $Summary["total"]
        }
        else {
            0
        }
        $Completed = if ($Summary.Contains("completed")) {
            $Summary["completed"]
        }
        else {
            0
        }
        if (
            -not ($Total -is [int] -or $Total -is [long]) -or
            -not ($Completed -is [int] -or $Completed -is [long])
        ) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding a Continuation condition Target"
            )
        }
        return [ordered]@{
            total = [long]$Total
            completed = [long]$Completed
        }
    }
    if ($Shape -ceq "commit") {
        if ($Raw["sha"] -isnot [string]) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding a Continuation condition Target"
            )
        }
        return [ordered]@{ sha = [string]$Raw["sha"] }
    }
    if ($Shape -ceq "branch") {
        if (
            $Raw["object"] -isnot [Collections.IDictionary] -or
            $Raw["object"]["sha"] -isnot [string]
        ) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding a Continuation condition Target"
            )
        }
        return [ordered]@{ sha = [string]$Raw["object"]["sha"] }
    }
    if ($Shape -ceq "issue-comment") {
        if (
            -not ($Raw["id"] -is [int] -or $Raw["id"] -is [long]) -or
            $Raw["user"] -isnot [Collections.IDictionary] -or
            $Raw["user"]["login"] -isnot [string]
        ) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding a Continuation condition Target"
            )
        }
        return [ordered]@{ exists = $true }
    }
    if ($Shape -ceq "pull-request-review") {
        if (
            -not ($Raw["id"] -is [int] -or $Raw["id"] -is [long]) -or
            $Raw["state"] -isnot [string]
        ) {
            throw [GitLoopyContinuationGitHubException]::new(
                "decoding a Continuation condition Target"
            )
        }
        return [ordered]@{ state = [string]$Raw["state"] }
    }
    throw [InvalidOperationException]::new(
        "unsupported Continuation condition Target shape: $Shape"
    )
}

function Test-GitLoopyNotFoundFailure {
    param([Parameter(Mandatory)][GitLoopyContinuationGitHubException]$Exception)

    $Message = $Exception.StderrTail.ToLowerInvariant()
    foreach ($Phrase in @("404", "not found", "could not resolve")) {
        if ($Message.Contains($Phrase, [StringComparison]::Ordinal)) {
            return $true
        }
    }
    return $false
}

function Test-GitLoopyFactAttemptEqual {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Left,
        [Parameter(Mandatory)][Collections.IDictionary]$Right
    )

    if ([bool]$Left["unavailable"] -ne [bool]$Right["unavailable"]) {
        return $false
    }
    if ([bool]$Left["unavailable"]) {
        return $true
    }
    if ($null -eq $Left["value"] -or $null -eq $Right["value"]) {
        return $null -eq $Left["value"] -and $null -eq $Right["value"]
    }
    return (
        ConvertTo-GitLoopyCanonicalJson $Left["value"]
    ) -ceq (
        ConvertTo-GitLoopyCanonicalJson $Right["value"]
    )
}

function Invoke-GitLoopyConditionFactAttempt {
    param([Parameter(Mandatory)][Collections.IDictionary]$Plan)

    try {
        return [ordered]@{
            value = Read-GitLoopyConditionFact -Plan $Plan
            unavailable = $false
        }
    }
    catch [GitLoopyContinuationGitHubException] {
        if (Test-GitLoopyNotFoundFailure $_.Exception) {
            return [ordered]@{ value = $null; unavailable = $false }
        }
        return [ordered]@{ value = $null; unavailable = $true }
    }
}

function Read-GitLoopyStableConditionFact {
    param([Parameter(Mandatory)][Collections.IDictionary]$Plan)

    $FirstSucceeded = $false
    try {
        $FirstValue = Read-GitLoopyConditionFact -Plan $Plan
        $FirstSucceeded = $true
    }
    catch [GitLoopyContinuationGitHubException] {
        if (Test-GitLoopyNotFoundFailure $_.Exception) {
            return [ordered]@{
                value = $null
                stable = $true
                unavailable = $false
            }
        }
        $Previous = [ordered]@{ value = $null; unavailable = $true }
    }
    if ($FirstSucceeded) {
        return [ordered]@{
            value = $FirstValue
            stable = $true
            unavailable = $false
        }
    }
    for ($Attempt = 1; $Attempt -lt 3; $Attempt++) {
        $Current = Invoke-GitLoopyConditionFactAttempt -Plan $Plan
        if (Test-GitLoopyFactAttemptEqual -Left $Previous -Right $Current) {
            return [ordered]@{
                value = $Current["value"]
                stable = $true
                unavailable = [bool]$Current["unavailable"]
            }
        }
        $Previous = $Current
    }
    return [ordered]@{
        value = $Previous["value"]
        stable = $false
        unavailable = [bool]$Previous["unavailable"]
    }
}

function Resolve-GitLoopyActionCompletion {
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][Collections.IDictionary]$Context,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Stack
    )

    if ($Context["status_cache"].Contains($Key)) {
        return [string]$Context["status_cache"][$Key]
    }
    $CycleIndex = [Array]::IndexOf([object[]]$Stack, $Key)
    if ($CycleIndex -ge 0) {
        $Cycle = [Collections.Generic.List[string]]::new()
        for ($Index = $CycleIndex; $Index -lt $Stack.Count; $Index++) {
            $Cycle.Add([string]$Stack[$Index])
        }
        $Cycle.Add($Key)
        $Context["diagnostics"].Add([ordered]@{
            code = "prerequisite_cycle"
            revision_id = $Context["revision_id"]
            actions = [object[]]@($Cycle)
        })
        foreach ($CycleKey in $Cycle) {
            $Context["status_cache"][$CycleKey] = "conflict"
        }
        return "conflict"
    }
    if (-not $Context["actions_by_key"].Contains($Key)) {
        $Context["status_cache"][$Key] = "unverified"
        return "unverified"
    }
    $Action = $Context["actions_by_key"][$Key]
    $Status = Test-GitLoopyConditionState `
        -Condition $Action["completion_condition"] `
        -Context $Context `
        -Stack ([object[]]@($Stack + @($Key)))
    $Context["status_cache"][$Key] = $Status
    return $Status
}

function Test-GitLoopyConditionState {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Condition,
        [Parameter(Mandatory)][Collections.IDictionary]$Context,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Stack
    )

    $Kind = [string]$Condition["kind"]
    if ($Kind -ceq "action-completed") {
        return Resolve-GitLoopyActionCompletion `
            -Key ([string]$Condition["action_key"]) `
            -Context $Context `
            -Stack $Stack
    }
    $Plan = Get-GitLoopyConditionReadPlan `
        -Condition $Condition `
        -Repository ([string]$Context["repository"])
    $Cache = $Context["fact_cache"]
    if (-not $Cache.Contains($Plan["key"])) {
        $Cache[$Plan["key"]] = Read-GitLoopyStableConditionFact -Plan $Plan
    }
    $Fact = $Cache[$Plan["key"]]
    if (-not [bool]$Fact["stable"] -or [bool]$Fact["unavailable"]) {
        return "unverified"
    }
    $Value = $Fact["value"]
    $Satisfied = $false
    if ($Kind -ceq "issue-open") {
        $Satisfied = $null -ne $Value -and $Value["state"] -ceq "OPEN"
    }
    elseif ($Kind -cin @("issue-closed", "dependency-satisfied")) {
        $Satisfied = $null -ne $Value -and $Value["state"] -ceq "CLOSED"
    }
    elseif ($Kind -ceq "pull-request-open") {
        $Satisfied = $null -ne $Value -and $Value["state"] -ceq "OPEN"
    }
    elseif ($Kind -ceq "pull-request-closed") {
        $Satisfied = (
            $null -ne $Value -and
            $Value["state"] -cin @("CLOSED", "MERGED")
        )
    }
    elseif ($Kind -ceq "pull-request-merged") {
        $Satisfied = $null -ne $Value -and $Value["state"] -ceq "MERGED"
    }
    elseif ($Kind -ceq "issue-label-present") {
        $Satisfied = (
            $null -ne $Value -and
            @($Value["labels"]) -ccontains $Condition["label"]
        )
    }
    elseif ($Kind -ceq "sub-issues-complete") {
        $Satisfied = (
            $null -ne $Value -and
            [long]$Value["completed"] -ge [long]$Value["total"]
        )
    }
    elseif ($Kind -cin @("commit-exists", "artifact-exists")) {
        $Satisfied = $null -ne $Value
    }
    elseif ($Kind -ceq "branch-head-equals") {
        $Satisfied = (
            $null -ne $Value -and
            $Value["sha"] -ceq $Condition["target"]["sha"]
        )
    }
    elseif ($Kind -ceq "pull-request-review-state") {
        $ReviewStates = [ordered]@{
            APPROVED = "approved"
            CHANGES_REQUESTED = "changes-requested"
            COMMENTED = "commented"
        }
        $ExpectedState = if ($null -ne $Value) {
            $ReviewStates[[string]$Value["state"]]
        }
        else {
            $null
        }
        $Satisfied = (
            $null -ne $Value -and
            $ExpectedState -ceq $Condition["state"]
        )
    }
    return $(if ($Satisfied) { "satisfied" } else { "unsatisfied" })
}

function Get-GitLoopyRecordActions {
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    if (-not $Record.Contains("actions") -or $null -eq $Record["actions"]) {
        return , [object[]]@()
    }
    return , [object[]]@($Record["actions"])
}

function Get-GitLoopyEvaluatedFragment {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][Collections.IDictionary]$FactCache
    )

    $RecordActions = Get-GitLoopyRecordActions $Record
    $ActionsByKey = [ordered]@{}
    foreach ($Action in $RecordActions) {
        $ActionsByKey[[string]$Action["key"]] = $Action
    }
    $Diagnostics = [Collections.Generic.List[object]]::new()
    $Context = [ordered]@{
        repository = $Repository
        revision_id = [string]$Record["revision_id"]
        actions_by_key = $ActionsByKey
        status_cache = [ordered]@{}
        diagnostics = $Diagnostics
        fact_cache = $FactCache
    }
    $Results = [Collections.Generic.List[object]]::new()
    foreach ($Action in $RecordActions) {
        $Key = [string]$Action["key"]
        $CompletionStatus = Resolve-GitLoopyActionCompletion `
            -Key $Key `
            -Context $Context `
            -Stack ([object[]]@())
        if ($CompletionStatus -cin @("conflict", "satisfied")) {
            continue
        }
        if ($CompletionStatus -ceq "unverified") {
            $Diagnostics.Add([ordered]@{
                code = "unverified_completion"
                revision_id = [string]$Record["revision_id"]
                action_key = $Key
            })
            continue
        }
        $Unsatisfied = [Collections.Generic.List[object]]::new()
        $PrerequisiteUnverified = $false
        $Conflicted = $false
        foreach ($Prerequisite in @($Action["prerequisites"])) {
            $Status = Test-GitLoopyConditionState `
                -Condition $Prerequisite `
                -Context $Context `
                -Stack ([object[]]@($Key))
            if ($Status -ceq "conflict") {
                $Conflicted = $true
                break
            }
            if ($Status -ceq "unverified") {
                $PrerequisiteUnverified = $true
            }
            elseif ($Status -ceq "unsatisfied") {
                $Unsatisfied.Add($Prerequisite)
            }
        }
        if ($Conflicted) {
            continue
        }
        if ($PrerequisiteUnverified) {
            $Diagnostics.Add([ordered]@{
                code = "unverified_prerequisite"
                revision_id = [string]$Record["revision_id"]
                action_key = $Key
            })
            continue
        }
        $Results.Add([ordered]@{
            action = $Action
            readiness = if ($Unsatisfied.Count -gt 0) { "Blocked" } else { "Ready" }
            unsatisfied = [object[]]@($Unsatisfied)
        })
    }
    return [ordered]@{
        results = $Results
        diagnostics = $Diagnostics
    }
}

function Get-GitLoopyUnionBasis {
    param([Parameter(Mandatory)][Collections.IList]$Contributions)

    $Seen = [ordered]@{}
    foreach ($Contribution in $Contributions) {
        foreach ($Item in @($Contribution["action"]["basis"])) {
            $Seen[(ConvertTo-GitLoopyCanonicalJson $Item)] = $Item
        }
    }
    return , [object[]]@(
        foreach ($Key in @($Seen.Keys | Sort-Object)) {
            $Seen[$Key]
        }
    )
}

function Get-GitLoopyUnionProvenance {
    param([Parameter(Mandatory)][Collections.IList]$Contributions)

    $Seen = [ordered]@{}
    foreach ($Contribution in $Contributions) {
        $Record = $Contribution["record"]
        $Comment = $Contribution["comment"]
        $Producer = $Contribution["producer"]
        $Key = (
            [string]$Record["carrier"]["number"] + "`0" +
            [string]$Record["revision_id"] + "`0" +
            [string]$Comment["id"]
        )
        $Seen[$Key] = [ordered]@{
            login = $Producer["login"]
            role = $Producer["role"]
            carrier = $Record["carrier"]
            revision_id = $Record["revision_id"]
            comment_id = [long]$Comment["id"]
            comment_url = [string]$Comment["url"]
        }
    }
    return , [object[]]@(
        foreach ($Key in @($Seen.Keys | Sort-Object)) {
            $Seen[$Key]
        }
    )
}

function Get-GitLoopyLocalTopologicalLayers {
    <#
        .SYNOPSIS
        Layer every local Action by its own record's action-completed graph.

        .DESCRIPTION
        Layer 0 has no local completed-Action prerequisite; otherwise the layer
        is one more than the deepest named local prerequisite. Layers relax to a
        fixed point rather than resolving by recursive descent, so the answer
        never depends on the order Actions were declared or visited. Anything
        still unresolved when the relaxation stops is on, or feeds, a
        prerequisite cycle and layers 0. Every distribution in the family
        implements this same fixed point.
    #>
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    $Prerequisites = [Collections.Specialized.OrderedDictionary]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Action in @($Record["actions"])) {
        $Named = [Collections.Generic.SortedSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Prerequisite in @($Action["prerequisites"])) {
            if ([string]$Prerequisite["kind"] -ceq "action-completed") {
                [void]$Named.Add([string]$Prerequisite["action_key"])
            }
        }
        $Prerequisites[[string]$Action["key"]] = @($Named)
    }
    $Layers = [Collections.Specialized.OrderedDictionary]::new(
        [StringComparer]::Ordinal
    )
    for ($Round = 0; $Round -le $Prerequisites.Count; $Round++) {
        $Resolved = [Collections.Specialized.OrderedDictionary]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Key in @($Layers.Keys)) {
            $Resolved[$Key] = $Layers[$Key]
        }
        foreach ($Key in @($Prerequisites.Keys)) {
            if ($Resolved.Contains($Key)) {
                continue
            }
            $Local = @($Prerequisites[$Key])
            $Pending = $false
            foreach ($Other in $Local) {
                if ($Prerequisites.Contains($Other) -and
                    -not $Resolved.Contains($Other)) {
                    $Pending = $true
                    break
                }
            }
            if ($Pending) {
                continue
            }
            if ($Local.Count -eq 0) {
                $Layers[$Key] = 0
                continue
            }
            $Deepest = 0
            foreach ($Other in $Local) {
                $Candidate = 0
                if ($Resolved.Contains($Other)) {
                    $Candidate = [int]$Resolved[$Other]
                }
                if ($Candidate -gt $Deepest) {
                    $Deepest = $Candidate
                }
            }
            $Layers[$Key] = $Deepest + 1
        }
        if ($Layers.Count -eq $Prerequisites.Count) {
            break
        }
    }
    $Result = [Collections.Specialized.OrderedDictionary]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Key in @($Prerequisites.Keys)) {
        if ($Layers.Contains($Key)) {
            $Result[$Key] = [int]$Layers[$Key]
        } else {
            $Result[$Key] = 0
        }
    }
    return $Result
}

function Compare-GitLoopyContinuationViewOrder {
    <#
        .SYNOPSIS
        Deterministic Continuation view order for verified guidance.

        .DESCRIPTION
        Orders Ready Actions ahead of Blocked ones, then by each Action's
        deterministic local topological layer. Canonical Workstream Anchor keeps
        a Producer's declaration position local to that Workstream; local
        Workflow-semantic precedence then orders its own Actions before
        canonical Action identity breaks the final tie. String comparisons are
        ordinal so the order matches the rest of the family byte for byte.
    #>
    param([object]$Left, [object]$Right)

    $LeftRank = if ([string]$Left["readiness"] -ceq "Ready") { 0 } else { 1 }
    $RightRank = if ([string]$Right["readiness"] -ceq "Ready") { 0 } else { 1 }
    if ($LeftRank -ne $RightRank) {
        return $LeftRank - $RightRank
    }
    $LeftLayer = [int]$Left["_topological_layer"]
    $RightLayer = [int]$Right["_topological_layer"]
    if ($LeftLayer -ne $RightLayer) {
        return $LeftLayer - $RightLayer
    }
    $AnchorOrder = [string]::CompareOrdinal(
        (ConvertTo-GitLoopyCanonicalJson $Left["workstream_anchor"]),
        (ConvertTo-GitLoopyCanonicalJson $Right["workstream_anchor"])
    )
    if ($AnchorOrder -ne 0) {
        return $AnchorOrder
    }
    $LeftLocal = [int]$Left["_local_order_index"]
    $RightLocal = [int]$Right["_local_order_index"]
    if ($LeftLocal -ne $RightLocal) {
        return $LeftLocal - $RightLocal
    }
    $IdentityOrder = [string]::CompareOrdinal(
        [string]$Left["identity"],
        [string]$Right["identity"]
    )
    if ($IdentityOrder -ne 0) {
        return $IdentityOrder
    }
    # List<T>.Sort is an unstable introsort, so break the final tie on arrival
    # order to reproduce the stable sort the rest of the family performs.
    return [int]$Left["_arrival_index"] - [int]$Right["_arrival_index"]
}

function Add-GitLoopyContinuationOrderKey {
    <#
        .SYNOPSIS
        Attach the ordering-only fields a projected Action is sorted by.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Item,
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][Collections.IDictionary]$Action,
        [Parameter(Mandatory)][Collections.IDictionary]$LayerCache
    )

    $RevisionId = [string]$Record["revision_id"]
    if (-not $LayerCache.Contains($RevisionId)) {
        $LayerCache[$RevisionId] =
            Get-GitLoopyLocalTopologicalLayers -Record $Record
    }
    $ActionKey = [string]$Action["key"]
    $LocalKeys = @(
        @($Record["actions"]) | ForEach-Object { [string]$_["key"] }
    )
    $Item["_topological_layer"] = [int]$LayerCache[$RevisionId][$ActionKey]
    $Item["_local_order_index"] = [int]([Array]::IndexOf($LocalKeys, $ActionKey))
}

function Get-GitLoopyContinuationViewOrder {
    <#
        .SYNOPSIS
        Sort projected Actions into Continuation view order and drop the
        ordering-only fields.
    #>
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[object]]$Actions
    )

    for ($Index = 0; $Index -lt $Actions.Count; $Index++) {
        $Actions[$Index]["_arrival_index"] = $Index
    }
    $Actions.Sort([Comparison[object]]{
        param($Left, $Right)
        Compare-GitLoopyContinuationViewOrder -Left $Left -Right $Right
    })
    foreach ($Item in $Actions) {
        $Item.Remove("_topological_layer")
        $Item.Remove("_local_order_index")
        $Item.Remove("_arrival_index")
    }
    return , [object[]]@($Actions)
}

function Get-GitLoopyActionIdentityFromParts {
    param(
        [Parameter(Mandatory)][object]$Anchor,
        [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][object]$Target,
        [Parameter(Mandatory)][string]$Occurrence
    )

    return Get-GitLoopySha256 (
        ConvertTo-GitLoopyCanonicalJson ([ordered]@{
            anchor = $Anchor
            kind = $Kind
            target = $Target
            occurrence = $Occurrence
        })
    )
}

function Get-GitLoopyActionIdentity {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][Collections.IDictionary]$Action
    )

    return Get-GitLoopyActionIdentityFromParts `
        -Anchor $Record["workstream"]["anchor"] `
        -Kind ([string]$Action["kind"]) `
        -Target $Action["target"] `
        -Occurrence ([string]$Action["occurrence"])
}

function Get-GitLoopyRecordIdentities {
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    $Identities = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Action in (Get-GitLoopyRecordActions $Record)) {
        [void]$Identities.Add(
            (Get-GitLoopyActionIdentity -Record $Record -Action $Action)
        )
    }
    return , $Identities
}

function Get-GitLoopyRecordRetirements {
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    if (
        -not $Record.Contains("retirements") -or
        $null -eq $Record["retirements"]
    ) {
        return , [object[]]@()
    }
    return , [object[]]@($Record["retirements"])
}

function Get-GitLoopyRecordParents {
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    if (-not $Record.Contains("parents") -or $null -eq $Record["parents"]) {
        return , [object[]]@()
    }
    return , [object[]]@($Record["parents"])
}

function Test-GitLoopyRecurrenceOf {
    <#
        .SYNOPSIS
        Decide whether one replacement repeats exactly the retired operation.

        .DESCRIPTION
        A recurrence is the *same* operation -- identical Workstream Anchor,
        Action kind, and durable Target -- declared again under a genuinely new
        durable occurrence discriminator. Reusing the retired occurrence is not
        a recurrence, and neither is pointing at some unrelated Action.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Replacement,
        [Parameter(Mandatory)][Collections.IDictionary]$PredecessorRecord,
        [Parameter(Mandatory)][Collections.IDictionary]$RetiredAction
    )

    return (
        (ConvertTo-GitLoopyCanonicalJson $Replacement["workstream_anchor"]) -ceq
            (
                ConvertTo-GitLoopyCanonicalJson `
                    $PredecessorRecord["workstream"]["anchor"]
            ) -and
        $Replacement["kind"] -ceq $RetiredAction["kind"] -and
        (ConvertTo-GitLoopyCanonicalJson $Replacement["target"]) -ceq
            (ConvertTo-GitLoopyCanonicalJson $RetiredAction["target"]) -and
        $Replacement["occurrence"] -cne $RetiredAction["occurrence"]
    )
}

function Get-GitLoopyRetirementRelationship {
    <#
        .SYNOPSIS
        Prove that one receipt removes its predecessor and names a new
        replacement, or return $null when it proves nothing.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][Collections.IDictionary]$Receipt,
        [Parameter(Mandatory)][Collections.IDictionary]$PredecessorRecord
    )

    $ActionKey = [string]$Receipt["action_key"]
    $RetiredAction = $null
    foreach ($Action in (Get-GitLoopyRecordActions $PredecessorRecord)) {
        if ([string]$Action["key"] -ceq $ActionKey) {
            $RetiredAction = $Action
            break
        }
    }
    if ($null -eq $RetiredAction) {
        return $null
    }
    $RetiredIdentity = Get-GitLoopyActionIdentity `
        -Record $PredecessorRecord `
        -Action $RetiredAction
    $CurrentIdentities = Get-GitLoopyRecordIdentities $Record
    if ($CurrentIdentities.Contains($RetiredIdentity)) {
        return $null
    }
    if ($Receipt["reason"] -cne "supersession") {
        return [ordered]@{
            retired_identity = $RetiredIdentity
            replacement_identity = $null
        }
    }
    $Replacement = $Receipt["replacement"]
    if (
        -not (
            Test-GitLoopyRecurrenceOf `
                -Replacement $Replacement `
                -PredecessorRecord $PredecessorRecord `
                -RetiredAction $RetiredAction
        )
    ) {
        return $null
    }
    $ReplacementIdentity = Get-GitLoopyActionIdentityFromParts `
        -Anchor $Replacement["workstream_anchor"] `
        -Kind ([string]$Replacement["kind"]) `
        -Target $Replacement["target"] `
        -Occurrence ([string]$Replacement["occurrence"])
    if (-not $CurrentIdentities.Contains($ReplacementIdentity)) {
        return $null
    }
    return [ordered]@{
        retired_identity = $RetiredIdentity
        replacement_identity = $ReplacementIdentity
    }
}

function Get-GitLoopyProvenRetirements {
    <#
        .SYNOPSIS
        Split one record's receipts into the proven ones and the unprovable
        ones.

        .DESCRIPTION
        This is the single place a receipt's live legitimacy is decided, so the
        retirement projection, the missing-receipt check, and the ancestry
        resurrection check can never disagree about what was really retired.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][Collections.IDictionary]$ById
    )

    $Proven = [Collections.Generic.List[object]]::new()
    $Unproven = [Collections.Generic.List[object]]::new()
    $Parents = Get-GitLoopyRecordParents $Record
    foreach ($Receipt in (Get-GitLoopyRecordRetirements $Record)) {
        $PredecessorId = [string]$Receipt["predecessor_revision_id"]
        $Relationship = $null
        if ($ById.Contains($PredecessorId) -and $Parents -ccontains $PredecessorId) {
            $Relationship = Get-GitLoopyRetirementRelationship `
                -Record $Record `
                -Receipt $Receipt `
                -PredecessorRecord $ById[$PredecessorId]["record"]
        }
        if ($null -eq $Relationship) {
            $Unproven.Add($Receipt)
            continue
        }
        $Proven.Add([ordered]@{
            receipt = $Receipt
            retired_identity = $Relationship["retired_identity"]
            replacement_identity = $Relationship["replacement_identity"]
        })
    }
    return [ordered]@{
        proven = $Proven
        unproven = $Unproven
    }
}

function New-GitLoopyInvalidReceiptDiagnostic {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Comment,
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][Collections.IDictionary]$Receipt
    )

    return [ordered]@{
        code = "invalid_retirement_receipt"
        comment_id = [long]$Comment["id"]
        revision_id = [string]$Record["revision_id"]
        predecessor_revision_id = [string]$Receipt["predecessor_revision_id"]
        action_key = [string]$Receipt["action_key"]
    }
}

function Get-GitLoopyRetirementProjection {
    <#
        .SYNOPSIS
        Derive bounded, transient Retirement receipts for one lineage's heads.

        .DESCRIPTION
        Nothing is journaled or cached: every receipt is proven, per call,
        directly against the immutable revision chain already read this
        Reconciliation. A live successor names the predecessor revision it
        retired as one of its own `parents` and must carry a typed receipt whose
        `action_key` really existed there; anything else is an unverifiable
        receipt and only becomes a diagnostic, never fatal to the rest of
        guidance.
    #>
    param(
        [Parameter(Mandatory)][Collections.IList]$LiveEntries,
        [Parameter(Mandatory)][Collections.IDictionary]$ById
    )

    $Retirements = [Collections.Generic.List[object]]::new()
    $Diagnostics = [Collections.Generic.List[object]]::new()
    foreach ($Entry in $LiveEntries) {
        $Record = $Entry["record"]
        $Split = Get-GitLoopyProvenRetirements -Record $Record -ById $ById
        foreach ($Receipt in $Split["unproven"]) {
            $Diagnostics.Add((
                New-GitLoopyInvalidReceiptDiagnostic `
                    -Comment $Entry["comment"] `
                    -Record $Record `
                    -Receipt $Receipt
            ))
        }
        foreach ($Item in $Split["proven"]) {
            $Receipt = $Item["receipt"]
            $RetirementEntry = [ordered]@{
                workstream_anchor = $Record["workstream"]["anchor"]
                action_identity = [string]$Item["retired_identity"]
                predecessor_revision_id =
                    [string]$Receipt["predecessor_revision_id"]
                reason = [string]$Receipt["reason"]
                evidence = $Receipt["evidence"]
            }
            if ($null -ne $Item["replacement_identity"]) {
                $RetirementEntry["replacement_identity"] =
                    [string]$Item["replacement_identity"]
            }
            $Retirements.Add($RetirementEntry)
        }
    }
    return [ordered]@{
        retirements = $Retirements
        diagnostics = $Diagnostics
    }
}

function Get-GitLoopyRetiredAncestorIdentities {
    <#
        .SYNOPSIS
        Collect every Action identity provably retired anywhere in the ancestry.

        .DESCRIPTION
        Retirement binds the whole descendant chain, not just the revision that
        proved it: a completed or invalidated occurrence stays retired however
        many revisions later it is re-declared. Walking the full ancestry is
        what makes that durable without a tombstone ledger -- the immutable
        chain already read this Reconciliation is the only evidence consulted.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][Collections.IDictionary]$ById
    )

    $Retired = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    $Seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $Frontier = [Collections.Generic.Stack[string]]::new()
    foreach ($Parent in (Get-GitLoopyRecordParents $Record)) {
        $Frontier.Push([string]$Parent)
    }
    while ($Frontier.Count -gt 0) {
        $RevisionId = $Frontier.Pop()
        if (-not $Seen.Add($RevisionId)) {
            continue
        }
        if (-not $ById.Contains($RevisionId)) {
            continue
        }
        $Ancestor = $ById[$RevisionId]["record"]
        $Split = Get-GitLoopyProvenRetirements -Record $Ancestor -ById $ById
        foreach ($Item in $Split["proven"]) {
            [void]$Retired.Add([string]$Item["retired_identity"])
        }
        foreach ($Parent in (Get-GitLoopyRecordParents $Ancestor)) {
            $Frontier.Push([string]$Parent)
        }
    }
    return , $Retired
}

function Get-GitLoopyResurrectedIdentities {
    <#
        .SYNOPSIS
        Find retired occurrences this record re-declares as live guidance.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][Collections.IDictionary]$ById
    )

    $Retired = Get-GitLoopyRetiredAncestorIdentities -Record $Record -ById $ById
    if ($Retired.Count -eq 0) {
        return , [object[]]@()
    }
    $Resurrected = [Collections.Generic.List[string]]::new()
    foreach ($Identity in (Get-GitLoopyRecordIdentities $Record)) {
        if ($Retired.Contains($Identity)) {
            $Resurrected.Add($Identity)
        }
    }
    $Sorted = Get-GitLoopyOrdinalSortedStrings $Resurrected.ToArray()
    return , [object[]]@($Sorted)
}

function Get-GitLoopyMissingRetirementReceipts {
    <#
        .SYNOPSIS
        Find predecessor Actions removed without a typed Retirement receipt.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][Collections.IDictionary]$ById
    )

    $CurrentIdentities = Get-GitLoopyRecordIdentities $Record
    $Split = Get-GitLoopyProvenRetirements -Record $Record -ById $ById
    $Declared = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Item in $Split["proven"]) {
        [void]$Declared.Add(
            [string]$Item["receipt"]["predecessor_revision_id"] + "`0" +
            [string]$Item["receipt"]["action_key"]
        )
    }
    $Missing = [Collections.Generic.List[object]]::new()
    foreach ($PredecessorId in (Get-GitLoopyRecordParents $Record)) {
        $Key = [string]$PredecessorId
        if (-not $ById.Contains($Key)) {
            continue
        }
        $Predecessor = $ById[$Key]["record"]
        foreach ($Action in (Get-GitLoopyRecordActions $Predecessor)) {
            $ActionKey = [string]$Action["key"]
            $Identity = Get-GitLoopyActionIdentity `
                -Record $Predecessor `
                -Action $Action
            if (
                -not $CurrentIdentities.Contains($Identity) -and
                -not $Declared.Contains($Key + "`0" + $ActionKey)
            ) {
                $Missing.Add([ordered]@{
                    predecessor_revision_id = $Key
                    action_key = $ActionKey
                })
            }
        }
    }
    return , [object[]]@($Missing)
}

function Test-GitLoopyPreviousActions {
    <#
        .SYNOPSIS
        Structurally validate a caller-supplied prior observation for delta.

        .DESCRIPTION
        This is deliberately the only source of "previous": there is no hidden
        process memory or cache. Callers must explicitly pass back what an
        earlier Reconciliation returned (or narrower lineage evidence in the
        same shape) for a bounded refresh delta to be computed at all.
    #>
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name
    )

    $Entries = Assert-GitLoopyArray $Value $Name
    $Result = [Collections.Generic.List[object]]::new()
    for ($Index = 0; $Index -lt $Entries.Count; $Index++) {
        $ItemName = "$Name[$Index]"
        $Entry = Assert-GitLoopyObject $Entries[$Index] $ItemName
        Assert-GitLoopyFields `
            -Value $Entry `
            -Name $ItemName `
            -Required @("identity", "semantic_fingerprint") `
            -Optional @()
        $Result.Add([ordered]@{
            identity = Assert-GitLoopyString $Entry["identity"] "$ItemName.identity"
            semantic_fingerprint = Assert-GitLoopyString `
                $Entry["semantic_fingerprint"] "$ItemName.semantic_fingerprint"
        })
    }
    return , [object[]]@($Result)
}

function Get-GitLoopyOrdinalSortedStrings {
    <#
        .SYNOPSIS
        Sort strings by ordinal code point, as Python's `sorted` does.

        .DESCRIPTION
        `Sort-Object` is culture-aware even with `-CaseSensitive`, and a culture
        comparison ignores characters such as `-` that appear in every canonical
        reference this contract orders by. Ordering is contract, not cosmetics,
        so it is taken from the code points rather than from the host culture.
    #>
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [string[]]$Values
    )

    $Sorted = [string[]]::new($Values.Count)
    if ($Values.Count -gt 0) {
        [Array]::Copy($Values, $Sorted, $Values.Count)
        [Array]::Sort($Sorted, [StringComparer]::Ordinal)
    }
    return , [object[]]@($Sorted)
}

function Get-GitLoopyOrdinalSortedBy {
    <#
        .SYNOPSIS
        Stably sort items by the ordinal join of the keys `KeySelector` returns.
    #>
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Items,
        [Parameter(Mandatory)][scriptblock]$KeySelector
    )

    $Count = $Items.Count
    if ($Count -le 1) {
        return , [object[]]@($Items)
    }
    $Keys = [string[]]::new($Count)
    for ($Index = 0; $Index -lt $Count; $Index++) {
        $Parts = [Collections.Generic.List[string]]::new()
        foreach ($Part in @(& $KeySelector $Items[$Index])) {
            $Parts.Add([string]$Part)
        }
        # A stable sort: equal keys keep their discovery order, as Python's does.
        $Parts.Add(
            $Index.ToString("D9", [Globalization.CultureInfo]::InvariantCulture)
        )
        $Keys[$Index] = [string]::Join([char]0, $Parts.ToArray())
    }
    [Array]::Sort($Keys, [StringComparer]::Ordinal)
    $Sorted = [object[]]::new($Count)
    for ($Index = 0; $Index -lt $Count; $Index++) {
        $Parts = $Keys[$Index].Split([char]0)
        $Sorted[$Index] = $Items[[int]::Parse(
            $Parts[$Parts.Length - 1],
            [Globalization.CultureInfo]::InvariantCulture
        )]
    }
    return , [object[]]@($Sorted)
}

function Get-GitLoopyActionsDelta {
    <#
        .SYNOPSIS
        Bound one refresh delta between an explicit prior observation and now.

        .DESCRIPTION
        Never uses hidden process memory: `PreviousActions` must be supplied by
        the caller (typically the prior reconcile result's own Actions) or
        derived from durable lineage evidence, per call.
    #>
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Actions,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$PreviousActions
    )

    $Current = [Collections.Generic.Dictionary[string, string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Action in $Actions) {
        $Current[[string]$Action["identity"]] =
            [string]$Action["semantic_fingerprint"]
    }
    $Previous = [Collections.Generic.Dictionary[string, string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Entry in $PreviousActions) {
        $Previous[[string]$Entry["identity"]] =
            [string]$Entry["semantic_fingerprint"]
    }
    $Added = [Collections.Generic.List[string]]::new()
    $Changed = [Collections.Generic.List[string]]::new()
    foreach ($Identity in @($Current.Keys)) {
        if (-not $Previous.ContainsKey($Identity)) {
            $Added.Add($Identity)
        }
        elseif ($Previous[$Identity] -cne $Current[$Identity]) {
            $Changed.Add($Identity)
        }
    }
    $Retired = [Collections.Generic.List[string]]::new()
    foreach ($Identity in @($Previous.Keys)) {
        if (-not $Current.ContainsKey($Identity)) {
            $Retired.Add($Identity)
        }
    }
    return [ordered]@{
        added = Get-GitLoopyOrdinalSortedStrings $Added.ToArray()
        retired = Get-GitLoopyOrdinalSortedStrings $Retired.ToArray()
        changed = Get-GitLoopyOrdinalSortedStrings $Changed.ToArray()
    }
}

function Test-GitLoopyHandoff {
    <#
        .SYNOPSIS
        Structurally validate one Handoff resume request.

        .DESCRIPTION
        `context_available` is the caller's own machine-local diagnostic: it is
        never used to derive Readiness, order, or completion, only to decide
        whether a Handoff reference can be attached at all.
    #>
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name
    )

    $Entry = Assert-GitLoopyObject $Value $Name
    Assert-GitLoopyFields `
        -Value $Entry `
        -Name $Name `
        -Required @("action_identity", "context_available") `
        -Optional @("reference", "note")
    $Result = [ordered]@{
        action_identity = Assert-GitLoopyString `
            $Entry["action_identity"] "$Name.action_identity"
    }
    if ($Entry["context_available"] -isnot [bool]) {
        throw (New-GitLoopyRejection "$Name.context_available must be a boolean")
    }
    $Result["context_available"] = [bool]$Entry["context_available"]
    if ($Result["context_available"]) {
        $Result["reference"] = Assert-GitLoopyString `
            $Entry["reference"] "$Name.reference"
    }
    elseif ($Entry.Contains("reference")) {
        throw (New-GitLoopyRejection (
            "$Name.reference requires available machine-local context"
        ))
    }
    if ($Entry.Contains("note")) {
        $Result["note"] = Assert-GitLoopyString $Entry["note"] "$Name.note"
    }
    return $Result
}

function Add-GitLoopyHandoffReference {
    <#
        .SYNOPSIS
        Attach at most one exact-occurrence Handoff reference, after ordering.

        .DESCRIPTION
        Handoff availability is diagnostic-only context: unavailable local
        context, or an Action Reconciliation no longer carries, is reported only
        as a diagnostic and never recreates the Action, never changes Readiness,
        eligibility, order, or completion.
    #>
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Actions,
        [Parameter(Mandatory)][Collections.IDictionary]$Handoff
    )

    if (-not $Handoff["context_available"]) {
        return , [object[]]@([ordered]@{
            code = "handoff_context_unavailable"
            action_identity = [string]$Handoff["action_identity"]
        })
    }
    foreach ($Action in $Actions) {
        if ([string]$Action["identity"] -ceq [string]$Handoff["action_identity"]) {
            $Reference = [ordered]@{
                available = $true
                reference = [string]$Handoff["reference"]
            }
            if ($Handoff.Contains("note")) {
                $Reference["note"] = [string]$Handoff["note"]
            }
            $Action["handoff_reference"] = $Reference
            return , [object[]]@()
        }
    }
    return , [object[]]@([ordered]@{
        code = "handoff_action_unavailable"
        action_identity = [string]$Handoff["action_identity"]
    })
}

function Get-GitLoopyRenderedLocator {
    <#
        .SYNOPSIS
        Render one durable Target/Basis/Anchor locator, never its content.
    #>
    param([Parameter(Mandatory)][Collections.IDictionary]$Reference)

    $Kind = [string]$Reference["kind"]
    $RepositoryUrl = "https://github.com/$([string]$Reference["repository"])"
    switch ($Kind) {
        "issue" { return "$RepositoryUrl/issues/$($Reference["number"])" }
        "pull-request" { return "$RepositoryUrl/pull/$($Reference["number"])" }
        "commit" { return "$RepositoryUrl/commit/$($Reference["sha"])" }
        "branch" { return "$RepositoryUrl/tree/$($Reference["sha"])" }
        "issue-comment" {
            return (
                "$RepositoryUrl/issues/$($Reference["issue"])" +
                "#issuecomment-$($Reference["comment_id"])"
            )
        }
        "pull-request-review" {
            return (
                "$RepositoryUrl/pull/$($Reference["pull_request"])" +
                "#pullrequestreview-$($Reference["review_id"])"
            )
        }
    }
    return $Kind
}

function Get-GitLoopyRenderedRemainder {
    <#
        .SYNOPSIS
        Render one bounded remainder group whose hidden count is truthful.

        .DESCRIPTION
        The group is genuinely bounded: at most `$Script:TerminalRemainderRows`
        compact rows are printed and the count of Actions actually withheld is
        stated, so the human projection never claims to hide rows it just
        printed. Nothing is dropped silently -- the machine projection remains
        the expansion.
    #>
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Remainder
    )

    if ($Remainder.Count -eq 0) {
        return , [string[]]@()
    }
    $Lines = [Collections.Generic.List[string]]::new()
    $ShownCount = [Math]::Min($Remainder.Count, $Script:TerminalRemainderRows)
    $Hidden = $Remainder.Count - $ShownCount
    $Lines.Add("")
    $Lines.Add("$Title ($($Remainder.Count) more, $Hidden hidden):")
    for ($Index = 0; $Index -lt $ShownCount; $Index++) {
        $Action = $Remainder[$Index]
        $Locator = Get-GitLoopyRenderedLocator $Action["target"]
        $Lines.Add("  - $([string]$Action["summary"]) [$Locator]")
    }
    if ($Hidden -gt 0) {
        $Lines.Add(
            "  $([char]0x2026) expand the remaining $Hidden with reconcile " +
            "without --terminal."
        )
    }
    return , [string[]]@($Lines)
}

function Get-GitLoopyTerminalRendering {
    <#
        .SYNOPSIS
        Render one reconcile result as plain native terminal text.

        .DESCRIPTION
        Presentation-only: this never re-derives Readiness, order, or
        completion, it only formats what Reconciliation already produced.
        Exactly one primary Action is shown in full, standalone-exact detail;
        any remainder is an expandable Ready/Blocked group with a hidden count
        rather than being silently dropped. Every Target/Basis locator is a
        durable reference, never inlined content.
    #>
    param([Parameter(Mandatory)][Collections.IDictionary]$Result)

    $StatusTitle = switch ([string]$Result["status"]) {
        "guidance" { "Guidance" }
        "waiting" { "Waiting" }
        "complete" { "Complete" }
    }
    $Lines = [Collections.Generic.List[string]]::new()
    $Lines.Add(
        "Continuation: $([string]$Result["observed"]["repository"]) " +
        "$([char]0x2014) $StatusTitle"
    )
    $Lines.Add("")
    $Actions = [object[]]@($Result["actions"])
    if ($Actions.Count -eq 0) {
        $Lines.Add($StatusTitle)
    }
    else {
        $Primary = $Actions[0]
        $Lines.Add(
            "Primary Action ($([string]$Primary["readiness"])): " +
            [string]$Primary["summary"]
        )
        $Lines.Add(
            "  Interaction: " +
            [string]$Primary["interaction"]["classification"]
        )
        $Lines.Add("  Instruction ($([string]$Primary["instruction"]["mode"])):")
        $Lines.Add([string]$Primary["instruction"]["value"])
        $Lines.Add(
            "  Target: " + (Get-GitLoopyRenderedLocator $Primary["target"])
        )
        $BasisLocators = [Collections.Generic.List[string]]::new()
        foreach ($Item in @($Primary["basis"])) {
            $BasisLocators.Add((Get-GitLoopyRenderedLocator $Item))
        }
        $Lines.Add("  Basis: " + ($BasisLocators -join ", "))
        if ($Primary.Contains("handoff_reference")) {
            $Lines.Add(
                "  Handoff: " +
                [string]$Primary["handoff_reference"]["reference"]
            )
        }
        $ReadyRemainder = [Collections.Generic.List[object]]::new()
        $BlockedRemainder = [Collections.Generic.List[object]]::new()
        for ($Index = 1; $Index -lt $Actions.Count; $Index++) {
            if ($Actions[$Index]["readiness"] -ceq "Ready") {
                $ReadyRemainder.Add($Actions[$Index])
            }
            elseif ($Actions[$Index]["readiness"] -ceq "Blocked") {
                $BlockedRemainder.Add($Actions[$Index])
            }
        }
        foreach ($Line in (
                Get-GitLoopyRenderedRemainder `
                    -Title "Ready" `
                    -Remainder ([object[]]@($ReadyRemainder))
            )) {
            $Lines.Add($Line)
        }
        foreach ($Line in (
                Get-GitLoopyRenderedRemainder `
                    -Title "Blocked" `
                    -Remainder ([object[]]@($BlockedRemainder))
            )) {
            $Lines.Add($Line)
        }
    }

    $Diagnostics = [object[]]@($Result["diagnostics"])
    if ($Diagnostics.Count -gt 0) {
        $Lines.Add("")
        $Lines.Add("Needs attention ($($Diagnostics.Count)):")
        foreach ($Diagnostic in $Diagnostics) {
            $Lines.Add("  - $([string]$Diagnostic["code"])")
        }
    }

    $Outcomes = [object[]]@()
    if ($Result.Contains("outcomes")) {
        $Outcomes = [object[]]@($Result["outcomes"])
    }
    if ($Outcomes.Count -gt 0) {
        $Lines.Add("")
        $Lines.Add("Outcomes:")
        foreach ($Outcome in $Outcomes) {
            $Satisfaction = if ([bool]$Outcome["destination_satisfied"]) {
                "destination satisfied"
            }
            else {
                "destination not satisfied"
            }
            $Locator = Get-GitLoopyRenderedLocator $Outcome["workstream_anchor"]
            $Lines.Add(
                "  - ${Locator}: $([string]$Outcome["kind"]) ($Satisfaction)"
            )
        }
    }

    $Retirements = [object[]]@($Result["retirements"])
    if ($Retirements.Count -gt 0) {
        $Lines.Add("")
        $Lines.Add("Retired this refresh ($($Retirements.Count)):")
        foreach ($Retirement in $Retirements) {
            $Predecessor = (
                [string]$Retirement["predecessor_revision_id"]
            ).Substring(0, 12)
            $Lines.Add(
                "  - $([string]$Retirement["reason"]) " +
                "(predecessor $Predecessor$([char]0x2026))"
            )
        }
    }

    if ($Result.Contains("delta")) {
        $Delta = $Result["delta"]
        $Lines.Add("")
        $Lines.Add(
            "Refresh delta: " +
            "+$(@($Delta["added"]).Count) added, " +
            "-$(@($Delta["retired"]).Count) retired, " +
            "~$(@($Delta["changed"]).Count) changed"
        )
    }

    $Lines.Add("")
    return $Lines -join "`n"
}

function Get-GitLoopyProjectedOutcome {
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    # `continue` and `no-guidance` records never carry an `outcome` and
    # contribute nothing here; only an affirmatively terminal disposition does.
    if ($Record["disposition"] -cne "terminal") {
        return $null
    }
    $Outcome = $Record["outcome"]
    $Projected = [ordered]@{
        workstream_anchor = $Record["workstream"]["anchor"]
        kind = $Outcome["kind"]
        destination_satisfied = $Outcome["destination_satisfied"]
        effective_at = $Outcome["effective_at"]
        evidence = $Outcome["evidence"]
        summary = $Outcome["summary"]
    }
    if ($Outcome.Contains("successor")) {
        $Projected["successor"] = $Outcome["successor"]
    }
    return $Projected
}

function Get-GitLoopyWorkstreamOutcomes {
    param([Parameter(Mandatory)][Collections.IList]$GuidanceEntries)

    $Outcomes = [Collections.Generic.List[object]]::new()
    foreach ($Entry in $GuidanceEntries) {
        $Outcome = Get-GitLoopyProjectedOutcome $Entry["record"]
        if ($null -ne $Outcome) {
            $Outcomes.Add($Outcome)
        }
    }
    $Sorted = Get-GitLoopyOrdinalSortedBy $Outcomes.ToArray() {
        param($Outcome)
        ConvertTo-GitLoopyCanonicalJson $Outcome["workstream_anchor"]
    }
    return , [object[]]@($Sorted)
}

function Get-GitLoopyGuidanceStatus {
    param(
        [Parameter(Mandatory)][Collections.IList]$GuidanceEntries,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Actions,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Outcomes,
        [Parameter(Mandatory)][bool]$ClosedCoverage
    )

    # Complete is never inferred from a merely empty Action list: it requires an
    # explicit destination-satisfied outcome for every currently observed
    # Workstream, gathered over a closed-coverage read. A project with no
    # guidance entries at all, or one still holding a non-terminal (for example
    # `no-guidance`) lineage, renders Waiting rather than an unproven Complete.
    if ($Actions.Count -gt 0) {
        return "guidance"
    }
    $EveryLineageTerminal = $GuidanceEntries.Count -gt 0
    foreach ($Entry in $GuidanceEntries) {
        if ($Entry["record"]["disposition"] -cne "terminal") {
            $EveryLineageTerminal = $false
        }
    }
    $EveryOutcomeComplete = $true
    foreach ($Outcome in $Outcomes) {
        if (
            $Outcome["kind"] -cne "complete" -or
            -not [bool]$Outcome["destination_satisfied"]
        ) {
            $EveryOutcomeComplete = $false
        }
    }
    if ($ClosedCoverage -and $EveryLineageTerminal -and $EveryOutcomeComplete) {
        return "complete"
    }
    return "waiting"
}

function Get-GitLoopyDerivedActions {
    param(
        [Parameter(Mandatory)][Collections.IList]$GuidanceEntries,
        [Parameter(Mandatory)][string]$Repository
    )

    $FactCache = [ordered]@{}
    $Diagnostics = [Collections.Generic.List[object]]::new()
    $Contributions = [ordered]@{}
    foreach ($Entry in $GuidanceEntries) {
        $Record = $Entry["record"]
        $Evaluated = Get-GitLoopyEvaluatedFragment `
            -Record $Record `
            -Repository $Repository `
            -FactCache $FactCache
        foreach ($Diagnostic in $Evaluated["diagnostics"]) {
            $Diagnostics.Add($Diagnostic)
        }
        foreach ($Result in $Evaluated["results"]) {
            $Action = $Result["action"]
            $Identity = Get-GitLoopySha256 (
                ConvertTo-GitLoopyCanonicalJson ([ordered]@{
                    anchor = $Record["workstream"]["anchor"]
                    kind = $Action["kind"]
                    target = $Action["target"]
                    occurrence = $Action["occurrence"]
                })
            )
            if (-not $Contributions.Contains($Identity)) {
                $Contributions[$Identity] =
                    [Collections.Generic.List[object]]::new()
            }
            $Contributions[$Identity].Add([ordered]@{
                comment = $Entry["comment"]
                record = $Record
                producer = $Record["producer"]
                action = $Action
                readiness = $Result["readiness"]
                unsatisfied = $Result["unsatisfied"]
                semantic_fingerprint =
                    $Record["semantic_fingerprints"][$Action["key"]]
            })
        }
    }
    $Actions = [Collections.Generic.List[object]]::new()
    $LayerCache = [Collections.Specialized.OrderedDictionary]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Identity in @($Contributions.Keys)) {
        $Contributed = $Contributions[$Identity]
        $Fingerprints = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Contribution in $Contributed) {
            [void]$Fingerprints.Add(
                [string]$Contribution["semantic_fingerprint"]
            )
        }
        if ($Fingerprints.Count -gt 1) {
            $Diagnostics.Add([ordered]@{
                code = "action_conflict"
                identity = $Identity
                revision_ids = [object[]]@(
                    $Contributed |
                        ForEach-Object { $_["record"]["revision_id"] } |
                        Sort-Object
                )
                semantic_fingerprints = [object[]]@(
                    $Fingerprints | Sort-Object
                )
            })
            continue
        }
        $Sorted = @(
            $Contributed |
                Sort-Object -Property `
                    { [string]$_["record"]["revision_id"] }, `
                    { [long]$_["comment"]["id"] }
        )
        $Canonical = $Sorted[0]
        $Action = $Canonical["action"]
        $CanonicalRecord = $Canonical["record"]
        $Producer = [ordered]@{}
        foreach ($Entry in $Canonical["producer"].GetEnumerator()) {
            $Producer[$Entry.Key] = $Entry.Value
        }
        $Producer["carrier"] = $Canonical["record"]["carrier"]
        $Producer["revision_id"] = $Canonical["record"]["revision_id"]
        $Producer["comment_id"] = [long]$Canonical["comment"]["id"]
        $Producer["comment_url"] = [string]$Canonical["comment"]["url"]
        $Item = [ordered]@{
            identity = $Identity
            semantic_fingerprint = $Canonical["semantic_fingerprint"]
            workstream_anchor = $Canonical["record"]["workstream"]["anchor"]
            summary = $Action["summary"]
            kind = $Action["kind"]
            readiness = $Canonical["readiness"]
            instruction = $Action["instruction"]
            target = $Action["target"]
            basis = Get-GitLoopyUnionBasis -Contributions $Contributed
            producer = $Producer
            prerequisites = $Action["prerequisites"]
            interaction = $Action["interaction"]
            completion_condition = $Action["completion_condition"]
        }
        Add-GitLoopyContinuationOrderKey `
            -Item $Item `
            -Record $CanonicalRecord `
            -Action $Action `
            -LayerCache $LayerCache
        if ($Contributed.Count -gt 1) {
            $Item["provenance"] =
                Get-GitLoopyUnionProvenance -Contributions $Contributed
        }
        if (@($Canonical["unsatisfied"]).Count -gt 0) {
            $Item["unsatisfied_prerequisites"] = $Canonical["unsatisfied"]
        }
        if ($Action.Contains("safety_case")) {
            # The positive AFK safety case is Action semantics a Consumer must
            # be able to read; it is what an authorization is bound to.
            $Item["safety_case"] = $Action["safety_case"]
        }
        $Actions.Add($Item)
    }
    return [ordered]@{
        actions = Get-GitLoopyContinuationViewOrder -Actions $Actions
        diagnostics = $Diagnostics
    }
}

# ---------------------------------------------------------------------------
# Fixed-frontier Automation authorization
#
# Reconciliation derives what is true. This section decides, for one Performer
# and one frozen Run boundary, which of those Actions that Performer could be
# authorized to perform unattended -- and, when none can be, says exactly why.
# It never performs an Action, and it never widens anything: every rule here
# can only remove an Action from consideration.
# ---------------------------------------------------------------------------

function New-GitLoopyOrdinalSet {
    param([string[]]$Items = @())

    $Set = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($Item in $Items) { [void]$Set.Add($Item) }
    # A bare `return` unrolls any IEnumerable, so an empty set would arrive as
    # $null. The unary comma keeps every set whole.
    return , $Set
}

function Get-GitLoopyPairKey {
    param(
        [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][string]$Value
    )
    return "$Kind`0$Value"
}

function Get-GitLoopyScopeEntries {
    <#
        Validate one typed effect-scope list and return its pair keys.
    #>
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name
    )

    Test-GitLoopyTypedSemantics `
        -Value $Value `
        -Name $Name `
        -Kinds $Script:EffectKinds `
        -SecondField "scope"
    $Keys = New-GitLoopyOrdinalSet
    foreach ($Entry in @($Value)) {
        [void]$Keys.Add(
            (Get-GitLoopyPairKey `
                    -Kind ([string]$Entry["kind"]) `
                    -Value ([string]$Entry["scope"]))
        )
    }
    return , $Keys
}

function Get-GitLoopyRequirementEntries {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name
    )

    Test-GitLoopyTypedSemantics `
        -Value $Value `
        -Name $Name `
        -Kinds $Script:RequirementKinds `
        -SecondField "name"
    $Keys = New-GitLoopyOrdinalSet
    foreach ($Entry in @($Value)) {
        [void]$Keys.Add(
            (Get-GitLoopyPairKey `
                    -Kind ([string]$Entry["kind"]) `
                    -Value ([string]$Entry["name"]))
        )
    }
    return , $Keys
}

function Get-GitLoopySortedPairs {
    <#
        Render a pair set as the deduplicated, ordinal-ordered typed list the
        contract pins. Culture-aware ordering would ignore the hyphens every
        effect and requirement kind carries.
    #>
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Keys,
        [Parameter(Mandatory)][string]$SecondField
    )

    $Unique = Get-GitLoopyOrdinalSortedStrings @(
        (New-GitLoopyOrdinalSet $Keys)
    )
    $Result = [Collections.Generic.List[object]]::new()
    foreach ($Key in $Unique) {
        $Parts = $Key.Split([char]0, 2)
        $Pair = [ordered]@{ kind = $Parts[0] }
        $Pair[$SecondField] = $Parts[1]
        $Result.Add($Pair)
    }
    return , [object[]]@($Result)
}

function Test-GitLoopyAutomation {
    <#
        Validate the caller's Automation scope, posture, and frozen frontier.
    #>
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name
    )

    $Automation = Assert-GitLoopyObject $Value $Name
    Assert-GitLoopyFields `
        -Value $Automation `
        -Name $Name `
        -Required @("performer", "scope") `
        -Optional @("frontier", "dispatched")

    $PerformerName = "$Name.performer"
    $Performer = Assert-GitLoopyObject $Automation["performer"] $PerformerName
    Assert-GitLoopyFields `
        -Value $Performer `
        -Name $PerformerName `
        -Required @("id", "posture")
    $PerformerId = Assert-GitLoopyString $Performer["id"] "$PerformerName.id"
    $PostureName = "$PerformerName.posture"
    $Posture = Assert-GitLoopyObject $Performer["posture"] $PostureName
    Assert-GitLoopyFields `
        -Value $Posture `
        -Name $PostureName `
        -Required @("noninteractive", "satisfied_requirements", "instruction_modes")
    if ($Posture["noninteractive"] -isnot [bool] -or -not $Posture["noninteractive"]) {
        throw (New-GitLoopyRejection "$PostureName.noninteractive must be true")
    }
    $Satisfied = Get-GitLoopyRequirementEntries `
        $Posture["satisfied_requirements"] "$PostureName.satisfied_requirements"
    # Closed world: a Performer executes only the Instruction modes it declares
    # a handler for. Silence is not a claim of universal competence.
    $HandledModes = New-GitLoopyOrdinalSet
    foreach ($Entry in (Assert-GitLoopyArray `
                $Posture["instruction_modes"] "$PostureName.instruction_modes")) {
        $Mode = Assert-GitLoopyString $Entry "$PostureName.instruction_modes item"
        if ($Mode -cnotin $Script:InstructionModes) {
            throw (New-GitLoopyRejection (
                "$PostureName.instruction_modes item is unsupported"
            ))
        }
        [void]$HandledModes.Add($Mode)
    }

    $ScopeName = "$Name.scope"
    $Scope = Assert-GitLoopyObject $Automation["scope"] $ScopeName
    Assert-GitLoopyFields `
        -Value $Scope `
        -Name $ScopeName `
        -Required @("ceilings", "revocations") `
        -Optional @("prior")
    $Ceilings = Assert-GitLoopyArray $Scope["ceilings"] "$ScopeName.ceilings" -NonEmpty
    $Sources = New-GitLoopyOrdinalSet
    $Repositories = $null
    $Granted = $null
    $Denied = New-GitLoopyOrdinalSet
    for ($Index = 0; $Index -lt $Ceilings.Count; $Index++) {
        $CeilingName = "$ScopeName.ceilings[$Index]"
        $Ceiling = Assert-GitLoopyObject $Ceilings[$Index] $CeilingName
        Assert-GitLoopyFields `
            -Value $Ceiling `
            -Name $CeilingName `
            -Required @("source", "coverage", "grants", "denials")
        $Source = Assert-GitLoopyString $Ceiling["source"] "$CeilingName.source"
        if ($Source -cnotin @("global", "project")) {
            throw (New-GitLoopyRejection "$CeilingName.source is unsupported")
        }
        if (-not $Sources.Add($Source)) {
            throw (New-GitLoopyRejection "$CeilingName.source is declared twice")
        }
        $CoverageName = "$CeilingName.coverage"
        $Coverage = Assert-GitLoopyObject $Ceiling["coverage"] $CoverageName
        Assert-GitLoopyFields `
            -Value $Coverage `
            -Name $CoverageName `
            -Required @("repositories")
        $CeilingRepositories = New-GitLoopyOrdinalSet
        foreach ($Entry in (Assert-GitLoopyArray `
                    $Coverage["repositories"] "$CoverageName.repositories" -NonEmpty)) {
            [void]$CeilingRepositories.Add(
                (Assert-GitLoopyString $Entry "$CoverageName.repositories item")
            )
        }
        # Ceilings intersect: a Run observes only what every ceiling admits.
        if ($null -eq $Repositories) {
            $Repositories = $CeilingRepositories
        }
        else {
            $Repositories.IntersectWith($CeilingRepositories)
        }
        $CeilingGrants = Get-GitLoopyScopeEntries `
            $Ceiling["grants"] "$CeilingName.grants"
        if ($null -eq $Granted) {
            $Granted = $CeilingGrants
        }
        else {
            $Granted.IntersectWith($CeilingGrants)
        }
        # Denials accumulate: one ceiling's refusal is the Run's refusal.
        $Denied.UnionWith(
            (Get-GitLoopyScopeEntries $Ceiling["denials"] "$CeilingName.denials")
        )
    }
    # A revocation observed during the Run narrows immediately and is
    # indistinguishable from a denial thereafter.
    $Denied.UnionWith(
        (Get-GitLoopyScopeEntries $Scope["revocations"] "$ScopeName.revocations")
    )
    if ($null -eq $Repositories) { $Repositories = New-GitLoopyOrdinalSet }
    $EffectiveGrants = New-GitLoopyOrdinalSet @(
        if ($null -eq $Granted) { @() } else { @($Granted) }
    )
    $EffectiveGrants.ExceptWith($Denied)

    # The frozen scope of a Run is replayed alongside its frozen frontier, and
    # the two must narrow together. Recomputing authority from whatever the
    # caller supplies next would let a grant added mid-Run authorize an Action
    # the Run was never entitled to.
    if ($Scope.Contains("prior")) {
        $PriorName = "$ScopeName.prior"
        $Prior = Assert-GitLoopyObject $Scope["prior"] $PriorName
        Assert-GitLoopyFields `
            -Value $Prior `
            -Name $PriorName `
            -Required @("coverage", "grants", "denials") `
            -Optional @("frozen")
        $PriorCoverage = Assert-GitLoopyObject $Prior["coverage"] "$PriorName.coverage"
        Assert-GitLoopyFields `
            -Value $PriorCoverage `
            -Name "$PriorName.coverage" `
            -Required @("repositories")
        $PriorRepositories = New-GitLoopyOrdinalSet
        foreach ($Entry in (Assert-GitLoopyArray `
                    $PriorCoverage["repositories"] "$PriorName.coverage.repositories")) {
            [void]$PriorRepositories.Add(
                (Assert-GitLoopyString $Entry "$PriorName.coverage.repositories item")
            )
        }
        $PriorGrants = Get-GitLoopyScopeEntries $Prior["grants"] "$PriorName.grants"
        $PriorDenials = Get-GitLoopyScopeEntries $Prior["denials"] "$PriorName.denials"
        $Repositories.IntersectWith($PriorRepositories)
        $EffectiveGrants.IntersectWith($PriorGrants)
        $Denied.UnionWith($PriorDenials)
        $EffectiveGrants.ExceptWith($Denied)
    }

    $Frontier = $null
    if ($Automation.Contains("frontier")) {
        $FrontierName = "$Name.frontier"
        $Declared = Assert-GitLoopyObject $Automation["frontier"] $FrontierName
        Assert-GitLoopyFields `
            -Value $Declared `
            -Name $FrontierName `
            -Required @("actions")
        $Frontier = Test-GitLoopyPreviousActions `
            $Declared["actions"] "$FrontierName.actions"
    }

    $Dispatched = New-GitLoopyOrdinalSet
    if ($Automation.Contains("dispatched")) {
        foreach ($Entry in (Assert-GitLoopyArray `
                    $Automation["dispatched"] "$Name.dispatched")) {
            [void]$Dispatched.Add(
                (Assert-GitLoopyString $Entry "$Name.dispatched item")
            )
        }
    }

    return [ordered]@{
        PerformerId = $PerformerId
        SatisfiedRequirements = $Satisfied
        InstructionModes = $HandledModes
        Repositories = $Repositories
        Grants = $EffectiveGrants
        Denials = $Denied
        Frontier = $Frontier
        Dispatched = $Dispatched
    }
}

function Get-GitLoopyActionRepositories {
    param([Parameter(Mandatory)][Collections.IDictionary]$Action)

    $Repositories = New-GitLoopyOrdinalSet
    foreach ($Reference in @($Action["workstream_anchor"], $Action["target"])) {
        if (
            $Reference -is [Collections.IDictionary] -and
            $Reference.Contains("repository")
        ) {
            [void]$Repositories.Add([string]$Reference["repository"])
        }
    }
    return , $Repositories
}

function Get-GitLoopyAutomationIneligibility {
    <#
        Every typed reason this Action is not Automation-selectable.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Action,
        [Parameter(Mandatory)][Collections.IDictionary]$Automation,
        [Parameter(Mandatory)][Collections.IDictionary]$Frontier,
        [Parameter(Mandatory)][Collections.IDictionary]$Quarantined
    )

    $Reasons = New-GitLoopyOrdinalSet
    $Identity = [string]$Action["identity"]
    $Fingerprint = [string]$Action["semantic_fingerprint"]
    if (
        -not (Get-GitLoopyActionRepositories $Action).IsSubsetOf(
            $Automation["Repositories"]
        )
    ) {
        [void]$Reasons.Add("outside-coverage")
    }
    if (
        -not $Frontier.Contains($Identity) -or
        [string]$Frontier[$Identity] -cne $Fingerprint
    ) {
        [void]$Reasons.Add("outside-frontier")
    }
    if ($Automation["Dispatched"].Contains($Identity)) {
        [void]$Reasons.Add("already-dispatched")
    }
    if ($Quarantined.Contains((Get-GitLoopyPairKey -Kind $Identity -Value $Fingerprint))) {
        [void]$Reasons.Add("quarantined")
    }
    if ([string]$Action["interaction"]["classification"] -cne "AFK-safe") {
        [void]$Reasons.Add("human-boundary")
    }
    if ([string]$Action["readiness"] -cne "Ready") {
        [void]$Reasons.Add("not-ready")
    }
    if (-not $Action.Contains("safety_case")) {
        # An AFK-safe classification without its positive safety case is an
        # assertion, not an argument. Absence is never upgraded to eligibility.
        if (-not $Reasons.Contains("human-boundary")) {
            [void]$Reasons.Add("safety-case-absent")
        }
    }
    else {
        $SafetyCase = $Action["safety_case"]
        $Effects = Get-GitLoopyScopeEntries `
            $SafetyCase["effects"] "safety_case.effects"
        if (-not $Effects.IsSubsetOf($Automation["Grants"])) {
            [void]$Reasons.Add("grant-missing")
        }
        $Requirements = Get-GitLoopyRequirementEntries `
            $SafetyCase["requirements"] "safety_case.requirements"
        if (
            -not $Requirements.IsSubsetOf($Automation["SatisfiedRequirements"]) -or
            -not $Automation["InstructionModes"].Contains(
                [string]$Action["instruction"]["mode"]
            )
        ) {
            [void]$Reasons.Add("performer-ineligible")
        }
    }
    return Get-GitLoopyOrdinalSortedStrings @($Reasons)
}

function Get-GitLoopyAutomationProjection {
    <#
        Project one Performer's authorization decision over a frozen frontier.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Request,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Actions,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Outcomes,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Diagnostics,
        [Parameter(Mandatory)][string]$Status,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Validators
    )

    $Automation = Test-GitLoopyAutomation $Request["automation"] "automation"

    # The frontier freezes at the first stable Reconciliation of the Run and is
    # replayed by the caller thereafter. Every in-coverage identity is frozen,
    # including Blocked, HITL-required, ineligible, and quarantined members --
    # otherwise a member that later became Ready would look like a newcomer.
    if ($null -eq $Automation["Frontier"]) {
        $Frozen = [Collections.Generic.List[object]]::new()
        foreach ($Action in $Actions) {
            if (
                (Get-GitLoopyActionRepositories $Action).IsSubsetOf(
                    $Automation["Repositories"]
                )
            ) {
                $Frozen.Add([ordered]@{
                    identity = [string]$Action["identity"]
                    semantic_fingerprint = [string]$Action["semantic_fingerprint"]
                })
            }
        }
        $Frozen = [object[]]@($Frozen)
    }
    else {
        $Frozen = [object[]]@($Automation["Frontier"])
    }
    $FrontierIndex = [Collections.Specialized.OrderedDictionary]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Entry in $Frozen) {
        $FrontierIndex[[string]$Entry["identity"]] =
            [string]$Entry["semantic_fingerprint"]
    }

    # Evidence names one *semantics*, not one identity forever: a Transition
    # owner that publishes a repaired occurrence moves the fingerprint, and the
    # evidence describing the broken one must stop holding the repaired one
    # down. The class rides along so the stop can name the right problem.
    $Quarantined = [Collections.Specialized.OrderedDictionary]::new(
        [StringComparer]::Ordinal
    )
    $GuidanceFault = $false
    foreach ($Diagnostic in $Diagnostics) {
        $Code = [string]$Diagnostic["code"]
        if ($Code -ceq "dispatch_evidence_quarantine") {
            foreach ($Identity in @($Diagnostic["identities"])) {
                $Quarantined[
                    (Get-GitLoopyPairKey `
                            -Kind ([string]$Identity) `
                            -Value ([string]$Diagnostic["semantic_fingerprint"]))
                ] = [string]$Diagnostic["class"]
            }
        }
        # A conflicted or unverifiable fragment never reaches `actions` at all,
        # so a guidance fault is observed from the diagnostics rather than from
        # any one Action. It must still stop the Run: the frontier it froze is
        # not a trustworthy description of the project.
        if (
            $Code -cin $Script:CoverageUncertaintyCodes -or
            $Code -cin @("action_conflict", "prerequisite_cycle")
        ) {
            $GuidanceFault = $true
        }
    }

    $Eligibility = [Collections.Generic.List[object]]::new()
    $ReportOnly = [Collections.Generic.List[object]]::new()
    $QuarantineStops = New-GitLoopyOrdinalSet
    $QuarantineIdentities = New-GitLoopyOrdinalSet
    $Selected = $null
    foreach ($Action in $Actions) {
        $Reasons = Get-GitLoopyAutomationIneligibility `
            -Action $Action `
            -Automation $Automation `
            -Frontier $FrontierIndex `
            -Quarantined $Quarantined
        $Identity = [string]$Action["identity"]
        $Fingerprint = [string]$Action["semantic_fingerprint"]
        $Entry = [ordered]@{
            identity = $Identity
            semantic_fingerprint = $Fingerprint
            automation_selectable = ($Reasons.Count -eq 0)
        }
        if ($Reasons.Count -gt 0) {
            $Entry["reasons"] = $Reasons
        }
        $Eligibility.Add($Entry)
        if ($Reasons -ccontains "outside-frontier") {
            $ReportOnly.Add([ordered]@{
                identity = $Identity
                semantic_fingerprint = $Fingerprint
                reason = if ($FrontierIndex.Contains($Identity)) {
                    "changed-semantics"
                }
                else {
                    "newly-produced"
                }
            })
        }
        if ($Reasons -ccontains "quarantined") {
            [void]$QuarantineStops.Add(
                [string]$Quarantined[
                    (Get-GitLoopyPairKey -Kind $Identity -Value $Fingerprint)
                ]
            )
            [void]$QuarantineIdentities.Add($Identity)
        }
        if ($Reasons.Count -eq 0 -and $null -eq $Selected) {
            $Selected = $Action
        }
    }

    $Projection = [ordered]@{
        performer = $Automation["PerformerId"]
        scope = [ordered]@{
            coverage = [ordered]@{
                repositories = Get-GitLoopyOrdinalSortedStrings `
                    @($Automation["Repositories"])
            }
            grants = Get-GitLoopySortedPairs `
                -Keys @($Automation["Grants"]) -SecondField "scope"
            denials = Get-GitLoopySortedPairs `
                -Keys @($Automation["Denials"]) -SecondField "scope"
            frozen = $true
        }
        frontier = [ordered]@{ actions = $Frozen }
        validators = $Validators
        eligibility = [object[]]@($Eligibility)
        report_only = [object[]]@($ReportOnly)
    }

    # A guidance fault is not a property of the selected Action -- the
    # conflicted or unverifiable fragment never reached `actions` at all. What
    # it makes untrustworthy is the coverage the Run froze, so no Action inside
    # that frozen description may be dispatched on the strength of it.
    if ($null -ne $Selected -and -not $GuidanceFault) {
        $SafetyCase = $Selected["safety_case"]
        $Effects = Get-GitLoopyScopeEntries $SafetyCase["effects"] "safety_case.effects"
        $Requirements = Get-GitLoopyRequirementEntries `
            $SafetyCase["requirements"] "safety_case.requirements"
        $Projection["authorization"] = [ordered]@{
            action_identity = [string]$Selected["identity"]
            semantic_fingerprint = [string]$Selected["semantic_fingerprint"]
            performer = $Automation["PerformerId"]
            workstream_anchor = $Selected["workstream_anchor"]
            target = $Selected["target"]
            safety_case_version = [string]$SafetyCase["version"]
            completion_condition = $Selected["completion_condition"]
            effects = Get-GitLoopySortedPairs -Keys @($Effects) -SecondField "scope"
            requirements = Get-GitLoopySortedPairs `
                -Keys @($Requirements) -SecondField "name"
            retry = $SafetyCase["retry"]
            triggers = $SafetyCase["triggers"]
        }
        return $Projection
    }

    $Projection["stop"] = Get-GitLoopyAutomationStop `
        -Actions $Actions `
        -Eligibility ([object[]]@($Eligibility)) `
        -ReportOnly ([object[]]@($ReportOnly)) `
        -Outcomes $Outcomes `
        -Status $Status `
        -Frontier $Frozen `
        -GuidanceFault $GuidanceFault `
        -QuarantineStops $QuarantineStops `
        -QuarantineIdentities $QuarantineIdentities
    return $Projection
}

function Get-GitLoopyAutomationStop {
    <#
        Explain, once and in typed terms, why nothing further can be selected.
    #>
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Actions,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Eligibility,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$ReportOnly,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Outcomes,
        [Parameter(Mandatory)][string]$Status,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Frontier,
        [Parameter(Mandatory)][bool]$GuidanceFault,
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [Collections.Generic.HashSet[string]]$QuarantineStops,
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [Collections.Generic.HashSet[string]]$QuarantineIdentities
    )

    $Observed = New-GitLoopyOrdinalSet
    foreach ($Entry in $Eligibility) {
        if (-not $Entry.Contains("reasons")) { continue }
        foreach ($Reason in @($Entry["reasons"])) {
            if ($Script:IneligibilityStopReasons.Contains([string]$Reason)) {
                [void]$Observed.Add(
                    [string]$Script:IneligibilityStopReasons[[string]$Reason]
                )
            }
        }
    }
    $Observed.UnionWith($QuarantineStops)
    if ($GuidanceFault) { [void]$Observed.Add("guidance-fault") }
    if ($Status -ceq "complete") { [void]$Observed.Add("workstreams-terminal") }
    if ($Frontier.Count -eq 0) { [void]$Observed.Add("frontier-drained") }

    $Reason = "frontier-drained"
    $Disposition = "expected-boundary"
    foreach ($Candidate in $Script:AutomationStopPrecedence) {
        if ($Observed.Contains($Candidate[0])) {
            $Reason = $Candidate[0]
            $Disposition = $Candidate[1]
            break
        }
    }

    $Decisive = [Collections.Generic.List[object]]::new()
    foreach ($Entry in $Eligibility) {
        if ($Reason -cin $Script:DispatchEvidenceClasses) {
            if ($QuarantineIdentities.Contains([string]$Entry["identity"])) {
                $Decisive.Add($Entry)
            }
            continue
        }
        if (-not $Entry.Contains("reasons")) { continue }
        foreach ($Item in @($Entry["reasons"])) {
            if (
                $Script:IneligibilityStopReasons.Contains([string]$Item) -and
                [string]$Script:IneligibilityStopReasons[[string]$Item] -ceq $Reason
            ) {
                $Decisive.Add($Entry)
                break
            }
        }
    }
    $DecisiveIdentities = New-GitLoopyOrdinalSet @(
        $Decisive | ForEach-Object { [string]$_["identity"] }
    )
    $Index = [Collections.Specialized.OrderedDictionary]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Action in $Actions) {
        $Index[[string]$Action["identity"]] = $Action
    }
    $Stop = [ordered]@{
        disposition = $Disposition
        reason = $Reason
        nonterminal_status = $Status
    }
    if ($Decisive.Count -gt 0) {
        $PrimaryIdentity = [string]$Decisive[0]["identity"]
        if ($Index.Contains($PrimaryIdentity)) {
            $Primary = $Index[$PrimaryIdentity]
            $Next = [ordered]@{
                kind = "action"
                identity = $PrimaryIdentity
                summary = $Primary["summary"]
                readiness = $Primary["readiness"]
            }
            if (
                $Primary.Contains("unsatisfied_prerequisites") -and
                @($Primary["unsatisfied_prerequisites"]).Count -gt 0
            ) {
                $Next["condition"] = @($Primary["unsatisfied_prerequisites"])[0]
            }
            $Stop["next"] = $Next
        }
    }
    $Evidence = [Collections.Generic.List[object]]::new()
    foreach ($Identity in (Get-GitLoopyOrdinalSortedStrings @($DecisiveIdentities))) {
        if ($Index.Contains($Identity)) {
            $Evidence.Add($Index[$Identity]["target"])
        }
    }
    $Stop["evidence"] = [object[]]@($Evidence)
    $Secondary = [Collections.Generic.List[object]]::new()
    foreach ($Entry in $Eligibility) {
        if (
            $Entry.Contains("reasons") -and
            -not $DecisiveIdentities.Contains([string]$Entry["identity"])
        ) {
            $Secondary.Add([ordered]@{
                identity = $Entry["identity"]
                reasons = $Entry["reasons"]
            })
        }
    }
    $Stop["secondary_barriers"] = [object[]]@($Secondary)
    $Stop["report_only_successors"] = $ReportOnly
    $Stop["outcomes"] = $Outcomes
    $Stop["successor_executed"] = $false
    $Stop["statement"] =
        "No successor Action was executed by this Reconciliation."
    return $Stop
}

function Test-GitLoopyDispatch {
    <#
        Validate one exceptional Dispatch-evidence record before it is written.

        Only two classes exist, and neither carries an Instruction: ordinary
        success and ordinary execution failure stay in the Runner's existing
        artifacts, Events, retry, and Strike paths. The record's shape is what
        keeps a runnable Instruction or a secret out of a durable comment --
        there is no field to put one in.
    #>
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Repository
    )

    $Dispatch = Assert-GitLoopyObject $Value $Name
    Assert-GitLoopyFields `
        -Value $Dispatch `
        -Name $Name `
        -Required @(
            "action_identity", "semantic_fingerprint", "performer", "carrier",
            "class", "summary", "evidence"
        ) `
        -Optional @("reason")
    foreach ($Field in @("action_identity", "semantic_fingerprint")) {
        $Digest = Assert-GitLoopyString $Dispatch[$Field] "$Name.$Field"
        if ($Digest -cnotmatch $Script:DigestPattern) {
            throw (New-GitLoopyRejection "$Name.$Field must be a sha256 digest")
        }
    }
    $null = Assert-GitLoopyString $Dispatch["performer"] "$Name.performer"
    $Summary = Assert-GitLoopyString $Dispatch["summary"] "$Name.summary"
    if ($Summary.Contains("`n") -or $Summary.Contains("`r")) {
        throw (New-GitLoopyRejection "$Name.summary must be one line")
    }
    $EvidenceClass = Assert-GitLoopyString $Dispatch["class"] "$Name.class"
    if ($EvidenceClass -cnotin $Script:DispatchEvidenceClasses) {
        throw (New-GitLoopyRejection "$Name.class is unsupported")
    }
    if ($EvidenceClass -ceq "safety-case-violation") {
        $Reason = Assert-GitLoopyString $Dispatch["reason"] "$Name.reason"
        if ($Reason -cnotin $Script:HumanBoundaryReasons) {
            throw (New-GitLoopyRejection "$Name.reason is unsupported")
        }
    }
    elseif ($Dispatch.Contains("reason")) {
        throw (New-GitLoopyRejection (
            "$Name.reason belongs only to a safety-case-violation"
        ))
    }
    $Carrier = Assert-GitLoopyDurableReference `
        -Value $Dispatch["carrier"] `
        -Name "$Name.carrier" `
        -Repository $Repository `
        -AllowedKinds @("issue")
    $Evidence = Assert-GitLoopyArray $Dispatch["evidence"] "$Name.evidence" -NonEmpty
    for ($Index = 0; $Index -lt $Evidence.Count; $Index++) {
        $null = Assert-GitLoopyDurableReference `
            -Value $Evidence[$Index] `
            -Name "$Name.evidence[$Index]" `
            -Repository $Repository
    }
    return [ordered]@{
        Dispatch = $Dispatch
        Carrier = $Carrier
    }
}

function New-GitLoopyDispatchEvidenceBody {
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    $Canonical = ConvertTo-GitLoopyCanonicalJson $Record
    return "$Script:DispatchMarker`n``````json`n$Canonical`n``````"
}

function Get-GitLoopyDispatchEvidenceFromComment {
    <#
        Read one durable Dispatch-evidence record, or `$null`.

        Dispatch evidence is deliberately *not* a Producer revision: it carries
        no lineage, creates and retires nothing, and is read only to
        quarantine. That is exactly why reading applies the *whole* closed
        schema the writer applied and the same Performer binding: a quarantine
        is a change of authority, and a fragment naming an identity is not
        enough to make one.
    #>
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Comment,
        [Parameter(Mandatory)][string]$Repository
    )

    $Body = $Comment["body"]
    if ($Body -isnot [string] -or -not $Body.Contains($Script:DispatchMarker)) {
        return $null
    }
    $Remainder = $Body.Substring(
        $Body.IndexOf($Script:DispatchMarker, [StringComparison]::Ordinal) +
        $Script:DispatchMarker.Length
    )
    $Opening = "``````json`n"
    $FenceStart = $Remainder.IndexOf($Opening, [StringComparison]::Ordinal)
    if ($FenceStart -lt 0) {
        return $null
    }
    $Fenced = $Remainder.Substring($FenceStart + $Opening.Length)
    $FenceEnd = $Fenced.IndexOf("`n``````", [StringComparison]::Ordinal)
    $Raw = if ($FenceEnd -lt 0) { $Fenced } else { $Fenced.Substring(0, $FenceEnd) }
    try {
        $Record = $Raw | ConvertFrom-Json -AsHashtable -DateKind String
    }
    catch {
        return $null
    }
    if ($Record -isnot [Collections.IDictionary]) {
        return $null
    }
    try {
        $Validated = Test-GitLoopyDispatch `
            -Value $Record `
            -Name "dispatch evidence" `
            -Repository $Repository
    }
    catch [GitLoopyContinuationRejection] {
        return $null
    }
    # Anyone with write access can leave a comment; only the Performer named in
    # the record can have performed the Dispatch it describes.
    if ($Validated.Dispatch["performer"] -cne [string]$Comment["author"]) {
        return $null
    }
    return $Validated.Dispatch
}

function Get-GitLoopyDispatchEvidenceDiagnostics {
    <#
        Quarantine the smallest justified scope named by Dispatch evidence.

        The record is immutable and non-Producer: it never retires the Action
        or creates a replacement. Only the Transition owner can do that, so
        until it does, the named semantics stay visible and stay unselectable.
    #>
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[object]]$Evidence
    )

    $Ordered = @(
        $Evidence | Sort-Object -Property { [long]$_["comment_id"] }
    )
    $Diagnostics = [Collections.Generic.List[object]]::new()
    foreach ($Entry in $Ordered) {
        $Record = $Entry["record"]
        $Diagnostics.Add([ordered]@{
            code = "dispatch_evidence_quarantine"
            comment_id = [long]$Entry["comment_id"]
            class = $Record["class"]
            identities = [object[]]@($Record["action_identity"])
            semantic_fingerprint = $Record["semantic_fingerprint"]
        })
    }
    return , [object[]]@($Diagnostics)
}

function Invoke-GitLoopyContinuationRecordDispatchResult {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

    Assert-GitLoopyFields `
        -Value $Request `
        -Name "request" `
        -Required @("repository", "trusted_producers", "dispatch") `
        -Optional @("trusted_apps")
    $Repository = Get-GitLoopyRepository $Request
    $null = Get-GitLoopyTrustedProducers -Request $Request
    $null = Get-GitLoopyTrustedApps -Request $Request
    $Validated = Test-GitLoopyDispatch `
        -Value $Request["dispatch"] `
        -Name "dispatch" `
        -Repository $Repository
    $Dispatch = $Validated.Dispatch
    $Carrier = $Validated.Carrier
    $Actor = Invoke-GitLoopyGitHub `
        -Arguments @("api", "user") `
        -Context "reading the authenticated GitHub actor"
    if (
        $Actor -isnot [Collections.IDictionary] -or
        $Actor["login"] -isnot [string] -or
        $Actor["type"] -isnot [string]
    ) {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding the authenticated GitHub actor"
        )
    }
    # Anyone with write access can leave a comment; only the Performer named in
    # the record can have performed the Dispatch it describes.
    if ($Actor["login"] -cne $Dispatch["performer"]) {
        throw (New-GitLoopyRejection (
            "dispatch.performer must be the authenticated actor writing the record"
        ))
    }
    $Record = [ordered]@{}
    foreach ($Key in (Get-GitLoopyOrdinalSortedStrings @($Dispatch.Keys))) {
        $Record[$Key] = $Dispatch[$Key]
    }
    $CanonicalRecord = ConvertTo-GitLoopyCanonicalJson $Record
    $Document = [Text.Json.JsonDocument]::Parse($CanonicalRecord)
    try {
        Test-GitLoopyPortablePhase $Document.RootElement "Dispatch evidence"
    }
    finally {
        $Document.Dispose()
    }
    $EvidenceId = Get-GitLoopySha256 $CanonicalRecord
    $Committed = Invoke-GitLoopyGitHub `
        -Arguments @(
            "api", "--method", "POST",
            "repos/$Repository/issues/$($Carrier["number"])/comments",
            "--input", "-"
        ) `
        -InputValue ([ordered]@{ body = (New-GitLoopyDispatchEvidenceBody $Record) }) `
        -Context "recording the Dispatch result"
    if ($Committed -isnot [Collections.IDictionary]) {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding the recorded Dispatch result"
        )
    }
    $CommentId = Get-GitLoopyCommentId $Committed
    $CommentUrl = if ($Committed.Contains("url")) {
        $Committed["url"]
    }
    else {
        $Committed["html_url"]
    }
    if ($null -eq $CommentId -or $CommentUrl -isnot [string]) {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding the recorded Dispatch result"
        )
    }
    return [ordered]@{
        ok = $true
        operation = "record-dispatch-result"
        receipt = [ordered]@{
            status = "committed"
            dispatch_evidence_id = $EvidenceId
            class = $Record["class"]
            action_identity = $Record["action_identity"]
            semantic_fingerprint = $Record["semantic_fingerprint"]
            carrier = $Carrier
            comment = [ordered]@{
                id = $CommentId
                url = $CommentUrl
            }
        }
    }
}

function Invoke-GitLoopyContinuationReconcileRevisionProtocol {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

    $Repository = Get-GitLoopyRepository $Request
    $Trusted = Get-GitLoopyTrustedProducers -Request $Request
    $TrustedApps = Get-GitLoopyTrustedApps -Request $Request
    $Carriers = Get-GitLoopyAllContinuationCarriers -Repository $Repository
    $Permissions = [ordered]@{}
    $Diagnostics = [Collections.Generic.List[object]]::new()
    $Entries = [Collections.Generic.List[object]]::new()
    $Indexed = [Collections.Generic.HashSet[long]]::new()
    $TrustedMarkerCarriers = [Collections.Generic.HashSet[long]]::new()
    $RecordCarriers = [Collections.Generic.HashSet[long]]::new()
    $DispatchEvidence = [Collections.Generic.List[object]]::new()
    foreach ($Carrier in $Carriers) {
        if (@($Carrier["labels"]) -ccontains $Script:IndexLabel) {
            [void]$Indexed.Add([long]$Carrier["number"])
        }
        foreach ($Comment in $Carrier["comments"]) {
            if (-not (Test-GitLoopyMarkedComment -Comment $Comment)) {
                continue
            }
            $Authorized = $false
            $Rejection = "untrusted_marker_ignored"
            if ($Comment["author_type"] -cin @("Bot", "App")) {
                $Authorized = @($TrustedApps) -ccontains $Comment["author"]
            }
            elseif (@($Trusted) -ccontains $Comment["author"]) {
                if (-not $Permissions.Contains($Comment["author"])) {
                    $Permission = Invoke-GitLoopyGitHub `
                        -Arguments @(
                            "api",
                            (
                                "repos/$Repository/collaborators/" +
                                "$($Comment["author"])/permission"
                            )
                        ) `
                        -Context "reading Producer repository permission"
                    if (
                        $Permission -isnot [Collections.IDictionary] -or
                        $Permission["permission"] -isnot [string]
                    ) {
                        throw [GitLoopyContinuationGitHubException]::new(
                            "decoding Producer repository permission"
                        )
                    }
                    $Permissions[$Comment["author"]] = (
                        [string]$Permission["permission"]
                    ).ToUpperInvariant()
                }
                if (
                    $Permissions[$Comment["author"]] -cin
                    $Script:WritePermissions
                ) {
                    $Authorized = $true
                }
                else {
                    $Rejection = "producer_permission_revoked"
                }
            }
            $HasMarker = ([string]$Comment["body"]).Contains(
                $Script:RecordMarker,
                [StringComparison]::Ordinal
            )
            if (-not $Authorized) {
                if ($HasMarker) {
                    $Diagnostics.Add([ordered]@{
                        code = $Rejection
                        carrier = [long]$Carrier["number"]
                        comment_id = [long]$Comment["id"]
                        author = [string]$Comment["author"]
                    })
                }
                continue
            }
            if ($HasMarker) {
                [void]$TrustedMarkerCarriers.Add([long]$Carrier["number"])
            }
            if (
                $null -ne $Comment["created_at"] -and
                $null -ne $Comment["updated_at"] -and
                $Comment["created_at"] -cne $Comment["updated_at"]
            ) {
                $Diagnostics.Add([ordered]@{
                    code = "mutated_revision"
                    carrier = [long]$Carrier["number"]
                    comment_id = [long]$Comment["id"]
                })
                continue
            }
            $Evidence = Get-GitLoopyDispatchEvidenceFromComment `
                -Comment $Comment `
                -Repository $Repository
            if ($null -ne $Evidence) {
                $DispatchEvidence.Add([ordered]@{
                    comment_id = [long]$Comment["id"]
                    record = $Evidence
                })
                continue
            }
            try {
                $Record = Read-GitLoopyRevisionRecord $Comment
                if ($null -eq $Record) {
                    continue
                }
                $Producer = Assert-GitLoopyObject $Record["producer"] "producer"
                if ($Producer["login"] -cne $Comment["author"]) {
                    throw (New-GitLoopyRejection (
                        "embedded Producer does not match authenticated comment author"
                    ))
                }
                $Completion = Get-GitLoopyRevisionCompletion $Record
                $null = Test-GitLoopyCompletion ([ordered]@{
                    repository = $Repository
                    trusted_producers = [object[]]@($Trusted)
                    trusted_apps = [object[]]@($TrustedApps)
                    completion = $Completion
                })
                $Parents = [object[]]@()
                if ($Record.Contains("parents")) {
                    $Parents = [object[]]@($Record["parents"])
                }
                if (
                    $Parents -isnot [Collections.IList] -or
                    $Parents -is [string]
                ) {
                    throw (New-GitLoopyRejection "revision parents are malformed")
                }
                $SeenParents = [Collections.Generic.HashSet[string]]::new(
                    [StringComparer]::Ordinal
                )
                foreach ($Parent in $Parents) {
                    if (
                        $Parent -isnot [string] -or
                        $Parent -cnotmatch $Script:DigestPattern
                    ) {
                        throw (New-GitLoopyRejection (
                            "revision parents are malformed"
                        ))
                    }
                    if (-not $SeenParents.Add($Parent)) {
                        throw (New-GitLoopyRejection (
                            "revision parents contain duplicates"
                        ))
                    }
                }
            }
            catch [GitLoopyContinuationRejection] {
                $AffectedHead = Get-GitLoopySha256 (
                    ConvertTo-GitLoopyCanonicalJson ([ordered]@{
                        carrier = [long]$Carrier["number"]
                        comment_id = [long]$Comment["id"]
                        kind = "invalid-producer-comment"
                    })
                )
                $Diagnostics.Add([ordered]@{
                    code = "invalid_revision"
                    carrier = [long]$Carrier["number"]
                    comment_id = [long]$Comment["id"]
                    affected_head = $AffectedHead
                    message = $_.Exception.Message
                })
                continue
            }
            $Entries.Add([ordered]@{
                carrier = $Carrier
                comment = $Comment
                record = $Record
            })
            [void]$RecordCarriers.Add([long]$Carrier["number"])
        }
    }
    foreach ($Number in @($RecordCarriers | Sort-Object)) {
        if (-not $Indexed.Contains($Number)) {
            $Diagnostics.Add([ordered]@{
                code = "index_label_missing"
                carrier = $Number
            })
        }
    }
    foreach ($Number in @($Indexed)) {
        if (-not $TrustedMarkerCarriers.Contains($Number)) {
            $Diagnostics.Add([ordered]@{
                code = "index_label_stale"
                carrier = $Number
            })
        }
    }

    $Lineages = [ordered]@{}
    foreach ($Entry in $Entries) {
        $Lineage = Get-GitLoopyLineageKey `
            -Carrier ([long]$Entry["carrier"]["number"]) `
            -Record $Entry["record"]
        if (-not $Lineages.Contains($Lineage)) {
            $Lineages[$Lineage] = [Collections.Generic.List[object]]::new()
        }
        $Lineages[$Lineage].Add($Entry)
    }
    $ObservedHeadEntries = [Collections.Generic.List[object]]::new()
    $GuidanceEntries = [Collections.Generic.List[object]]::new()
    $Retirements = [Collections.Generic.List[object]]::new()
    foreach ($LineageEntries in $Lineages.Values) {
        $ById = [ordered]@{}
        foreach ($Entry in $LineageEntries) {
            $ById[$Entry["record"]["revision_id"]] = $Entry
        }
        $Tainted = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Entry in $LineageEntries) {
            $Record = $Entry["record"]
            $Missing = [Collections.Generic.List[string]]::new()
            foreach ($Parent in (Get-GitLoopyRecordParents $Record)) {
                if (-not $ById.Contains($Parent)) {
                    $Missing.Add($Parent)
                }
            }
            if ($Missing.Count -gt 0) {
                [void]$Tainted.Add([string]$Record["revision_id"])
                $Diagnostics.Add([ordered]@{
                    code = "missing_predecessor"
                    comment_id = [long]$Entry["comment"]["id"]
                    revision_id = [string]$Record["revision_id"]
                    missing = @($Missing | Sort-Object)
                })
                continue
            }
            $MissingReceipts = Get-GitLoopyMissingRetirementReceipts `
                -Record $Record `
                -ById $ById
            if ($MissingReceipts.Count -gt 0) {
                [void]$Tainted.Add([string]$Record["revision_id"])
                $Split = Get-GitLoopyProvenRetirements -Record $Record -ById $ById
                foreach ($Receipt in $Split["unproven"]) {
                    $Diagnostics.Add((
                        New-GitLoopyInvalidReceiptDiagnostic `
                            -Comment $Entry["comment"] `
                            -Record $Record `
                            -Receipt $Receipt
                    ))
                }
                $Diagnostics.Add([ordered]@{
                    code = "missing_retirement_receipt"
                    comment_id = [long]$Entry["comment"]["id"]
                    revision_id = [string]$Record["revision_id"]
                    missing = $MissingReceipts
                })
            }
            $Resurrected = Get-GitLoopyResurrectedIdentities `
                -Record $Record `
                -ById $ById
            if ($Resurrected.Count -gt 0) {
                [void]$Tainted.Add([string]$Record["revision_id"])
                $Diagnostics.Add([ordered]@{
                    code = "retired_occurrence_resurrected"
                    comment_id = [long]$Entry["comment"]["id"]
                    revision_id = [string]$Record["revision_id"]
                    identities = $Resurrected
                })
            }
        }
        $Changed = $true
        while ($Changed) {
            $Changed = $false
            foreach ($Entry in $LineageEntries) {
                $RevisionId = [string]$Entry["record"]["revision_id"]
                if ($Tainted.Contains($RevisionId)) {
                    continue
                }
                foreach ($Parent in (Get-GitLoopyRecordParents $Entry["record"])) {
                    if ($Tainted.Contains($Parent)) {
                        [void]$Tainted.Add($RevisionId)
                        $Changed = $true
                        break
                    }
                }
            }
        }
        $Usable = [Collections.Generic.List[object]]::new()
        $Referenced = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Entry in $LineageEntries) {
            if (-not $Tainted.Contains($Entry["record"]["revision_id"])) {
                $Usable.Add($Entry)
                foreach ($Parent in (Get-GitLoopyRecordParents $Entry["record"])) {
                    [void]$Referenced.Add($Parent)
                }
            }
        }
        $Live = [Collections.Generic.List[object]]::new()
        foreach ($Entry in $Usable) {
            if (-not $Referenced.Contains($Entry["record"]["revision_id"])) {
                $Live.Add($Entry)
                $ObservedHeadEntries.Add($Entry)
            }
        }
        $Semantics = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Entry in $Live) {
            [void]$Semantics.Add(
                (Get-GitLoopyRevisionSemantics $Entry["record"])
            )
        }
        if ($Semantics.Count -gt 1) {
            $Diagnostics.Add([ordered]@{
                code = "revision_fork"
                carrier = [long]$Live[0]["carrier"]["number"]
                heads = @(
                    $Live |
                        ForEach-Object { $_["record"]["revision_id"] } |
                        Sort-Object
                )
            })
        }
        elseif ($Live.Count -gt 0) {
            $GuidanceEntries.Add(@(
                $Live |
                    Sort-Object -Property {
                        [string]$_["record"]["revision_id"]
                    }
            )[0])
        }
        $Projection = Get-GitLoopyRetirementProjection `
            -LiveEntries $Live `
            -ById $ById
        foreach ($Retirement in $Projection["retirements"]) {
            $Retirements.Add($Retirement)
        }
        foreach ($Diagnostic in $Projection["diagnostics"]) {
            $Diagnostics.Add($Diagnostic)
        }
    }
    $SortedRetirements = Get-GitLoopyOrdinalSortedBy $Retirements.ToArray() {
        param($Retirement)
        @(
            (ConvertTo-GitLoopyCanonicalJson $Retirement["workstream_anchor"]),
            [string]$Retirement["predecessor_revision_id"]
        )
    }
    $SortedHeadEntries = @(
        $ObservedHeadEntries |
            Sort-Object -Property `
                { [long]$_["carrier"]["number"] }, `
                { [string]$_["record"]["revision_id"] }
    )
    $Heads = @(
        foreach ($Entry in $SortedHeadEntries) {
            [ordered]@{
                carrier = [long]$Entry["carrier"]["number"]
                producer = [string]$Entry["record"]["producer"]["login"]
                revision_id = [string]$Entry["record"]["revision_id"]
                workstream_anchor = $Entry["record"]["workstream"]["anchor"]
            }
        }
    )
    $Validators = @(
        foreach ($Entry in @(
                $Entries |
                    Sort-Object -Property { [long]$_["comment"]["id"] }
            )) {
            [ordered]@{
                comment_id = [long]$Entry["comment"]["id"]
                sha256 = Get-GitLoopySha256 ([string]$Entry["comment"]["body"])
            }
        }
    )
    $Derived = Get-GitLoopyDerivedActions `
        -GuidanceEntries $GuidanceEntries `
        -Repository $Repository
    foreach ($Diagnostic in $Derived["diagnostics"]) {
        $Diagnostics.Add($Diagnostic)
    }
    $OrderedActions = [object[]]@($Derived["actions"])
    foreach ($Diagnostic in (
            Get-GitLoopyDispatchEvidenceDiagnostics -Evidence $DispatchEvidence
        )) {
        $Diagnostics.Add($Diagnostic)
    }
    $Outcomes = Get-GitLoopyWorkstreamOutcomes -GuidanceEntries $GuidanceEntries
    # A complete all-state read establishes closed coverage only when every
    # discovered lineage remains trustworthy. A malformed, incomplete, or forked
    # lineage cannot disappear from the terminal-completion proof.
    $ClosedCoverage = $true
    foreach ($Diagnostic in $Diagnostics) {
        if ($Diagnostic["code"] -cin $Script:CoverageUncertaintyCodes) {
            $ClosedCoverage = $false
        }
    }
    $Status = Get-GitLoopyGuidanceStatus `
        -GuidanceEntries $GuidanceEntries `
        -Actions $OrderedActions `
        -Outcomes $Outcomes `
        -ClosedCoverage $ClosedCoverage
    $Delta = $null
    if ($Request.Contains("previous_actions")) {
        $Delta = Get-GitLoopyActionsDelta `
            -Actions $OrderedActions `
            -PreviousActions (
                Test-GitLoopyPreviousActions `
                    $Request["previous_actions"] "previous_actions"
            )
    }
    if ($Request.Contains("handoff")) {
        $Handoff = Test-GitLoopyHandoff $Request["handoff"] "handoff"
        foreach ($Diagnostic in (
                Add-GitLoopyHandoffReference `
                    -Actions $OrderedActions `
                    -Handoff $Handoff
            )) {
            $Diagnostics.Add($Diagnostic)
        }
    }
    $ObservationSource = [ordered]@{
        repository = $Repository
        heads = $Heads
        validators = $Validators
    }
    $Result = [ordered]@{
        status = $Status
        observed = [ordered]@{
            repository = $Repository
            indexed_carriers = $Indexed.Count
            producer_revisions = $Entries.Count
        }
        actions = $OrderedActions
    }
    if ($Outcomes.Count -gt 0) {
        $Result["outcomes"] = $Outcomes
    }
    $Result["retirements"] = [object[]]@($SortedRetirements)
    if ($null -ne $Delta) {
        $Result["delta"] = $Delta
    }
    $Result["diagnostics"] = @($Diagnostics)
    $Result["observation"] = [ordered]@{
        heads = $Heads
        token = "sha256:" + (
            Get-GitLoopySha256 (
                ConvertTo-GitLoopyCanonicalJson $ObservationSource
            )
        )
        validators = $Validators
    }
    if ($Request.Contains("automation")) {
        $Result["automation"] = Get-GitLoopyAutomationProjection `
            -Request $Request `
            -Actions $OrderedActions `
            -Outcomes ([object[]]@($Outcomes)) `
            -Diagnostics ([object[]]@($Diagnostics)) `
            -Status $Status `
            -Validators ([object[]]@($Validators))
    }
    return [ordered]@{
        ok = $true
        operation = "reconcile"
        result = $Result
    }
}

function Invoke-GitLoopyContinuationReconcile {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

    $RevisionProtocol = $false
    if ($Request.Contains("revision_protocol")) {
        if ($Request["revision_protocol"] -isnot [bool]) {
            throw (New-GitLoopyRejection "revision_protocol must be a boolean")
        }
        $RevisionProtocol = [bool]$Request["revision_protocol"]
    }
    if ($RevisionProtocol) {
        return Invoke-GitLoopyContinuationReconcileRevisionProtocol $Request
    }

    $Repository = Get-GitLoopyRepository $Request
    $Trusted = Get-GitLoopyTrustedProducers -Request $Request
    $TrustedArray = @($Trusted)
    $SortedTrusted = [string[]]$TrustedArray
    [Array]::Sort($SortedTrusted, [StringComparer]::Ordinal)

    $Carriers = Invoke-GitLoopyGitHub `
        -Arguments @(
            "issue", "list", "--repo", $Repository, "--state", "all",
            "--label", $Script:IndexLabel, "--limit", "100",
            "--json", "number,state,url,comments"
        ) `
        -Context "discovering indexed carriers"
    if ($Carriers -isnot [Collections.IList]) {
        throw [GitLoopyContinuationGitHubException]::new("decoding indexed carriers")
    }

    $Diagnostics = [Collections.Generic.List[object]]::new()
    $GuidanceEntries = [Collections.Generic.List[object]]::new()
    $DispatchEvidence = [Collections.Generic.List[object]]::new()
    $RevisionCount = 0
    foreach ($Carrier in $Carriers) {
        if (
            $Carrier -isnot [Collections.IDictionary] -or
            $Carrier["comments"] -isnot [Collections.IList]
        ) {
            throw [GitLoopyContinuationGitHubException]::new("decoding indexed carriers")
        }
        foreach ($RawComment in $Carrier["comments"]) {
            if ($RawComment -isnot [Collections.IDictionary]) {
                continue
            }
            $Comment = ConvertTo-GitLoopyIndexedComment $RawComment
            if ($null -eq $Comment) {
                continue
            }
            $Evidence = Get-GitLoopyDispatchEvidenceFromComment `
                -Comment $Comment `
                -Repository $Repository
            if ($null -ne $Evidence) {
                $DispatchEvidence.Add([ordered]@{
                    comment_id = [long]$Comment["id"]
                    record = $Evidence
                })
                continue
            }
            $Author = [string]$Comment["author"]
            if ($Author -cnotin $TrustedArray) {
                continue
            }
            $Parsed = Get-GitLoopyRecordFromComment $Comment
            if ($null -eq $Parsed) {
                continue
            }
            $Record = $Parsed.Record
            $Completion = $Parsed.Completion
            $ProducerObject = Assert-GitLoopyObject $Record["producer"] "producer"
            if ($ProducerObject["login"] -cne $Author) {
                continue
            }
            $CompletionRequest = [ordered]@{
                repository = $Repository
                trusted_producers = $SortedTrusted
                completion = $Completion
            }
            $null = Test-GitLoopyCompletion $CompletionRequest
            $RevisionCount++
            $GuidanceEntries.Add([ordered]@{
                carrier = $Carrier
                comment = $Comment
                record = $Record
            })
        }
    }
    # Label-indexed discovery reaches the *same* Action derivation the revision
    # protocol reaches. The path decides which carriers are visible; it never
    # decides what an Action means. A private, narrower derivation here would
    # leave a Prerequisite unevaluated and -- worse -- let two disagreeing
    # Producers each contribute a healthy-looking Action that section 9 would
    # then authorize, where the shared derivation raises `action_conflict` and
    # the guidance fault refuses the Dispatch outright.
    $Derived = Get-GitLoopyDerivedActions `
        -GuidanceEntries $GuidanceEntries `
        -Repository $Repository
    $OrderedActions = [object[]]@($Derived["actions"])
    foreach ($Diagnostic in $Derived["diagnostics"]) {
        $Diagnostics.Add($Diagnostic)
    }
    foreach ($Diagnostic in (
            Get-GitLoopyDispatchEvidenceDiagnostics -Evidence $DispatchEvidence
        )) {
        $Diagnostics.Add($Diagnostic)
    }
    $Outcomes = Get-GitLoopyWorkstreamOutcomes -GuidanceEntries $GuidanceEntries
    # Label-indexed discovery is not a complete, paginated all-state read, so it
    # never has closed coverage and must never claim project-wide "complete"
    # even when every discovered lineage happens to be terminal.
    $Status = Get-GitLoopyGuidanceStatus `
        -GuidanceEntries $GuidanceEntries `
        -Actions $OrderedActions `
        -Outcomes $Outcomes `
        -ClosedCoverage $false
    $Delta = $null
    if ($Request.Contains("previous_actions")) {
        $Delta = Get-GitLoopyActionsDelta `
            -Actions $OrderedActions `
            -PreviousActions (
                Test-GitLoopyPreviousActions `
                    $Request["previous_actions"] "previous_actions"
            )
    }
    if ($Request.Contains("handoff")) {
        $Handoff = Test-GitLoopyHandoff $Request["handoff"] "handoff"
        foreach ($Diagnostic in (
                Add-GitLoopyHandoffReference `
                    -Actions $OrderedActions `
                    -Handoff $Handoff
            )) {
            $Diagnostics.Add($Diagnostic)
        }
    }
    # Retirement legitimacy is proven only against an immutable revision chain.
    # Label-indexed discovery is deliberately lineage-free (the atomic-root
    # capability subset), so it can neither prove nor project a Retirement. Say
    # so rather than silently dropping the receipts.
    #
    # The `retirements` key is still always emitted, so its absence never
    # carries meaning on any path. Empty here is not the claim "nothing was
    # retired" -- the diagnostic below is the sole discriminator, and it names
    # every revision whose receipts went unevaluated.
    $GatedRetirements = [Collections.Generic.List[string]]::new()
    foreach ($Entry in $GuidanceEntries) {
        if ((Get-GitLoopyRecordRetirements $Entry["record"]).Count -gt 0) {
            $GatedRetirements.Add([string]$Entry["record"]["revision_id"])
        }
    }
    if ($GatedRetirements.Count -gt 0) {
        $Diagnostics.Add([ordered]@{
            code = "retirements_require_revision_protocol"
            revision_ids = Get-GitLoopyOrdinalSortedStrings `
                $GatedRetirements.ToArray()
        })
    }
    $Result = [ordered]@{
        status = $Status
        observed = [ordered]@{
            repository = $Repository
            indexed_carriers = $Carriers.Count
            producer_revisions = $RevisionCount
        }
        actions = $OrderedActions
    }
    if ($Outcomes.Count -gt 0) {
        $Result["outcomes"] = $Outcomes
    }
    $Result["retirements"] = @()
    if ($null -ne $Delta) {
        $Result["delta"] = $Delta
    }
    $Result["diagnostics"] = @($Diagnostics)
    if ($Request.Contains("automation")) {
        $Validators = [Collections.Generic.List[object]]::new()
        foreach ($Entry in (
                $GuidanceEntries |
                    Sort-Object -Property { [long]$_["comment"]["id"] }
            )) {
            $Validators.Add([ordered]@{
                comment_id = [long]$Entry["comment"]["id"]
                sha256 = Get-GitLoopySha256 ([string]$Entry["comment"]["body"])
            })
        }
        $Result["automation"] = Get-GitLoopyAutomationProjection `
            -Request $Request `
            -Actions $OrderedActions `
            -Outcomes ([object[]]@($Outcomes)) `
            -Diagnostics ([object[]]@($Diagnostics)) `
            -Status $Status `
            -Validators ([object[]]@($Validators))
    }
    return [ordered]@{
        ok = $true
        operation = "reconcile"
        result = $Result
    }
}

function Invoke-GitLoopyContinuationRepairIndex {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

    Assert-GitLoopyFields `
        -Value $Request `
        -Name "request" `
        -Required @("repository", "trusted_producers") `
        -Optional @("trusted_apps")
    $Repository = Get-GitLoopyRepository $Request
    $Trusted = Get-GitLoopyTrustedProducers -Request $Request
    $TrustedApps = Get-GitLoopyTrustedApps -Request $Request
    Assert-GitLoopyAuthorizedPolicyActor `
        -Request $Request `
        -Repository $Repository
    $Carriers = Get-GitLoopyAllContinuationCarriers -Repository $Repository
    $Permissions = [ordered]@{}
    $Added = [Collections.Generic.List[long]]::new()
    $Removed = [Collections.Generic.List[long]]::new()
    foreach ($Carrier in $Carriers) {
        $HasRecord = $false
        $HasTrustedMarker = $false
        foreach ($Comment in $Carrier["comments"]) {
            # Repair only ever asks whether a carrier holds a trusted *record*,
            # so only the record marker earns a permission read here.
            if (
                -not ([string]$Comment["body"]).Contains(
                    $Script:RecordMarker,
                    [StringComparison]::Ordinal
                )
            ) {
                continue
            }
            $Authorized = $false
            if ($Comment["author_type"] -cin @("Bot", "App")) {
                $Authorized = @($TrustedApps) -ccontains $Comment["author"]
            }
            elseif (@($Trusted) -ccontains $Comment["author"]) {
                if (-not $Permissions.Contains($Comment["author"])) {
                    $Permission = Invoke-GitLoopyGitHub `
                        -Arguments @(
                            "api",
                            (
                                "repos/$Repository/collaborators/" +
                                "$($Comment["author"])/permission"
                            )
                        ) `
                        -Context "reading Producer repository permission"
                    if (
                        $Permission -isnot [Collections.IDictionary] -or
                        $Permission["permission"] -isnot [string]
                    ) {
                        throw [GitLoopyContinuationGitHubException]::new(
                            "decoding Producer repository permission"
                        )
                    }
                    $Permissions[$Comment["author"]] = (
                        [string]$Permission["permission"]
                    ).ToUpperInvariant()
                }
                $Authorized = (
                    $Permissions[$Comment["author"]] -cin
                    $Script:WritePermissions
                )
            }
            if (-not $Authorized) {
                continue
            }
            if (
                ([string]$Comment["body"]).Contains(
                    $Script:RecordMarker,
                    [StringComparison]::Ordinal
                )
            ) {
                $HasTrustedMarker = $true
            }
            try {
                $Record = Read-GitLoopyRevisionRecord $Comment
            }
            catch [GitLoopyContinuationRejection] {
                continue
            }
            if ($null -eq $Record) {
                continue
            }
            try {
                $Producer = Assert-GitLoopyObject $Record["producer"] "producer"
                if ($Producer["login"] -cne $Comment["author"]) {
                    continue
                }
                $AllTrusted = [object[]]@(
                    @($Trusted) + @($TrustedApps) | Sort-Object -Unique
                )
                $null = Test-GitLoopyCompletion ([ordered]@{
                    repository = $Repository
                    trusted_producers = $AllTrusted
                    completion = Get-GitLoopyRevisionCompletion $Record
                })
                $HasRecord = $true
            }
            catch [GitLoopyContinuationRejection] {
                continue
            }
        }
        $Indexed = @($Carrier["labels"]) -ccontains $Script:IndexLabel
        if ($HasRecord -and -not $Indexed) {
            $null = Invoke-GitLoopyGitHub `
                -Arguments @(
                    "label", "create", $Script:IndexLabel, "--repo", $Repository,
                    "--color", "5319E7", "--description",
                    "Repairable discovery index for git-loopy Continuation records",
                    "--force"
                ) `
                -Context "establishing the discovery index label" `
                -NoJson
            $null = Invoke-GitLoopyGitHub `
                -Arguments @(
                    "issue", "edit", [string]$Carrier["number"],
                    "--repo", $Repository, "--add-label", $Script:IndexLabel
                ) `
                -Context "indexing the Producer carrier" `
                -NoJson
            $Added.Add([long]$Carrier["number"])
        }
        elseif ($Indexed -and -not $HasTrustedMarker) {
            $null = Invoke-GitLoopyGitHub `
                -Arguments @(
                    "issue", "edit", [string]$Carrier["number"],
                    "--repo", $Repository, "--remove-label", $Script:IndexLabel
                ) `
                -Context "removing a stale Producer carrier index" `
                -NoJson
            $Removed.Add([long]$Carrier["number"])
        }
    }
    return [ordered]@{
        ok = $true
        operation = "repair-index"
        result = [ordered]@{
            status = "repaired"
            index_label = $Script:IndexLabel
            added = [object[]]@($Added | Sort-Object)
            removed = [object[]]@($Removed | Sort-Object)
        }
    }
}

function Get-GitLoopyAuthoritySet {
    <#
    .SYNOPSIS
        Validate an array as a de-duplicated string set, optionally against a
        closed vocabulary.
    #>
    [CmdletBinding()]
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name,
        [AllowNull()][string[]]$Vocabulary = $null
    )
    $Items = Assert-GitLoopyArray $Value $Name
    $Entries = [Collections.Generic.List[string]]::new()
    $Seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($Item in $Items) {
        $Entry = Assert-GitLoopyString $Item "$Name item"
        $Entries.Add($Entry)
        [void]$Seen.Add($Entry)
    }
    if ($Seen.Count -ne $Entries.Count) {
        throw (New-GitLoopyRejection "$Name must not contain duplicates")
    }
    if ($null -ne $Vocabulary) {
        $Allowed = [Collections.Generic.HashSet[string]]::new(
            [string[]]$Vocabulary, [StringComparer]::Ordinal
        )
        foreach ($Entry in $Entries) {
            if (-not $Allowed.Contains($Entry)) {
                throw (New-GitLoopyRejection "$Name item is unsupported")
            }
        }
    }
    return , [Collections.Generic.HashSet[string]]::new(
        [string[]]@($Entries), [StringComparer]::Ordinal
    )
}

function Get-GitLoopyAuthorityCeilings {
    <#
    .SYNOPSIS
        Validate the five positive ceilings, each against its own vocabulary.
    #>
    [CmdletBinding()]
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name
    )
    $Ceilings = Assert-GitLoopyObject $Value $Name
    Assert-GitLoopyFields `
        -Value $Ceilings `
        -Name $Name `
        -Required @($Script:CeilingVocabularies.Keys)
    $Resolved = [ordered]@{}
    foreach ($Axis in $Script:CeilingVocabularies.Keys) {
        $Vocabulary = $Script:CeilingVocabularies[$Axis]
        $Resolved[$Axis] = Get-GitLoopyAuthoritySet `
            $Ceilings[$Axis] "$Name.$Axis" $Vocabulary
    }
    return $Resolved
}

function Get-GitLoopyAuthoritySource {
    <#
    .SYNOPSIS
        Validate one operator-declared configuration source.
    #>
    [CmdletBinding()]
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name
    )
    $Source = Assert-GitLoopyObject $Value $Name
    Assert-GitLoopyFields `
        -Value $Source `
        -Name $Name `
        -Required @("source", "mode", "trusted_producers", "ceilings") `
        -Optional @("actor", "maintainers")
    $Scope = Assert-GitLoopyString $Source["source"] "$Name.source"
    if ($Scope -cnotin $Script:AuthoritySources) {
        throw (New-GitLoopyRejection "$Name.source is unsupported")
    }
    $Mode = Assert-GitLoopyString $Source["mode"] "$Name.mode"
    if (-not $Script:ModeRank.Contains($Mode)) {
        throw (New-GitLoopyRejection "$Name.mode is unsupported")
    }
    $Maintainers = [object[]]@()
    if ($Source.Contains("maintainers")) {
        $Maintainers = $Source["maintainers"]
    }
    $Resolved = [ordered]@{
        source = $Scope
        mode = $Mode
        trusted_producers = Get-GitLoopyAuthoritySet `
            $Source["trusted_producers"] "$Name.trusted_producers" $null
        ceilings = Get-GitLoopyAuthorityCeilings $Source["ceilings"] "$Name.ceilings"
        maintainers = Get-GitLoopyAuthoritySet $Maintainers "$Name.maintainers" $null
    }
    $Resolved["actor"] = if ($Source.Contains("actor")) {
        Assert-GitLoopyString $Source["actor"] "$Name.actor"
    }
    else {
        $null
    }
    return $Resolved
}

function Get-GitLoopyPersistedAuthority {
    <#
    .SYNOPSIS
        Validate one previously resolved authority, in the shape this command
        emits.
    #>
    [CmdletBinding()]
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name
    )
    $Prior = Assert-GitLoopyObject $Value $Name
    Assert-GitLoopyFields `
        -Value $Prior `
        -Name $Name `
        -Required @("mode", "trusted_producers", "ceilings") `
        -Optional @("actor", "maintainers")
    $Mode = Assert-GitLoopyString $Prior["mode"] "$Name.mode"
    if (-not $Script:ModeRank.Contains($Mode)) {
        throw (New-GitLoopyRejection "$Name.mode is unsupported")
    }
    $Actor = $null
    if ($Prior.Contains("actor") -and $null -ne $Prior["actor"]) {
        $Actor = Assert-GitLoopyString $Prior["actor"] "$Name.actor"
    }
    $Maintainers = [object[]]@()
    if ($Prior.Contains("maintainers")) {
        $Maintainers = $Prior["maintainers"]
    }
    return [ordered]@{
        source = $Name
        mode = $Mode
        trusted_producers = Get-GitLoopyAuthoritySet `
            $Prior["trusted_producers"] "$Name.trusted_producers" $null
        maintainers = Get-GitLoopyAuthoritySet $Maintainers "$Name.maintainers" $null
        ceilings = Get-GitLoopyAuthorityCeilings $Prior["ceilings"] "$Name.ceilings"
        actor = $Actor
    }
}

function Assert-GitLoopyModeSupported {
    <#
    .SYNOPSIS
        Refuse a mode this distribution's own manifest does not advertise.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Mode,
        [Parameter(Mandatory)][string]$TrackerAdapter
    )
    $Manifest = $Script:CapabilityManifest
    $Adapters = $Manifest["tracker_adapters"]
    if (-not $Adapters.Contains($TrackerAdapter)) {
        throw [GitLoopyContinuationCapabilityUnsupported]::new(
            "tracker adapter $TrackerAdapter is not supported by this distribution"
        )
    }
    if ($Manifest["continuation_modes"][$Mode] -ne $true) {
        throw [GitLoopyContinuationCapabilityUnsupported]::new(
            "continuation mode $Mode is not supported by this distribution"
        )
    }
    # Report mode is read-only Reconciliation, so the Adapter must be able to
    # reconcile; a mode advertised over an Adapter that cannot read records would
    # fail during the Run instead of before it.
    $Advertised = [Collections.Generic.HashSet[string]]::new(
        [string[]]@($Adapters[$TrackerAdapter]["operations"]), [StringComparer]::Ordinal
    )
    $Missing = [Collections.Generic.List[string]]::new()
    foreach ($Operation in @($Script:ModeRequiredOperations[$Mode])) {
        if (-not $Advertised.Contains($Operation)) {
            $Missing.Add($Operation)
        }
    }
    if ($Missing.Count -gt 0) {
        throw [GitLoopyContinuationCapabilityUnsupported]::new(
            "continuation mode $Mode requires the $TrackerAdapter adapter " +
            "operation $(Get-GitLoopyFirstOrdinal $Missing.ToArray())"
        )
    }
}

function Merge-GitLoopyAuthority {
    <#
    .SYNOPSIS
        Intersect one authority with another. Nothing here may widen.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$State,
        [Parameter(Mandatory)][Collections.IDictionary]$Other,
        [Parameter(Mandatory)][string]$Reason,
        [Collections.Generic.HashSet[string]]$Narrowed
    )
    if ($Script:ModeRank[[string]$Other["mode"]] -lt $Script:ModeRank[[string]$State["mode"]]) {
        $State["mode"] = $Other["mode"]
        [void]$Narrowed.Add("mode|$Reason")
    }
    $Producers = $State["trusted_producers"]
    $OtherProducers = $Other["trusted_producers"]
    $NarrowedProducers = [Collections.Generic.HashSet[string]]::new(
        $Producers, [StringComparer]::Ordinal
    )
    $NarrowedProducers.IntersectWith($OtherProducers)
    if ($NarrowedProducers.Count -ne $Producers.Count) {
        [void]$Narrowed.Add("trusted_producers|$Reason")
    }
    $State["trusted_producers"] = $NarrowedProducers

    $Maintainers = $State["maintainers"]
    $OtherMaintainers = $Other["maintainers"]
    $NarrowedMaintainers = [Collections.Generic.HashSet[string]]::new(
        $Maintainers, [StringComparer]::Ordinal
    )
    $NarrowedMaintainers.IntersectWith($OtherMaintainers)
    if ($NarrowedMaintainers.Count -ne $Maintainers.Count) {
        [void]$Narrowed.Add("maintainers|$Reason")
    }
    $State["maintainers"] = $NarrowedMaintainers

    $Ceilings = $State["ceilings"]
    $OtherCeilings = $Other["ceilings"]
    foreach ($Axis in @($Ceilings.Keys)) {
        $Values = $Ceilings[$Axis]
        $NarrowedValues = [Collections.Generic.HashSet[string]]::new(
            $Values, [StringComparer]::Ordinal
        )
        $NarrowedValues.IntersectWith($OtherCeilings[$Axis])
        if ($NarrowedValues.Count -ne $Values.Count) {
            [void]$Narrowed.Add("$Axis|$Reason")
        }
        $Ceilings[$Axis] = $NarrowedValues
    }

    if ($null -ne $Other["actor"]) {
        if ($null -ne $State["actor"] -and $State["actor"] -cne $Other["actor"]) {
            throw (New-GitLoopyRejection (
                "actor is declared twice with different identities"
            ))
        }
        $State["actor"] = $Other["actor"]
    }
}

function Resolve-GitLoopyContinuationAuthority {
    <#
    .SYNOPSIS
        Narrow the operator's configuration sources into one effective authority.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

    Assert-GitLoopyFields `
        -Value $Request `
        -Name "request" `
        -Required @("sources") `
        -Optional @(
            "continuation_contract_version", "record_format", "tracker_adapter",
            "prior"
        )
    $Declared = Assert-GitLoopyArray $Request["sources"] "sources" -NonEmpty
    $Seen = [Collections.Generic.List[string]]::new()
    $Resolved = [Collections.Generic.List[Collections.IDictionary]]::new()
    for ($Index = 0; $Index -lt $Declared.Count; $Index++) {
        $Source = Get-GitLoopyAuthoritySource $Declared[$Index] "sources[$Index]"
        $Scope = [string]$Source["source"]
        if ($Seen.Contains($Scope)) {
            throw (New-GitLoopyRejection "sources[$Index].source is declared twice")
        }
        # The narrowing order is the contract, so an out-of-order source is a
        # malformed request rather than something to sort quietly: a caller that
        # believes runtime narrows project is wrong about the result either way.
        if (
            $Seen.Count -gt 0 -and
            [array]::IndexOf($Script:AuthoritySources, $Scope) -lt
                [array]::IndexOf($Script:AuthoritySources, $Seen[$Seen.Count - 1])
        ) {
            throw (New-GitLoopyRejection "sources[$Index].source is out of order")
        }
        $Seen.Add($Scope)
        $Resolved.Add($Source)
    }

    $Narrowed = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $State = [ordered]@{
        mode = $Resolved[0]["mode"]
        trusted_producers = $Resolved[0]["trusted_producers"]
        maintainers = $Resolved[0]["maintainers"]
        ceilings = [ordered]@{}
        actor = $Resolved[0]["actor"]
    }
    foreach ($Axis in @($Resolved[0]["ceilings"].Keys)) {
        $State["ceilings"][$Axis] = $Resolved[0]["ceilings"][$Axis]
    }
    $DeclaredMode = [string]$State["mode"]

    for ($Index = 1; $Index -lt $Resolved.Count; $Index++) {
        $Source = $Resolved[$Index]
        if ($Script:ModeRank[[string]$Source["mode"]] -gt $Script:ModeRank[$DeclaredMode]) {
            $DeclaredMode = [string]$Source["mode"]
        }
        Merge-GitLoopyAuthority `
            -State $State -Other $Source -Reason "source-ceiling" -Narrowed $Narrowed
    }

    # Persisted authority is narrowed against, never replaced. Without this, an
    # operator who widened a config file after a runtime revocation would hand the
    # next Run back the authority the revocation took away.
    if ($Request.Contains("prior")) {
        $Prior = Get-GitLoopyPersistedAuthority $Request["prior"] "prior"
        Merge-GitLoopyAuthority `
            -State $State -Other $Prior -Reason "persisted-authority" -Narrowed $Narrowed
    }

    # A mode is only as real as the ceilings carrying it. With no repository in
    # coverage, or no Producer whose records may be trusted, a `report` Run would
    # render an authoritative empty projection over a project full of work.
    if ([string]$State["mode"] -cne "off") {
        if ($State["ceilings"]["repositories"].Count -eq 0) {
            $State["mode"] = "off"
            [void]$Narrowed.Add("repositories|coverage-empty")
        }
        if ($State["trusted_producers"].Count -eq 0) {
            $State["mode"] = "off"
            [void]$Narrowed.Add("trusted_producers|trusted-producers-empty")
        }
    }

    $TrackerAdapter = "github"
    if ($Request.Contains("tracker_adapter")) {
        $TrackerAdapter = Assert-GitLoopyString $Request["tracker_adapter"] "tracker_adapter"
    }
    # `off` is the claim that Continuation does not participate, so it is the one
    # mode a distribution never has to be able to serve. Everything above it is
    # checked against the advertised manifest before the caller is told it holds.
    if ([string]$State["mode"] -cne "off") {
        Assert-GitLoopyModeSupported -Mode ([string]$State["mode"]) -TrackerAdapter $TrackerAdapter
    }

    $CeilingsOut = [ordered]@{}
    foreach ($Axis in @($State["ceilings"].Keys)) {
        $CeilingsOut[$Axis] = [object[]]@(
            $State["ceilings"][$Axis] | Sort-Object -CaseSensitive
        )
    }

    $NarrowedOut = [Collections.Generic.List[Collections.IDictionary]]::new()
    foreach ($Entry in $Narrowed) {
        $Parts = $Entry.Split("|", 2)
        $NarrowedOut.Add([ordered]@{ axis = $Parts[0]; reason = $Parts[1] })
    }
    $NarrowedSorted = @(
        $NarrowedOut | Sort-Object -CaseSensitive -Property `
            @{Expression = { $_["axis"] } }, @{Expression = { $_["reason"] } }
    )

    return [ordered]@{
        ok = $true
        operation = "resolve-authority"
        result = [ordered]@{
            mode = [string]$State["mode"]
            declared_mode = $DeclaredMode
            participates = ([string]$State["mode"] -cne "off")
            tracker_adapter = $TrackerAdapter
            actor = $State["actor"]
            maintainers = [object[]]@($State["maintainers"] | Sort-Object -CaseSensitive)
            trusted_producers = [object[]]@(
                $State["trusted_producers"] | Sort-Object -CaseSensitive
            )
            ceilings = $CeilingsOut
            narrowed = [object[]]@($NarrowedSorted)
        }
    }
}

function Invoke-GitLoopyContinuationMain {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$Arguments
    )

    if ($Arguments.Count -eq 0) {
        [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
        return 2
    }
    $Operation = $Arguments[0]
    if ($Operation -ceq "capabilities") {
        if ($Arguments.Count -ne 1) {
            [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
            return 2
        }
        $Capabilities = Get-GitLoopyCapabilityManifest
        Write-GitLoopyContinuationJson ([ordered]@{
            ok = $true
            capabilities = $Capabilities
        })
        return 0
    }

    $SupportedSurface = @(
        "resolve-authority", "publish", "reconcile", "record-dispatch-result",
        "repair-index"
    )
    if ($Operation -cnotin $SupportedSurface) {
        [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
        return 2
    }

    $InputPath = $null
    $Terminal = $false
    for ($Index = 1; $Index -lt $Arguments.Count; $Index++) {
        $Argument = $Arguments[$Index]
        if ($Argument -ceq "--input") {
            $Index++
            if (
                $Index -ge $Arguments.Count -or
                $Arguments[$Index].StartsWith("-", [StringComparison]::Ordinal) -or
                $null -ne $InputPath
            ) {
                [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
                return 2
            }
            $InputPath = $Arguments[$Index]
            continue
        }
        if ($Argument.StartsWith("--input=", [StringComparison]::Ordinal)) {
            $Value = $Argument.Substring("--input=".Length)
            if ([string]::IsNullOrEmpty($Value) -or $null -ne $InputPath) {
                [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
                return 2
            }
            $InputPath = $Value
            continue
        }
        if ($Argument -ceq "--terminal") {
            if ($Operation -cne "reconcile" -or $Terminal) {
                [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
                return 2
            }
            $Terminal = $true
            continue
        }
        [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
        return 2
    }

    if ($Terminal -and $Operation -cne "reconcile") {
        [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
        return 2
    }

    try {
        $Request = Read-GitLoopyContinuationRequest -InputPath $InputPath
        if ($Operation -ceq "publish") {
            $Result = Invoke-GitLoopyContinuationPublish $Request
        }
        elseif ($Operation -ceq "resolve-authority") {
            $Result = Resolve-GitLoopyContinuationAuthority $Request
        }
        elseif ($Operation -ceq "reconcile") {
            $Result = Invoke-GitLoopyContinuationReconcile $Request
        }
        elseif ($Operation -ceq "repair-index") {
            $Result = Invoke-GitLoopyContinuationRepairIndex $Request
        }
        elseif ($Operation -ceq "record-dispatch-result") {
            $Result = Invoke-GitLoopyContinuationRecordDispatchResult $Request
        }
        else {
            $Result = $null
        }
    }
    catch [GitLoopyContinuationRepairRequired] {
        return Write-GitLoopyContinuationError `
            -Operation $Operation `
            -Code "repair_required" `
            -Message $_.Exception.Message
    }
    catch [GitLoopyContinuationCapabilityUnsupported] {
        return Write-GitLoopyContinuationError `
            -Operation $Operation `
            -Code "unsupported_operation" `
            -Message $_.Exception.Message
    }
    catch [GitLoopyContinuationGitHubException] {
        return Write-GitLoopyContinuationError `
            -Operation $Operation `
            -Code "github_error" `
            -Message $_.Exception.Message
    }
    catch [GitLoopyContinuationRejection] {
        return Write-GitLoopyContinuationError `
            -Operation $Operation `
            -Code "invalid_request" `
            -Message $_.Exception.Message
    }

    if ($null -ne $Result) {
        if ($Terminal) {
            [Console]::Out.Write(
                (Get-GitLoopyTerminalRendering -Result $Result["result"])
            )
            return 0
        }
        Write-GitLoopyContinuationJson $Result
        return 0
    }
    return Write-GitLoopyContinuationError `
        -Operation $Operation `
        -Code "unsupported_operation" `
        -Message "$Operation is not supported by this distribution"
}

Export-ModuleMember -Function @(
    "Get-GitLoopyContinuationUsage",
    "Invoke-GitLoopyContinuationMain",
    "Get-GitLoopyCapabilityManifest",
    "Get-GitLoopyContinuationProfile",
    "Get-GitLoopyContinuationVerification",
    "New-GitLoopyFrontierPlan",
    "Test-GitLoopyDistributionCapabilities"
)
