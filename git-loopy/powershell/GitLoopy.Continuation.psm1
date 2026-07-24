Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

class GitLoopyContinuationRejection : System.Exception {
    GitLoopyContinuationRejection([string]$Message) : base($Message) {}
}

class GitLoopyContinuationRepairRequired : System.Exception {
    GitLoopyContinuationRepairRequired([string]$Message) : base($Message) {}
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

$Script:ContinuationContractVersion = "1.0"
$Script:RecordFormat = 1
$Script:WrapperContractVersion = "1.3"
$Script:EventSchemaVersion = "1.1"

$Script:IndexLabel = "git-loopy-continuation"
$Script:RecordMarker = "<!-- git-loopy-continuation:1 -->"
$Script:MaxInteger = [System.Numerics.BigInteger]::Pow(2, 53) - 1
$Script:MaxDepth = 16
$Script:MaxArrayLength = 256
$Script:MaxStringBytes = 8 * 1024
$Script:MaxRecordBytes = 48 * 1024
$Script:MaxCarrierBodyBytes = 64 * 1024
$Script:DigestPattern = "^[0-9a-f]{64}$"
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
$Script:NoGuidanceReasons = @("no-successor-created", "ephemeral-only")

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
$Script:ShaPattern = "^[0-9a-f]{40}$"

$Script:CapabilityManifest = [ordered]@{
    continuation_contract_versions = @($Script:ContinuationContractVersion)
    record_formats = @($Script:RecordFormat)
    wrapper_contract_version = $Script:WrapperContractVersion
    event_schema_version = $Script:EventSchemaVersion
    tracker_adapters = [ordered]@{
        github = [ordered]@{
            operations = @("publish", "reconcile", "repair-index")
        }
    }
    operations = [ordered]@{
        capabilities = $true
        publish = $true
        reconcile = $true
        "record-dispatch-result" = $false
        "repair-index" = $true
    }
    instruction_handlers = @()
    instruction_modes = @()
    evaluators = @()
    effect_scopes = @()
    optional_capabilities = [ordered]@{
        immutable_producer_revisions = $true
        terminal_rendering = $false
        concurrent_dispatch = $false
    }
    continuation_modes = [ordered]@{
        default = "off"
        off = $true
        report = $false
        "execute-frontier" = $false
    }
}

function Get-GitLoopyContinuationUsage {
    [CmdletBinding()]
    param()
    return @"
Usage: git-loopy.ps1 continuation <operation> [options]

Operations:
  capabilities
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

function Test-GitLoopyAction {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][string]$TransitionOwner
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
            "context_references", "effects", "requirements", "triggers",
            "advisory_extensions"
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
    return [ordered]@{
        Action = $Action
        LocalReferences = $LocalReferences
    }
}

function Test-GitLoopyCompletion {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

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
    Assert-GitLoopyFields `
        -Value $Completion `
        -Name "completion" `
        -Required @(
            "continuation_contract_version", "record_format", "publication",
            "disposition", "workstream", "transition", "producer"
        ) `
        -Optional @("carrier", "actions", "outcome", "no_guidance", "advisory_extensions")
    if ($Completion["continuation_contract_version"] -cne $Script:ContinuationContractVersion) {
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
                -TransitionOwner $TransitionOwner
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
    foreach ($Argument in $Arguments) {
        $StartInfo.ArgumentList.Add($Argument)
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
        foreach ($Parent in @($Records[$RevisionId]["parents"])) {
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
            foreach ($Parent in @($Records[$RevisionId]["parents"])) {
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
        foreach ($Parent in @($Records[$RevisionId]["parents"])) {
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
            foreach ($Parent in @($Entry["record"]["parents"])) {
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
    $Completion = [ordered]@{}
    foreach ($Entry in $Record.GetEnumerator()) {
        if (
            $Entry.Key -cne "revision_id" -and
            $Entry.Key -cne "semantic_fingerprints"
        ) {
            $Completion[$Entry.Key] = $Entry.Value
        }
    }
    $ExpectedRevision = Get-GitLoopySha256 (
        ConvertTo-GitLoopyCanonicalJson $Completion
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

function Get-GitLoopyEvaluatedFragment {
    param(
        [Parameter(Mandatory)][Collections.IDictionary]$Record,
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][Collections.IDictionary]$FactCache
    )

    $ActionsByKey = [ordered]@{}
    foreach ($Action in @($Record["actions"])) {
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
    foreach ($Action in @($Record["actions"])) {
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
        if ($Contributed.Count -gt 1) {
            $Item["provenance"] =
                Get-GitLoopyUnionProvenance -Contributions $Contributed
        }
        if (@($Canonical["unsatisfied"]).Count -gt 0) {
            $Item["unsatisfied_prerequisites"] = $Canonical["unsatisfied"]
        }
        $Actions.Add($Item)
    }
    return [ordered]@{
        actions = [object[]]@(
            $Actions | Sort-Object -Property { [string]$_["identity"] }
        )
        diagnostics = $Diagnostics
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
    foreach ($Carrier in $Carriers) {
        if (@($Carrier["labels"]) -ccontains $Script:IndexLabel) {
            [void]$Indexed.Add([long]$Carrier["number"])
        }
        foreach ($Comment in $Carrier["comments"]) {
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
    foreach ($LineageEntries in $Lineages.Values) {
        $ById = [ordered]@{}
        foreach ($Entry in $LineageEntries) {
            $ById[$Entry["record"]["revision_id"]] = $Entry
        }
        $Tainted = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Entry in $LineageEntries) {
            $Missing = [Collections.Generic.List[string]]::new()
            foreach ($Parent in @($Entry["record"]["parents"])) {
                if (-not $ById.Contains($Parent)) {
                    $Missing.Add($Parent)
                }
            }
            if ($Missing.Count -gt 0) {
                [void]$Tainted.Add([string]$Entry["record"]["revision_id"])
                $Diagnostics.Add([ordered]@{
                    code = "missing_predecessor"
                    comment_id = [long]$Entry["comment"]["id"]
                    revision_id = [string]$Entry["record"]["revision_id"]
                    missing = @($Missing | Sort-Object)
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
                foreach ($Parent in @($Entry["record"]["parents"])) {
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
                foreach ($Parent in @($Entry["record"]["parents"])) {
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
    $ObservationSource = [ordered]@{
        repository = $Repository
        heads = $Heads
        validators = $Validators
    }
    return [ordered]@{
        ok = $true
        operation = "reconcile"
        result = [ordered]@{
            status = if ($OrderedActions.Count -gt 0) {
                "guidance"
            }
            else {
                "waiting"
            }
            observed = [ordered]@{
                repository = $Repository
                indexed_carriers = $Indexed.Count
                producer_revisions = $Entries.Count
            }
            actions = $OrderedActions
            diagnostics = @($Diagnostics)
            observation = [ordered]@{
                heads = $Heads
                token = "sha256:" + (
                    Get-GitLoopySha256 (
                        ConvertTo-GitLoopyCanonicalJson $ObservationSource
                    )
                )
                validators = $Validators
            }
        }
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

    $Actions = [Collections.Generic.List[object]]::new()
    $Diagnostics = [Collections.Generic.List[object]]::new()
    $RevisionCount = 0
    foreach ($Carrier in $Carriers) {
        if (
            $Carrier -isnot [Collections.IDictionary] -or
            $Carrier["comments"] -isnot [Collections.IList]
        ) {
            throw [GitLoopyContinuationGitHubException]::new("decoding indexed carriers")
        }
        foreach ($Comment in $Carrier["comments"]) {
            if (
                $Comment -isnot [Collections.IDictionary] -or
                $Comment["author"] -isnot [Collections.IDictionary]
            ) {
                continue
            }
            $Author = [string]$Comment["author"]["login"]
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

            if (-not $Record.Contains("actions")) {
                continue
            }
            foreach ($Action in $Record["actions"]) {
                $CompletionCondition = $Action["completion_condition"]
                $Prerequisites = $Action["prerequisites"]
                if (
                    $Action["target"]["kind"] -cne "issue" -or
                    ($Prerequisites -is [Collections.IList] -and $Prerequisites.Count -gt 0) -or
                    $CompletionCondition["kind"] -cne "issue-closed" -or
                    $CompletionCondition["target"]["kind"] -cne "issue"
                ) {
                    $Diagnostics.Add([ordered]@{
                        code = "unsupported_reconciliation_semantics"
                        revision_id = $Record["revision_id"]
                        action_key = $Action["key"]
                    })
                    continue
                }
                $Target = Invoke-GitLoopyGitHub `
                    -Arguments @(
                        "issue", "view", [string]$Action["target"]["number"],
                        "--repo", $Repository, "--json", "number,state,url"
                    ) `
                    -Context "reading an Action Target"
                if (
                    $Target -isnot [Collections.IDictionary] -or
                    $Target["state"] -isnot [string]
                ) {
                    throw [GitLoopyContinuationGitHubException]::new("decoding an Action Target")
                }
                if ($Target["state"] -cne "OPEN") {
                    continue
                }

                $IdentitySource = [ordered]@{
                    anchor = $Record["workstream"]["anchor"]
                    kind = $Action["kind"]
                    target = $Action["target"]
                    occurrence = $Action["occurrence"]
                }
                $Identity = Get-GitLoopySha256 (
                    ConvertTo-GitLoopyCanonicalJson $IdentitySource
                )
                $CommentId = Get-GitLoopyCommentId $Comment
                if ($null -eq $CommentId) {
                    continue
                }
                $ProducerEntry = [ordered]@{}
                foreach ($Entry in $ProducerObject.GetEnumerator()) {
                    $ProducerEntry[$Entry.Key] = $Entry.Value
                }
                $ProducerEntry["carrier"] = $Record["carrier"]
                $ProducerEntry["revision_id"] = $Record["revision_id"]
                $ProducerEntry["comment_id"] = $CommentId
                $ProducerEntry["comment_url"] = $Comment["url"]
                $Actions.Add([ordered]@{
                    identity = $Identity
                    semantic_fingerprint =
                        $Record["semantic_fingerprints"][$Action["key"]]
                    workstream_anchor = $Record["workstream"]["anchor"]
                    summary = $Action["summary"]
                    kind = $Action["kind"]
                    readiness = "Ready"
                    instruction = $Action["instruction"]
                    target = $Action["target"]
                    basis = $Action["basis"]
                    producer = $ProducerEntry
                    prerequisites = $Action["prerequisites"]
                    interaction = $Action["interaction"]
                    completion_condition = $Action["completion_condition"]
                })
            }
        }
    }
    $OrderedActions = @(
        $Actions | Sort-Object -Property { [string]$_["identity"] }
    )
    return [ordered]@{
        ok = $true
        operation = "reconcile"
        result = [ordered]@{
            status = if ($OrderedActions.Count -gt 0) { "guidance" } else { "waiting" }
            observed = [ordered]@{
                repository = $Repository
                indexed_carriers = $Carriers.Count
                producer_revisions = $RevisionCount
            }
            actions = $OrderedActions
            diagnostics = @($Diagnostics)
        }
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
        $Capabilities = [ordered]@{
            release_version = Get-GitLoopyReleaseVersion
        }
        foreach ($Name in $Script:CapabilityManifest.Keys) {
            $Capabilities[$Name] = $Script:CapabilityManifest[$Name]
        }
        Write-GitLoopyContinuationJson ([ordered]@{
            ok = $true
            capabilities = $Capabilities
        })
        return 0
    }

    $SupportedSurface = @(
        "publish", "reconcile", "record-dispatch-result", "repair-index"
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

    if ($Terminal) {
        return Write-GitLoopyContinuationError `
            -Operation $Operation `
            -Code "unsupported_operation" `
            -Message "terminal rendering is not supported by this distribution"
    }

    try {
        $Request = Read-GitLoopyContinuationRequest -InputPath $InputPath
        if ($Operation -ceq "publish") {
            $Result = Invoke-GitLoopyContinuationPublish $Request
        }
        elseif ($Operation -ceq "reconcile") {
            $Result = Invoke-GitLoopyContinuationReconcile $Request
        }
        elseif ($Operation -ceq "repair-index") {
            $Result = Invoke-GitLoopyContinuationRepairIndex $Request
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
    "Invoke-GitLoopyContinuationMain"
)
