Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

class GitLoopyContinuationGitHubException : System.Exception {
    [string]$Context

    GitLoopyContinuationGitHubException([string]$Context) : base(
        "GitHub operation failed while $Context"
    ) {
        $this.Context = $Context
    }
}

$Script:IndexLabel = "git-loopy-continuation"
$Script:RecordMarker = "<!-- git-loopy-continuation:1 -->"

$Script:CapabilityManifest = [ordered]@{
    continuation_contract_versions = @("1.0")
    record_formats = @(1)
    wrapper_contract_version = "1.2"
    event_schema_version = "1.1"
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
        ($Value | ConvertTo-Json -Compress -Depth 20)
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

function Read-GitLoopyContinuationRequest {
    param([AllowNull()][object]$InputPath)

    $Bytes = if ($null -ne $InputPath) {
        try {
            [IO.File]::ReadAllBytes($InputPath)
        }
        catch {
            throw "could not read request: $($_.Exception.Message)"
        }
    }
    else {
        $Memory = [IO.MemoryStream]::new()
        try {
            [Console]::OpenStandardInput().CopyTo($Memory)
            $Memory.ToArray()
        }
        finally {
            $Memory.Dispose()
        }
    }

    try {
        $Encoding = [Text.UTF8Encoding]::new($false, $true)
        $Text = $Encoding.GetString($Bytes)
        $Document = [Text.Json.JsonDocument]::Parse($Text)
        try {
            if (
                $Document.RootElement.ValueKind -ne
                [Text.Json.JsonValueKind]::Object
            ) {
                throw "request must be one UTF-8 JSON object"
            }
            $Duplicate = Find-GitLoopyDuplicateJsonKey $Document.RootElement
            if ($null -ne $Duplicate) {
                throw "request contains duplicate object key: $Duplicate"
            }
        }
        finally {
            $Document.Dispose()
        }
        $Request = $Text | ConvertFrom-Json -AsHashtable
    }
    catch {
        if (
            $_.Exception.Message.StartsWith(
                "request contains duplicate object key:",
                [StringComparison]::Ordinal
            )
        ) {
            throw $_.Exception.Message
        }
        throw "request must be one UTF-8 JSON object"
    }
    if ($Request -isnot [Collections.IDictionary]) {
        throw "request must be one UTF-8 JSON object"
    }
    return $Request
}

function Find-GitLoopyDuplicateJsonKey {
    param(
        [Parameter(Mandatory)]
        [Text.Json.JsonElement]$Element
    )

    if ($Element.ValueKind -eq [Text.Json.JsonValueKind]::Object) {
        $Seen = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Property in $Element.EnumerateObject()) {
            if (-not $Seen.Add($Property.Name)) {
                return $Property.Name
            }
            $Nested = Find-GitLoopyDuplicateJsonKey $Property.Value
            if ($null -ne $Nested) {
                return $Nested
            }
        }
    }
    elseif ($Element.ValueKind -eq [Text.Json.JsonValueKind]::Array) {
        foreach ($Item in $Element.EnumerateArray()) {
            $Nested = Find-GitLoopyDuplicateJsonKey $Item
            if ($null -ne $Nested) {
                return $Nested
            }
        }
    }
    return $null
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
        return ,$Result
    }
    return $Value
}

