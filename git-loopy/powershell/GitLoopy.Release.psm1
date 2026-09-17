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
$script:ReleaseVersionPaths = [string[]]@(
    "VERSION",
    "git-loopy/python/git_loopy/__init__.py",
    "git-loopy/python/git_loopy/VERSION",
    "git-loopy/python/pyproject.toml",
    "git-loopy/python/uv.lock",
    "git-loopy/tui/Cargo.toml",
    "git-loopy/tui/Cargo.lock",
    "git-loopy/tui/README.md"
)
$script:ReleaseLineInitialized = $false
$script:ReleaseLastStable = $null
$script:ReleaseTarget = $null
$script:ReleaseCounter = [bigint]0
$script:BumpClassKeys = [string[]]@("major", "minor", "patch", "none")
$script:ReleaseNotesDirectory = "docs/releases"

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

function Resolve-GitLoopyBumpClass {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string[]]$Labels
    )

    $Keys = @(
        foreach ($Label in $Labels) {
            if ($Label.StartsWith("semver:", [StringComparison]::Ordinal)) {
                $Label.Substring("semver:".Length)
            }
        }
    )
    $Unknown = @($Keys | Where-Object { $_ -cnotin $script:BumpClassKeys })
    if ($Unknown.Count -gt 0) {
        throw "Release-line Bump class: unknown_semver_key"
    }
    if ($Keys.Count -eq 0) {
        throw "Release-line Bump class: unclassified_bump_class"
    }
    if ($Keys.Count -ne 1) {
        throw "Release-line Bump class: conflicting_semver_labels"
    }
    return $Keys[0]
}

function Get-GitLoopyReleaseTargetParts {
    param(
        [Parameter(Mandatory)]
        [string]$Value,
        [Parameter(Mandatory)]
        [string]$Label
    )

    $Match = [regex]::Match(
        $Value,
        "\A(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\z",
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    if (-not $Match.Success) {
        throw "$Label must be a stable major.minor.patch Semantic Versioning value"
    }
    return [pscustomobject]@{
        Major = [bigint]::Parse($Match.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
        Minor = [bigint]::Parse($Match.Groups[2].Value, [Globalization.CultureInfo]::InvariantCulture)
        Patch = [bigint]::Parse($Match.Groups[3].Value, [Globalization.CultureInfo]::InvariantCulture)
    }
}

function Invoke-GitLoopyReleaseLineAdvance {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$LastStableVersion,
        [Parameter(Mandatory)]
        [string]$CurrentTarget,
        [Parameter(Mandatory)]
        [object]$CurrentCounter,
        [Parameter(Mandatory)]
        [string]$BumpClass
    )

    if ($BumpClass -cnotin $script:BumpClassKeys) {
        throw "unknown Release-line Bump class '$BumpClass'"
    }
    if (
        $CurrentCounter -is [bool] -or
        -not ($CurrentCounter -is [int] -or $CurrentCounter -is [long] -or $CurrentCounter -is [bigint]) -or
        $CurrentCounter -lt 0
    ) {
        throw "Release-line counter must be a non-negative integer"
    }

    $Stable = Get-GitLoopyReleaseTargetParts `
        -Value $LastStableVersion -Label "Last stable Release version"
    $Current = Get-GitLoopyReleaseTargetParts `
        -Value $CurrentTarget -Label "Release target"
    $Candidate = switch ($BumpClass) {
        "major" { [pscustomobject]@{ Major = $Stable.Major + 1; Minor = [bigint]0; Patch = [bigint]0 } }
        "minor" { [pscustomobject]@{ Major = $Stable.Major; Minor = $Stable.Minor + 1; Patch = [bigint]0 } }
        "patch" { [pscustomobject]@{ Major = $Stable.Major; Minor = $Stable.Minor; Patch = $Stable.Patch + 1 } }
        "none" { $Stable }
    }
    $UseCandidate = (
        $Candidate.Major -gt $Current.Major -or
        ($Candidate.Major -eq $Current.Major -and $Candidate.Minor -gt $Current.Minor) -or
        ($Candidate.Major -eq $Current.Major -and $Candidate.Minor -eq $Current.Minor -and
            $Candidate.Patch -gt $Current.Patch)
    )
    $TargetParts = if ($UseCandidate) { $Candidate } else { $Current }
    [bigint]$Counter = [bigint]$CurrentCounter
    if ($BumpClass -cne "none") {
        $Counter += 1
    }
    $Target = "$($TargetParts.Major).$($TargetParts.Minor).$($TargetParts.Patch)"
    return [pscustomobject]@{
        Target = $Target
        Counter = $Counter
        Version = if ($Counter -eq 0) { $Target } else { "$Target-dev.$Counter" }
    }
}

function Invoke-GitLoopyReleaseLinePromotion {
    <#
    .SYNOPSIS
    Cut a stable Release from the one Bump class exempt from a milestone.

    .DESCRIPTION
    A `major` publishes on the label alone; every other class stays on its
    `dev.N` line until the `vX.Y.Z` milestone it promised closes (ADR-0052).
    Callers ask this rather than testing the class themselves, so *which* class
    is exempt is one decision rather than one per call site.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$ReleaseLine,
        [Parameter(Mandatory)]
        [string]$BumpClass
    )

    if ($script:BumpClassKeys -cnotcontains $BumpClass) {
        throw "unknown Release-line Bump class '$BumpClass'"
    }
    if ($BumpClass -cne "major") {
        return $ReleaseLine
    }
    return [pscustomobject]@{
        Target = $ReleaseLine.Target
        Counter = [bigint]0
        Version = $ReleaseLine.Target
    }
}

