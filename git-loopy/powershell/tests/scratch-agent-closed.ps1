Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$Entrypoint = Join-Path $PortDir "git-loopy.ps1"
$OrchestratorModule = Join-Path $PortDir "GitLoopy.Orchestrator.psm1"
Import-Module $OrchestratorModule -Force
$ReleaseFixturePath = Join-Path (
    Split-Path -Parent $PortDir
) "conformance/release-version.json"
$ReleaseFixture = Get-Content -LiteralPath $ReleaseFixturePath -Raw |
    ConvertFrom-Json -AsHashtable -DateKind String
$Pwsh = (
    Get-Command pwsh -CommandType Application |
        Select-Object -First 1
).Source

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

function Remove-TestDirectory {
    param([Parameter(Mandatory)][string]$Path)

    $Deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ([IO.Directory]::Exists($Path)) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $Deadline) {
                throw
            }
            Start-Sleep -Milliseconds 100
        }
        catch [UnauthorizedAccessException] {
            if ([DateTime]::UtcNow -ge $Deadline) {
                throw
            }
            Start-Sleep -Milliseconds 100
        }
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

function Assert-Contains {
    param(
        [Parameter(Mandatory)]
        [string]$Text,
        [Parameter(Mandatory)]
        [string]$Needle,
        [Parameter(Mandatory)]
        [string]$Description
    )

    if (-not $Text.Contains($Needle, [StringComparison]::Ordinal)) {
        throw "FAIL: $Description`nmissing: $Needle`nactual:  $Text"
    }
}

function Write-FakeCommand {
    param(
        [Parameter(Mandatory)]
        [string]$BinDir,
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$Body,
        [switch]$DirectPowerShell
    )

    if ($IsWindows) {
        $ScriptPath = Join-Path $BinDir "$Name-fake.ps1"
        [IO.File]::WriteAllText(
            $ScriptPath,
            $Body,
            [Text.UTF8Encoding]::new($false)
        )
        if ($DirectPowerShell) {
            [IO.File]::Move(
                $ScriptPath,
                (Join-Path $BinDir "$Name.ps1"),
                $true
            )
            return
        }
        $LauncherPath = Join-Path $BinDir "$Name.cmd"
        $Launcher = "@pwsh -NoLogo -NoProfile -File `"%~dp0$Name-fake.ps1`" %*`r`n"
        [IO.File]::WriteAllText(
            $LauncherPath,
            $Launcher,
            [Text.ASCIIEncoding]::new()
        )
        return
    }

    $CommandPath = Join-Path $BinDir $Name
    $Script = "#!/usr/bin/env pwsh`n" + $Body
    [IO.File]::WriteAllText(
        $CommandPath,
        $Script,
        [Text.UTF8Encoding]::new($false)
    )
    & chmod +x $CommandPath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not make fake $Name executable."
    }
}

function Write-FakeTools {
    param(
        [Parameter(Mandatory)]
        [string]$BinDir
    )

    [IO.Directory]::CreateDirectory($BinDir) | Out-Null
    Write-FakeCommand -BinDir $BinDir -Name "git" -Body @'
$ErrorActionPreference = "Stop"
if (($args -join " ") -ceq "rev-parse --show-toplevel") {
    [Console]::Out.WriteLine($env:FAKE_REPO_ROOT)
    exit 0
}
[Console]::Error.WriteLine("unexpected git invocation: " + ($args -join " "))
exit 90
'@
    Write-FakeCommand -BinDir $BinDir -Name "copilot" -DirectPowerShell -Body @'
[Console]::Error.WriteLine("copilot must not run in the discovery slice")
$Status = 91
if ($IsWindows) {
    $global:LASTEXITCODE = $Status
    return
}
exit $Status
'@
    Write-FakeCommand -BinDir $BinDir -Name "gh" -Body @'
$ErrorActionPreference = "Stop"
[IO.File]::AppendAllText(
    $env:FAKE_GH_LOG,
    ($args -join " ") + [Environment]::NewLine
)
$Command = if ($args.Count -ge 2) { "$($args[0]) $($args[1])" } else { "" }
switch -CaseSensitive ($Command) {
    "auth status" {
        exit $(if ($env:FAKE_GH_AUTH_STATUS) {
            [int]$env:FAKE_GH_AUTH_STATUS
        } else {
            0
        })
    }
    "repo view" {
        [Console]::Out.WriteLine(
            '{"owner":{"login":"example"},"name":"repo","defaultBranchRef":{"name":"main"}}'
        )
        exit 0
    }
    "issue list" {
        $Count = if ([IO.File]::Exists($env:FAKE_GH_LIST_COUNT)) {
            [int][IO.File]::ReadAllText($env:FAKE_GH_LIST_COUNT)
        } else {
            0
        }
        [IO.File]::WriteAllText(
            $env:FAKE_GH_LIST_COUNT,
            [string]($Count + 1)
        )
        [Console]::Out.Write([IO.File]::ReadAllText($env:FAKE_GH_LIST_JSON))
        exit 0
    }
    "issue view" {
        $ViewPath = Join-Path $env:FAKE_GH_VIEW_DIR "$($args[2]).json"
        [Console]::Out.Write([IO.File]::ReadAllText($ViewPath))
        exit 0
    }
    default {
        [Console]::Error.WriteLine(
            "unexpected gh invocation: " + ($args -join " ")
        )
        exit 92
    }
}
'@
}

function New-TestRepo {
    param(
        [Parameter(Mandatory)]
        [string]$Root
    )

    [IO.Directory]::CreateDirectory((Join-Path $Root "docs/agents")) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $Root "git-loopy")) | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $Root "docs/agents/issue-tracker.md"),
        "# Issue tracker`n"
    )
    [IO.File]::WriteAllText(
        (Join-Path $Root "git-loopy/PROMPT.md"),
        "# Project prompt`n"
    )
}

# Turn scenarios exercise the real Copilot turn, so they run against a real git
# repository (real HEAD, commits_between, and recent-commit rendering) with only
# `gh` and `copilot` faked. `Write-TurnTools` deliberately ships no fake `git`,
# and the entry point keeps the inherited PATH so the real `git` resolves.
function Write-TurnTools {
    param(
        [Parameter(Mandatory)]
        [string]$BinDir
    )

    [IO.Directory]::CreateDirectory($BinDir) | Out-Null
    Write-FakeCommand -BinDir $BinDir -Name "copilot" -DirectPowerShell -Body @'
$ErrorActionPreference = "Stop"
$Prompt = ""
$Capture = $false
$Flags = [Collections.Generic.List[string]]::new()
foreach ($Arg in $args) {
    if ($Capture) {
        $Prompt = [string]$Arg
        $Capture = $false
        continue
    }
    if ([string]$Arg -ceq "-p") {
        $Capture = $true
        continue
    }
    $Flags.Add([string]$Arg)
}
[IO.File]::WriteAllText(
    $env:FAKE_COPILOT_FLAGS,
    ($Flags -join "`n") + "`n"
)
[IO.File]::WriteAllText($env:FAKE_COPILOT_PROMPT, $Prompt)
$Calls = 0
if ([IO.File]::Exists($env:FAKE_COPILOT_CALLS)) {
    $Calls = [int][IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
}
[IO.File]::WriteAllText($env:FAKE_COPILOT_CALLS, [string]($Calls + 1))
# Emit on stdout to prove native CLI text remains human-readable on stderr and
# is also represented as unclassified Events.
if ($env:FAKE_COPILOT_OUTPUT_FILE) {
    foreach ($Line in [IO.File]::ReadAllLines($env:FAKE_COPILOT_OUTPUT_FILE)) {
        Write-Output $Line
    }
} else {
    Write-Output "copilot agent stream marker"
}
if ($env:FAKE_COPILOT_STDERR_FILE) {
    foreach ($Line in [IO.File]::ReadAllLines($env:FAKE_COPILOT_STDERR_FILE)) {
        [Console]::Error.WriteLine($Line)
    }
}
# A per-call commit plan (opt-in) lets a scenario vary commit messages across
# Iterations — each `<call>/<n>.msg` file is one commit's full message, read via
# `-F` so multi-line close-keyword bodies survive. Falling back to the simple
# empty-commit count keeps every existing turn scenario unchanged.
$CurrentCall = $Calls + 1
if ($env:FAKE_COPILOT_PLAN_DIR) {
    $CallDir = Join-Path $env:FAKE_COPILOT_PLAN_DIR ([string]$CurrentCall)
    if ([IO.Directory]::Exists($CallDir)) {
        foreach ($MsgFile in ([IO.Directory]::GetFiles($CallDir, "*.msg") | Sort-Object)) {
            & git commit -q --allow-empty -F $MsgFile
        }
        # An optional per-call `worktree.ps1` hook runs in the repo root so a
        # scenario can leave the tree dirty/untracked/ignored exactly like a real
        # agent that forgot to commit — the Checkpoint durability net (ADR-0004)
        # is what captures it.
        $WorktreeScript = Join-Path $CallDir "worktree.ps1"
        if ([IO.File]::Exists($WorktreeScript)) {
            Push-Location $env:FAKE_REPO_ROOT
            try { & $WorktreeScript } finally { Pop-Location }
        }
    }
} else {
    $Commits = if ($env:FAKE_COPILOT_COMMITS) { [int]$env:FAKE_COPILOT_COMMITS } else { 0 }
    for ($i = 0; $i -lt $Commits; $i++) {
        & git commit -q --allow-empty -m "agent: work $($i + 1)"
    }
}
$Status = if ($env:FAKE_COPILOT_EXIT) { [int]$env:FAKE_COPILOT_EXIT } else { 0 }
if ($IsWindows) {
    $global:LASTEXITCODE = $Status
    return
}
exit $Status
'@
    Write-FakeCommand -BinDir $BinDir -Name "gh" -DirectPowerShell -Body @'
$ErrorActionPreference = "Stop"
function Complete-FakeCommand {
    param([Parameter(Mandatory)][int]$Status)
    if ($IsWindows) {
        $global:LASTEXITCODE = $Status
        return
    }
    exit $Status
}
[IO.File]::AppendAllText(
    $env:FAKE_GH_LOG,
    ($args -join " ") + [Environment]::NewLine
)
$Command = if ($args.Count -ge 2) { "$($args[0]) $($args[1])" } else { "" }
switch -CaseSensitive ($Command) {
    "auth status" {
        $Status = if ($env:FAKE_GH_AUTH_STATUS) {
            [int]$env:FAKE_GH_AUTH_STATUS
        } else {
            0
        }
        Complete-FakeCommand $Status
        return
    }
    "repo view" {
        Write-Output (
            '{"owner":{"login":"example"},"name":"repo","defaultBranchRef":{"name":"main"}}'
        )
        Complete-FakeCommand 0
        return
    }
    "issue list" {
        $Count = if ([IO.File]::Exists($env:FAKE_GH_LIST_COUNT)) {
            [int][IO.File]::ReadAllText($env:FAKE_GH_LIST_COUNT)
        } else {
            0
        }
        $Count = $Count + 1
        [IO.File]::WriteAllText($env:FAKE_GH_LIST_COUNT, [string]$Count)
        if ($env:FAKE_GH_EMPTY_AFTER -and
            ($Count -gt [int]$env:FAKE_GH_EMPTY_AFTER)) {
            Write-Output "[]"
        } else {
            Write-Output ([IO.File]::ReadAllText($env:FAKE_GH_LIST_JSON))
        }
        Complete-FakeCommand 0
        return
    }
    "issue view" {
        $ViewPath = Join-Path $env:FAKE_GH_VIEW_DIR "$($args[2]).json"
        Write-Output ([IO.File]::ReadAllText($ViewPath))
        Complete-FakeCommand 0
        return
    }
    "issue close" {
        if ($env:FAKE_GH_CLOSE_STATUS) {
            Complete-FakeCommand ([int]$env:FAKE_GH_CLOSE_STATUS)
            return
        }
        # Record the auto-closure: the issue number (one per line) and the
        # wrap-up comment, so a scenario can assert which Pool issues the loop
        # closed and which commit SHAs the comment attributed.
        [IO.File]::AppendAllText(
            $env:FAKE_GH_CLOSED,
            [string]$args[2] + [Environment]::NewLine
        )
        if ($env:FAKE_GH_CLOSE_DIR) {
            [IO.Directory]::CreateDirectory($env:FAKE_GH_CLOSE_DIR) | Out-Null
            [IO.File]::WriteAllText(
                (Join-Path $env:FAKE_GH_CLOSE_DIR "$($args[2]).comment"),
                [string]$args[4]
            )
        }
        Complete-FakeCommand 0
        return
    }
    default {
        [Console]::Error.WriteLine(
            "unexpected gh invocation: " + ($args -join " ")
        )
        Complete-FakeCommand 92
        return
    }
}
'@
}

function New-RealTestRepo {
    param(
        [Parameter(Mandatory)]
        [string]$Root
    )

    New-TestRepo -Root $Root
    & git -C $Root init -q
    & git -C $Root config user.email "tester@example.invalid"
    & git -C $Root config user.name "Test Runner"
    # A realistic project ignores the runner's own `.git-loopy/` artefacts, so the
    # replay log never trips the Checkpoint dirty-check. Commit every scaffolding
    # file too, so a clean-tree scenario starts genuinely clean.
    [IO.File]::WriteAllText((Join-Path $Root ".gitignore"), ".git-loopy/`n")
    & git -C $Root add -A
    & git -C $Root commit -q -m "initial commit"
}

# Give a real repo a bare upstream so the ADR-0004 auto-push has somewhere to go.
# `push -u origin HEAD` seeds the remote and sets the branch's upstream tracking
# ref, so a later bare `git push` from the Orchestrator fast-forwards it.
function Add-FakeRemote {
    param(
        [Parameter(Mandatory)]
        [string]$Root,
        [Parameter(Mandatory)]
        [string]$Remote
    )

    & git init --bare -q $Remote
    & git -C $Root remote add origin $Remote
    & git -C $Root push -q -u origin HEAD
}

function Set-CopilotEnv {
    param(
        [Parameter(Mandatory)]
        [string]$Prefix
    )

    foreach ($Suffix in @("flags", "prompt", "calls")) {
        $Path = Join-Path $TempDir "$Prefix-copilot.$Suffix"
        if ([IO.File]::Exists($Path)) {
            [IO.File]::Delete($Path)
        }
    }
    $env:FAKE_COPILOT_FLAGS = Join-Path $TempDir "$Prefix-copilot.flags"
    $env:FAKE_COPILOT_PROMPT = Join-Path $TempDir "$Prefix-copilot.prompt"
    $env:FAKE_COPILOT_CALLS = Join-Path $TempDir "$Prefix-copilot.calls"
}

function Invoke-Entrypoint {
    param(
        [Parameter(Mandatory)]
        [string]$Repo,
        [Parameter(Mandatory)]
        [string]$FakeBin,
        [Parameter(Mandatory)]
        [string]$StdoutPath,
        [Parameter(Mandatory)]
        [string]$StderrPath,
        [string[]]$Arguments = @()
    )

    $OldPath = $env:PATH
    $OldHome = $env:HOME
    $OldXdg = $env:XDG_CONFIG_HOME
    $OldRepoRoot = $env:FAKE_REPO_ROOT
    $OriginalLocation = Get-Location
    try {
        $env:PATH = $FakeBin + [IO.Path]::PathSeparator + $OldPath
        # Keep HOME/XDG outside the worktree: pwsh writes a startup-profile cache
        # under HOME, which would otherwise show up as untracked work and trip the
        # ADR-0004 Checkpoint dirty-check. Empty scratch dirs still isolate the
        # global-prompt lookup exactly as before.
        $env:HOME = "$Repo-home"
        $env:XDG_CONFIG_HOME = "$Repo-xdg"
        $env:FAKE_REPO_ROOT = $Repo
        Set-Location $Repo
        & $Pwsh `
            -NoLogo `
            -NoProfile `
            -File $Entrypoint `
            @Arguments 1> $StdoutPath 2> $StderrPath
        return $LASTEXITCODE
    }
    finally {
        Set-Location $OriginalLocation
        $env:PATH = $OldPath
        $env:HOME = $OldHome
        $env:XDG_CONFIG_HOME = $OldXdg
        $env:FAKE_REPO_ROOT = $OldRepoRoot
    }
}

function Invoke-VersionEntrypoint {
    param(
        [Parameter(Mandatory)]
        [string]$RuntimeEntrypoint,
        [Parameter(Mandatory)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory)]
        [string]$FakeBin,
        [Parameter(Mandatory)]
        [string]$ToolLog,
        [Parameter(Mandatory)]
        [string]$StdoutPath,
        [Parameter(Mandatory)]
        [string]$StderrPath,
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$Arguments
    )

    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Pwsh
    $StartInfo.WorkingDirectory = $WorkingDirectory
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.Environment["PATH"] = (
        $FakeBin + [IO.Path]::PathSeparator + $env:PATH
    )
    $ScratchRoot = Split-Path -Parent $WorkingDirectory
    $StartInfo.Environment["HOME"] = Join-Path $ScratchRoot "version-home"
    $StartInfo.Environment["XDG_CONFIG_HOME"] = Join-Path $ScratchRoot "version-config"
    $StartInfo.Environment["GIT_LOOPY_ISSUE_SOURCE"] = "unavailable"
    $StartInfo.Environment["GIT_LOOPY_MAX_NMT_STRIKES"] = "not-an-integer"
    $StartInfo.Environment["VERSION_TOOL_LOG"] = $ToolLog
    foreach ($Argument in @(
        "-NoLogo", "-NoProfile", "-File", $RuntimeEntrypoint
    )) {
        $StartInfo.ArgumentList.Add($Argument)
    }
    foreach ($Argument in $Arguments) {
        $StartInfo.ArgumentList.Add($Argument)
    }

    $Process = [Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    Assert-True ($Process.Start()) "Release version process starts"
    $Stdout = $Process.StandardOutput.ReadToEnd()
    $Stderr = $Process.StandardError.ReadToEnd()
    $Process.WaitForExit()
    [IO.File]::WriteAllText($StdoutPath, $Stdout)
    [IO.File]::WriteAllText($StderrPath, $Stderr)
    return $Process.ExitCode
}

function Read-Events {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    return @(
        [IO.File]::ReadAllLines($Path) |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json -AsHashtable }
    )
}