function ConvertTo-GitLoopyCanonicalJson {
    param([AllowNull()][object]$Value)

    return ConvertTo-Json `
        -InputObject (ConvertTo-GitLoopyCanonicalValue $Value) `
        -Compress `
        -Depth 50
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
        return ,$Result
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
    if ($Action.Contains("effects")) {
        $Effects = $Action["effects"]
    }
    if ($Action.Contains("requirements")) {
        $Requirements = $Action["requirements"]
    }
    if ($Action.Contains("triggers")) {
        $Triggers = $Action["triggers"]
    }
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
    foreach ($Action in @($Completion["actions"])) {
        $Fingerprints[[string]$Action["key"]] =
            Get-GitLoopySemanticFingerprint $Action
    }
    return $Fingerprints
}

function Test-GitLoopyNonemptyString {
    param([AllowNull()][object]$Value)
    return $Value -is [string] -and $Value.Length -gt 0
}

function Test-GitLoopyIssueNumber {
    param([AllowNull()][object]$Value)
    return (
        ($Value -is [int] -or $Value -is [long]) -and
        [long]$Value -gt 0
    )
}

function Test-GitLoopyRepository {
    param([AllowNull()][object]$Value)
    return (
        $Value -is [string] -and
        $Value -cmatch "^[^/]+/[^/]+$"
    )
}

function Test-GitLoopyIssueReference {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)]
        [string]$Repository
    )
    return (
        $Value -is [Collections.IDictionary] -and
        $Value["kind"] -ceq "issue" -and
        $Value["repository"] -ceq $Repository -and
        (Test-GitLoopyIssueNumber $Value["number"])
    )
}

function Test-GitLoopyTrustedProducers {
    param([AllowNull()][object]$Value)

    if ($Value -isnot [Collections.IList] -or $Value.Count -eq 0) {
        return $false
    }
    $Seen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($Producer in $Value) {
        if (
            -not (Test-GitLoopyNonemptyString $Producer) -or
            -not $Seen.Add($Producer)
        ) {
            return $false
        }
    }
    return $true
}

function Test-GitLoopyTracerRequest {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Request
    )

    $Repository = $Request["repository"]
    $Trusted = $Request["trusted_producers"]
    $Completion = $Request["completion"]
    if (
        -not (Test-GitLoopyRepository $Repository) -or
        -not (Test-GitLoopyTrustedProducers $Trusted) -or
        $Completion -isnot [Collections.IDictionary]
    ) {
        return $false
    }
    if (
        $Completion["continuation_contract_version"] -cne "1.0" -or
        $Completion["record_format"] -ne 1 -or
        $Completion["publication"] -cne "shared" -or
        $Completion["disposition"] -cne "continue"
    ) {
        return $false
    }

    $Workstream = $Completion["workstream"]
    $Destination = if ($Workstream -is [Collections.IDictionary]) {
        $Workstream["destination"]
    }
    else {
        $null
    }
    if (
        $Workstream -isnot [Collections.IDictionary] -or
        -not (Test-GitLoopyIssueReference $Workstream["anchor"] $Repository) -or
        $Destination -isnot [Collections.IDictionary] -or
        $Destination["kind"] -cne "issue-closed" -or
        -not (
            Test-GitLoopyIssueReference `
                $Destination["target"] `
                $Repository
        )
    ) {
        return $false
    }

    $Transition = $Completion["transition"]
    $Producer = $Completion["producer"]
    $Carrier = $Completion["carrier"]
    if (
        $Transition -isnot [Collections.IDictionary] -or
        -not (Test-GitLoopyNonemptyString $Transition["owner"]) -or
        $Producer -isnot [Collections.IDictionary] -or
        $Producer["role"] -cne "planning" -or
        -not (Test-GitLoopyNonemptyString $Producer["login"]) -or
        [string]$Producer["login"] -cnotin @($Trusted) -or
        -not (Test-GitLoopyIssueReference $Carrier $Repository)
    ) {
        return $false
    }

    $Evidence = $Transition["evidence"]
    if ($Evidence -isnot [Collections.IList] -or $Evidence.Count -eq 0) {
        return $false
    }
    foreach ($Reference in $Evidence) {
        if (
            $Reference -isnot [Collections.IDictionary] -or
            $Reference["kind"] -cne "issue-comment" -or
            $Reference["repository"] -cne $Repository -or
            -not (Test-GitLoopyIssueNumber $Reference["issue"]) -or
            -not (Test-GitLoopyIssueNumber $Reference["comment_id"])
        ) {
            return $false
        }
    }

    $Actions = $Completion["actions"]
    if ($Actions -isnot [Collections.IList] -or $Actions.Count -ne 1) {
        return $false
    }
    $Action = $Actions[0]
    if ($Action -isnot [Collections.IDictionary]) {
        return $false
    }
    $Instruction = $Action["instruction"]
    $Interaction = $Action["interaction"]
    $InteractionEvidence = if ($Interaction -is [Collections.IDictionary]) {
        $Interaction["evidence"]
    }
    else {
        $null
    }
    $CompletionCondition = $Action["completion_condition"]
    if (
        -not (Test-GitLoopyNonemptyString $Action["key"]) -or
        -not (Test-GitLoopyNonemptyString $Action["summary"]) -or
        $Action["kind"] -cne "Publish spec" -or
        -not (Test-GitLoopyNonemptyString $Action["occurrence"]) -or
        $Instruction -isnot [Collections.IDictionary] -or
        $Instruction["mode"] -cne "skill" -or
        -not (Test-GitLoopyNonemptyString $Instruction["value"]) -or
        -not (Test-GitLoopyIssueReference $Action["target"] $Repository) -or
        $Action["basis"] -isnot [Collections.IList] -or
        $Action["basis"].Count -eq 0 -or
        $Action["prerequisites"] -isnot [Collections.IList] -or
        $Action["prerequisites"].Count -ne 0 -or
        $Interaction -isnot [Collections.IDictionary] -or
        $Interaction["classification"] -cne "AFK-safe" -or
        $InteractionEvidence -isnot [Collections.IDictionary] -or
        $InteractionEvidence["kind"] -cne "transition-owner-attestation" -or
        $InteractionEvidence["owner"] -cne $Transition["owner"] -or
        $CompletionCondition -isnot [Collections.IDictionary] -or
        $CompletionCondition["kind"] -cne "issue-closed" -or
        -not (
            Test-GitLoopyIssueReference `
                $CompletionCondition["target"] `
                $Repository
        )
    ) {
        return $false
    }
    foreach ($Basis in $Action["basis"]) {
        if (-not (Test-GitLoopyIssueReference $Basis $Repository)) {
            return $false
        }
    }
    return $true
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
        $Parsed = $Stdout | ConvertFrom-Json -AsHashtable -NoEnumerate
        if ($Parsed -is [Collections.IList]) {
            return ,$Parsed
        }
        return $Parsed
    }
    catch {
        throw [GitLoopyContinuationGitHubException]::new(
            "decoding $Context"
        )
    }
}

function New-GitLoopyRecord {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Completion
    )

    $RevisionId = Get-GitLoopySha256 (
        ConvertTo-GitLoopyCanonicalJson $Completion
    )
    $Fingerprints = Get-GitLoopySemanticFingerprints $Completion
    $Record = [ordered]@{
        revision_id = $RevisionId
        semantic_fingerprints = $Fingerprints
    }
    foreach ($Entry in $Completion.GetEnumerator()) {
        $Record[$Entry.Key] = $Entry.Value
    }
    $CanonicalRecord = ConvertTo-GitLoopyCanonicalJson $Record
    return [ordered]@{
        RevisionId = $RevisionId
        Fingerprints = $Fingerprints
        Record = $Record
        Body = "$Script:RecordMarker`n``````json`n$CanonicalRecord`n``````"
    }
}