function Get-GitLoopyReleaseLineCommitSubject {
    <#
    .SYNOPSIS
    Word the commit that moves the Release line to one version.

    .DESCRIPTION
    A Promotion and an advance are different events in the domain, so they read
    differently in `git log`: only a stable value was cut from its line.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Version
    )

    $Verb = if ($Version.Split("+")[0].Contains("-")) { "advance" } else { "promote" }
    return "chore(release): $Verb Release line to $Version"
}

function Get-GitLoopyClosedMilestonePromotion {
    <#
    .SYNOPSIS
    Return the stable Release a matching closed milestone promotes, if any.

    .DESCRIPTION
    Deliberately separate from the per-issue advance: a prerelease takes no
    milestone input at any point, while a milestone-close event may promote only
    the exact `dev.N` target its own title names.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$CurrentVersion,
        [Parameter(Mandatory)]
        [string]$MilestoneTitle,
        [Parameter(Mandatory)]
        [string]$MilestoneState
    )

    $Match = [regex]::Match(
        $CurrentVersion,
        "\A((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))-dev\.(?:0|[1-9][0-9]*)\z",
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    if (
        -not $Match.Success -or
        -not $MilestoneState.Equals("closed", [StringComparison]::OrdinalIgnoreCase) -or
        $MilestoneTitle -cne "v$($Match.Groups[1].Value)"
    ) {
        return $null
    }
    return $Match.Groups[1].Value
}

function Assert-GitLoopyReleaseLineVersion {
    param(
        [Parameter(Mandatory)]
        [string]$Version
    )

    if ($Version -cnotmatch "\A(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-dev\.(?:0|[1-9][0-9]*))?\z") {
        throw "Release line must be a stable or -dev.N Semantic Versioning value"
    }
}

function Get-GitLoopyUtf8FileContent {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$Label
    )

    try {
        return [Text.UTF8Encoding]::new($false, $true).GetString(
            [IO.File]::ReadAllBytes($Path)
        )
    }
    catch {
        throw "cannot read $Label ${Path}: $($_.Exception.Message)"
    }
}

