Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Import-Module (Join-Path $PSScriptRoot "../GitLoopy.Orchestrator.psm1") -Force
$Fixture = ConvertFrom-Json -AsHashtable -InputObject (
    Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "../../conformance/repository-identity.json")
)

$Cases = 0
foreach ($Case in $Fixture.cases) {
    $Actual = Get-GitLoopyRepositoryFromRemoteUrl -Url $Case.url
    if ($Actual -cne $Case.expected) {
        throw "FAIL: repository identity $($Case.id): expected $($Case.expected), got $Actual"
    }
    $Cases++
}
if ($Cases -eq 0) { throw "FAIL: the repository identity fixture drove no case" }

Write-Output "PASS: repository identity Conformance ($Cases cases)"
