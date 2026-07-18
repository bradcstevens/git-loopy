Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$FixturePath = Join-Path (Split-Path -Parent $PortDir) "conformance/event-schema.json"
$ModulePath = Join-Path $PortDir "GitLoopy.Events.psm1"

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
$ExpectedTypes = $Fixture["event_types"]
$ActualTypes = Get-GitLoopyEventTypes

Assert-Equal $ExpectedTypes.Count $ActualTypes.Count "event type count"
foreach ($Name in $ExpectedTypes.Keys) {
    Assert-True $ActualTypes.Contains($Name) "missing event type $Name"
    Assert-Equal $ExpectedTypes[$Name] $ActualTypes[$Name] "event type $Name"
}

foreach ($Case in $Fixture["serialization_cases"]) {
    $Actual = ConvertTo-GitLoopyJsonLine -Event $Case["event"]
    Assert-Equal $Case["jsonl"] $Actual "serialization fixture: $($Case["id"])"
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
}
finally {
    if ([IO.Directory]::Exists($TempDir)) {
        [IO.Directory]::Delete($TempDir, $true)
    }
}

Write-Output "PowerShell Event-schema conformance: ok"