function Get-GitLoopyPythonDistributionVersion {
    param(
        [Parameter(Mandatory)]
        [string]$Version
    )

    $Match = [regex]::Match(
        $Version,
        "\A((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))(?:-dev\.(0|[1-9][0-9]*))?\z",
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    if (-not $Match.Success) {
        throw "Release version must be stable or a -dev.N prerelease"
    }
    if ($Match.Groups[2].Success) {
        return "$($Match.Groups[1].Value).dev$($Match.Groups[2].Value)"
    }
    return $Match.Groups[1].Value
}

function Set-GitLoopyAtomicFileUpdates {
    param(
        [Parameter(Mandatory)]
        [object[]]$Updates,
        [Parameter(Mandatory)]
        [string]$Label
    )

    if ($Updates.Count -eq 0) {
        return
    }

    $Staged = [Collections.Generic.List[object]]::new()
    $TemporaryPaths = [Collections.Generic.List[string]]::new()
    try {
        foreach ($Update in $Updates) {
            $Directory = Split-Path -Parent $Update.Path
            if (
                -not [string]::IsNullOrEmpty($Directory) -and
                -not [IO.Directory]::Exists($Directory)
            ) {
                [IO.Directory]::CreateDirectory($Directory) | Out-Null
            }
            $Stage = "$($Update.Path).$([guid]::NewGuid()).git-loopy-release"
            $Backup = "$($Update.Path).$([guid]::NewGuid()).git-loopy-release-original"
            $HadOriginal = [IO.File]::Exists($Update.Path)
            $TemporaryPaths.Add($Stage)
            if ($HadOriginal) {
                $TemporaryPaths.Add($Backup)
            }
            [IO.File]::WriteAllText($Stage, $Update.Content, [Text.UTF8Encoding]::new($false))
            if ($HadOriginal) {
                [IO.File]::Copy($Update.Path, $Backup)
            }
            $Staged.Add([pscustomobject]@{
                Path = $Update.Path
                Stage = $Stage
                Backup = $Backup
                HadOriginal = $HadOriginal
            })
        }
        $Replaced = [Collections.Generic.List[object]]::new()
        try {
            foreach ($Update in $Staged) {
                [IO.File]::Move($Update.Stage, $Update.Path, $true)
                $Replaced.Add($Update)
            }
        }
        catch {
            for ($Index = $Replaced.Count - 1; $Index -ge 0; $Index--) {
                $Update = $Replaced[$Index]
                if ($Update.HadOriginal) {
                    [IO.File]::Move($Update.Backup, $Update.Path, $true)
                    continue
                }
                Remove-Item -LiteralPath $Update.Path -Force -ErrorAction SilentlyContinue
            }
            throw "cannot complete $Label write; all copies were restored: $($_.Exception.Message)"
        }
    }
    finally {
        foreach ($Path in $TemporaryPaths) {
            Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        }
    }
}

function Replace-GitLoopyReleaseValue {
    param(
        [Parameter(Mandatory)]
        [string]$Content,
        [Parameter(Mandatory)]
        [string]$Pattern,
        [Parameter(Mandatory)]
        [string]$Replacement,
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$Label
    )

    $Regex = [regex]::new(
        $Pattern,
        [Text.RegularExpressions.RegexOptions]::CultureInvariant -bor
            [Text.RegularExpressions.RegexOptions]::Multiline
    )
    if ($Regex.Matches($Content).Count -ne 1) {
        throw "$Label must occur exactly once in $Path"
    }
    return $Regex.Replace($Content, $Replacement, 1)
}

function Replace-GitLoopyPackageReleaseValue {
    param(
        [Parameter(Mandatory)]
        [string]$Content,
        [Parameter(Mandatory)]
        [string]$PackageName,
        [Parameter(Mandatory)]
        [string]$Expected,
        [Parameter(Mandatory)]
        [string]$Version,
        [Parameter(Mandatory)]
        [string]$Path
    )

    $Headers = [regex]::Matches($Content, "(?m)^\[\[package\]\]$")
    $PackageSections = @()
    for ($Index = 0; $Index -lt $Headers.Count; $Index++) {
        $Start = $Headers[$Index].Index
        $End = if ($Index + 1 -lt $Headers.Count) {
            $Headers[$Index + 1].Index
        }
        else {
            $Content.Length
        }
        $Section = $Content.Substring($Start, $End - $Start)
        if ($Section -cmatch ('(?m)^name\s*=\s*"' + [regex]::Escape($PackageName) + '"$')) {
            $PackageSections += [pscustomobject]@{ Start = $Start; End = $End }
        }
    }
    if ($PackageSections.Count -ne 1) {
        throw "$PackageName Release metadata must occur exactly once in $Path"
    }
    $Match = $PackageSections[0]
    $Section = $Content.Substring($Match.Start, $Match.End - $Match.Start)
    $Updated = Replace-GitLoopyReleaseValue `
        -Content $Section `
        -Pattern ('^version\s*=\s*"' + [regex]::Escape($Expected) + '"$') `
        -Replacement ('version = "' + $Version + '"') `
        -Path $Path `
        -Label "$PackageName Release version"
    return $Content.Substring(0, $Match.Start) + $Updated + $Content.Substring($Match.End)
}

function Replace-GitLoopyProjectReleaseValue {
    param(
        [Parameter(Mandatory)]
        [string]$Content,
        [Parameter(Mandatory)]
        [string]$Expected,
        [Parameter(Mandatory)]
        [string]$Version,
        [Parameter(Mandatory)]
        [string]$Path
    )

    $Match = [regex]::Match($Content, '(?ms)^\[project\]$(.*?)(?=^\[|\z)')
    if (-not $Match.Success) {
        throw "cannot find [project] metadata in $Path"
    }
    $Updated = Replace-GitLoopyReleaseValue `
        -Content $Match.Groups[1].Value `
        -Pattern ('^version\s*=\s*"' + [regex]::Escape($Expected) + '"$') `
        -Replacement ('version = "' + $Version + '"') `
        -Path $Path `
        -Label "Python package Release version"
    return $Content.Substring(0, $Match.Groups[1].Index) + $Updated +
        $Content.Substring($Match.Groups[1].Index + $Match.Groups[1].Length)
}

function Replace-GitLoopyTuiManifestReleaseValue {
    param(
        [Parameter(Mandatory)]
        [string]$Content,
        [Parameter(Mandatory)]
        [string]$Expected,
        [Parameter(Mandatory)]
        [string]$Version,
        [Parameter(Mandatory)]
        [string]$Path
    )

    $Match = [regex]::Match($Content, '(?ms)^\[package\]$(.*?)(?=^\[|\z)')
    if (-not $Match.Success) {
        throw "cannot find [package] metadata in $Path"
    }
    $Updated = Replace-GitLoopyReleaseValue `
        -Content $Match.Groups[1].Value `
        -Pattern ('^version\s*=\s*"' + [regex]::Escape($Expected) + '"$') `
        -Replacement ('version = "' + $Version + '"') `
        -Path $Path `
        -Label "TUI manifest Release version"
    return $Content.Substring(0, $Match.Groups[1].Index) + $Updated +
        $Content.Substring($Match.Groups[1].Index + $Match.Groups[1].Length)
}

function Set-GitLoopyRepositoryReleaseVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory)]
        [string]$Version
    )

    Assert-GitLoopyReleaseLineVersion -Version $Version
    $Root = [IO.Path]::GetFullPath($RepositoryRoot)
    $Authority = Get-GitLoopyReleaseVersion -Path (Join-Path $Root "VERSION")
    $PythonAuthority = Get-GitLoopyPythonDistributionVersion -Version $Authority
    $PythonVersion = Get-GitLoopyPythonDistributionVersion -Version $Version
    $Updates = [Collections.Generic.List[object]]::new()
    foreach ($RelativePath in $script:ReleaseVersionPaths) {
        $Path = Join-Path $Root $RelativePath
        if (-not [IO.File]::Exists($Path)) {
            throw "cannot read Release metadata $Path"
        }
        $Content = [IO.File]::ReadAllText($Path, [Text.UTF8Encoding]::new($false, $true))
        $Updated = switch ($RelativePath) {
            "VERSION" { "$Version`n" }
            "git-loopy/python/git_loopy/VERSION" {
                $Runtime = Get-GitLoopyReleaseVersion -Path $Path
                if ($Runtime -cne $Authority) {
                    throw "Python runtime Release version mismatch: expected '$Authority' from VERSION, found '$Runtime' in $Path"
                }
                "$Version`n"
            }
            "git-loopy/python/git_loopy/__init__.py" {
                Replace-GitLoopyReleaseValue `
                    -Content $Content `
                    -Pattern ('(__version__\s*=\s*")' + [regex]::Escape($Authority) + '(")') `
                    -Replacement ('${1}' + $Version + '${2}') `
                    -Path $Path `
                    -Label "Python source Release version"
            }
            "git-loopy/python/pyproject.toml" {
                Replace-GitLoopyProjectReleaseValue `
                    -Content $Content -Expected $Authority -Version $Version -Path $Path
            }
            "git-loopy/python/uv.lock" {
                Replace-GitLoopyPackageReleaseValue `
                    -Content $Content -PackageName "git-loopy" `
                    -Expected $PythonAuthority -Version $PythonVersion -Path $Path
            }
            "git-loopy/tui/Cargo.toml" {
                Replace-GitLoopyTuiManifestReleaseValue `
                    -Content $Content -Expected $Authority -Version $Version -Path $Path
            }
            "git-loopy/tui/Cargo.lock" {
                Replace-GitLoopyPackageReleaseValue `
                    -Content $Content -PackageName "git-loopy-tui" `
                    -Expected $Authority -Version $Version -Path $Path
            }
            "git-loopy/tui/README.md" {
                Replace-GitLoopyReleaseValue `
                    -Content $Content `
                    -Pattern ('("version":\s*)"' + [regex]::Escape($Authority) + '"') `
                    -Replacement ('${1}"' + $Version + '"') `
                    -Path $Path `
                    -Label "TUI documented probe Release version"
            }
        }
        $Updates.Add([pscustomobject]@{ Path = $Path; Content = $Updated })
    }
    Set-GitLoopyAtomicFileUpdates -Updates $Updates.ToArray() -Label "Release version"
}

