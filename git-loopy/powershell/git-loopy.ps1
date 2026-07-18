if ($PSVersionTable.PSVersion.Major -lt 7) {
    [Console]::Error.WriteLine(
        "git-loopy's PowerShell Orchestrator requires PowerShell 7+ " +
        "(found $($PSVersionTable.PSVersion)). Install PowerShell 7 and " +
        "rerun this script with pwsh."
    )
    exit 1
}

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ModulePath = Join-Path $PSScriptRoot "GitLoopy.Orchestrator.psm1"
$PackagedPrompt = Join-Path (Split-Path -Parent $PSScriptRoot) "PROMPT.md"
Import-Module $ModulePath -Force

try {
    $ExitCode = Invoke-GitLoopyMain `
        -Arguments $args `
        -PackagedPrompt $PackagedPrompt
}
catch [System.Management.Automation.ParseException] {
    [Console]::Error.WriteLine($_.Exception.Message)
    [Console]::Error.WriteLine((Get-GitLoopyUsage))
    $ExitCode = Get-GitLoopyExitCode -Reason "usage_error"
}
catch {
    [Console]::Error.WriteLine(
        "git-loopy: discovery Run failed: $($_.Exception.Message)"
    )
    $ExitCode = 1
}

exit $ExitCode