$TempDir = Join-Path ([IO.Path]::GetTempPath()) (
    "git-loopy-pwsh-boundary-$([guid]::NewGuid())"
)
[IO.Directory]::CreateDirectory($TempDir) | Out-Null

try {
    $VersionRuntime = Join-Path $TempDir "version-runtime"
    $VersionPort = Join-Path $VersionRuntime "git-loopy/powershell"
    $VersionOutside = Join-Path $TempDir "version-outside"
    $VersionBin = Join-Path $TempDir "version-bin"
    $VersionToolLog = Join-Path $TempDir "version-tools.log"
    [IO.Directory]::CreateDirectory(
        (Split-Path -Parent $VersionPort)
    ) | Out-Null
    Copy-Item -LiteralPath $PortDir -Destination $VersionPort -Recurse
    [IO.Directory]::CreateDirectory($VersionOutside) | Out-Null
    [IO.Directory]::CreateDirectory($VersionBin) | Out-Null
    foreach ($Tool in @("git", "gh", "copilot")) {
        Write-FakeCommand -BinDir $VersionBin -Name $Tool -Body @'
[IO.File]::AppendAllText(
    $env:VERSION_TOOL_LOG,
    [IO.Path]::GetFileNameWithoutExtension($PSCommandPath) + [Environment]::NewLine
)
exit 97
'@
    }
    $VersionEntrypoint = Join-Path $VersionPort "git-loopy.ps1"
    $VersionPath = Join-Path $VersionRuntime "VERSION"
    $ExpectedReleaseVersion = [string]$ReleaseFixture["expected_release_version"]
    [IO.File]::WriteAllText(
        $VersionPath,
        "$ExpectedReleaseVersion`n",
        [Text.UTF8Encoding]::new($false)
    )

    $VersionStdout = Join-Path $TempDir "version.stdout"
    $VersionStderr = Join-Path $TempDir "version.stderr"
    $Status = Invoke-VersionEntrypoint `
        -RuntimeEntrypoint $VersionEntrypoint `
        -WorkingDirectory $VersionOutside `
        -FakeBin $VersionBin `
        -ToolLog $VersionToolLog `
        -StdoutPath $VersionStdout `
        -StderrPath $VersionStderr `
        -Arguments @("--version")
    Assert-Equal 0 $Status "Release version exit"
    Assert-Equal (
        "git-loopy $ExpectedReleaseVersion$([Environment]::NewLine)"
    ) ([IO.File]::ReadAllText($VersionStdout)) "Release version stdout"
    Assert-Equal 0 (
        [IO.File]::ReadAllText($VersionStderr).Length
    ) "Release version wrote to stderr"
    Assert-True (-not [IO.File]::Exists($VersionToolLog)) (
        "Release version invoked no Run dependency"
    )
    Assert-Equal 0 (
        @(Get-ChildItem -LiteralPath $VersionOutside -Force).Count
    ) "Release version created no Run artifacts"

    foreach ($Case in $ReleaseFixture["valid_versions"]) {
        [IO.File]::WriteAllText(
            $VersionPath,
            "$($Case["value"])`n",
            [Text.UTF8Encoding]::new($false)
        )
        $CaseStdout = Join-Path $TempDir "version-$($Case["id"]).stdout"
        $CaseStderr = Join-Path $TempDir "version-$($Case["id"]).stderr"
        $Status = Invoke-VersionEntrypoint `
            -RuntimeEntrypoint $VersionEntrypoint `
            -WorkingDirectory $VersionOutside `
            -FakeBin $VersionBin `
            -ToolLog $VersionToolLog `
            -StdoutPath $CaseStdout `
            -StderrPath $CaseStderr `
            -Arguments @("--version")
        Assert-Equal 0 $Status "valid Release version exit: $($Case["id"])"
        Assert-Equal (
            "git-loopy $($Case["value"])$([Environment]::NewLine)"
        ) ([IO.File]::ReadAllText($CaseStdout)) (
            "valid Release version stdout: $($Case["id"])"
        )
        Assert-Equal 0 ([IO.File]::ReadAllText($CaseStderr).Length) (
            "valid Release version wrote no stderr: $($Case["id"])"
        )
    }

    foreach ($Case in $ReleaseFixture["invalid_versions"]) {
        [IO.File]::WriteAllText(
            $VersionPath,
            [string]$Case["value"],
            [Text.UTF8Encoding]::new($false)
        )
        $CaseStdout = Join-Path $TempDir "version-$($Case["id"]).stdout"
        $CaseStderr = Join-Path $TempDir "version-$($Case["id"]).stderr"
        $Status = Invoke-VersionEntrypoint `
            -RuntimeEntrypoint $VersionEntrypoint `
            -WorkingDirectory $VersionOutside `
            -FakeBin $VersionBin `
            -ToolLog $VersionToolLog `
            -StdoutPath $CaseStdout `
            -StderrPath $CaseStderr `
            -Arguments @("--version")
        Assert-True ($Status -ne 0) (
            "malformed Release version was rejected: $($Case["id"])"
        )
        Assert-Equal 0 ([IO.File]::ReadAllText($CaseStdout).Length) (
            "malformed Release version wrote no stdout: $($Case["id"])"
        )
        $Diagnostic = [IO.File]::ReadAllText($CaseStderr)
        Assert-True ($Diagnostic.Length -gt 0) (
            "malformed Release version failed visibly: $($Case["id"])"
        )
        Assert-True (-not $Diagnostic.Contains("unknown")) (
            "malformed Release version did not report unknown: $($Case["id"])"
        )
    }

    $InvalidAuthorityCases = @(
        $ReleaseFixture["invalid_authority_inputs"]
        [ordered]@{
            id = "utf16-bom"
            kind = "utf16_bom"
        }
    )
    foreach ($Case in $InvalidAuthorityCases) {
        Remove-Item -LiteralPath $VersionPath -Recurse -Force -ErrorAction SilentlyContinue
        switch -CaseSensitive ($Case["kind"]) {
            "missing" {}
            "directory" {
                [IO.Directory]::CreateDirectory($VersionPath) | Out-Null
            }
            "invalid_utf8" {
                [IO.File]::WriteAllBytes($VersionPath, [byte[]]@(0xff, 0x0a))
            }
            "utf16_bom" {
                [IO.File]::WriteAllBytes(
                    $VersionPath,
                    [byte[]]@(
                        0xff, 0xfe, 0x31, 0x00, 0x2e, 0x00, 0x32, 0x00,
                        0x2e, 0x00, 0x33, 0x00, 0x0a, 0x00
                    )
                )
            }
            default {
                throw "Unsupported Release authority fixture kind: $($Case["kind"])"
            }
        }
        $CaseStdout = Join-Path $TempDir "authority-$($Case["id"]).stdout"
        $CaseStderr = Join-Path $TempDir "authority-$($Case["id"]).stderr"
        $Status = Invoke-VersionEntrypoint `
            -RuntimeEntrypoint $VersionEntrypoint `
            -WorkingDirectory $VersionOutside `
            -FakeBin $VersionBin `
            -ToolLog $VersionToolLog `
            -StdoutPath $CaseStdout `
            -StderrPath $CaseStderr `
            -Arguments @("--version")
        Assert-True ($Status -ne 0) (
            "invalid Release metadata was rejected: $($Case["id"])"
        )
        Assert-Equal 0 ([IO.File]::ReadAllText($CaseStdout).Length) (
            "invalid Release metadata wrote no stdout: $($Case["id"])"
        )
        $Diagnostic = [IO.File]::ReadAllText($CaseStderr)
        Assert-True ($Diagnostic.Length -gt 0) (
            "invalid Release metadata failed visibly: $($Case["id"])"
        )
        Assert-True (-not $Diagnostic.Contains("unknown")) (
            "invalid Release metadata did not report unknown: $($Case["id"])"
        )
    }

    Remove-Item -LiteralPath $VersionPath -Recurse -Force -ErrorAction SilentlyContinue
    $CapabilitiesStdout = Join-Path $TempDir "capabilities-missing.stdout"
    $CapabilitiesStderr = Join-Path $TempDir "capabilities-missing.stderr"
    $Status = Invoke-VersionEntrypoint `
        -RuntimeEntrypoint $VersionEntrypoint `
        -WorkingDirectory $VersionOutside `
        -FakeBin $VersionBin `
        -ToolLog $VersionToolLog `
        -StdoutPath $CapabilitiesStdout `
        -StderrPath $CapabilitiesStderr `
        -Arguments @("continuation", "capabilities")
    Assert-True ($Status -ne 0) (
        "Continuation capabilities rejected missing Release metadata"
    )
    Assert-Equal 0 ([IO.File]::ReadAllText($CapabilitiesStdout).Length) (
        "Continuation capabilities wrote no success without Release metadata"
    )
    Assert-Contains (
        [IO.File]::ReadAllText($CapabilitiesStderr)
    ) "cannot read Release" "Continuation Release metadata diagnostic"
    [IO.File]::WriteAllText(
        $VersionPath,
        "$ExpectedReleaseVersion`n",
        [Text.UTF8Encoding]::new($false)
    )

    $HelpStdout = Join-Path $TempDir "help.stdout"
    $HelpStderr = Join-Path $TempDir "help.stderr"
    & $Pwsh `
        -NoLogo `
        -NoProfile `
        -File $Entrypoint `
        --help 1> $HelpStdout 2> $HelpStderr
    Assert-Equal 0 $LASTEXITCODE "help exit"
    Assert-Contains (
        [IO.File]::ReadAllText($HelpStdout)
    ) "Usage:" "help stdout"
    Assert-Equal 0 (
        [IO.File]::ReadAllText($HelpStderr).Length
    ) "help wrote to stderr"

    $EmptyRepo = Join-Path $TempDir "empty"
    $EmptyBin = Join-Path $TempDir "empty-bin"
    New-TestRepo -Root $EmptyRepo
    Write-FakeTools -BinDir $EmptyBin
    $EmptyList = Join-Path $TempDir "empty-list.json"
    $EmptyViews = Join-Path $TempDir "empty-views"
    [IO.File]::WriteAllText($EmptyList, "[]`n")
    [IO.Directory]::CreateDirectory($EmptyViews) | Out-Null
    $env:FAKE_GH_LOG = Join-Path $TempDir "empty-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "empty-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews

    $EmptyStdout = Join-Path $TempDir "empty.stdout"
    $EmptyStderr = Join-Path $TempDir "empty.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $EmptyRepo `
        -FakeBin $EmptyBin `
        -StdoutPath $EmptyStdout `
        -StderrPath $EmptyStderr
    Assert-Equal 0 $Status "empty GitHub Pool exit"

    $Events = Read-Events -Path $EmptyStdout
    Assert-Equal (
        "wrapper.run.start,wrapper.iteration.start," +
        "wrapper.afk_ready.collected,wrapper.iteration.end,wrapper.run.end"
    ) ([string]::Join(",", @($Events | ForEach-Object { $_["type"] }))) (
        "empty-Pool event sequence"
    )
    Assert-Equal "github" $Events[0]["issue_source"] "Run issue source"
    Assert-Equal (
        $ExpectedReleaseVersion
    ) $Events[0]["release_version"] "Run Release version"
    Assert-Equal 1 $Events[0]["schema_version"] "Run Event-schema version"
    $ExpectedInsightCapabilities = [ordered]@{
        agent_output = $true
        structured_agent_events = $false
        token_usage = $false
        context_window = $false
        skill_consultation = $false
        cost = $false
    }
    foreach ($Name in $ExpectedInsightCapabilities.Keys) {
        Assert-True (
            $Events[0]["insight_capabilities"].Contains($Name)
        ) "Run Insight capability $Name is declared"
        Assert-Equal (
            $ExpectedInsightCapabilities[$Name]
        ) $Events[0]["insight_capabilities"][$Name] (
            "Run Insight capability $Name"
        )
    }
    Assert-Equal 0 $Events[2]["issues"].Count "empty collected Pool"
    Assert-Equal "no_progress" $Events[3]["outcome"] "empty Iteration outcome"
    Assert-True (
        $Events[3]["duration_seconds"] -is [double] -or
        $Events[3]["duration_seconds"] -is [long]
    ) "empty Iteration duration is numeric"
    Assert-True (
        $Events[3]["duration_seconds"] -ge 0
    ) "empty Iteration duration is non-negative"
    Assert-Equal 0 $Events[3]["issues"].Count "empty Iteration issue contributions"
    Assert-Equal 0 $Events[3]["summary"]["commits"] "empty Iteration commits"
    Assert-Equal 0 $Events[3]["summary"]["auto_closures"] (
        "empty Iteration closures"
    )
    Assert-Equal 0 $Events[3]["summary"]["strikes"] "empty Iteration Strikes"
    foreach ($Name in @(
        "model",
        "tokens_in",
        "tokens_out",
        "observed_tokens",
        "cost_usd",
        "tool_count",
        "skill_call_count",
        "skills_consulted",
        "peak_context_window"
    )) {
        Assert-True (
            $null -eq $Events[3]["summary"][$Name]
        ) "empty Iteration unavailable $Name"
    }
    Assert-Equal "empty_pool" $Events[4]["outcome"] "empty Run outcome"
    Assert-Equal 1 $Events[4]["iterations_run"] "empty Run Iteration count"

    $ReplayFiles = @(
        Get-ChildItem `
            -LiteralPath (Join-Path $EmptyRepo ".git-loopy/logs") `
            -Filter "*.jsonl" `
            -File
    )
    Assert-Equal 1 $ReplayFiles.Count "empty Run replay file count"
    Assert-Equal (
        [IO.File]::ReadAllText($EmptyStdout)
    ) ([IO.File]::ReadAllText($ReplayFiles[0].FullName)) (
        "empty Run stream and replay parity"
    )
    $GhLog = [IO.File]::ReadAllText($env:FAKE_GH_LOG)
    Assert-Contains $GhLog "auth status" "GitHub auth preflight"
    Assert-Contains $GhLog "repo view" "GitHub repo preflight"
    Assert-Contains $GhLog "issue list" "GitHub Pool collection"

    $env:GIT_LOOPY_MODEL = "env-model"
    $env:GIT_LOOPY_REASONING_EFFORT = "high"
    $env:GIT_LOOPY_ISSUE_SOURCE = "prds"
    $env:GIT_LOOPY_MAX_NMT_STRIKES = "7"
    $env:GIT_LOOPY_DENY_TOOLS = "env-tool"
    $env:GIT_LOOPY_DENY_SKILLS = "env-skill"
    $env:GIT_LOOPY_SEND_TIMEOUT_SECONDS = "90"
    $EnvStdout = Join-Path $TempDir "env.stdout"
    $EnvStderr = Join-Path $TempDir "env.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $EmptyRepo `
        -FakeBin $EmptyBin `
        -StdoutPath $EnvStdout `
        -StderrPath $EnvStderr
    foreach ($Name in @(
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_ISSUE_SOURCE",
        "GIT_LOOPY_MAX_NMT_STRIKES",
        "GIT_LOOPY_DENY_TOOLS",
        "GIT_LOOPY_DENY_SKILLS",
        "GIT_LOOPY_SEND_TIMEOUT_SECONDS"
    )) {
        [Environment]::SetEnvironmentVariable($Name, $null)
    }
    Assert-Equal 0 $Status "environment-only Run exit"
    $EnvEvents = Read-Events -Path $EnvStdout
    Assert-Equal "env-model" $EnvEvents[0]["model"] "environment model"
    Assert-Equal "high" (
        $EnvEvents[0]["reasoning_effort"]
    ) "environment reasoning effort"
    Assert-Equal "prds" $EnvEvents[0]["issue_source"] "environment issue source"
    Assert-Equal 7 (
        $EnvEvents[0]["max_nmt_strikes"]
    ) "environment Strike threshold"
    Assert-Equal "env-tool" (
        [string]::Join(",", $EnvEvents[0]["deny_tools"])
    ) "environment tool denylist"
    Assert-Equal "env-skill" (
        [string]::Join(",", $EnvEvents[0]["deny_skills"])
    ) "environment skill denylist"
    Assert-Equal 90.0 (
        $EnvEvents[0]["send_timeout_seconds"]
    ) "environment send timeout"

    $CapRepo = Join-Path $TempDir "github-cap"
    $CapBin = Join-Path $TempDir "github-cap-bin"
    New-RealTestRepo -Root $CapRepo
    Write-TurnTools -BinDir $CapBin
    $CapList = Join-Path $TempDir "github-list.json"
    [IO.File]::WriteAllText($CapList, @'
[
  {
    "number": 41,
    "title": "Eligible",
    "body": "## What to build\nShip it.\n\n## Acceptance criteria\n- Done.",
    "labels": [{"name": "ready-for-agent"}],
    "state": "OPEN",
    "url": "https://example.invalid/issues/41"
  },
  {
    "number": 42,
    "title": "Bare planning issue",
    "body": "No required headings.",
    "labels": [{"name": "ready-for-agent"}],
    "state": "OPEN",
    "url": "https://example.invalid/issues/42"
  }
]
'@)
    $CapViews = Join-Path $TempDir "github-views"
    [IO.Directory]::CreateDirectory($CapViews) | Out-Null
    [IO.File]::WriteAllText((Join-Path $CapViews "41.json"), @'
{
  "number": 41,
  "title": "Eligible",
  "body": "## What to build\nShip it.\n\n## Acceptance criteria\n- Done.",
  "labels": [{"name": "ready-for-agent"}],
  "state": "OPEN",
  "url": "https://example.invalid/issues/41",
  "comments": [
    {
      "author": "maintainer",
      "body": "please prioritise",
      "createdAt": "2026-03-01T00:00:00Z"
    }
  ]
}
'@)
    $env:FAKE_GH_LOG = Join-Path $TempDir "github-cap-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "github-cap-list.count"
    $env:FAKE_GH_LIST_JSON = $CapList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    Set-CopilotEnv -Prefix "github-cap"
    $env:FAKE_COPILOT_COMMITS = "0"
    $CapAgentOutput = Join-Path $TempDir "github-cap-agent-output.txt"
    [IO.File]::WriteAllText(
        $CapAgentOutput,
        (
            "pre-marker output`n" +
            "<working issue=99>`n" +
            "<working issue=041>`n" +
            "<working issue=42>`n" +
            "post-marker output`n"
        ),
        [Text.UTF8Encoding]::new($false)
    )
    $env:FAKE_COPILOT_OUTPUT_FILE = $CapAgentOutput
    $CapAgentStderr = Join-Path $TempDir "github-cap-agent-stderr.txt"
    [IO.File]::WriteAllText(
        $CapAgentStderr,
        "native stderr chatter`n",
        [Text.UTF8Encoding]::new($false)
    )
    $env:FAKE_COPILOT_STDERR_FILE = $CapAgentStderr
    $env:GIT_LOOPY_MODEL = "env-model"
    $env:GIT_LOOPY_REASONING_EFFORT = "medium"
    $env:GIT_LOOPY_DENY_TOOLS = "env-tool"
    $env:GIT_LOOPY_DENY_SKILLS = "env-skill"

    $CapStdout = Join-Path $TempDir "github-cap.stdout"
    $CapStderr = Join-Path $TempDir "github-cap.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $CapRepo `
        -FakeBin $CapBin `
        -StdoutPath $CapStdout `
        -StderrPath $CapStderr `
        -Arguments @(
            "2",
            "--model", "cli-model",
            "--deny-tool", "cli-tool",
            "--deny-skill", "cli-skill"
        )
    foreach ($Name in @(
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_DENY_TOOLS",
        "GIT_LOOPY_DENY_SKILLS",
        "FAKE_COPILOT_COMMITS",
        "FAKE_COPILOT_OUTPUT_FILE",
        "FAKE_COPILOT_STDERR_FILE"
    )) {
        [Environment]::SetEnvironmentVariable($Name, $null)
    }
    Assert-Equal 0 $Status "bounded turn Run exit"
    Assert-Equal "2" (
        [IO.File]::ReadAllText($env:FAKE_GH_LIST_COUNT)
    ) "Pool is rebuilt each Iteration"
    Assert-Equal "2" (
        [IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
    ) "exactly one Copilot turn per non-empty Iteration"
    $CapEvents = Read-Events -Path $CapStdout
    $CollectionEvents = @(
        $CapEvents |
            Where-Object { $_["type"] -ceq "wrapper.afk_ready.collected" }
    )
    Assert-Equal 2 $CollectionEvents.Count "collection event count"
    foreach ($Event in $CollectionEvents) {
        Assert-Equal "41" (
            [string]::Join(",", $Event["issues"])
        ) "filtered Pool refs"
    }
    Assert-Equal "iteration_cap" $CapEvents[-1]["outcome"] "bounded Run outcome"
    Assert-Equal 2 $CapEvents[-1]["iterations_run"] "bounded Iteration count"
    Assert-Equal 0 (
        @(
            $CapEvents |
                Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }
        ).Count
    ) "a turn with no new commits records no commit events"
    Assert-True (
        -not ([IO.File]::ReadAllText($env:FAKE_GH_LOG)).Contains(
            "issue view 42 ",
            [StringComparison]::Ordinal
        )
    ) "ineligible issue was enriched after the cheap discriminator pass"

    # The Iteration assembled the Python-reference minimum context: last-5
    # commits, the filtered Pool block (with recent comments), and the prompt.
    $CapPrompt = [IO.File]::ReadAllText($env:FAKE_COPILOT_PROMPT)
    Assert-Contains $CapPrompt "Previous commits: " "prompt carries the commits prefix"
    Assert-Contains $CapPrompt "initial commit" "prompt carries recent commit subjects"
    Assert-Contains $CapPrompt (
        "=== Issue #41: Eligible [labels: ready-for-agent] ==="
    ) "prompt carries the filtered issue block"
    Assert-Contains $CapPrompt (
        "--- Recent comments (newest first, up to 5) ---"
    ) "prompt carries recent comments"
    Assert-Contains $CapPrompt (
        "[2026-03-01T00:00:00Z @maintainer] please prioritise"
    ) "recent comments keep the raw ISO timestamp (no locale datetime coercion)"
    Assert-Contains $CapPrompt "please prioritise" "prompt carries comment bodies"
    Assert-Contains $CapPrompt "# Project prompt" (
        "prompt carries the resolved shared prompt"
    )

    # Resolved settings honor CLI-over-environment-over-default precedence.
    $CapFlags = [IO.File]::ReadAllText($env:FAKE_COPILOT_FLAGS)
    Assert-Contains $CapFlags "--yolo" "turn passes --yolo"
    Assert-Contains $CapFlags "--no-color" "turn streams without color"
    $CapFlagLines = [IO.File]::ReadAllLines($env:FAKE_COPILOT_FLAGS)
    Assert-True (
        $CapFlagLines -ccontains "cli-model"
    ) "CLI --model overrode the environment model"
    Assert-True (
        -not ($CapFlagLines -ccontains "env-model")
    ) "environment model leaked past the CLI override"
    Assert-True (
        $CapFlagLines -ccontains "medium"
    ) "environment reasoning effort was forwarded"
    Assert-True (
        $CapFlagLines -ccontains "cli-tool"
    ) "CLI deny-tool forwarded"
    Assert-True (
        $CapFlagLines -ccontains "env-tool"
    ) "environment deny-tool forwarded"
    Assert-True (
        $CapFlagLines -ccontains "skill(cli-skill)"
    ) "CLI deny-skill mapped onto --deny-tool skill(...)"
    Assert-True (
        $CapFlagLines -ccontains "skill(env-skill)"
    ) "environment deny-skill mapped onto --deny-tool skill(...)"

    # Native CLI text remains human-readable on stderr and is represented once
    # as truthful unclassified Events in stdout and replay.
    Assert-Contains (
        [IO.File]::ReadAllText($CapStderr)
    ) "pre-marker output" "agent output streams to stderr"
    Assert-Contains (
        [IO.File]::ReadAllText($CapStderr)
    ) "native stderr chatter" "native CLI stderr remains visible"
    $OutputEvents = @(
        $CapEvents |
            Where-Object { $_["type"] -ceq "agent.output" }
    )
    Assert-Equal (
        "pre-marker output,<working issue=99>,<working issue=041>," +
        "<working issue=42>,post-marker output,pre-marker output," +
        "<working issue=99>,<working issue=041>,<working issue=42>," +
        "post-marker output"
    ) ([string]::Join(",", @($OutputEvents | ForEach-Object { $_["text"] }))) (
        "native CLI output Event order"
    )
    foreach ($Event in $OutputEvents) {
        Assert-Equal "unclassified" $Event["kind"] "native CLI output kind"
        Assert-True (
            [string]$Event["text"] -cne "native stderr chatter"
        ) "native CLI stderr was mislabeled as agent output"
    }
    $ActivationEvents = @(
        $CapEvents |
            Where-Object { $_["type"] -ceq "wrapper.issue.activated" }
    )
    Assert-Equal 2 $ActivationEvents.Count "one Active-issue binding per Iteration"
    foreach ($Event in $ActivationEvents) {
        Assert-Equal 41 $Event["issue"] "Working marker Active issue"
        Assert-Equal "working_marker" $Event["binding_source"] (
            "Working marker binding source"
        )
        Assert-Equal $Event["ts"] $Event["activated_at"] (
            "activation observation timestamp"
        )
    }
    Assert-Contains (
        [IO.File]::ReadAllText($CapStderr)
    ) (
        "Active-issue marker for #99 ignored; " +
        "issue is not in the current Pool"
    ) "out-of-Pool marker diagnostic"
    Assert-Contains (
        [IO.File]::ReadAllText($CapStderr)
    ) (
        "conflicting Active-issue marker for #42 ignored; " +
        "Iteration is already bound to #41"
    ) "conflicting marker diagnostic"
    $CapReplay = @(
        Get-ChildItem `
            -LiteralPath (Join-Path $CapRepo ".git-loopy/logs") `
            -Filter "*.jsonl" `
            -File
    )
    Assert-Equal 1 $CapReplay.Count "turn Run replay file count"
    Assert-Equal (
        [IO.File]::ReadAllText($CapStdout)
    ) ([IO.File]::ReadAllText($CapReplay[0].FullName)) (
        "turn Run stream and replay parity"
    )

    [IO.File]::Delete($env:FAKE_GH_LIST_COUNT)
    Set-CopilotEnv -Prefix "github-default"
    $env:FAKE_COPILOT_COMMITS = "0"
    $env:FAKE_GH_EMPTY_AFTER = "1"
    $DefaultStdout = Join-Path $TempDir "github-default.stdout"
    $DefaultStderr = Join-Path $TempDir "github-default.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $CapRepo `
        -FakeBin $CapBin `
        -StdoutPath $DefaultStdout `
        -StderrPath $DefaultStderr
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_COMMITS", $null)
    [Environment]::SetEnvironmentVariable("FAKE_GH_EMPTY_AFTER", $null)
    Assert-Equal 0 $Status (
        "unlimited turn Run exit: " + [IO.File]::ReadAllText($DefaultStderr)
    )
    Assert-Equal "2" (
        [IO.File]::ReadAllText($env:FAKE_GH_LIST_COUNT)
    ) "unlimited Run rebuilds the Pool until it empties"
    Assert-Equal "1" (
        [IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
    ) "unlimited Run runs one turn before its Pool empties"
    $DefaultEvents = Read-Events -Path $DefaultStdout
    $DefaultCollected = @(
        $DefaultEvents |
            Where-Object { $_["type"] -ceq "wrapper.afk_ready.collected" } |
            ForEach-Object { [string]::Join(",", $_["issues"]) }
    )
    Assert-Equal "41;" ([string]::Join(";", $DefaultCollected)) (
        "unlimited Run rebuilds then empties the Pool"
    )
    Assert-Equal "empty_pool" (
        $DefaultEvents[-1]["outcome"]
    ) "unlimited turn Run terminates on an empty Pool"
    Assert-Equal 2 (
        $DefaultEvents[-1]["iterations_run"]
    ) "unlimited Run Iteration count"

    # A turn that produces new commits records one commit event per commit, in
    # git's newest-first order, and only closes the Iteration afterwards.
    $CommitsRepo = Join-Path $TempDir "agent-commits"
    $CommitsBin = Join-Path $TempDir "agent-commits-bin"
    New-RealTestRepo -Root $CommitsRepo
    Write-TurnTools -BinDir $CommitsBin
    $CommitsList = Join-Path $TempDir "agent-commits-list.json"
    [IO.File]::Copy($CapList, $CommitsList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "agent-commits-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "agent-commits-list.count"
    $env:FAKE_GH_LIST_JSON = $CommitsList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    Set-CopilotEnv -Prefix "agent-commits"
    $env:FAKE_COPILOT_COMMITS = "2"
    $CommitsStdout = Join-Path $TempDir "agent-commits.stdout"
    $CommitsStderr = Join-Path $TempDir "agent-commits.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $CommitsRepo `
        -FakeBin $CommitsBin `
        -StdoutPath $CommitsStdout `
        -StderrPath $CommitsStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_COMMITS", $null)
    Assert-Equal 0 $Status "agent-commit turn Run exit"
    $CommitsEvents = Read-Events -Path $CommitsStdout
    Assert-Equal (
        "wrapper.run.start,wrapper.iteration.start," +
        "wrapper.afk_ready.collected,agent.output,wrapper.commit.recorded," +
        "wrapper.commit.recorded,wrapper.issue.activated," +
        "wrapper.iteration.end,wrapper.run.end"
    ) ([string]::Join(",", @($CommitsEvents | ForEach-Object { $_["type"] }))) (
        "commit events precede the Iteration end that closes their Iteration"
    )
    $RecordedCommits = @(
        $CommitsEvents |
            Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }
    )
    Assert-Equal "agent: work 2,agent: work 1" (
        [string]::Join(",", @($RecordedCommits | ForEach-Object { $_["subject"] }))
    ) "new commits are recorded newest-first"
    foreach ($Commit in $RecordedCommits) {
        Assert-True (
            $Commit.Contains("sha") -and
            $Commit.Contains("subject") -and
            $Commit.Contains("date")
        ) "commit event carries the contract payload keys"
        Assert-True (
            [string]$Commit["sha"] -match '^[0-9a-f]{40}$'
        ) "commit event carries a full SHA"
        Assert-True (
            [string]$Commit["date"] -match '^\d{4}-\d{2}-\d{2}$'
        ) "commit event carries an ISO date"
    }
    $SingleMemberBindings = @(
        $CommitsEvents |
            Where-Object { $_["type"] -ceq "wrapper.issue.activated" }
    )
    Assert-Equal 1 $SingleMemberBindings.Count (
        "single-member Pool produces one Active-issue binding"
    )
    Assert-Equal 41 $SingleMemberBindings[0]["issue"] (
        "single-member Pool Active issue"
    )
    Assert-Equal "single_member_pool" $SingleMemberBindings[0]["binding_source"] (
        "single-member Pool binding source"
    )
    $CommitsIterationEnd = @(
        $CommitsEvents |
            Where-Object { $_["type"] -ceq "wrapper.iteration.end" }
    )[0]
    Assert-Equal "advanced" $CommitsIterationEnd["outcome"] (
        "commit Iteration outcome"
    )
    Assert-True (
        $CommitsIterationEnd["duration_seconds"] -ge 0
    ) "commit Iteration duration"
    Assert-Equal 2 $CommitsIterationEnd["summary"]["commits"] (
        "commit Iteration Summary commits"
    )
    Assert-Equal 0 $CommitsIterationEnd["summary"]["auto_closures"] (
        "commit Iteration Summary closures"
    )
    Assert-Equal 0 $CommitsIterationEnd["summary"]["strikes"] (
        "commit Iteration Summary Strikes"
    )
    Assert-Equal 1 $CommitsIterationEnd["issues"].Count (
        "commit Iteration issue contribution count"
    )
    $CommitContribution = $CommitsIterationEnd["issues"][0]
    Assert-Equal 41 $CommitContribution["issue"] "commit contribution issue"
    Assert-Equal "advanced" $CommitContribution["status"] (
        "commit contribution status"
    )
    Assert-Equal (
        $SingleMemberBindings[0]["activated_at"]
    ) $CommitContribution["first_started_at"] (
        "commit contribution first activation"
    )
    Assert-True (
        $null -eq $CommitContribution["closed_at"] -and
        $null -eq $CommitContribution["issue_elapsed_seconds"]
    ) "advanced contribution has no closure-only facts"
    Assert-True (
        $CommitContribution["active_seconds"] -ge 0 -and
        $CommitContribution["cumulative_active_seconds"] -eq
            $CommitContribution["active_seconds"]
    ) "fallback contribution carries monotonic Active time"
    Assert-True (
        $null -eq $CommitContribution["consumption"]["model"] -and
        $null -eq $CommitContribution["consumption"]["tokens_in"] -and
        $null -eq $CommitContribution["consumption"]["tokens_out"] -and
        $null -eq $CommitContribution["cost_usd"] -and
        $null -eq $CommitContribution["peak_context_window"]
    ) "commit contribution does not guess unavailable telemetry"
    Assert-Equal "iteration_cap" (
        $CommitsEvents[-1]["outcome"]
    ) "agent-commit Run outcome"

    # A non-zero agent process warns and the Run still finishes cleanly
    # (warn-and-continue); the turn's real exit status is preserved.
    $NonZeroRepo = Join-Path $TempDir "agent-nonzero"
    $NonZeroBin = Join-Path $TempDir "agent-nonzero-bin"
    New-RealTestRepo -Root $NonZeroRepo
    Write-TurnTools -BinDir $NonZeroBin
    $NonZeroList = Join-Path $TempDir "agent-nonzero-list.json"
    [IO.File]::Copy($CapList, $NonZeroList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "agent-nonzero-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "agent-nonzero-list.count"
    $env:FAKE_GH_LIST_JSON = $NonZeroList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    Set-CopilotEnv -Prefix "agent-nonzero"
    $env:FAKE_COPILOT_COMMITS = "0"
    $env:FAKE_COPILOT_EXIT = "7"
    $NonZeroStdout = Join-Path $TempDir "agent-nonzero.stdout"
    $NonZeroStderr = Join-Path $TempDir "agent-nonzero.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $NonZeroRepo `
        -FakeBin $NonZeroBin `
        -StdoutPath $NonZeroStdout `
        -StderrPath $NonZeroStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_COMMITS", $null)
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_EXIT", $null)
    Assert-Equal 0 $Status "non-zero agent turn must not fail the Run"
    Assert-Equal "1" (
        [IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
    ) "the turn ran despite its non-zero exit"
    Assert-Contains (
        [IO.File]::ReadAllText($NonZeroStderr)
    ) "copilot turn exited with status 7" "non-zero agent exit warns to stderr"
    $NonZeroEvents = Read-Events -Path $NonZeroStdout
    Assert-Equal 0 (
        @(
            $NonZeroEvents |
                Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }
        ).Count
    ) "a non-zero turn with no commits records no commit events"
    Assert-Equal "iteration_cap" (
        $NonZeroEvents[-1]["outcome"]
    ) "non-zero agent turn stayed on warn-and-continue"

    # The turn feeds EXACTLY the last five commits (contract §4), newest-first,
    # and truncates older history. Every other turn scenario runs against a
    # <=3-commit repo, so this is the only guard on the shared `-n5`
    # recent-commits bound the Python reference and both native ports agree on.
    $RecentRepo = Join-Path $TempDir "recent-five"
    $RecentBin = Join-Path $TempDir "recent-five-bin"
    New-RealTestRepo -Root $RecentRepo
    foreach ($n in 1..7) {
        & git -C $RecentRepo commit -q --allow-empty -m "history commit $n"
    }
    Write-TurnTools -BinDir $RecentBin
    $RecentList = Join-Path $TempDir "recent-five-list.json"
    [IO.File]::Copy($CapList, $RecentList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "recent-five-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "recent-five-list.count"
    $env:FAKE_GH_LIST_JSON = $RecentList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    Set-CopilotEnv -Prefix "recent-five"
    $env:FAKE_COPILOT_COMMITS = "0"
    $RecentStdout = Join-Path $TempDir "recent-five.stdout"
    $RecentStderr = Join-Path $TempDir "recent-five.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $RecentRepo `
        -FakeBin $RecentBin `
        -StdoutPath $RecentStdout `
        -StderrPath $RecentStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_COMMITS", $null)
    Assert-Equal 0 $Status "recent-five turn Run exit"
    $RecentPrompt = [IO.File]::ReadAllText($env:FAKE_COPILOT_PROMPT)
    foreach ($n in 3..7) {
        Assert-Contains $RecentPrompt "history commit $n" (
            "prompt carries the last-five commit $n"
        )
    }
    foreach ($n in 1..2) {
        Assert-True (
            -not $RecentPrompt.Contains(
                "history commit $n",
                [StringComparison]::Ordinal
            )
        ) "prompt carried commit $n from beyond the last five"
    }
    Assert-True (
        -not $RecentPrompt.Contains("initial commit", [StringComparison]::Ordinal)
    ) "prompt carried the initial commit from beyond the last five"
    # Newest-first: commit 7 is rendered before commit 3 in the commits block.
    $RecentIdx7 = $RecentPrompt.IndexOf(
        "history commit 7",
        [StringComparison]::Ordinal
    )
    $RecentIdx3 = $RecentPrompt.IndexOf(
        "history commit 3",
        [StringComparison]::Ordinal
    )
    Assert-True (
        $RecentIdx7 -ge 0 -and $RecentIdx3 -ge 0 -and $RecentIdx7 -lt $RecentIdx3
    ) "recent commits are rendered newest-first"

    $PrdsRepo = Join-Path $TempDir "prds"
    $PrdsBin = Join-Path $TempDir "prds-bin"
    New-RealTestRepo -Root $PrdsRepo
    Write-TurnTools -BinDir $PrdsBin
    $FeatureDir = Join-Path $PrdsRepo "prds/feature"
    $AlphaDir = Join-Path $PrdsRepo "prds/alpha"
    $AlphaBetaDir = Join-Path $PrdsRepo "prds/alpha-beta"
    [IO.Directory]::CreateDirectory((Join-Path $FeatureDir "done")) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $AlphaDir "done")) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $AlphaBetaDir "done")) | Out-Null
    $OutsidePrds = Join-Path $TempDir "outside-prds"
    [IO.Directory]::CreateDirectory($OutsidePrds) | Out-Null
    [IO.File]::WriteAllText((Join-Path $AlphaDir "001-ready.md"), @'
## What to build
Ship alpha.

## Acceptance criteria
- Done.
'@)
    [IO.File]::WriteAllText((Join-Path $AlphaBetaDir "001-ready.md"), @'
## What to build
Ship alpha-beta.

## Acceptance criteria
- Done.
'@)
    [IO.File]::WriteAllText((Join-Path $FeatureDir "001-ready.md"), @'
## What to build
Ship it.

## Acceptance criteria
- Done.
'@)
    [IO.File]::WriteAllText(
        (Join-Path $FeatureDir "002-bare.md"),
        "No required headings.`n"
    )
    [IO.File]::WriteAllText((Join-Path $OutsidePrds "004-escaped.md"), @'
## What to build
Read outside the worktree.

## Acceptance criteria
- Escaped.
'@)
    if (-not $IsWindows) {
        New-Item `
            -ItemType SymbolicLink `
            -Path (Join-Path $PrdsRepo "prds/escaped") `
            -Target $OutsidePrds | Out-Null
    }
    [IO.File]::WriteAllText((Join-Path $FeatureDir "done/003-archived.md"), @'
## What to build
Old work.

## Acceptance criteria
- Archived.
'@)
    $env:FAKE_GH_LOG = Join-Path $TempDir "prds-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "prds-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews
    $env:GIT_LOOPY_ISSUE_SOURCE = "github"
    Set-CopilotEnv -Prefix "prds"

    $PrdsStdout = Join-Path $TempDir "prds.stdout"
    $PrdsStderr = Join-Path $TempDir "prds.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $PrdsRepo `
        -FakeBin $PrdsBin `
        -StdoutPath $PrdsStdout `
        -StderrPath $PrdsStderr `
        -Arguments @("1", "--issue-source", "prds")
    $env:GIT_LOOPY_ISSUE_SOURCE = $null
    Assert-Equal 0 $Status "local-PRD discovery exit"
    $PrdsEvents = Read-Events -Path $PrdsStdout
    Assert-Equal "prds" $PrdsEvents[0]["issue_source"] "CLI source precedence"
    $PrdsCollected = @(
        $PrdsEvents |
            Where-Object { $_["type"] -ceq "wrapper.afk_ready.collected" }
    )[0]
    Assert-Equal (
        "prds/alpha-beta/001-ready.md," +
        "prds/alpha/001-ready.md," +
        "prds/feature/001-ready.md"
    ) (
        [string]::Join(",", $PrdsCollected["issues"])
    ) "local-PRD discriminator"
    Assert-Equal "iteration_cap" $PrdsEvents[-1]["outcome"] "PRDs cap outcome"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_LOG)
    ) "PRDs mode invoked gh"
    # The local-PRD turn assembled the same minimum context: the resolved prompt
    # plus the PRD block rendered from its worktree-relative reference.
    Assert-Equal "1" (
        [IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
    ) "local-PRD Iteration ran exactly one turn"
    $PrdsPrompt = [IO.File]::ReadAllText($env:FAKE_COPILOT_PROMPT)
    Assert-Contains $PrdsPrompt (
        "=== prds/alpha-beta/001-ready.md ==="
    ) "prompt carries the local-PRD block"
    Assert-Contains $PrdsPrompt "Ship alpha-beta." "prompt carries the PRD body"
    Assert-Contains $PrdsPrompt "# Project prompt" (
        "prompt carries the resolved shared prompt"
    )

    $LinkedRootRepo = Join-Path $TempDir "prds-root-link"
    $LinkedRootBin = Join-Path $TempDir "prds-root-link-bin"
    New-TestRepo -Root $LinkedRootRepo
    Write-FakeTools -BinDir $LinkedRootBin
    $OutsideRoot = Join-Path $TempDir "outside-prds-root"
    $OutsideFeature = Join-Path $OutsideRoot "feature"
    [IO.Directory]::CreateDirectory($OutsideFeature) | Out-Null
    [IO.File]::WriteAllText((Join-Path $OutsideFeature "001-escaped.md"), @'
## What to build
Read outside the worktree.

## Acceptance criteria
- Escaped.
'@)
    $LinkType = if ($IsWindows) { "Junction" } else { "SymbolicLink" }
    New-Item `
        -ItemType $LinkType `
        -Path (Join-Path $LinkedRootRepo "prds") `
        -Target $OutsideRoot | Out-Null
    $env:FAKE_GH_LOG = Join-Path $TempDir "prds-root-link-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "prds-root-link-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews

    $LinkedRootStdout = Join-Path $TempDir "prds-root-link.stdout"
    $LinkedRootStderr = Join-Path $TempDir "prds-root-link.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $LinkedRootRepo `
        -FakeBin $LinkedRootBin `
        -StdoutPath $LinkedRootStdout `
        -StderrPath $LinkedRootStderr `
        -Arguments @("1", "--issue-source", "prds")
    Assert-Equal 0 $Status "linked-PRD-root Run exit"
    $LinkedRootEvents = Read-Events -Path $LinkedRootStdout
    $LinkedRootCollected = @(
        $LinkedRootEvents |
            Where-Object { $_["type"] -ceq "wrapper.afk_ready.collected" }
    )[0]
    Assert-Equal 0 (
        $LinkedRootCollected["issues"].Count
    ) "local-PRD collection followed a linked root outside the worktree"
    Assert-Equal "empty_pool" (
        $LinkedRootEvents[-1]["outcome"]
    ) "linked-PRD-root outcome"
    Assert-Contains (
        [IO.File]::ReadAllText($LinkedRootStderr)
    ) "linked prds root is not allowed" "linked local-PRD root warning"

    $MissingRepo = Join-Path $TempDir "missing-tracker"
    $MissingBin = Join-Path $TempDir "missing-tracker-bin"
    [IO.Directory]::CreateDirectory((Join-Path $MissingRepo "git-loopy")) |
        Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $MissingRepo "git-loopy/PROMPT.md"),
        "# Prompt`n"
    )
    Write-FakeTools -BinDir $MissingBin
    $env:FAKE_GH_LOG = Join-Path $TempDir "missing-tracker-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "missing-tracker-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews

    $MissingStdout = Join-Path $TempDir "missing-tracker.stdout"
    $MissingStderr = Join-Path $TempDir "missing-tracker.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $MissingRepo `
        -FakeBin $MissingBin `
        -StdoutPath $MissingStdout `
        -StderrPath $MissingStderr
    Assert-Equal 1 $Status "missing issue-tracker configuration exit"
    Assert-Contains (
        [IO.File]::ReadAllText($MissingStderr)
    ) "/setup-agent-skills" "missing setup guidance"
    Assert-Equal 0 (
        [IO.File]::ReadAllText($MissingStdout).Length
    ) "preflight failure emitted Iteration work"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_LOG)
    ) "preflight continued after missing tracker"

    $AuthRepo = Join-Path $TempDir "auth-failure"
    $AuthBin = Join-Path $TempDir "auth-failure-bin"
    New-TestRepo -Root $AuthRepo
    Write-FakeTools -BinDir $AuthBin
    $env:FAKE_GH_LOG = Join-Path $TempDir "auth-failure-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "auth-failure-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews
    $env:FAKE_GH_AUTH_STATUS = "1"

    $AuthStdout = Join-Path $TempDir "auth-failure.stdout"
    $AuthStderr = Join-Path $TempDir "auth-failure.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $AuthRepo `
        -FakeBin $AuthBin `
        -StdoutPath $AuthStdout `
        -StderrPath $AuthStderr
    $env:FAKE_GH_AUTH_STATUS = $null
    Assert-Equal 1 $Status "GitHub authentication preflight exit"
    Assert-Contains (
        [IO.File]::ReadAllText($AuthStderr)
    ) "gh auth login" "GitHub authentication guidance"
    Assert-Equal 0 (
        [IO.File]::ReadAllText($AuthStdout).Length
    ) "authentication failure emitted Run events"
    Assert-True (
        -not ([IO.File]::ReadAllText($env:FAKE_GH_LOG)).Contains(
            "issue list ",
            [StringComparison]::Ordinal
        )
    ) "authentication failure reached Pool collection"

    $UsageRepo = Join-Path $TempDir "usage"
    $UsageBin = Join-Path $TempDir "usage-bin"
    New-TestRepo -Root $UsageRepo
    Write-FakeTools -BinDir $UsageBin
    $env:FAKE_GH_LOG = Join-Path $TempDir "usage-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "usage-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews

    $UsageStdout = Join-Path $TempDir "usage.stdout"
    $UsageStderr = Join-Path $TempDir "usage.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $UsageRepo `
        -FakeBin $UsageBin `
        -StdoutPath $UsageStdout `
        -StderrPath $UsageStderr `
        -Arguments @("nope")
    Assert-Equal 2 $Status "malformed invocation exit"
    Assert-Equal 0 (
        [IO.File]::ReadAllText($UsageStdout).Length
    ) "usage error emitted Run events"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_LOG)
    ) "usage error reached preflight"

    # A turn whose commits carry closing keywords auto-closes the referenced
    # *Pool issue* exactly once — repeated references to the same issue collapse
    # to one closure attributing every referencing SHA, and an out-of-Pool
    # reference (a PR or a stranger issue) is never touched.
    $AutoRepo = Join-Path $TempDir "auto-close"
    $AutoBin = Join-Path $TempDir "auto-close-bin"
    New-RealTestRepo -Root $AutoRepo
    Write-TurnTools -BinDir $AutoBin
    $AutoList = Join-Path $TempDir "auto-close-list.json"
    [IO.File]::Copy($CapList, $AutoList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "auto-close-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "auto-close-list.count"
    $env:FAKE_GH_LIST_JSON = $AutoList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "auto-close-closed.log"
    $env:FAKE_GH_CLOSE_DIR = Join-Path $TempDir "auto-close-comments"
    Set-CopilotEnv -Prefix "auto-close"
    $AutoPlan = Join-Path $TempDir "auto-close-plan"
    [IO.Directory]::CreateDirectory((Join-Path $AutoPlan "1")) | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $AutoPlan "1/1.msg"),
        "feat: land the eligible work`n`nCloses #41 Fixes #77`n"
    )
    [IO.File]::WriteAllText(
        (Join-Path $AutoPlan "1/2.msg"),
        "chore: follow-up tidy`n`nResolves #41`n"
    )
    $env:FAKE_COPILOT_PLAN_DIR = $AutoPlan
    $AutoStdout = Join-Path $TempDir "auto-close.stdout"
    $AutoStderr = Join-Path $TempDir "auto-close.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $AutoRepo `
        -FakeBin $AutoBin `
        -StdoutPath $AutoStdout `
        -StderrPath $AutoStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_PLAN_DIR", $null)
    Assert-Equal 0 $Status "auto-close turn Run exit"
    $AutoEvents = Read-Events -Path $AutoStdout
    $ClosedLog = @([IO.File]::ReadAllLines($env:FAKE_GH_CLOSED))
    Assert-Equal 1 (
        @($ClosedLog | Where-Object { $_ -ceq "41" }).Count
    ) "the referenced Pool issue is closed exactly once"
    Assert-Equal 0 (
        @($ClosedLog | Where-Object { $_ -ceq "77" }).Count
    ) "an out-of-Pool reference was not closed"
    $AutoCloseEvents = @(
        $AutoEvents | Where-Object { $_["type"] -ceq "wrapper.auto_close" }
    )
    Assert-Equal 1 $AutoCloseEvents.Count "exactly one auto_close event"
    Assert-Equal 41 $AutoCloseEvents[0]["issue"] "auto_close targets the Pool issue"
    $AutoBindings = @(
        $AutoEvents |
            Where-Object { $_["type"] -ceq "wrapper.issue.activated" }
    )
    Assert-Equal 1 $AutoBindings.Count "closure produces one Active-issue binding"
    Assert-Equal 41 $AutoBindings[0]["issue"] "closure Active issue"
    Assert-Equal "closure" $AutoBindings[0]["binding_source"] (
        "closure binding source"
    )
    Assert-True (
        [Array]::IndexOf(
            @($AutoEvents | ForEach-Object { $_["type"] }),
            "wrapper.issue.activated"
        ) -lt [Array]::IndexOf(
            @($AutoEvents | ForEach-Object { $_["type"] }),
            "wrapper.auto_close"
        )
    ) "closure binding precedes auto-close"
    Assert-Equal 2 (
        $AutoCloseEvents[0]["shas"].Count
    ) "auto_close attributes both referencing SHAs"
    Assert-Equal (
        [string]$AutoCloseEvents[0]["shas"][0]
    ) ([string]$AutoCloseEvents[0]["sha"]) "auto_close primary sha is the first attributed sha"
    Assert-Equal "chore: follow-up tidy,feat: land the eligible work" (
        [string]::Join(",", @(
            $AutoEvents |
                Where-Object { $_["type"] -ceq "wrapper.commit.recorded" } |
                ForEach-Object { $_["subject"] }
        ))
    ) "both agent commits recorded newest-first"
    Assert-Equal 0 (
        @($AutoEvents | Where-Object { $_["type"] -ceq "wrapper.strike" }).Count
    ) "a progress Iteration records no Strike"
    $AutoIterationEnd = @(
        $AutoEvents |
            Where-Object { $_["type"] -ceq "wrapper.iteration.end" }
    )[0]
    Assert-Equal "closed" $AutoIterationEnd["outcome"] (
        "auto-close Iteration outcome"
    )
    Assert-Equal 2 $AutoIterationEnd["summary"]["commits"] (
        "auto-close Summary commits"
    )
    Assert-Equal 1 $AutoIterationEnd["summary"]["auto_closures"] (
        "auto-close Summary closures"
    )
    Assert-Equal 0 $AutoIterationEnd["summary"]["strikes"] (
        "auto-close Summary Strikes"
    )
    Assert-Equal 1 $AutoIterationEnd["issues"].Count (
        "auto-close issue contribution count"
    )
    $ClosedContribution = $AutoIterationEnd["issues"][0]
    Assert-Equal 41 $ClosedContribution["issue"] "closed contribution issue"
    Assert-Equal "closed" $ClosedContribution["status"] (
        "closed contribution status"
    )
    Assert-Equal (
        $AutoBindings[0]["activated_at"]
    ) $ClosedContribution["first_started_at"] (
        "closed contribution first activation"
    )
    Assert-Equal (
        $AutoCloseEvents[0]["ts"]
    ) $ClosedContribution["closed_at"] (
        "closed contribution authoritative closure time"
    )
    Assert-True (
        $ClosedContribution["issue_elapsed_seconds"] -ge 0 -and
        $ClosedContribution["active_seconds"] -ge 0 -and
        $ClosedContribution["cumulative_active_seconds"] -eq
            $ClosedContribution["active_seconds"]
    ) "closed contribution uses monotonic lifecycle durations"
    Assert-Equal "iteration_cap" $AutoEvents[-1]["outcome"] "auto-close Run outcome"
    $CloseComment = [IO.File]::ReadAllText(
        (Join-Path $env:FAKE_GH_CLOSE_DIR "41.comment")
    )
    foreach ($Sha in @($AutoCloseEvents[0]["shas"])) {
        Assert-Contains $CloseComment ([string]$Sha) "closure comment cites commit $Sha"
    }
    Assert-Contains $CloseComment "gh issue reopen 41" (
        "closure comment documents how to reopen"
    )
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSE_DIR", $null)

    # An agent may perform the required source closure itself before the
    # Orchestrator reaches its closing-keyword backstop. The source's CLOSED
    # state is still authoritative lifecycle evidence, but it is not an
    # auto-closure and must not cause a duplicate `gh issue close`.
    $AgentClosedRepo = Join-Path $TempDir "agent-closed"
    $AgentClosedBin = Join-Path $TempDir "agent-closed-bin"
    New-RealTestRepo -Root $AgentClosedRepo
    Write-TurnTools -BinDir $AgentClosedBin
    $AgentClosedList = Join-Path $TempDir "agent-closed-list.json"
    [IO.File]::Copy($CapList, $AgentClosedList, $true)
    $AgentClosedViews = Join-Path $TempDir "agent-closed-views"
    [IO.Directory]::CreateDirectory($AgentClosedViews) | Out-Null
    [IO.File]::WriteAllText((Join-Path $AgentClosedViews "41.json"), @'
{
  "number": 41,
  "state": "CLOSED",
  "url": "https://example.invalid/issues/41"
}
'@)
    $env:FAKE_GH_LOG = Join-Path $TempDir "agent-closed-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "agent-closed-list.count"
    $env:FAKE_GH_LIST_JSON = $AgentClosedList
    $env:FAKE_GH_VIEW_DIR = $AgentClosedViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "agent-closed-closed.log"
    Set-CopilotEnv -Prefix "agent-closed"
    $AgentClosedPlan = Join-Path $TempDir "agent-closed-plan"
    [IO.Directory]::CreateDirectory((Join-Path $AgentClosedPlan "1")) | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $AgentClosedPlan "1/1.msg"),
        "feat: close at the source`n`nCloses #41`n"
    )
    $env:FAKE_COPILOT_PLAN_DIR = $AgentClosedPlan
    $AgentClosedStdout = Join-Path $TempDir "agent-closed.stdout"
    $AgentClosedStderr = Join-Path $TempDir "agent-closed.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $AgentClosedRepo `
        -FakeBin $AgentClosedBin `
        -StdoutPath $AgentClosedStdout `
        -StderrPath $AgentClosedStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_PLAN_DIR", $null)
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)
    Assert-Equal 0 $Status "agent-closed turn Run exit"
    Assert-True (
        -not [IO.File]::Exists((Join-Path $TempDir "agent-closed-closed.log"))
    ) "an already-closed source issue was closed again"
    $AgentClosedEvents = Read-Events -Path $AgentClosedStdout
    Write-Host "=== EVENTS ==="
    Write-Host ($AgentClosedEvents | ConvertTo-Json -Depth 12)
    Write-Host "=== STDERR ==="
    Write-Host ([IO.File]::ReadAllText($AgentClosedStderr))
    Write-Host "=== GH LOG ==="
    if ([IO.File]::Exists($env:FAKE_GH_LOG)) { Write-Host ([IO.File]::ReadAllText($env:FAKE_GH_LOG)) }
    exit 0
    Assert-Equal 0 (
        @(
            $AgentClosedEvents |
                Where-Object { $_["type"] -ceq "wrapper.auto_close" }
        ).Count
    ) "agent source closure was mislabeled as an auto-close"
    $AgentClosedIterationEnd = @(
        $AgentClosedEvents |
            Where-Object { $_["type"] -ceq "wrapper.iteration.end" }
    )[0]
    Assert-Equal "closed" $AgentClosedIterationEnd["outcome"] (
        "agent source closure Iteration outcome"
    )
    Assert-Equal 0 $AgentClosedIterationEnd["summary"]["auto_closures"] (
        "agent source closure Summary auto-closures"
    )
    Assert-Equal "closed" $AgentClosedIterationEnd["issues"][0]["status"] (
        "agent source closure contribution status"
    )
    Assert-True (
        $null -ne $AgentClosedIterationEnd["issues"][0]["closed_at"] -and
        $null -ne $AgentClosedIterationEnd["issues"][0]["issue_elapsed_seconds"]
    ) "agent source closure omitted closure-only facts"

    # Progress resets the Strike counter: a no-progress Iteration records a
    # Strike, the next Iteration's agent commit clears it, and a following
    # no-progress Iteration is Strike 1 again — never 2.
    $ResetRepo = Join-Path $TempDir "strike-reset"
    $ResetBin = Join-Path $TempDir "strike-reset-bin"
    New-RealTestRepo -Root $ResetRepo
    Write-TurnTools -BinDir $ResetBin
    $ResetList = Join-Path $TempDir "strike-reset-list.json"
    [IO.File]::Copy($CapList, $ResetList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "strike-reset-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "strike-reset-list.count"
    $env:FAKE_GH_LIST_JSON = $ResetList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "strike-reset-closed.log"
    Set-CopilotEnv -Prefix "strike-reset"
    $ResetPlan = Join-Path $TempDir "strike-reset-plan"
    [IO.Directory]::CreateDirectory((Join-Path $ResetPlan "2")) | Out-Null
    [IO.File]::WriteAllText((Join-Path $ResetPlan "2/1.msg"), "agent: real work`n")
    $env:FAKE_COPILOT_PLAN_DIR = $ResetPlan
    $ResetStdout = Join-Path $TempDir "strike-reset.stdout"
    $ResetStderr = Join-Path $TempDir "strike-reset.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $ResetRepo `
        -FakeBin $ResetBin `
        -StdoutPath $ResetStdout `
        -StderrPath $ResetStderr `
        -Arguments @("3")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_PLAN_DIR", $null)
    Assert-Equal 0 $Status "strike-reset Run exit"
    $ResetEvents = Read-Events -Path $ResetStdout
    $ResetStrikes = @(
        $ResetEvents | Where-Object { $_["type"] -ceq "wrapper.strike" }
    )
    Assert-Equal 2 $ResetStrikes.Count "two no-progress Iterations record a Strike each"
    foreach ($Strike in $ResetStrikes) {
        Assert-Equal 1 $Strike["strikes"] "each Strike is the first after a reset"
        Assert-Equal "warn" $Strike["outcome"] "a running Strike warns"
    }
    Assert-Equal "agent: real work" (
        [string]::Join(",", @(
            $ResetEvents |
                Where-Object { $_["type"] -ceq "wrapper.commit.recorded" } |
                ForEach-Object { $_["subject"] }
        ))
    ) "the intervening agent commit is recorded"
    Assert-Equal 0 (
        @($ResetEvents | Where-Object { $_["type"] -ceq "wrapper.auto_close" }).Count
    ) "no closing keyword means no auto-close"
    $ResetIterationEnds = @(
        $ResetEvents |
            Where-Object { $_["type"] -ceq "wrapper.iteration.end" }
    )
    Assert-Equal "no_progress,advanced,no_progress" (
        [string]::Join(",", @(
            $ResetIterationEnds | ForEach-Object { $_["outcome"] }
        ))
    ) "Strike-reset Iteration outcomes"
    Assert-Equal "1,0,1" (
        [string]::Join(",", @(
            $ResetIterationEnds | ForEach-Object { $_["summary"]["strikes"] }
        ))
    ) "Strike-reset normalized Summary"
    Assert-Equal "no-progress,advanced,no-progress" (
        [string]::Join(",", @(
            $ResetIterationEnds | ForEach-Object { $_["issues"][0]["status"] }
        ))
    ) "Strike-reset contribution statuses"
    foreach ($IterationEnd in $ResetIterationEnds) {
        Assert-True (
            $null -eq $IterationEnd["issues"][0]["closed_at"] -and
            $null -eq $IterationEnd["issues"][0]["issue_elapsed_seconds"]
        ) "non-closure contribution leaves closure facts unknown"
    }
    Assert-Equal 1 (
        @(
            $ResetIterationEnds |
                ForEach-Object { $_["issues"][0]["first_started_at"] } |
                Sort-Object -Unique
        ).Count
    ) "repeated issue keeps its first activation"
    Assert-True (
        $ResetIterationEnds[0]["issues"][0]["cumulative_active_seconds"] -le
            $ResetIterationEnds[1]["issues"][0]["cumulative_active_seconds"] -and
        $ResetIterationEnds[1]["issues"][0]["cumulative_active_seconds"] -le
            $ResetIterationEnds[2]["issues"][0]["cumulative_active_seconds"]
    ) "repeated issue accumulates monotonic Active time"
    Assert-Equal "iteration_cap" $ResetEvents[-1]["outcome"] "strike-reset Run outcome"
    Assert-Equal 3 $ResetEvents[-1]["iterations_run"] "strike-reset ran every Iteration"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_CLOSED)
    ) "strike-reset closed no issue"
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)

    # Consecutive no-progress Iterations accumulate Strikes and the threshold
    # ends the Run as stuck (exit 1), even with the iteration cap unlimited.
    $StuckRepo = Join-Path $TempDir "stuck"
    $StuckBin = Join-Path $TempDir "stuck-bin"
    New-RealTestRepo -Root $StuckRepo
    Write-TurnTools -BinDir $StuckBin
    $StuckList = Join-Path $TempDir "stuck-list.json"
    [IO.File]::Copy($CapList, $StuckList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "stuck-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "stuck-list.count"
    $env:FAKE_GH_LIST_JSON = $StuckList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "stuck-closed.log"
    Set-CopilotEnv -Prefix "stuck"
    $StuckPlan = Join-Path $TempDir "stuck-plan"
    [IO.Directory]::CreateDirectory($StuckPlan) | Out-Null
    $env:FAKE_COPILOT_PLAN_DIR = $StuckPlan
    $StuckStdout = Join-Path $TempDir "stuck.stdout"
    $StuckStderr = Join-Path $TempDir "stuck.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $StuckRepo `
        -FakeBin $StuckBin `
        -StdoutPath $StuckStdout `
        -StderrPath $StuckStderr `
        -Arguments @("0")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_PLAN_DIR", $null)
    Assert-Equal 1 $Status "a stuck Run exits 1"
    $StuckEvents = Read-Events -Path $StuckStdout
    $StuckStrikes = @(
        $StuckEvents | Where-Object { $_["type"] -ceq "wrapper.strike" }
    )
    Assert-Equal 3 $StuckStrikes.Count "each no-progress Iteration records a Strike"
    Assert-Equal "1,2,3" (
        [string]::Join(",", @($StuckStrikes | ForEach-Object { $_["strikes"] }))
    ) "Strikes accumulate to the threshold"
    Assert-Equal "warn,warn,abort" (
        [string]::Join(",", @($StuckStrikes | ForEach-Object { $_["outcome"] }))
    ) "the threshold Strike aborts"
    Assert-Equal 0 (
        @($StuckEvents | Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }).Count
    ) "no agent commits in a stuck Run"
    Assert-Equal "wrapper.run.end" $StuckEvents[-1]["type"] "stuck Run ends with run.end"
    Assert-Equal "stuck" $StuckEvents[-1]["outcome"] "stuck Run outcome"
    Assert-Equal 3 $StuckEvents[-1]["iterations_run"] "stuck Run iterations"
    $StuckIterationEnds = @(
        $StuckEvents |
            Where-Object { $_["type"] -ceq "wrapper.iteration.end" }
    )
    Assert-Equal "no_progress,no_progress,aborted" (
        [string]::Join(",", @(
            $StuckIterationEnds | ForEach-Object { $_["outcome"] }
        ))
    ) "stuck Iteration outcomes"
    Assert-Equal "aborted" $StuckIterationEnds[-1]["issues"][0]["status"] (
        "threshold Strike contribution status"
    )
    Assert-True (
        $null -eq $StuckIterationEnds[-1]["issues"][0]["closed_at"] -and
        $null -eq $StuckIterationEnds[-1]["issues"][0]["issue_elapsed_seconds"]
    ) "aborted contribution leaves closure facts unknown"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_CLOSED)
    ) "stuck Run closed no issue"
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)

    # A recognized runner Checkpoint is excluded from the agent-commit tally: it
    # is not recorded as a contract commit and does not count as progress, so its
    # Iteration still records a Strike.
    $CheckRepo = Join-Path $TempDir "checkpoint-skip"
    $CheckBin = Join-Path $TempDir "checkpoint-skip-bin"
    New-RealTestRepo -Root $CheckRepo
    Write-TurnTools -BinDir $CheckBin
    $CheckList = Join-Path $TempDir "checkpoint-skip-list.json"
    [IO.File]::Copy($CapList, $CheckList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "checkpoint-skip-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "checkpoint-skip-list.count"
    $env:FAKE_GH_LIST_JSON = $CheckList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "checkpoint-skip-closed.log"
    Set-CopilotEnv -Prefix "checkpoint-skip"
    $CheckPlan = Join-Path $TempDir "checkpoint-skip-plan"
    [IO.Directory]::CreateDirectory((Join-Path $CheckPlan "1")) | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $CheckPlan "1/1.msg"),
        "Checkpoint: capture uncommitted work-in-progress`n`nGitLoopy-Checkpoint: 41`n"
    )
    $env:FAKE_COPILOT_PLAN_DIR = $CheckPlan
    $CheckStdout = Join-Path $TempDir "checkpoint-skip.stdout"
    $CheckStderr = Join-Path $TempDir "checkpoint-skip.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $CheckRepo `
        -FakeBin $CheckBin `
        -StdoutPath $CheckStdout `
        -StderrPath $CheckStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_PLAN_DIR", $null)
    Assert-Equal 0 $Status "checkpoint-skip Run exit"
    $CheckEvents = Read-Events -Path $CheckStdout
    Assert-Equal 0 (
        @($CheckEvents | Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }).Count
    ) "a runner Checkpoint is not recorded as an agent commit"
    $CheckStrikes = @(
        $CheckEvents | Where-Object { $_["type"] -ceq "wrapper.strike" }
    )
    Assert-Equal 1 $CheckStrikes.Count "a Checkpoint-only Iteration makes no progress"
    Assert-Equal 1 $CheckStrikes[0]["strikes"] "the Checkpoint-only Iteration records Strike 1"
    Assert-Equal "warn" $CheckStrikes[0]["outcome"] "the Checkpoint Strike warns"
    Assert-Equal 0 (
        @($CheckEvents | Where-Object { $_["type"] -ceq "wrapper.auto_close" }).Count
    ) "a Checkpoint carries no closing keyword"
    Assert-Equal "iteration_cap" $CheckEvents[-1]["outcome"] "checkpoint-skip Run outcome"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_CLOSED)
    ) "checkpoint-skip closed no issue"
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)

    # A dirty worktree the agent left uncommitted is captured in exactly one
    # runner Checkpoint (ADR-0004): staged with `git add -A`, attributed to the
    # Active issue, close-keyword-free, surfaced as wrapper.checkpoint.recorded
    # (never a commit.recorded), and excluded from Strike progress (the Iteration
    # still strikes). The Checkpoint is a new local commit, so the branch is
    # auto-pushed to its upstream and the remote receives it.
    $DirtyRepo = Join-Path $TempDir "checkpoint-dirty"
    $DirtyBin = Join-Path $TempDir "checkpoint-dirty-bin"
    $DirtyRemote = Join-Path $TempDir "checkpoint-dirty-remote.git"
    New-RealTestRepo -Root $DirtyRepo
    Add-FakeRemote -Root $DirtyRepo -Remote $DirtyRemote
    Write-TurnTools -BinDir $DirtyBin
    $DirtyList = Join-Path $TempDir "checkpoint-dirty-list.json"
    [IO.File]::Copy($CapList, $DirtyList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "checkpoint-dirty-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "checkpoint-dirty-list.count"
    $env:FAKE_GH_LIST_JSON = $DirtyList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "checkpoint-dirty-closed.log"
    Set-CopilotEnv -Prefix "checkpoint-dirty"
    $DirtyPlan = Join-Path $TempDir "checkpoint-dirty-plan"
    [IO.Directory]::CreateDirectory((Join-Path $DirtyPlan "1")) | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $DirtyPlan "1/worktree.ps1"),
        "Set-Content -Path 'wip.txt' -Value 'work in progress the agent forgot to commit'`n"
    )
    $env:FAKE_COPILOT_PLAN_DIR = $DirtyPlan
    $DirtyStdout = Join-Path $TempDir "checkpoint-dirty.stdout"
    $DirtyStderr = Join-Path $TempDir "checkpoint-dirty.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $DirtyRepo `
        -FakeBin $DirtyBin `
        -StdoutPath $DirtyStdout `
        -StderrPath $DirtyStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_PLAN_DIR", $null)
    Assert-Equal 0 $Status "checkpoint-dirty Run exit"
    $DirtyEvents = Read-Events -Path $DirtyStdout
    $DirtyCheckpoints = @(
        $DirtyEvents | Where-Object { $_["type"] -ceq "wrapper.checkpoint.recorded" }
    )
    Assert-Equal 1 $DirtyCheckpoints.Count `
        "a dirty worktree records exactly one Checkpoint"
    Assert-Equal 41 $DirtyCheckpoints[0]["issue"] `
        "the Checkpoint is attributed to the Active issue"
    Assert-True (
        -not [string]::IsNullOrEmpty([string]$DirtyCheckpoints[0]["sha"])
    ) "the Checkpoint records its SHA"
    Assert-Equal 0 (
        @($DirtyEvents | Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }).Count
    ) "the Checkpoint is not recorded as an agent commit"
    Assert-Equal 1 (
        @($DirtyEvents | Where-Object { $_["type"] -ceq "wrapper.push.recorded" }).Count
    ) "the Checkpoint is auto-pushed"
    $DirtyStrikes = @(
        $DirtyEvents | Where-Object { $_["type"] -ceq "wrapper.strike" }
    )
    Assert-Equal 1 $DirtyStrikes.Count "a Checkpoint-only Iteration makes no progress"
    Assert-Equal 1 $DirtyStrikes[0]["strikes"] "the Checkpoint Iteration records Strike 1"
    Assert-Equal "warn" $DirtyStrikes[0]["outcome"] "the Checkpoint Strike warns"
    Assert-Equal 0 (
        @($DirtyEvents | Where-Object { $_["type"] -ceq "wrapper.auto_close" }).Count
    ) "the Checkpoint closes no issue via keyword"
    Assert-Equal "iteration_cap" $DirtyEvents[-1]["outcome"] "checkpoint-dirty Run outcome"
    $DirtySha = [string]$DirtyCheckpoints[0]["sha"]
    $DirtyMessage = (& git -C $DirtyRepo show -s --format=%B $DirtySha) -join "`n"
    Assert-Contains $DirtyMessage `
        "Checkpoint: capture work-in-progress for issue 41" `
        "the Checkpoint subject is attributed to the Active issue"
    Assert-Contains $DirtyMessage "GitLoopy-Checkpoint: 41" `
        "the Checkpoint carries the runner trailer"
    Assert-True (
        -not [regex]::IsMatch(
            $DirtyMessage,
            '(?i)(close[sd]?|fix(es|ed)?|resolve[sd]?)\s+#\d+'
        )
    ) "the Checkpoint message matches no closing keyword"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_CLOSED)
    ) "the Checkpoint closed no issue"
    Assert-Equal "" (
        (& git -C $DirtyRepo status --porcelain) -join "`n"
    ) "the worktree is clean after the Checkpoint"
    Assert-Contains (
        (& git -C $DirtyRepo show "${DirtySha}:wip.txt") -join "`n"
    ) "work in progress" "the Checkpoint captured the uncommitted file"
    $DirtyBranch = ([string](& git -C $DirtyRepo rev-parse --abbrev-ref HEAD)).Trim()
    Assert-Equal (
        ([string](& git -C $DirtyRepo rev-parse HEAD)).Trim()
    ) (
        ([string](& git --git-dir=$DirtyRemote rev-parse "refs/heads/$DirtyBranch")).Trim()
    ) "the push landed the Checkpoint on the remote"
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)

    # A clean tree with one agent commit makes no Checkpoint but still
    # auto-pushes: the commit is recorded, no checkpoint event fires,
    # wrapper.push.recorded lands, and the remote receives the agent commit.
    $PushRepo = Join-Path $TempDir "agent-commit-push"
    $PushBin = Join-Path $TempDir "agent-commit-push-bin"
    $PushRemote = Join-Path $TempDir "agent-commit-push-remote.git"
    New-RealTestRepo -Root $PushRepo
    Add-FakeRemote -Root $PushRepo -Remote $PushRemote
    Write-TurnTools -BinDir $PushBin
    $PushList = Join-Path $TempDir "agent-commit-push-list.json"
    [IO.File]::Copy($CapList, $PushList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "agent-commit-push-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "agent-commit-push-list.count"
    $env:FAKE_GH_LIST_JSON = $PushList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "agent-commit-push-closed.log"
    Set-CopilotEnv -Prefix "agent-commit-push"
    $PushPlan = Join-Path $TempDir "agent-commit-push-plan"
    [IO.Directory]::CreateDirectory((Join-Path $PushPlan "1")) | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $PushPlan "1/1.msg"),
        "feat: real work`n`nCloses #41`n"
    )
    $env:FAKE_COPILOT_PLAN_DIR = $PushPlan
    $env:FAKE_GH_CLOSE_STATUS = "1"
    $PushStdout = Join-Path $TempDir "agent-commit-push.stdout"
    $PushStderr = Join-Path $TempDir "agent-commit-push.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $PushRepo `
        -FakeBin $PushBin `
        -StdoutPath $PushStdout `
        -StderrPath $PushStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_PLAN_DIR", $null)
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSE_STATUS", $null)
    Assert-Equal 0 $Status "agent-commit-push Run exit"
    $PushEvents = Read-Events -Path $PushStdout
    Assert-Equal "feat: real work" (
        [string]::Join(",", @(
            $PushEvents |
                Where-Object { $_["type"] -ceq "wrapper.commit.recorded" } |
                ForEach-Object { $_["subject"] }
        ))
    ) "the agent commit is recorded"
    Assert-Equal 0 (
        @($PushEvents | Where-Object { $_["type"] -ceq "wrapper.checkpoint.recorded" }).Count
    ) "a clean tree records no Checkpoint"
    Assert-Equal 1 (
        @($PushEvents | Where-Object { $_["type"] -ceq "wrapper.push.recorded" }).Count
    ) "the agent commit is auto-pushed"
    Assert-Equal 0 (
        @($PushEvents | Where-Object { $_["type"] -ceq "wrapper.strike" }).Count
    ) "a progress Iteration records no Strike"
    Assert-Equal 0 (
        @($PushEvents | Where-Object { $_["type"] -ceq "wrapper.auto_close" }).Count
    ) "a failed closure records no auto-close"
    $CommitBindings = @(
        $PushEvents |
            Where-Object { $_["type"] -ceq "wrapper.issue.activated" }
    )
    Assert-Equal 1 $CommitBindings.Count "commit produces one Active-issue binding"
    Assert-Equal 41 $CommitBindings[0]["issue"] "commit Active issue"
    Assert-Equal "commit" $CommitBindings[0]["binding_source"] (
        "commit binding source"
    )
    Assert-Equal "iteration_cap" $PushEvents[-1]["outcome"] "agent-commit-push Run outcome"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_CLOSED)
    ) "a failed closure was not recorded as closed"
    $PushBranch = ([string](& git -C $PushRepo rev-parse --abbrev-ref HEAD)).Trim()
    Assert-Equal (
        ([string](& git -C $PushRepo rev-parse HEAD)).Trim()
    ) (
        ([string](& git --git-dir=$PushRemote rev-parse "refs/heads/$PushBranch")).Trim()
    ) "the push landed the agent commit on the remote"
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)

    # Ignored files are never captured: the agent leaves only a .gitignore-matched
    # artefact, so the tree is clean under normal ignore rules — no Checkpoint, no
    # push, and (no progress) a Strike. The ignored file stays on disk,
    # uncommitted.
    $IgnoreRepo = Join-Path $TempDir "ignored-clean"
    $IgnoreBin = Join-Path $TempDir "ignored-clean-bin"
    New-RealTestRepo -Root $IgnoreRepo
    [IO.File]::AppendAllText((Join-Path $IgnoreRepo ".gitignore"), "*.ignored`n")
    & git -C $IgnoreRepo commit -q -am "ignore scratch artefacts"
    Write-TurnTools -BinDir $IgnoreBin
    $IgnoreList = Join-Path $TempDir "ignored-clean-list.json"
    [IO.File]::Copy($CapList, $IgnoreList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "ignored-clean-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "ignored-clean-list.count"
    $env:FAKE_GH_LIST_JSON = $IgnoreList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "ignored-clean-closed.log"
    Set-CopilotEnv -Prefix "ignored-clean"
    $IgnorePlan = Join-Path $TempDir "ignored-clean-plan"
    [IO.Directory]::CreateDirectory((Join-Path $IgnorePlan "1")) | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $IgnorePlan "1/worktree.ps1"),
        "Set-Content -Path 'scratch.ignored' -Value 'ignored noise'`n"
    )
    $env:FAKE_COPILOT_PLAN_DIR = $IgnorePlan
    $IgnorePreHead = ([string](& git -C $IgnoreRepo rev-parse HEAD)).Trim()
    $IgnoreStdout = Join-Path $TempDir "ignored-clean.stdout"
    $IgnoreStderr = Join-Path $TempDir "ignored-clean.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $IgnoreRepo `
        -FakeBin $IgnoreBin `
        -StdoutPath $IgnoreStdout `
        -StderrPath $IgnoreStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_PLAN_DIR", $null)
    Assert-Equal 0 $Status "ignored-clean Run exit"
    $IgnoreEvents = Read-Events -Path $IgnoreStdout
    Assert-Equal 0 (
        @($IgnoreEvents | Where-Object { $_["type"] -ceq "wrapper.checkpoint.recorded" }).Count
    ) "an ignored-only worktree records no Checkpoint"
    Assert-Equal 0 (
        @($IgnoreEvents | Where-Object { $_["type"] -ceq "wrapper.push.recorded" }).Count
    ) "an ignored-only Iteration pushes nothing"
    Assert-Equal 0 (
        @($IgnoreEvents | Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }).Count
    ) "an ignored-only Iteration records no commit"
    $IgnoreStrikes = @(
        $IgnoreEvents | Where-Object { $_["type"] -ceq "wrapper.strike" }
    )
    Assert-Equal 1 $IgnoreStrikes.Count "an ignored-only Iteration makes no progress"
    Assert-Equal 1 $IgnoreStrikes[0]["strikes"] "the ignored-only Iteration records Strike 1"
    Assert-Equal "warn" $IgnoreStrikes[0]["outcome"] "the ignored-only Strike warns"
    Assert-Equal "iteration_cap" $IgnoreEvents[-1]["outcome"] "ignored-clean Run outcome"
    Assert-Equal $IgnorePreHead (
        ([string](& git -C $IgnoreRepo rev-parse HEAD)).Trim()
    ) "no commit was authored for an ignored-only change"
    Assert-True (
        [IO.File]::Exists((Join-Path $IgnoreRepo "scratch.ignored"))
    ) "the ignored artefact stays on disk"
    Assert-Equal "" (
        (& git -C $IgnoreRepo ls-files scratch.ignored) -join "`n"
    ) "the ignored artefact was never committed"
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)

    # A local-only repo (no upstream) keeps working: the agent commit is recorded,
    # the auto-push fails and warns without aborting, no wrapper.push.recorded
    # lands, and the Run still exits 0.
    $LocalRepo = Join-Path $TempDir "local-only"
    $LocalBin = Join-Path $TempDir "local-only-bin"
    New-RealTestRepo -Root $LocalRepo
    Write-TurnTools -BinDir $LocalBin
    $LocalList = Join-Path $TempDir "local-only-list.json"
    [IO.File]::Copy($CapList, $LocalList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "local-only-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "local-only-list.count"
    $env:FAKE_GH_LIST_JSON = $LocalList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "local-only-closed.log"
    Set-CopilotEnv -Prefix "local-only"
    $LocalPlan = Join-Path $TempDir "local-only-plan"
    [IO.Directory]::CreateDirectory((Join-Path $LocalPlan "1")) | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $LocalPlan "1/1.msg"),
        "feat: local work`n`nRefs #41`n"
    )
    $env:FAKE_COPILOT_PLAN_DIR = $LocalPlan
    $LocalStdout = Join-Path $TempDir "local-only.stdout"
    $LocalStderr = Join-Path $TempDir "local-only.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $LocalRepo `
        -FakeBin $LocalBin `
        -StdoutPath $LocalStdout `
        -StderrPath $LocalStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_PLAN_DIR", $null)
    Assert-Equal 0 $Status "local-only Run exit"
    $LocalEvents = Read-Events -Path $LocalStdout
    Assert-Equal "feat: local work" (
        [string]::Join(",", @(
            $LocalEvents |
                Where-Object { $_["type"] -ceq "wrapper.commit.recorded" } |
                ForEach-Object { $_["subject"] }
        ))
    ) "the local agent commit is recorded"
    Assert-Equal 0 (
        @($LocalEvents | Where-Object { $_["type"] -ceq "wrapper.push.recorded" }).Count
    ) "a failed push records no push event"
    Assert-Equal 0 (
        @($LocalEvents | Where-Object { $_["type"] -ceq "wrapper.checkpoint.recorded" }).Count
    ) "a clean local-only tree records no Checkpoint"
    Assert-Equal "iteration_cap" $LocalEvents[-1]["outcome"] "local-only Run outcome"
    Assert-Contains (
        [IO.File]::ReadAllText($LocalStderr)
    ) "auto-push failed" "the local-only push failure warned"
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)

    # A pathologically slow agent turn is bounded by the resolved send timeout:
    # the Orchestrator's inner-pwsh watchdog force-terminates it at ~the bound
    # rather than letting a hung agent hang the Iteration forever (issue #113).
    # The terminated turn lands no agent commit, so the Iteration is a failed,
    # no-progress turn (contract §4/§6) that still completes cleanly at the cap.
    $SendTimeoutRepo = Join-Path $TempDir "send-timeout"
    $SendTimeoutBin = Join-Path $TempDir "send-timeout-bin"
    New-RealTestRepo -Root $SendTimeoutRepo
    Write-TurnTools -BinDir $SendTimeoutBin
    # Overwrite the shared fake `copilot` with one that sleeps far past the bound.
    # Process.Kill($true) is unconditionally forceful (SIGKILL on Unix /
    # TerminateProcess on Windows), so — unlike the shell port — the fake needs no
    # SIGTERM trap; the "finished unbounded" line only prints if it was never
    # bounded (the pre-fix bug), making that regression loud.
    Write-FakeCommand `
        -BinDir $SendTimeoutBin `
        -Name "copilot" `
        -DirectPowerShell `
        -Body @'