function Get-GitLoopyReleaseNotesRelativePath {
    param(
        [Parameter(Mandatory)]
        [string]$Version
    )

    Assert-GitLoopyReleaseLineVersion -Version $Version
    return "$($script:ReleaseNotesDirectory)/v$Version.md"
}

function New-GitLoopyReleaseLineFragmentContent {
    param(
        [Parameter(Mandatory)]
        [string]$Version,
        [Parameter(Mandatory)]
        [string]$Target
    )

    return (
        "# git-loopy $Version`n`n" +
        "This development fragment advances the Release line to ``$Version`` " +
        "on the way to stable ``$Target``.`n"
    )
}

function Get-GitLoopyReleaseNotesBody {
    param(
        [Parameter(Mandatory)]
        [string]$Content
    )

    $Lines = [Collections.Generic.List[string]]::new()
    foreach ($Line in ($Content.Replace("`r`n", "`n") -split "`n")) {
        $Lines.Add($Line)
    }
    if ($Lines.Count -gt 0 -and $Lines[0].StartsWith("# ", [StringComparison]::Ordinal)) {
        $Lines.RemoveAt(0)
    }
    while ($Lines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($Lines[0])) {
        $Lines.RemoveAt(0)
    }
    while (
        $Lines.Count -gt 0 -and
        [string]::IsNullOrWhiteSpace($Lines[$Lines.Count - 1])
    ) {
        $Lines.RemoveAt($Lines.Count - 1)
    }
    if ($Lines.Count -eq 0) {
        return ""
    }
    return ($Lines.ToArray() -join "`n")
}

