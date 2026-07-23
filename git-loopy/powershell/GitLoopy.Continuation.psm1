Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

class GitLoopyContinuationRejection : System.Exception {
    GitLoopyContinuationRejection([string]$Message) : base($Message) {}
}

class GitLoopyContinuationGitHubException : System.Exception {
    [string]$Context

    GitLoopyContinuationGitHubException([string]$Context) : base(
        "GitHub operation failed while $Context"
    ) {
        $this.Context = $Context
    }
}

$Script:ContinuationContractVersion = "1.0"
$Script:RecordFormat = 1
$Script:WrapperContractVersion = "1.2"
$Script:EventSchemaVersion = "1.1"

$Script:IndexLabel = "git-loopy-continuation"
$Script:RecordMarker = "<!-- git-loopy-continuation:1 -->"
$Script:MaxInteger = [System.Numerics.BigInteger]::Pow(2, 53) - 1
$Script:MaxDepth = 16
$Script:MaxArrayLength = 256
$Script:MaxStringBytes = 8 * 1024
$Script:MaxRecordBytes = 48 * 1024
$Script:MaxCarrierBodyBytes = 64 * 1024

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
        github = [ordered]@{ operations = @("publish", "reconcile") }
    }
    operations = [ordered]@{
        capabilities = $true
        publish = $true
        reconcile = $true
        "record-dispatch-result" = $false
        "repair-index" = $false
    }
    instruction_handlers = @()
    instruction_modes = @()
    evaluators = @()
    effect_scopes = @()
    optional_capabilities = [ordered]@{
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
    [Console]::Out.WriteLine(
        ($Value | ConvertTo-Json -Compress -Depth 50)
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
    [Console]::Error.WriteLine("git-loopy continuation: $Message")
    return 1
}

function New-GitLoopyRejection {
    param([Parameter(Mandatory)][string]$Message)
    return [GitLoopyContinuationRejection]::new($Message)
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
        -Required @("repository", "trusted_producers", "completion")
    $Repository = Get-GitLoopyRepository $Request
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
        -Request $Request -AllowEmpty:($Publication -ceq "ephemeral")
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
    if ($Publication -ceq "shared" -and $Login -cnotin @($Trusted)) {
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
    param([Parameter(Mandatory)][Collections.IDictionary]$Completion)

    $RevisionId = Get-GitLoopySha256 (ConvertTo-GitLoopyCanonicalJson $Completion)
    $Fingerprints = Get-GitLoopySemanticFingerprints $Completion
    $Record = [ordered]@{
        revision_id = $RevisionId
        semantic_fingerprints = $Fingerprints
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
        $null = $StderrTask.GetAwaiter().GetResult()
        if ($Process.ExitCode -ne 0) {
            throw [GitLoopyContinuationGitHubException]::new($Context)
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

function Invoke-GitLoopyContinuationPublish {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

    $Validated = Test-GitLoopyCompletion $Request
    $Repository = [string]$Validated.Repository
    $Completion = $Validated.Completion
    $Publication = [string]$Validated.Publication
    $Fingerprints = Get-GitLoopySemanticFingerprints $Completion

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
    $Record = New-GitLoopyRecordBody $Completion

    foreach ($EvidenceRef in $Completion["transition"]["evidence"]) {
        $null = Invoke-GitLoopyGitHub `
            -Arguments @(
                "api",
                "repos/$Repository/issues/comments/$($EvidenceRef["comment_id"])"
            ) `
            -Context "reading transition evidence"
    }
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
    if (
        $Appended -isnot [Collections.IDictionary] -or
        $Appended["user"] -isnot [Collections.IDictionary] -or
        $Appended["user"]["login"] -cne $Producer
    ) {
        throw (New-GitLoopyRejection (
            "authenticated comment author does not match completion producer"
        ))
    }
    $CommentId = $Appended["id"]
    $Committed = Invoke-GitLoopyGitHub `
        -Arguments @(
            "api", "repos/$Repository/issues/comments/$CommentId"
        ) `
        -Context "rereading the Producer revision"
    if (
        $Committed -isnot [Collections.IDictionary] -or
        $Committed["user"] -isnot [Collections.IDictionary] -or
        $Committed["body"] -cne $Record.Body -or
        $Committed["user"]["login"] -cne $Producer
    ) {
        throw (New-GitLoopyRejection (
            "Producer revision reread did not match the append"
        ))
    }

    return [ordered]@{
        ok = $true
        operation = "publish"
        receipt = [ordered]@{
            status = "committed"
            revision_id = $Record.RevisionId
            carrier = $Carrier
            comment = [ordered]@{
                id = $CommentId
                url = $Committed["html_url"]
            }
            index_label = $Script:IndexLabel
            semantic_fingerprints = $Record.Fingerprints
        }
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

function Invoke-GitLoopyContinuationReconcile {
    param([Parameter(Mandatory)][Collections.IDictionary]$Request)

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
        Write-GitLoopyContinuationJson ([ordered]@{
            ok = $true
            capabilities = $Script:CapabilityManifest
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
        else {
            $Result = $null
        }
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