function Invoke-GitLoopyContinuationPublish {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Request
    )

    if (-not $Request.Contains("completion")) {
        return Write-GitLoopyContinuationError `
            -Operation "publish" `
            -Code "invalid_request" `
            -Message "request is missing required field: completion"
    }
    if (-not (Test-GitLoopyTracerRequest $Request)) {
        return Write-GitLoopyContinuationError `
            -Operation "publish" `
            -Code "invalid_request" `
            -Message (
                "request is outside the supported trusted planning " +
                "publication contract"
            )
    }

    $Repository = [string]$Request["repository"]
    $Completion = $Request["completion"]
    $Carrier = $Completion["carrier"]
    $CarrierNumber = [string]$Carrier["number"]
    $Producer = [string]$Completion["producer"]["login"]
    $Record = New-GitLoopyRecord $Completion

    try {
        foreach ($Evidence in $Completion["transition"]["evidence"]) {
            $null = Invoke-GitLoopyGitHub `
                -Arguments @(
                    "api",
                    "repos/$Repository/issues/comments/$($Evidence["comment_id"])"
                ) `
                -Context "reading transition evidence"
        }
        $null = Invoke-GitLoopyGitHub `
            -Arguments @(
                "label",
                "create",
                $Script:IndexLabel,
                "--repo",
                $Repository,
                "--color",
                "5319E7",
                "--description",
                "Repairable discovery index for git-loopy Continuation records",
                "--force"
            ) `
            -Context "establishing the discovery index label" `
            -NoJson
        $null = Invoke-GitLoopyGitHub `
            -Arguments @(
                "issue",
                "edit",
                $CarrierNumber,
                "--repo",
                $Repository,
                "--add-label",
                $Script:IndexLabel
            ) `
            -Context "indexing the Producer carrier" `
            -NoJson
        $Appended = Invoke-GitLoopyGitHub `
            -Arguments @(
                "api",
                "--method",
                "POST",
                "repos/$Repository/issues/$CarrierNumber/comments",
                "--input",
                "-"
            ) `
            -InputValue ([ordered]@{ body = $Record.Body }) `
            -Context "appending the Producer revision"
        if (
            $Appended -isnot [Collections.IDictionary] -or
            $Appended["user"] -isnot [Collections.IDictionary] -or
            $Appended["user"]["login"] -cne $Producer
        ) {
            return Write-GitLoopyContinuationError `
                -Operation "publish" `
                -Code "invalid_request" `
                -Message (
                    "authenticated comment author does not match " +
                    "completion producer"
                )
        }
        $CommentId = $Appended["id"]
        $Committed = Invoke-GitLoopyGitHub `
            -Arguments @(
                "api",
                "repos/$Repository/issues/comments/$CommentId"
            ) `
            -Context "rereading the Producer revision"
    }
    catch [GitLoopyContinuationGitHubException] {
        return Write-GitLoopyContinuationError `
            -Operation "publish" `
            -Code "github_error" `
            -Message $_.Exception.Message
    }

    if (
        $Committed -isnot [Collections.IDictionary] -or
        $Committed["user"] -isnot [Collections.IDictionary] -or
        $Committed["body"] -cne $Record.Body -or
        $Committed["user"]["login"] -cne $Producer
    ) {
        return Write-GitLoopyContinuationError `
            -Operation "publish" `
            -Code "invalid_request" `
            -Message "Producer revision reread did not match the append"
    }

    Write-GitLoopyContinuationJson ([ordered]@{
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
    })
    return 0
}