function Get-GitLoopyReleaseTargetFragments {
    param(
        [Parameter(Mandatory)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory)]
        [string]$StableVersion,
        [Parameter(Mandatory)]
        [object[]]$PendingFragments
    )

    $Fragments = @{}
    $ReleaseDir = Join-Path $RepositoryRoot $script:ReleaseNotesDirectory
    $Pattern = '\Av' + [regex]::Escape($StableVersion) + '-dev\.(0|[1-9][0-9]*)\.md\z'
    if ([IO.Directory]::Exists($ReleaseDir)) {
        foreach ($Entry in Get-ChildItem -LiteralPath $ReleaseDir -File) {
            $Match = [regex]::Match(
                $Entry.Name,
                $Pattern,
                [Text.RegularExpressions.RegexOptions]::CultureInvariant
            )
            if (-not $Match.Success) {
                continue
            }
            $Version = $Entry.BaseName.Substring(1)
            $Fragments[$Entry.Name] = [pscustomobject]@{
                Name = $Entry.Name
                Version = $Version
                Counter = [bigint]::Parse(
                    $Match.Groups[1].Value,
                    [Globalization.CultureInfo]::InvariantCulture
                )
                Content = Get-GitLoopyUtf8FileContent `
                    -Path $Entry.FullName `
                    -Label "Release-note fragment"
            }
        }
    }
    foreach ($Fragment in $PendingFragments) {
        $Name = Split-Path -Leaf $Fragment.RelativePath
        $Match = [regex]::Match(
            $Name,
            $Pattern,
            [Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
        if (-not $Match.Success) {
            throw "pending Release-note fragment $($Fragment.RelativePath) does not match stable Release $StableVersion"
        }
        $Fragments[$Name] = [pscustomobject]@{
            Name = $Name
            Version = $Fragment.Version
            Counter = [bigint]::Parse(
                $Match.Groups[1].Value,
                [Globalization.CultureInfo]::InvariantCulture
            )
            Content = $Fragment.Content
        }
    }
    return @(
        $Fragments.Values |
            Sort-Object -Property @{ Expression = { $_.Counter } }, @{ Expression = { $_.Version } }
    )
}

function New-GitLoopyStableReleaseNotesContent {
    param(
        [Parameter(Mandatory)]
        [string]$Version,
        [Parameter(Mandatory)]
        [object[]]$Fragments
    )

    $Lines = [Collections.Generic.List[string]]::new()
    $Lines.Add("# git-loopy $Version")
    $Lines.Add("")
    $Lines.Add("git-loopy $Version was promoted from the committed development fragments below.")
    if ($Fragments.Count -eq 0) {
        $Lines.Add("")
        $Lines.Add("No development fragments were available when this stable draft was composed.")
        return ($Lines.ToArray() -join "`n") + "`n"
    }
    $Lines.Add("")
    $Lines.Add("## Development fragments")
    foreach ($Fragment in $Fragments) {
        $Lines.Add("")
        $Lines.Add("### $($Fragment.Version)")
        $Lines.Add("")
        $Body = Get-GitLoopyReleaseNotesBody -Content $Fragment.Content
        if ([string]::IsNullOrWhiteSpace($Body)) {
            $Lines.Add("No additional notes were recorded in this fragment.")
            continue
        }
        foreach ($Line in ($Body -split "`n")) {
            $Lines.Add($Line)
        }
    }
    return ($Lines.ToArray() -join "`n") + "`n"
}

function New-GitLoopyFileSnapshot {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$RelativePath
    )

    return [pscustomobject]@{
        Path = $Path
        RelativePath = $RelativePath
        Existed = [IO.File]::Exists($Path)
        Content = if ([IO.File]::Exists($Path)) {
            Get-GitLoopyUtf8FileContent -Path $Path -Label "Release-managed file"
        }
        else {
            $null
        }
    }
}

