Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Import-Module (Join-Path $PSScriptRoot "../GitLoopy.Orchestrator.psm1") -Force
$Fixture = ConvertFrom-Json -AsHashtable -InputObject (
    Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "../../conformance/issue-lease.json")
)

$Cases = 0
foreach ($Case in $Fixture.cases) {
    $Record = $Fixture.record.Clone()
    if ($Case.Contains("set")) {
        foreach ($Field in $Case.set.Keys) { $Record[$Field] = $Case.set[$Field] }
    }
    if ($Case.Contains("remove")) {
        foreach ($Field in $Case.remove) { $Record.Remove($Field) }
    }
    $Raw = if ($Case.Contains("raw")) { $Case.raw } else {
        ConvertTo-Json -Depth 10 -Compress -InputObject $Record
    }
    $Parameters = @{
        Raw = $Raw
        Now = $Case.now
        Repository = $Fixture.repository
        Issue = $Fixture.issue
    }
    if ($Case.Contains("skew_tolerance_seconds")) {
        $Parameters.SkewToleranceSeconds = $Case.skew_tolerance_seconds
    }
    $Actual = Get-GitLoopyLeaseInspection @Parameters
    $Owner = if ($null -ne $Actual.record) { $Actual.record.run_id } else { $null }
    if ($Actual.state -cne $Case.expected.state -or $Owner -cne $Case.expected.owner -or
        (ConvertTo-Json -Compress -InputObject @($Actual.diagnostics)) -cne
        (ConvertTo-Json -Compress -InputObject @($Case.expected.diagnostics))) {
        throw "FAIL: Lease record $($Case.id): $(ConvertTo-Json -Compress -Depth 10 $Actual)"
    }
    $Cases++
}
if ($Cases -eq 0) { throw "FAIL: the Lease fixture drove no record case" }

$Cases = 0
foreach ($Case in $Fixture.invalid_context_cases) {
    $Parameters = @{
        Raw = (ConvertTo-Json -Compress -InputObject $Fixture.record)
        Now = 1000
        Repository = $Fixture.repository
        Issue = $Fixture.issue
    }
    $Name = if ($Case.field -eq "skew_tolerance_seconds") {
        "SkewToleranceSeconds"
    } else { $Case.field }
    $Parameters[$Name] = $Case.value
    $Failure = $null
    try { $null = Get-GitLoopyLeaseInspection @Parameters }
    catch { $Failure = $_.Exception.Message }
    if ($null -eq $Failure -or -not $Failure.Contains("invalid $($Case.field)")) {
        throw "FAIL: invalid Lease inspection context $($Case.id): $Failure"
    }
    $Cases++
}
if ($Cases -eq 0) { throw "FAIL: the Lease fixture drove no invalid-context case" }

# A verdict must not quietly rewrite the record it read: the eventual transport
# renews and fences against these exact fields.
$Preserved = Get-GitLoopyLeaseInspection -Raw (
    ConvertTo-Json -Compress -InputObject $Fixture.record
) -Now 1000 -Repository $Fixture.repository -Issue $Fixture.issue
foreach ($Field in $Fixture.record.Keys) {
    if ($Preserved.record[$Field] -cne $Fixture.record[$Field]) {
        throw "FAIL: Lease inspection changed $Field"
    }
}

Write-Output "PASS: Lease record Conformance"