function Get-GitLoopyRecordFromComment {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Comment
    )

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
        $Record = $Raw | ConvertFrom-Json -AsHashtable
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
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Comment
    )

    foreach ($Key in @("databaseId", "id")) {
        if (Test-GitLoopyIssueNumber $Comment[$Key]) {
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
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Request
    )

    if (
        -not (Test-GitLoopyRepository $Request["repository"]) -or
        -not (Test-GitLoopyTrustedProducers $Request["trusted_producers"])
    ) {
        return Write-GitLoopyContinuationError `
            -Operation "reconcile" `
            -Code "invalid_request" `
            -Message (
                "request is outside the supported trusted " +
                "Reconciliation contract"
            )
    }
    $Repository = [string]$Request["repository"]
    $Trusted = @($Request["trusted_producers"])
    try {
        $Carriers = Invoke-GitLoopyGitHub `
            -Arguments @(
                "issue",
                "list",
                "--repo",
                $Repository,
                "--state",
                "all",
                "--label",
                $Script:IndexLabel,
                "--limit",
                "100",
                "--json",
                "number,state,url,comments"
            ) `
            -Context "discovering indexed carriers"
    }
    catch [GitLoopyContinuationGitHubException] {
        return Write-GitLoopyContinuationError `
            -Operation "reconcile" `
            -Code "github_error" `
            -Message $_.Exception.Message
    }
    if ($Carriers -isnot [Collections.IList]) {
        return Write-GitLoopyContinuationError `
            -Operation "reconcile" `
            -Code "github_error" `
            -Message "GitHub operation failed while decoding indexed carriers"
    }

    $Actions = [Collections.Generic.List[object]]::new()
    $RevisionCount = 0
    foreach ($Carrier in $Carriers) {
        if (
            $Carrier -isnot [Collections.IDictionary] -or
            $Carrier["comments"] -isnot [Collections.IList]
        ) {
            return Write-GitLoopyContinuationError `
                -Operation "reconcile" `
                -Code "github_error" `
                -Message (
                    "GitHub operation failed while decoding indexed carriers"
                )
        }
        foreach ($Comment in $Carrier["comments"]) {
            if (
                $Comment -isnot [Collections.IDictionary] -or
                $Comment["author"] -isnot [Collections.IDictionary]
            ) {
                continue
            }
            $Author = [string]$Comment["author"]["login"]
            if ($Author -cnotin $Trusted) {
                continue
            }
            $Parsed = Get-GitLoopyRecordFromComment $Comment
            if ($null -eq $Parsed) {
                continue
            }
            $Record = $Parsed.Record
            $Completion = $Parsed.Completion
            if (
                $Record["producer"] -isnot [Collections.IDictionary] -or
                $Record["producer"]["login"] -cne $Author
            ) {
                continue
            }
            $ValidationRequest = [ordered]@{
                repository = $Repository
                trusted_producers = $Trusted
                completion = $Completion
            }
            if (-not (Test-GitLoopyTracerRequest $ValidationRequest)) {
                continue
            }
            $RevisionCount++

            foreach ($Action in $Record["actions"]) {
                try {
                    $Target = Invoke-GitLoopyGitHub `
                        -Arguments @(
                            "issue",
                            "view",
                            [string]$Action["target"]["number"],
                            "--repo",
                            $Repository,
                            "--json",
                            "number,state,url"
                        ) `
                        -Context "reading an Action Target"
                }
                catch [GitLoopyContinuationGitHubException] {
                    return Write-GitLoopyContinuationError `
                        -Operation "reconcile" `
                        -Code "github_error" `
                        -Message $_.Exception.Message
                }
                if (
                    $Target -isnot [Collections.IDictionary] -or
                    $Target["state"] -isnot [string]
                ) {
                    return Write-GitLoopyContinuationError `
                        -Operation "reconcile" `
                        -Code "github_error" `
                        -Message (
                            "GitHub operation failed while decoding " +
                            "an Action Target"
                        )
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
                    producer = [ordered]@{
                        login = $Record["producer"]["login"]
                        role = $Record["producer"]["role"]
                        carrier = $Record["carrier"]
                        revision_id = $Record["revision_id"]
                        comment_id = $CommentId
                        comment_url = $Comment["url"]
                    }
                    prerequisites = $Action["prerequisites"]
                    interaction = $Action["interaction"]
                    completion_condition = $Action["completion_condition"]
                })
            }
        }
    }
    $OrderedActions = @(
        $Actions | Sort-Object -Property {
            [string]$_["identity"]
        }
    )
    Write-GitLoopyContinuationJson ([ordered]@{
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
                indexed_carriers = $Carriers.Count
                producer_revisions = $RevisionCount
            }
            actions = $OrderedActions
            diagnostics = @()
        }
    })
    return 0
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
        "publish",
        "reconcile",
        "record-dispatch-result",
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

    try {
        $Request = Read-GitLoopyContinuationRequest -InputPath $InputPath
    }
    catch {
        return Write-GitLoopyContinuationError `
            -Operation $Operation `
            -Code "invalid_request" `
            -Message $_.Exception.Message
    }

    if ($Operation -ceq "publish") {
        return Invoke-GitLoopyContinuationPublish $Request
    }
    if ($Operation -ceq "reconcile") {
        if ($Terminal) {
            return Write-GitLoopyContinuationError `
                -Operation "reconcile" `
                -Code "unsupported_operation" `
                -Message (
                    "terminal rendering is not supported by this distribution"
                )
        }
        return Invoke-GitLoopyContinuationReconcile $Request
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