function Get-GitLoopyRepositoryReleaseNotesUpdate {
    param(
        [Parameter(Mandatory)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory)]
        [object]$AdvancedLine,
        [Parameter(Mandatory)]
        [object]$ReleaseLine
    )

    $Root = [IO.Path]::GetFullPath($RepositoryRoot)
    $Updates = [Collections.Generic.List[object]]::new()
    $Snapshots = [Collections.Generic.List[object]]::new()
    $RelativePaths = [Collections.Generic.List[string]]::new()

    $FragmentRelativePath = Get-GitLoopyReleaseNotesRelativePath -Version $AdvancedLine.Version
    $FragmentPath = Join-Path $Root $FragmentRelativePath
    $RelativePaths.Add($FragmentRelativePath)
    $CurrentFragment = if ([IO.File]::Exists($FragmentPath)) {
        Get-GitLoopyUtf8FileContent -Path $FragmentPath -Label "Release-note fragment"
    }
    else {
        $null
    }
    $FragmentContent = if ($null -eq $CurrentFragment) {
        New-GitLoopyReleaseLineFragmentContent `
            -Version $AdvancedLine.Version `
            -Target $AdvancedLine.Target
    }
    else {
        $CurrentFragment
    }
    if ($null -eq $CurrentFragment) {
        $Updates.Add([pscustomobject]@{
            Path = $FragmentPath
            Content = $FragmentContent
        })
        $Snapshots.Add((New-GitLoopyFileSnapshot -Path $FragmentPath -RelativePath $FragmentRelativePath))
    }
    $PendingFragments = @([pscustomobject]@{
        RelativePath = $FragmentRelativePath
        Version = $AdvancedLine.Version
        Content = $FragmentContent
    })

    if ($ReleaseLine.Counter -eq 0) {
        $StableRelativePath = Get-GitLoopyReleaseNotesRelativePath -Version $ReleaseLine.Version
        $StablePath = Join-Path $Root $StableRelativePath
        if (-not [IO.File]::Exists($StablePath)) {
            $StableContent = New-GitLoopyStableReleaseNotesContent `
                -Version $ReleaseLine.Version `
                -Fragments (Get-GitLoopyReleaseTargetFragments `
                    -RepositoryRoot $Root `
                    -StableVersion $ReleaseLine.Target `
                    -PendingFragments $PendingFragments)
            $Updates.Add([pscustomobject]@{
                Path = $StablePath
                Content = $StableContent
            })
            $Snapshots.Add((New-GitLoopyFileSnapshot -Path $StablePath -RelativePath $StableRelativePath))
        }
        # A human's draft must travel with its Promotion even though this writer
        # deliberately does not replace its content.
        $RelativePaths.Add($StableRelativePath)
    }

    return [pscustomobject]@{
        Updates = $Updates.ToArray()
        Snapshots = $Snapshots.ToArray()
        RelativePaths = $RelativePaths.ToArray()
    }
}

function Set-GitLoopyRepositoryReleaseNotes {
    param(
        [Parameter(Mandatory)]
        [object]$NoteUpdate
    )

    Set-GitLoopyAtomicFileUpdates `
        -Updates $NoteUpdate.Updates `
        -Label "Release notes"
}

function Restore-GitLoopyFileSnapshots {
    param(
        [Parameter(Mandatory)]
        [object[]]$Snapshots,
        [Parameter(Mandatory)]
        [string]$Label
    )

    if ($Snapshots.Count -eq 0) {
        return
    }

    $Updates = [Collections.Generic.List[object]]::new()
    foreach ($Snapshot in $Snapshots) {
        if (-not $Snapshot.Existed) {
            continue
        }
        $Updates.Add([pscustomobject]@{
            Path = $Snapshot.Path
            Content = $Snapshot.Content
        })
    }
    if ($Updates.Count -gt 0) {
        Set-GitLoopyAtomicFileUpdates -Updates $Updates.ToArray() -Label $Label
    }
    foreach ($Snapshot in $Snapshots) {
        if ($Snapshot.Existed -or -not [IO.File]::Exists($Snapshot.Path)) {
            continue
        }
        Remove-Item -LiteralPath $Snapshot.Path -Force
    }
}

function Initialize-GitLoopyReleaseLine {
    param(
        [Parameter(Mandatory)]
        [string]$RepositoryRoot
    )

    if ($script:ReleaseLineInitialized) {
        return
    }
    $CurrentVersion = Get-GitLoopyReleaseVersion -Path (Join-Path $RepositoryRoot "VERSION")
    $Match = [regex]::Match(
        $CurrentVersion,
        '\A((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))(?:-dev\.(0|[1-9][0-9]*))?\z',
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    if (-not $Match.Success) {
        throw "Release line must be a stable or -dev.N Semantic Versioning value"
    }
    $script:ReleaseTarget = $Match.Groups[1].Value
    $script:ReleaseCounter = if ($Match.Groups[2].Success) {
        [bigint]::Parse($Match.Groups[2].Value, [Globalization.CultureInfo]::InvariantCulture)
    }
    else {
        [bigint]0
    }
    if ($script:ReleaseCounter -eq 0) {
        $script:ReleaseLastStable = $script:ReleaseTarget
    }
    else {
        $Tags = @(& git -C $RepositoryRoot tag --merged HEAD --list "v*" --sort=-version:refname)
        $Tag = @(
            @($Tags | Where-Object {
                $_ -cmatch "^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
            }) | Select-Object -First 1
        )
        if ($Tag.Count -eq 0) {
            throw "a prerelease Release line requires a reachable stable Release tag"
        }
        $script:ReleaseLastStable = $Tag[0].Substring(1)
    }
    $script:ReleaseLineInitialized = $true
}

function Invoke-GitLoopyRepositoryReleaseLineAdvance {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory)]
        [string[]]$Labels
    )

    $BumpClass = Resolve-GitLoopyBumpClass -Labels $Labels
    if ($BumpClass -ceq "none") {
        return $null
    }
    Initialize-GitLoopyReleaseLine -RepositoryRoot $RepositoryRoot
    $AdvancedLine = Invoke-GitLoopyReleaseLineAdvance `
        -LastStableVersion $script:ReleaseLastStable `
        -CurrentTarget $script:ReleaseTarget `
        -CurrentCounter $script:ReleaseCounter `
        -BumpClass $BumpClass
    $NextLine = Invoke-GitLoopyReleaseLinePromotion `
        -ReleaseLine $AdvancedLine `
        -BumpClass $BumpClass
    $PreviousVersion = if ($script:ReleaseCounter -eq 0) {
        $script:ReleaseTarget
    }
    else {
        "$($script:ReleaseTarget)-dev.$($script:ReleaseCounter)"
    }
    $NoteUpdate = Get-GitLoopyRepositoryReleaseNotesUpdate `
        -RepositoryRoot $RepositoryRoot `
        -AdvancedLine $AdvancedLine `
        -ReleaseLine $NextLine
    $CommitPaths = @($script:ReleaseVersionPaths + $NoteUpdate.RelativePaths)
    try {
        Set-GitLoopyRepositoryReleaseVersion -RepositoryRoot $RepositoryRoot -Version $NextLine.Version
        Set-GitLoopyRepositoryReleaseNotes -NoteUpdate $NoteUpdate
        & git -C $RepositoryRoot add -- $CommitPaths | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Release metadata could not be staged"
        }
        & git -C $RepositoryRoot commit -m (
            Get-GitLoopyReleaseLineCommitSubject -Version $NextLine.Version
        ) -- $CommitPaths | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $script:ReleaseTarget = $NextLine.Target
            $script:ReleaseCounter = $NextLine.Counter
            if ($NextLine.Counter -eq 0) {
                # A Promotion is the new stable base the next issue ratchets
                # from, and its `dev.N` counter has already restarted at zero.
                $script:ReleaseLastStable = $NextLine.Target
            }
            return [pscustomobject]@{
                BumpClass = $BumpClass
                Target = $NextLine.Target
                Counter = $NextLine.Counter
                Version = $NextLine.Version
            }
        }
        throw "Release-line commit failed"
    }
    catch {
        $Cause = $_.Exception
        & git -C $RepositoryRoot reset -- $CommitPaths | Out-Null
        try {
            Set-GitLoopyRepositoryReleaseVersion `
                -RepositoryRoot $RepositoryRoot `
                -Version $PreviousVersion
            Restore-GitLoopyFileSnapshots `
                -Snapshots $NoteUpdate.Snapshots `
                -Label "Release notes"
        }
        catch {
            throw "Release-line advance failed and its prior Release line could not be restored: $($_.Exception.Message)"
        }
        throw "Release-line advance failed and the prior Release line was restored: $($Cause.Message)"
    }
}

Export-ModuleMember -Function @(
    "Get-GitLoopyReleaseVersion",
    "Resolve-GitLoopyBumpClass",
    "Invoke-GitLoopyReleaseLineAdvance",
    "Invoke-GitLoopyReleaseLinePromotion",
    "Get-GitLoopyReleaseLineCommitSubject",
    "Get-GitLoopyClosedMilestonePromotion",
    "Set-GitLoopyRepositoryReleaseVersion",
    "Invoke-GitLoopyRepositoryReleaseLineAdvance"
)
