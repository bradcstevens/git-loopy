Set-StrictMode -Version Latest

# The single semver authority for the PowerShell distribution's Release version.
#
# The Orchestrator answers `--version` with it, stamps it onto `wrapper.run.start`,
# and refuses a clone-local TUI helper that disagrees with it. The installer
# stages that helper. Both read the same file
# through this one reader, so there is no second opinion about what Release a
# clone is (ADR-0016, Wrapper contract §16, issue #194).

$Script:ReleaseVersionPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "../../VERSION")
)

function Get-GitLoopyReleaseVersion {
    [CmdletBinding()]
    param(
        [string]$Path = $Script:ReleaseVersionPath
    )

    try {
        $Content = [Text.UTF8Encoding]::new($false, $true).GetString(
            [IO.File]::ReadAllBytes($Path)
        )
    }
    catch {
        throw [IO.InvalidDataException]::new(
            "cannot read Release version authority ${Path}: $($_.Exception.Message)",
            $_.Exception
        )
    }

    $Value = if ($Content.EndsWith("`r`n", [StringComparison]::Ordinal)) {
        $Content.Substring(0, $Content.Length - 2)
    }
    elseif ($Content.EndsWith("`n", [StringComparison]::Ordinal)) {
        $Content.Substring(0, $Content.Length - 1)
    }
    else {
        $Content
    }
    $Identifier = "(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    $Pattern = (
        "\A(?:0|[1-9][0-9]*)\." +
        "(?:0|[1-9][0-9]*)\." +
        "(?:0|[1-9][0-9]*)" +
        "(?:-$Identifier(?:\.$Identifier)*)?" +
        "(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\z"
    )
    if (-not [regex]::IsMatch(
        $Value,
        $Pattern,
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
    )) {
        throw [IO.InvalidDataException]::new(
            "Release version authority $Path must contain exactly one " +
                "Semantic Versioning value"
        )
    }
    return $Value
}

Export-ModuleMember -Function @(
    "Get-GitLoopyReleaseVersion"
)
