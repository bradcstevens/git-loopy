<#
.SYNOPSIS
    Refuse a PowerShell gate that ran to completion having asserted nothing.

.DESCRIPTION
    The PowerShell half of the Runner-family gate's assertion census (issue #317).

    The PowerShell member is the measured fail-open case: a `foreach` over a
    `test-*.ps1` pattern that matched nothing runs no suite and exits 0, so the
    gate reports success having proved nothing. `pwsh` prints no assertion total of
    its own, so the gate's census is the number of suites it actually executed, and
    this guard refuses a zero.

    Its bash sibling is .github/scripts/assert-nonzero-census.sh. This one exists
    because the step it protects is a `shell: pwsh` step that also runs on Windows,
    where the bash guard is not a dependency the gate should take.

    The guarantee is non-zero and deliberately not a pinned floor: a floor has to be
    bumped every time a test is added, and a gate that must be edited to stay green
    is a gate that eventually gets edited to stay quiet.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Label,

    [Parameter(Mandatory)]
    [AllowEmptyString()]
    [string]$Census
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Census -notmatch '^[0-9]+$') {
    $Reported = if ([string]::IsNullOrEmpty($Census)) { "<empty>" } else { $Census }
    [Console]::Error.WriteLine(
        "$Label assertion census: $Reported is not a count -- " +
        "the $Label gate could not say what it asserted.")
    exit 1
}

Write-Output "$Label assertion census: $Census"

if ([int]$Census -lt 1) {
    [Console]::Error.WriteLine(
        "$Label assertion census is zero: the $Label gate ran to completion " +
        "having asserted nothing, so it proved nothing.")
    exit 1
}