$ErrorActionPreference = "Stop"
Write-Output "copilot agent stream marker"
$Sleep = if ($env:FAKE_COPILOT_SLEEP) { [int]$env:FAKE_COPILOT_SLEEP } else { 60 }
Start-Sleep -Seconds $Sleep
[Console]::Error.WriteLine("slow copilot: turn finished unbounded")
'@
    $SendTimeoutList = Join-Path $TempDir "send-timeout-list.json"
    [IO.File]::Copy($CapList, $SendTimeoutList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "send-timeout-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "send-timeout-list.count"
    $env:FAKE_GH_LIST_JSON = $SendTimeoutList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    $env:FAKE_GH_CLOSED = Join-Path $TempDir "send-timeout-closed.log"
    $env:GIT_LOOPY_SEND_TIMEOUT_SECONDS = "1"
    $env:FAKE_COPILOT_SLEEP = "60"
    $SendTimeoutStdout = Join-Path $TempDir "send-timeout.stdout"
    $SendTimeoutStderr = Join-Path $TempDir "send-timeout.stderr"
    $SendTimeoutWatch = [Diagnostics.Stopwatch]::StartNew()
    $Status = Invoke-Entrypoint `
        -Repo $SendTimeoutRepo `
        -FakeBin $SendTimeoutBin `
        -StdoutPath $SendTimeoutStdout `
        -StderrPath $SendTimeoutStderr `
        -Arguments @("1")
    $SendTimeoutWatch.Stop()
    [Environment]::SetEnvironmentVariable("GIT_LOOPY_SEND_TIMEOUT_SECONDS", $null)
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_SLEEP", $null)
    Assert-Equal 0 $Status "a bounded slow turn must not fail the Run"
    Assert-True (
        $SendTimeoutWatch.Elapsed.TotalSeconds -lt 30
    ) "the slow turn was not bounded (Run took $($SendTimeoutWatch.Elapsed.TotalSeconds)s, bound 1s)"
    $SendTimeoutStderrText = [IO.File]::ReadAllText($SendTimeoutStderr)
    Assert-Contains $SendTimeoutStderrText `
        "copilot turn exceeded the 1s send timeout" `
        "the bounded turn warns that the send timeout fired"
    Assert-True (
        -not $SendTimeoutStderrText.Contains(
            "turn finished unbounded",
            [StringComparison]::Ordinal
        )
    ) "the slow turn ran to completion instead of being terminated at the bound"
    $SendTimeoutEvents = Read-Events -Path $SendTimeoutStdout
    Assert-Equal 0 (
        @($SendTimeoutEvents | Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }).Count
    ) "a terminated turn lands no agent commit"
    $SendTimeoutStrikes = @(
        $SendTimeoutEvents | Where-Object { $_["type"] -ceq "wrapper.strike" }
    )
    Assert-Equal 1 $SendTimeoutStrikes.Count "a bounded slow turn makes no progress"
    Assert-Equal "warn" $SendTimeoutStrikes[0]["outcome"] "the no-progress Strike warns"
    Assert-Equal "wrapper.run.end" $SendTimeoutEvents[-1]["type"] "send-timeout Run ends with run.end"
    Assert-Equal "iteration_cap" $SendTimeoutEvents[-1]["outcome"] "send-timeout Run outcome"
    Assert-Equal 1 $SendTimeoutEvents[-1]["iterations_run"] "send-timeout ran one Iteration"
    [Environment]::SetEnvironmentVariable("FAKE_GH_CLOSED", $null)

    # UTC timestamps are durable facts, not a duration clock. A backwards
    # wall-clock adjustment therefore preserves native monotonic lifecycle
    # durations while retaining both authoritative timestamps verbatim.
    $WallAdjusted = Get-GitLoopyIterationRollup `
        -IterationStartedMonotonic 10 `
        -FinishedMonotonic 15 `
        -ActiveIssue 41 `
        -ActiveStartedAt "2026-05-16T00:00:10.000Z" `
        -ActiveStartedMonotonic 10 `
        -FirstStartedAt "2026-05-16T00:00:10.000Z" `
        -FirstStartedMonotonic 10 `
        -ActiveClosedAt "2026-05-16T00:00:01.000Z" `
        -ActiveClosedMonotonic 12 `
        -AutoClosures 1
    Assert-Equal "closed" $WallAdjusted["outcome"] (
        "wall-adjusted closure outcome"
    )
    Assert-Equal 5 $WallAdjusted["duration_seconds"] (
        "wall-adjusted Iteration duration"
    )
    Assert-Equal 2 $WallAdjusted["issues"][0]["active_seconds"] (
        "wall-adjusted Active duration"
    )
    Assert-Equal 2 $WallAdjusted["issues"][0]["issue_elapsed_seconds"] (
        "wall-adjusted issue elapsed"
    )
    Assert-Equal "2026-05-16T00:00:10.000Z" (
        $WallAdjusted["issues"][0]["first_started_at"]
    ) "wall-adjusted first activation timestamp"
    Assert-Equal "2026-05-16T00:00:01.000Z" (
        $WallAdjusted["issues"][0]["closed_at"]
    ) "wall-adjusted closure timestamp"
}
finally {
    foreach ($Name in @(
        "FAKE_GH_LOG",
        "FAKE_GH_LIST_COUNT",
        "FAKE_GH_LIST_JSON",
        "FAKE_GH_VIEW_DIR",
        "FAKE_GH_AUTH_STATUS",
        "FAKE_GH_EMPTY_AFTER",
        "FAKE_GH_CLOSED",
        "FAKE_GH_CLOSE_DIR",
        "FAKE_GH_CLOSE_STATUS",
        "FAKE_COPILOT_FLAGS",
        "FAKE_COPILOT_PROMPT",
        "FAKE_COPILOT_CALLS",
        "FAKE_COPILOT_COMMITS",
        "FAKE_COPILOT_EXIT",
        "FAKE_COPILOT_OUTPUT_FILE",
        "FAKE_COPILOT_STDERR_FILE",
        "FAKE_COPILOT_PLAN_DIR",
        "FAKE_COPILOT_SLEEP",
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_ISSUE_SOURCE",
        "GIT_LOOPY_MAX_NMT_STRIKES",
        "GIT_LOOPY_DENY_TOOLS",
        "GIT_LOOPY_DENY_SKILLS",
        "GIT_LOOPY_SEND_TIMEOUT_SECONDS"
    )) {
        [Environment]::SetEnvironmentVariable($Name, $null)
    }
    if ([IO.Directory]::Exists($TempDir)) {
        Remove-TestDirectory -Path $TempDir
    }
}

Write-Output "PowerShell Orchestrator boundary: ok"
