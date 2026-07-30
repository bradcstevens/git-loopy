Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$Entrypoint = Join-Path $PortDir "git-loopy.ps1"
Import-Module (Join-Path $PortDir "GitLoopy.Continuation.psm1") -Force
$ScriptedGitHubPath = Join-Path $PSScriptRoot "ScriptedGitHub.ps1"
$FixturePath = Join-Path (
    Split-Path -Parent $PortDir
) "conformance/continuation-scenarios.json"
$ReleaseFixturePath = Join-Path (
    Split-Path -Parent $PortDir
) "conformance/release-version.json"
$Fixture = Get-Content -LiteralPath $FixturePath -Raw |
    ConvertFrom-Json -AsHashtable -DateKind String
$ReleaseFixture = Get-Content -LiteralPath $ReleaseFixturePath -Raw |
    ConvertFrom-Json -AsHashtable -DateKind String
$Pwsh = (
    Get-Command pwsh -CommandType Application |
        Select-Object -First 1
).Source
$TempRoot = Join-Path (
    [IO.Path]::GetTempPath()
) ("git-loopy-continuation-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($TempRoot) | Out-Null
$FakeBin = Join-Path $TempRoot "bin"
[IO.Directory]::CreateDirectory($FakeBin) | Out-Null
if ($IsWindows) {
    $FakeGh = Join-Path $FakeBin "gh.cmd"
    [IO.File]::WriteAllText(
        $FakeGh,
        "@echo off`r`n" +
            "`"$Pwsh`" -NoLogo -NoProfile -File " +
            "`"$ScriptedGitHubPath`" %*`r`n" +
            "exit /b %ERRORLEVEL%`r`n",
        [Text.ASCIIEncoding]::new()
    )
}
else {
    $FakeGh = Join-Path $FakeBin "gh"
    [IO.File]::WriteAllText(
        $FakeGh,
        "#!/bin/sh`nexec `"$Pwsh`" -NoLogo -NoProfile -File " +
            "`"$ScriptedGitHubPath`" `"`$@`"`n",
        [Text.UTF8Encoding]::new($false)
    )
    & chmod +x $FakeGh
    if ($LASTEXITCODE -ne 0) {
        throw "Could not make scripted gh transport executable."
    }
}

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

function Get-ConsumedSteps {
    param([Parameter(Mandatory)][string]$StatePath)

    if ([IO.File]::Exists($StatePath)) {
        return [int][IO.File]::ReadAllText($StatePath)
    }
    return 0
}

function Test-ScriptedGitHubTransport {
    $Probe = $Fixture["github_transport_probe"]
    $ScriptPath = Join-Path $TempRoot "probe-github-script.json"
    $StatePath = Join-Path $TempRoot "probe-github-state"
    $LogPath = Join-Path $TempRoot "probe-github-calls"
    [IO.File]::WriteAllText(
        $ScriptPath,
        (ConvertTo-Json -InputObject @($Probe["github_script"]) -Compress -Depth 50),
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText($LogPath, "", [Text.UTF8Encoding]::new($false))
    [IO.File]::Delete($StatePath)

    foreach ($Invocation in $Probe["invocations"]) {
        $StartInfo = [Diagnostics.ProcessStartInfo]::new()
        $StartInfo.FileName = $Pwsh
        $StartInfo.UseShellExecute = $false
        $StartInfo.RedirectStandardInput = $true
        $StartInfo.RedirectStandardOutput = $true
        $StartInfo.RedirectStandardError = $true
        $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_LOG"] = $LogPath
        $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT"] = $ScriptPath
        $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_STATE"] = $StatePath
        foreach ($Argument in @(
            "-NoLogo",
            "-NoProfile",
            "-File",
            $ScriptedGitHubPath
        )) {
            $StartInfo.ArgumentList.Add($Argument)
        }
        foreach ($Argument in $Invocation["arguments"]) {
            $StartInfo.ArgumentList.Add($Argument)
        }

        $Process = [Diagnostics.Process]::new()
        $Process.StartInfo = $StartInfo
        Assert-True ($Process.Start()) "scripted GitHub probe process starts"
        $ProbeInput = if ($Invocation.Contains("stdin_json")) {
            $Invocation["stdin_json"] | ConvertTo-Json -Compress -Depth 50
        }
        else {
            [string]($Invocation["stdin"] ?? "")
        }
        $Process.StandardInput.Write($ProbeInput)
        $Process.StandardInput.Close()
        $Stdout = $Process.StandardOutput.ReadToEnd()
        $Stderr = $Process.StandardError.ReadToEnd()
        $Process.WaitForExit()

        $Expected = $Invocation["expected"]
        Assert-True (
            $Process.ExitCode -eq $Expected["exit_code"]
        ) "scripted GitHub probe exit code"
        if ($Expected.Contains("stdout_json")) {
            $ActualJson = $Stdout | ConvertFrom-Json -AsHashtable |
                ConvertTo-Json -Compress -Depth 50
            $ExpectedJson = $Expected["stdout_json"] |
                ConvertTo-Json -Compress -Depth 50
            Assert-True (
                $ActualJson -ceq $ExpectedJson
            ) "scripted GitHub probe JSON stdout"
        }
        else {
            Assert-True (
                $Stdout -ceq [string]$Expected["stdout"]
            ) "scripted GitHub probe stdout"
        }
        Assert-True (
            $Stderr.Contains(
                [string]$Expected["stderr_contains"],
                [StringComparison]::OrdinalIgnoreCase
            )
        ) "scripted GitHub probe stderr"
    }

    Assert-True (
        (Get-ConsumedSteps $StatePath) -eq @($Probe["github_script"]).Count
    ) "scripted GitHub probe consumed every listed call"
    $ActualCalls = @([IO.File]::ReadAllLines($LogPath))
    Assert-True (
        (
            $ActualCalls | ConvertTo-Json -Compress
        ) -ceq (
            @($Probe["expected_github_calls"]) | ConvertTo-Json -Compress
        )
    ) "scripted GitHub probe call log"
}

function Invoke-Scenario {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Scenario,
        [AllowNull()]
        [Collections.IDictionary]$Transport
    )
    $Arguments = [Collections.Generic.List[string]]::new()
    $InputFile = Join-Path $TempRoot "$($Scenario["id"])-request.json"
    $Request = $Scenario["request"]
    $RequestContent = ""
    if ($null -ne $Request) {
        if ($Request.Contains("base64")) {
            $RequestContent = ""
        }
        elseif ($Request.Contains("raw")) {
            $RequestContent = [string]$Request["raw"]
        }
        elseif ($Request.Contains("raw_segments")) {
            $Builder = [Text.StringBuilder]::new()
            foreach ($Segment in $Request["raw_segments"]) {
                $Repeat = if ($Segment.Contains("repeat")) {
                    [int]$Segment["repeat"]
                }
                else {
                    1
                }
                for ($Index = 0; $Index -lt $Repeat; $Index++) {
                    [void]$Builder.Append([string]$Segment["text"])
                }
            }
            $RequestContent = $Builder.ToString()
        }
        else {
            $RequestContent = $Request["json"] |
                ConvertTo-Json -Compress -Depth 20
        }
        if ($Request["source"] -ceq "file") {
            if ($Request.Contains("base64")) {
                [IO.File]::WriteAllBytes(
                    $InputFile,
                    [Convert]::FromBase64String($Request["base64"])
                )
            }
            else {
                [IO.File]::WriteAllText(
                    $InputFile,
                    $RequestContent,
                    [Text.UTF8Encoding]::new($false)
                )
            }
        }
    }
    foreach ($Argument in $Scenario["arguments"]) {
        $Arguments.Add(
            $(if ($Argument -ceq '$INPUT_FILE') { $InputFile } else { $Argument })
        )
    }

    if ($null -eq $Transport) {
        $GithubLog = Join-Path $TempRoot "$($Scenario["id"])-github.log"
        $ScriptPath = Join-Path $TempRoot "$($Scenario["id"])-github-script.json"
        $StatePath = Join-Path $TempRoot "$($Scenario["id"])-github-state"
        [IO.File]::WriteAllText(
            $ScriptPath,
            (
                ConvertTo-Json `
                    -InputObject @($Scenario["github_script"]) `
                    -Compress `
                    -Depth 50
            ),
            [Text.UTF8Encoding]::new($false)
        )
        [IO.File]::WriteAllText($GithubLog, "", [Text.UTF8Encoding]::new($false))
        [IO.File]::Delete($StatePath)
    }
    else {
        $GithubLog = $Transport["GithubLog"]
        $ScriptPath = $Transport["ScriptPath"]
        $StatePath = $Transport["StatePath"]
    }

    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Pwsh
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_LOG"] = $GithubLog
    $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT"] = $ScriptPath
    $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_STATE"] = $StatePath
    $StartInfo.Environment["PATH"] = (
        $FakeBin + [IO.Path]::PathSeparator + $env:PATH
    )
    foreach ($Argument in @("-NoLogo", "-NoProfile", "-File", $Entrypoint)) {
        $StartInfo.ArgumentList.Add($Argument)
    }
    foreach ($Argument in $Arguments) {
        $StartInfo.ArgumentList.Add($Argument)
    }

    $Process = [Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    Assert-True ($Process.Start()) "$($Scenario["id"]) process starts"
    if ($null -ne $Request -and $Request["source"] -ceq "stdin") {
        $Process.StandardInput.Write($RequestContent)
    }
    $Process.StandardInput.Close()
    $Stdout = $Process.StandardOutput.ReadToEnd()
    $Stderr = $Process.StandardError.ReadToEnd()
    $Process.WaitForExit()
    return [ordered]@{
        ExitCode = $Process.ExitCode
        Stdout = $Stdout
        Stderr = $Stderr
        GithubCalls = if ([IO.File]::Exists($GithubLog)) {
            @([IO.File]::ReadAllLines($GithubLog))
        }
        else {
            @()
        }
        ConsumedSteps = Get-ConsumedSteps $StatePath
    }
}

function Assert-ScenarioResult {
    param(
        [Parameter(Mandatory)]
        [string]$Id,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Result,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Expected
    )

    Assert-True (
        $Result.ExitCode -eq $Expected["exit_code"]
    ) (
        "$Id exit code: expected $($Expected["exit_code"]), " +
        "got $($Result.ExitCode); stderr: $($Result.Stderr)"
    )

    if ($Expected.Contains("stdout_exact")) {
        Assert-True (
            $Result.Stdout -ceq [string]$Expected["stdout_exact"]
        ) "$Id exact stdout matches the shared fixture"
    }
    elseif ($null -eq $Expected["stdout"]) {
        Assert-True (
            [string]::IsNullOrEmpty($Result.Stdout)
        ) "$Id writes no stdout"
    }
    else {
        $ActualJson = $Result.Stdout |
            ConvertFrom-Json -AsHashtable |
            ConvertTo-Json -Compress -Depth 20
        $ExpectedJson = $Expected["stdout"] |
            ConvertTo-Json -Compress -Depth 20
        Assert-True (
            [Text.Json.Nodes.JsonNode]::DeepEquals(
                [Text.Json.Nodes.JsonNode]::Parse($ActualJson),
                [Text.Json.Nodes.JsonNode]::Parse($ExpectedJson)
            )
        ) (
            "$Id stdout matches the shared fixture; " +
            "expected: $ExpectedJson; actual: $ActualJson"
        )
        $Lines = @(
            $Result.Stdout -split "\r?\n" |
                Where-Object { $_.Length -gt 0 }
        )
        Assert-True (
            $Lines.Count -eq 1
        ) "$Id writes exactly one stdout object"
    }

    if ($Expected.Contains("stderr_exact")) {
        Assert-True (
            $Result.Stderr -ceq [string]$Expected["stderr_exact"]
        ) "$Id exact stderr matches the shared fixture"
    }
    elseif ($null -eq $Expected["stderr_contains"]) {
        Assert-True (
            [string]::IsNullOrEmpty($Result.Stderr)
        ) "$Id writes no stderr"
    }
    else {
        $Needle = [string]$Expected["stderr_contains"]
        Assert-True (
            $Result.Stderr.Contains($Needle, [StringComparison]::OrdinalIgnoreCase)
        ) "$Id stderr contains '$Needle'"
    }
}

function Test-GitHubFailureBoundaries {
    $Workflow = @(
        $Fixture["workflows"] |
            Where-Object { $_["id"] -ceq "trusted-planning-action" }
    )[0]
    $Cases = @(
        [ordered]@{
            id = "publish-github-failure"
            arguments = @("continuation", "publish")
            request = $Workflow["commands"][0]["request"]
            github_script = @(
                [ordered]@{
                    command = "api repos/octo/example/issues/comments/7001"
                    exit_code = 1
                    stdout = ""
                    stderr = "evidence unavailable"
                }
            )
            expected = [ordered]@{
                exit_code = 1
                stdout = [ordered]@{
                    ok = $false
                    operation = "publish"
                    error = [ordered]@{
                        code = "github_error"
                        message = (
                            "GitHub operation failed while reading " +
                            "transition evidence"
                        )
                    }
                }
                stderr_contains = "GitHub operation failed"
                github_calls = @(
                    "api repos/octo/example/issues/comments/7001"
                )
            }
        },
        [ordered]@{
            id = "reconcile-github-failure"
            arguments = @("continuation", "reconcile")
            request = $Workflow["commands"][1]["request"]
            github_script = @(
                [ordered]@{
                    command = (
                        "issue list --repo octo/example --state all " +
                        "--label git-loopy-continuation --limit 100 " +
                        "--json number,state,url,comments"
                    )
                    exit_code = 1
                    stdout = ""
                    stderr = "carrier discovery unavailable"
                }
            )
            expected = [ordered]@{
                exit_code = 1
                stdout = [ordered]@{
                    ok = $false
                    operation = "reconcile"
                    error = [ordered]@{
                        code = "github_error"
                        message = (
                            "GitHub operation failed while discovering " +
                            "indexed carriers"
                        )
                    }
                }
                stderr_contains = "GitHub operation failed"
                github_calls = @(
                    (
                        "issue list --repo octo/example --state all " +
                        "--label git-loopy-continuation --limit 100 " +
                        "--json number,state,url,comments"
                    )
                )
            }
        }
    )

    foreach ($Case in $Cases) {
        $Result = Invoke-Scenario -Scenario $Case
        Assert-ScenarioResult `
            -Id $Case["id"] `
            -Result $Result `
            -Expected $Case["expected"]
        Assert-True (
            (
                $Result.GithubCalls | ConvertTo-Json -Compress
            ) -ceq (
                @($Case["expected"]["github_calls"]) |
                    ConvertTo-Json -Compress
            )
        ) "$($Case["id"]) stops at the failed GitHub boundary"
        Assert-True (
            $Result.ConsumedSteps -eq @($Case["github_script"]).Count
        ) "$($Case["id"]) consumes the scripted failure"
    }
}

function Copy-GitLoopyDeepValue {
    param([AllowNull()][object]$Value)

    if ($Value -is [Collections.IDictionary]) {
        $Result = [ordered]@{}
        foreach ($Entry in $Value.GetEnumerator()) {
            $Result[[string]$Entry.Key] = Copy-GitLoopyDeepValue $Entry.Value
        }
        return $Result
    }
    if ($Value -is [Collections.IList] -and $Value -isnot [string]) {
        $Result = [Collections.Generic.List[object]]::new()
        foreach ($Item in $Value) {
            $Result.Add((Copy-GitLoopyDeepValue $Item))
        }
        return , $Result
    }
    return $Value
}

function ConvertTo-GitLoopyPointerTokens {
    param([Parameter(Mandatory)][string]$Path)

    $Trimmed = $Path.TrimStart("/")
    if ($Trimmed.Length -eq 0) {
        return @()
    }
    return @(
        $Trimmed.Split("/") |
            ForEach-Object { $_.Replace("~1", "/").Replace("~0", "~") }
    )
}

function Invoke-GitLoopyApplyPatch {
    param(
        [Parameter(Mandatory)][object]$Root,
        [AllowNull()][object]$Operations
    )

    foreach ($Operation in @($Operations)) {
        $Tokens = @(ConvertTo-GitLoopyPointerTokens ([string]$Operation["path"]))
        $Parent = $Root
        for ($Index = 0; $Index -lt $Tokens.Count - 1; $Index++) {
            $Token = $Tokens[$Index]
            if ($Parent -is [Collections.IList] -and $Parent -isnot [string]) {
                $Parent = $Parent[[int]$Token]
            }
            else {
                $Parent = $Parent[$Token]
            }
        }
        $Last = $Tokens[$Tokens.Count - 1]
        if ($Operation["op"] -ceq "remove") {
            if ($Parent -is [Collections.IList] -and $Parent -isnot [string]) {
                $Parent.RemoveAt([int]$Last)
            }
            else {
                $Parent.Remove([string]$Last)
            }
            continue
        }
        $Value = Copy-GitLoopyDeepValue $Operation["value"]
        if ($Parent -is [Collections.IList] -and $Parent -isnot [string]) {
            $TargetIndex = [int]$Last
            if ($TargetIndex -eq $Parent.Count) {
                $Parent.Add($Value)
            }
            else {
                $Parent[$TargetIndex] = $Value
            }
        }
        else {
            $Parent[[string]$Last] = $Value
        }
    }
    return $Root
}

function Get-GitLoopyMaterializedRequest {
    param([Parameter(Mandatory)][Collections.IDictionary]$Case)

    $Records = $Fixture["completion_records"]
    if ($Case.Contains("base_case")) {
        $Base = @(
            $Records["valid_publish_cases"] |
                Where-Object { $_["id"] -ceq $Case["base_case"] }
        )[0]
        $Request = Copy-GitLoopyDeepValue (
            $Records["publish_request_templates"][$Base["template"]]
        )
        $null = Invoke-GitLoopyApplyPatch -Root $Request -Operations $Base["patch"]
    }
    else {
        $Request = Copy-GitLoopyDeepValue (
            $Records["publish_request_templates"][$Case["template"]]
        )
    }
    $null = Invoke-GitLoopyApplyPatch -Root $Request -Operations $Case["patch"]
    return $Request
}

function ConvertTo-GitLoopyTestCanonicalValue {
    param([AllowNull()][object]$Value)

    if ($Value -is [Collections.IDictionary]) {
        $Result = [ordered]@{}
        $Keys = [string[]]@($Value.Keys)
        [Array]::Sort($Keys, [StringComparer]::Ordinal)
        foreach ($Key in $Keys) {
            $Result[$Key] = ConvertTo-GitLoopyTestCanonicalValue $Value[$Key]
        }
        return $Result
    }
    if ($Value -is [Collections.IList] -and $Value -isnot [string]) {
        $Result = [object[]]::new($Value.Count)
        for ($Index = 0; $Index -lt $Value.Count; $Index++) {
            $Result[$Index] = ConvertTo-GitLoopyTestCanonicalValue $Value[$Index]
        }
        return , $Result
    }
    return $Value
}

function Convert-GitLoopyTestJsonEscapesToRawUtf8 {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Json)

    # Language-neutral canonical bytes: rewrite only the U+0085, U+2028, and
    # U+2029 escapes that PowerShell's ConvertTo-Json emits so this oracle
    # matches Python json.dumps(ensure_ascii=False) and jq instead of merely
    # mirroring the production serializer's quirk.
    return [Text.RegularExpressions.Regex]::Replace(
        $Json,
        '(\\+)u(0085|2028|2029)',
        {
            param($Match)
            $Slashes = $Match.Groups[1].Value
            if (($Slashes.Length % 2) -eq 0) {
                return $Match.Value
            }
            $CodePoint = [Convert]::ToInt32($Match.Groups[2].Value, 16)
            return $Slashes.Substring(0, $Slashes.Length - 1) +
                [char]$CodePoint
        },
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
}

function Get-GitLoopyTestCanonicalJson {
    param([AllowNull()][object]$Value)

    $Json = ConvertTo-Json `
        -InputObject (ConvertTo-GitLoopyTestCanonicalValue $Value) `
        -Compress `
        -Depth 64
    return Convert-GitLoopyTestJsonEscapesToRawUtf8 $Json
}

function Get-GitLoopyTestSha256 {
    param([Parameter(Mandatory)][string]$Value)

    return [Convert]::ToHexString(
        [Security.Cryptography.SHA256]::HashData(
            [Text.UTF8Encoding]::new($false).GetBytes($Value)
        )
    ).ToLowerInvariant()
}

function Invoke-GitLoopyPublishProbe {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][object]$Request,
        [AllowNull()][object]$GithubScript
    )

    $Raw = ConvertTo-Json -InputObject $Request -Compress -Depth 64
    $Scenario = [ordered]@{
        id = $Id
        arguments = @("continuation", "publish", "--input", '$INPUT_FILE')
        request = [ordered]@{ source = "file"; raw = $Raw }
        github_script = @($GithubScript)
    }
    return Invoke-Scenario -Scenario $Scenario
}

function Assert-GitLoopyRejection {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][Collections.IDictionary]$Result,
        [Parameter(Mandatory)][string]$ExpectedStdout,
        [Parameter(Mandatory)][string]$ExpectedStderr
    )

    Assert-True (
        $Result.ExitCode -eq 1
    ) "$Id exit 1 (got $($Result.ExitCode)); stderr: $($Result.Stderr)"
    Assert-True (
        $Result.Stdout -ceq $ExpectedStdout
    ) "$Id stdout_exact; expected [$ExpectedStdout] actual [$($Result.Stdout)]"
    Assert-True (
        $Result.Stderr -ceq $ExpectedStderr
    ) "$Id stderr_exact; expected [$ExpectedStderr] actual [$($Result.Stderr)]"
    Assert-True (
        @($Result.GithubCalls).Count -eq 0
    ) "$Id reached GitHub before rejection"
}

# The shared live-frontier fixtures fit on one issue page and one comment page,
# so only a production-boundary probe can prove that complete all-state
# discovery traverses every page. Extra pages that carry nothing but unindexed
# carriers and ordinary discussion must reproduce the fixture's Reconciliation
# result exactly; stopping early would report an optimistic empty frontier.
function Test-ReconciliationPagination {
    $Scenario = @(
        $Fixture["scenarios"] |
            Where-Object {
                $_["id"] -ceq "missing-index-label-does-not-hide-revision"
            }
    )
    Assert-True ($Scenario.Count -eq 1) (
        "paginated Reconciliation reuses one shared live-frontier fixture"
    )
    $Scenario = $Scenario[0]
    $Steps = @($Scenario["github_script"])
    $IssueStep = $Steps[0]
    $CommentStep = $Steps[1]

    $FirstIssuePage = [Collections.Generic.List[object]]::new()
    foreach ($Number in 1000..1099) {
        $FirstIssuePage.Add([ordered]@{
            number = $Number
            state = "open"
            html_url = "https://github.com/octo/example/issues/$Number"
            labels = @()
            comments = 0
        })
    }
    $CarrierIssue = Copy-GitLoopyDeepValue @($IssueStep["stdout_json"])[0]
    $CarrierIssue["comments"] = 101

    $FirstCommentPage = [Collections.Generic.List[object]]::new()
    foreach ($CommentId in 8000..8099) {
        $FirstCommentPage.Add([ordered]@{
            id = $CommentId
            html_url = (
                "https://github.com/octo/example/issues/237" +
                "#issuecomment-$CommentId"
            )
            body = "Ordinary issue discussion."
            user = [ordered]@{ login = "maintainer"; type = "User" }
            created_at = "2026-07-22T19:00:00Z"
            updated_at = "2026-07-22T19:00:00Z"
        })
    }

    $GithubScript = [Collections.Generic.List[object]]::new()
    $GithubScript.Add([ordered]@{
        command = $IssueStep["command"]
        exit_code = $IssueStep["exit_code"]
        stdout_json = $FirstIssuePage.ToArray()
    })
    $GithubScript.Add([ordered]@{
        command = "api repos/octo/example/issues?state=all&per_page=100&page=2"
        exit_code = $IssueStep["exit_code"]
        stdout_json = @($CarrierIssue)
    })
    $GithubScript.Add([ordered]@{
        command = $CommentStep["command"]
        exit_code = $CommentStep["exit_code"]
        stdout_json = $FirstCommentPage.ToArray()
    })
    $GithubScript.Add([ordered]@{
        command = (
            "api repos/octo/example/issues/237/comments?per_page=100&page=2"
        )
        exit_code = $CommentStep["exit_code"]
        stdout_json = @($CommentStep["stdout_json"])
    })
    foreach ($Step in $Steps[2..($Steps.Count - 1)]) {
        $GithubScript.Add($Step)
    }

    $Transport = [ordered]@{
        GithubLog = Join-Path $TempRoot "reconciliation-pagination-github.log"
        ScriptPath = Join-Path $TempRoot "reconciliation-pagination-script.json"
        StatePath = Join-Path $TempRoot "reconciliation-pagination-state"
    }
    [IO.File]::WriteAllText(
        $Transport["ScriptPath"],
        (
            ConvertTo-Json `
                -InputObject $GithubScript.ToArray() `
                -Compress `
                -Depth 50
        ),
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText(
        $Transport["GithubLog"],
        "",
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::Delete($Transport["StatePath"])

    $Case = [ordered]@{
        id = "reconciliation-pagination"
        arguments = $Scenario["arguments"]
        request = $Scenario["request"]
    }
    $Result = Invoke-Scenario -Scenario $Case -Transport $Transport
    Assert-ScenarioResult `
        -Id "reconciliation-pagination" `
        -Result $Result `
        -Expected $Scenario["expected"]

    $ExpectedCalls = @($GithubScript | ForEach-Object { [string]$_["command"] })
    Assert-True (
        ($Result.GithubCalls | ConvertTo-Json -Compress) -ceq
            ($ExpectedCalls | ConvertTo-Json -Compress)
    ) (
        "paginated Reconciliation consumed every issue and comment page; " +
        "expected: $($ExpectedCalls | ConvertTo-Json -Compress); " +
        "actual: $($Result.GithubCalls | ConvertTo-Json -Compress)"
    )
    Assert-True (
        $Result.ConsumedSteps -eq $GithubScript.Count
    ) "paginated Reconciliation consumed every scripted GitHub call"
}

function Test-CompletionSemanticRejections {
    foreach ($Case in $Fixture["completion_records"]["semantic_rejections"]) {
        $Request = Get-GitLoopyMaterializedRequest -Case $Case
        $Result = Invoke-GitLoopyPublishProbe `
            -Id "semantic-$($Case["id"])" `
            -Request $Request `
            -GithubScript @()
        Assert-GitLoopyRejection `
            -Id "semantic-$($Case["id"])" `
            -Result $Result `
            -ExpectedStdout ([string]$Case["expected"]["stdout_exact"]) `
            -ExpectedStderr ([string]$Case["expected"]["stderr_exact"])
    }
}

$Script:RecordMarker = "<!-- git-loopy-continuation:1 -->"

function Invoke-GitLoopyEphemeralAcceptance {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][object]$Request,
        [AllowEmptyCollection()][string[]]$ExpectedKeys,
        [AllowNull()][string]$ExpectedStdout
    )

    $Result = Invoke-GitLoopyPublishProbe -Id $Id -Request $Request -GithubScript @()
    Assert-True (
        $Result.ExitCode -eq 0
    ) "$Id ephemeral exit 0 (got $($Result.ExitCode)); stderr: $($Result.Stderr)"
    $Receipt = ($Result.Stdout | ConvertFrom-Json -AsHashtable)["receipt"]
    Assert-True (
        $Receipt["status"] -ceq "unpublished"
    ) "$Id ephemeral receipt is unpublished"
    $ActualKeys = [string[]]@($Receipt["semantic_fingerprints"].Keys)
    [Array]::Sort($ActualKeys, [StringComparer]::Ordinal)
    $SortedExpected = [string[]]$ExpectedKeys
    [Array]::Sort($SortedExpected, [StringComparer]::Ordinal)
    Assert-True (
        ($ActualKeys -join ",") -ceq ($SortedExpected -join ",")
    ) "$Id ephemeral fingerprint keys; expected [$($SortedExpected -join ',')] actual [$($ActualKeys -join ',')]"
    foreach ($Fingerprint in $Receipt["semantic_fingerprints"].Values) {
        Assert-True (
            [string]$Fingerprint -cmatch "^[0-9a-f]{64}$"
        ) "$Id ephemeral fingerprint is a SHA-256 digest"
    }
    if (-not [string]::IsNullOrEmpty($ExpectedStdout)) {
        Assert-True (
            $Result.Stdout -ceq $ExpectedStdout
        ) "$Id ephemeral stdout_exact; expected [$ExpectedStdout] actual [$($Result.Stdout)]"
    }
    Assert-True (
        [string]::IsNullOrEmpty($Result.Stderr)
    ) "$Id ephemeral writes no stderr"
    Assert-True (
        @($Result.GithubCalls).Count -eq 0
    ) "$Id ephemeral publication reached GitHub"
}

function Invoke-GitLoopySharedDispositionProbe {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][object]$Request,
        [AllowNull()][string]$ExpectedStdout
    )

    $Completion = $Request["completion"]
    $CanonicalCompletion = Get-GitLoopyTestCanonicalJson $Completion
    $RevisionId = Get-GitLoopyTestSha256 $CanonicalCompletion
    $ExpectedFingerprints = [ordered]@{}
    if (-not [string]::IsNullOrEmpty($ExpectedStdout)) {
        $ExpectedFingerprints =
            ($ExpectedStdout | ConvertFrom-Json -AsHashtable -DateKind String)["receipt"]["semantic_fingerprints"]
    }
    $Record = [ordered]@{}
    foreach ($Entry in $Completion.GetEnumerator()) {
        $Record[[string]$Entry.Key] = $Entry.Value
    }
    $Record["revision_id"] = $RevisionId
    $Record["semantic_fingerprints"] = $ExpectedFingerprints
    $CanonicalRecord = Get-GitLoopyTestCanonicalJson $Record
    $Fence = [string][char]0x60 * 3
    $NewLine = "`n"
    $Body = "$Script:RecordMarker$NewLine${Fence}json$NewLine$CanonicalRecord$NewLine$Fence"

    $GithubScript = @(
        [ordered]@{
            command = "api repos/octo/example/issues/comments/7001"
            exit_code = 0
            stdout_json = [ordered]@{ id = 7001; user = [ordered]@{ login = "planner" } }
        },
        [ordered]@{
            command = (
                "label create git-loopy-continuation --repo octo/example " +
                "--color 5319E7 --description Repairable discovery index for " +
                "git-loopy Continuation records --force"
            )
            exit_code = 0
            stdout = ""
        },
        [ordered]@{
            command = (
                "issue edit 237 --repo octo/example " +
                "--add-label git-loopy-continuation"
            )
            exit_code = 0
            stdout = ""
        },
        [ordered]@{
            command = (
                "api --method POST repos/octo/example/issues/237/comments --input -"
            )
            exit_code = 0
            expected_stdin_json = [ordered]@{ body = $Body }
            stdout_json = [ordered]@{
                id = 9001
                html_url = "https://github.com/octo/example/issues/237#issuecomment-9001"
                user = [ordered]@{ login = "planner" }
            }
        },
        [ordered]@{
            command = "api repos/octo/example/issues/comments/9001"
            exit_code = 0
            stdout_json = [ordered]@{
                id = 9001
                html_url = "https://github.com/octo/example/issues/237#issuecomment-9001"
                body = $Body
                user = [ordered]@{ login = "planner" }
            }
        }
    )

    $Result = Invoke-GitLoopyPublishProbe -Id $Id -Request $Request -GithubScript $GithubScript
    Assert-True (
        $Result.ExitCode -eq 0
    ) "$Id shared exit 0 (got $($Result.ExitCode)); stderr: $($Result.Stderr)"
    $Receipt = ($Result.Stdout | ConvertFrom-Json -AsHashtable)["receipt"]
    Assert-True (
        $Receipt["status"] -ceq "committed"
    ) "$Id shared receipt is committed"
    Assert-True (
        $Receipt["revision_id"] -ceq $RevisionId
    ) "$Id shared receipt revision_id matches derived digest"
    Assert-True (
        (Get-GitLoopyTestCanonicalJson $Receipt["semantic_fingerprints"]) -ceq
        (Get-GitLoopyTestCanonicalJson $ExpectedFingerprints)
    ) "$Id shared receipt fingerprints match"
    if (-not [string]::IsNullOrEmpty($ExpectedStdout)) {
        Assert-True (
            $Result.Stdout -ceq $ExpectedStdout
        ) "$Id shared stdout_exact; expected [$ExpectedStdout] actual [$($Result.Stdout)]"
    }
    Assert-True (
        [string]::IsNullOrEmpty($Result.Stderr)
    ) "$Id shared writes no stderr"
    Assert-True (
        @($Result.GithubCalls).Count -eq 5
    ) "$Id shared publication GitHub boundary is exactly five calls"
    Assert-True (
        $Result.ConsumedSteps -eq 5
    ) "$Id shared publication consumed every scripted GitHub call"
}

function Get-GitLoopyEphemeralBaseRequest {
    $Request = Copy-GitLoopyDeepValue (
        $Fixture["completion_records"]["publish_request_templates"]["shared-continue"]
    )
    $Completion = $Request["completion"]
    $Completion["publication"] = "ephemeral"
    $Completion.Remove("carrier")
    $Completion["workstream"].Remove("anchor")
    $Completion["transition"]["evidence"] = [Collections.Generic.List[object]]::new()
    $Request["trusted_producers"] = [Collections.Generic.List[object]]::new()
    return $Request
}

function Invoke-GitLoopyLiteralPublishCase {
    param(
        [Parameter(Mandatory)][string]$Group,
        [Parameter(Mandatory)][Collections.IDictionary]$Case
    )

    $Id = "$Group-$($Case["id"])"
    $Request = Get-GitLoopyMaterializedRequest -Case $Case
    $ExpectedStdout = [string]$Case["expected"]["stdout_exact"]
    $Publication = [string]$Request["completion"]["publication"]
    if ($Publication -ceq "ephemeral") {
        $ExpectedKeys = [string[]]@(
            ($ExpectedStdout | ConvertFrom-Json -AsHashtable)["receipt"]["semantic_fingerprints"].Keys
        )
        Invoke-GitLoopyEphemeralAcceptance `
            -Id $Id -Request $Request -ExpectedKeys $ExpectedKeys -ExpectedStdout $ExpectedStdout
    }
    elseif ($Publication -ceq "shared") {
        Invoke-GitLoopySharedDispositionProbe `
            -Id $Id -Request $Request -ExpectedStdout $ExpectedStdout
    }
    else {
        Assert-True $false "$Id has unsupported fixture publication"
    }
}

function Test-CanonicalJsonRejections {
    foreach ($Case in $Fixture["completion_records"]["canonical_json_rejections"]) {
        $Request = Get-GitLoopyMaterializedRequest -Case $Case
        $Result = Invoke-GitLoopyPublishProbe `
            -Id "portable-$($Case["id"])" -Request $Request -GithubScript @()
        Assert-GitLoopyRejection `
            -Id "portable-$($Case["id"])" `
            -Result $Result `
            -ExpectedStdout ([string]$Case["expected"]["stdout_exact"]) `
            -ExpectedStderr ([string]$Case["expected"]["stderr_exact"])
    }
}

function Test-CanonicalJsonAcceptances {
    foreach ($Case in $Fixture["completion_records"]["canonical_json_acceptances"]) {
        $Request = Get-GitLoopyMaterializedRequest -Case $Case
        if ($Case.Contains("canonical_completion_bytes")) {
            $ActualBytes = [Text.Encoding]::UTF8.GetByteCount(
                (Get-GitLoopyTestCanonicalJson $Request["completion"])
            )
            Assert-True (
                $ActualBytes -eq [int]$Case["canonical_completion_bytes"]
            ) "portable-$($Case["id"]) canonical completion byte length"
        }
        $ExpectedStdout = [string]$Case["expected"]["stdout_exact"]
        $ExpectedKeys = [string[]]@(
            ($ExpectedStdout | ConvertFrom-Json -AsHashtable)["receipt"]["semantic_fingerprints"].Keys
        )
        Invoke-GitLoopyEphemeralAcceptance `
            -Id "portable-$($Case["id"])" `
            -Request $Request `
            -ExpectedKeys $ExpectedKeys `
            -ExpectedStdout $ExpectedStdout
    }
}

function Test-ValidPublishCases {
    foreach ($Case in $Fixture["completion_records"]["valid_publish_cases"]) {
        Invoke-GitLoopyLiteralPublishCase -Group "valid-publish" -Case $Case
    }
}

function Test-FingerprintCases {
    foreach ($Case in $Fixture["completion_records"]["fingerprint_cases"]) {
        Invoke-GitLoopyLiteralPublishCase -Group "fingerprint" -Case $Case
    }
}

function Test-TerminalOutcomeCases {
    foreach ($Case in $Fixture["completion_records"]["terminal_outcome_cases"]) {
        $Request = Get-GitLoopyMaterializedRequest -Case $Case
        Invoke-GitLoopySharedDispositionProbe `
            -Id "terminal-$($Case["id"])" `
            -Request $Request `
            -ExpectedStdout ([string]$Case["expected"]["stdout_exact"])
    }
}

function Test-ActionKindSchemas {
    $Records = $Fixture["completion_records"]
    foreach ($Entry in $Records["action_kind_schemas"].GetEnumerator()) {
        $Kind = [string]$Entry.Key
        $Schema = $Entry.Value
        $Request = Get-GitLoopyEphemeralBaseRequest
        $Action = $Request["completion"]["actions"][0]
        $Action["kind"] = $Kind
        $Action["interaction"] = Copy-GitLoopyDeepValue (
            $Records["interaction_examples"][$Schema["example_interaction"]]
        )
        Invoke-GitLoopyEphemeralAcceptance `
            -Id "action-kind-$Kind" `
            -Request $Request `
            -ExpectedKeys @("action") `
            -ExpectedStdout ([string]$Schema["expected_stdout_exact"])
    }
}

function Test-ConditionSchemas {
    $Records = $Fixture["completion_records"]
    foreach ($Entry in $Records["condition_schemas"].GetEnumerator()) {
        $Kind = [string]$Entry.Key
        $Schema = $Entry.Value
        $Request = Get-GitLoopyEphemeralBaseRequest
        $BaseAction = $Request["completion"]["actions"][0]
        $NewActions = [Collections.Generic.List[object]]::new()
        $ExpectedKeys = [Collections.Generic.List[string]]::new()
        foreach ($SupportKey in $Schema["supporting_action_keys"]) {
            $Support = Copy-GitLoopyDeepValue $BaseAction
            $Support["key"] = [string]$SupportKey
            $NewActions.Add($Support)
            $ExpectedKeys.Add([string]$SupportKey)
        }
        $Main = Copy-GitLoopyDeepValue $BaseAction
        $Prerequisites = [Collections.Generic.List[object]]::new()
        $Prerequisites.Add((Copy-GitLoopyDeepValue $Schema["example"]))
        $Main["prerequisites"] = $Prerequisites
        $NewActions.Add($Main)
        $ExpectedKeys.Add([string]$Main["key"])
        $Request["completion"]["actions"] = $NewActions
        Invoke-GitLoopyEphemeralAcceptance `
            -Id "condition-kind-$Kind" `
            -Request $Request `
            -ExpectedKeys $ExpectedKeys.ToArray() `
            -ExpectedStdout ([string]$Schema["expected_stdout_exact"])
    }
}

function Test-EphemeralPublicationExcludedFromReconciliation {
    $Request = Get-GitLoopyEphemeralBaseRequest
    Invoke-GitLoopyEphemeralAcceptance `
        -Id "ephemeral-publication" `
        -Request $Request `
        -ExpectedKeys @("action") `
        -ExpectedStdout $null
}

function Test-ProducerRevisionBound {
    $Request = Copy-GitLoopyDeepValue (
        $Fixture["completion_records"]["publish_request_templates"]["shared-continue"]
    )
    $Completion = $Request["completion"]
    $Advisory = [ordered]@{}
    for ($Index = 0; $Index -lt 5; $Index++) {
        $Advisory["note_$Index"] = ("x" * 8000)
    }
    $Advisory["note_5"] = ""
    $Completion["advisory_extensions"] = $Advisory
    $CompletionLength = [Text.Encoding]::UTF8.GetByteCount(
        (Get-GitLoopyTestCanonicalJson $Completion)
    )
    $Padding = 49000 - $CompletionLength + 1
    Assert-True (
        $Padding -gt 0 -and $Padding -le 8192
    ) "producer revision bound fixture padding is valid"
    $Completion["advisory_extensions"]["note_5"] = ("x" * $Padding)

    $Result = Invoke-GitLoopyPublishProbe `
        -Id "producer-revision-bound" -Request $Request -GithubScript @()
    Assert-True (
        $Result.ExitCode -eq 1
    ) "producer revision bound exit 1 (got $($Result.ExitCode))"
    $ErrorObject = ($Result.Stdout | ConvertFrom-Json -AsHashtable)["error"]
    Assert-True (
        $ErrorObject["code"] -ceq "invalid_request" -and
        $ErrorObject["message"] -ceq "Producer revision exceeds maximum record length 49152"
    ) "producer revision bound diagnostic; got [$($ErrorObject["message"])]"
    Assert-True (
        @($Result.GithubCalls).Count -eq 0
    ) "oversized Producer revision reached GitHub"
}

function Test-SemanticBeforeSize {
    $Request = Copy-GitLoopyDeepValue (
        $Fixture["completion_records"]["publish_request_templates"]["shared-continue"]
    )
    $Completion = $Request["completion"]
    $Completion.Remove("workstream")
    $Advisory = [ordered]@{}
    for ($Index = 0; $Index -lt 7; $Index++) {
        $Advisory["note_$Index"] = ("x" * 8192)
    }
    $Completion["advisory_extensions"] = $Advisory

    $Result = Invoke-GitLoopyPublishProbe `
        -Id "semantic-before-size" -Request $Request -GithubScript @()
    Assert-True (
        $Result.ExitCode -eq 1
    ) "semantic-before-size exit 1 (got $($Result.ExitCode))"
    $ErrorObject = ($Result.Stdout | ConvertFrom-Json -AsHashtable)["error"]
    Assert-True (
        $ErrorObject["message"] -ceq "completion is missing required field: workstream"
    ) "completion size rejection preceded semantic validation; got [$($ErrorObject["message"])]"
    Assert-True (
        @($Result.GithubCalls).Count -eq 0
    ) "malformed oversized completion reached GitHub"
}

function Test-NoGuidanceDispositions {
    $SharedRequest = Copy-GitLoopyDeepValue (
        $Fixture["completion_records"]["publish_request_templates"]["shared-continue"]
    )
    $SharedCompletion = $SharedRequest["completion"]
    $SharedCompletion.Remove("actions")
    $SharedCompletion["disposition"] = "no-guidance"
    $SharedReferences = [Collections.Generic.List[object]]::new()
    $SharedReferences.Add([ordered]@{
        kind = "issue"; repository = "octo/example"; number = 237
    })
    $SharedCompletion["no_guidance"] = [ordered]@{
        reason = "no-successor-created"
        summary = "No trusted successor exists."
        references = $SharedReferences
    }
    Invoke-GitLoopySharedDispositionProbe `
        -Id "no-guidance" -Request $SharedRequest -ExpectedStdout $null

    $EphemeralRequest = Get-GitLoopyEphemeralBaseRequest
    $EphemeralCompletion = $EphemeralRequest["completion"]
    $EphemeralCompletion.Remove("actions")
    $EphemeralCompletion["disposition"] = "no-guidance"
    $EphemeralReferences = [Collections.Generic.List[object]]::new()
    $EphemeralReferences.Add([ordered]@{
        kind = "issue"; repository = "octo/example"; number = 237
    })
    $EphemeralCompletion["no_guidance"] = [ordered]@{
        reason = "ephemeral-only"
        summary = "Advice remains outside shared Reconciliation."
        references = $EphemeralReferences
    }
    Invoke-GitLoopyEphemeralAcceptance `
        -Id "ephemeral-no-guidance" `
        -Request $EphemeralRequest `
        -ExpectedKeys @() `
        -ExpectedStdout $null
}

# The capability-coverage gate (Wrapper contract §8). Every scenario scoped to fewer
# than the whole family is a question the rest are never asked, so each narrowing must
# be registered and derived from what each distribution actually advertises. This runs
# in every family: an operator who installs only this distribution must still be able
# to prove it does not advertise an operation its own fixtures never exercise.
function Get-CoverageRecords {
    $Records = [ordered]@{}
    foreach ($Record in @($Fixture["scenarios"]) + @($Fixture["workflows"])) {
        $Records[[string]$Record["id"]] = $Record
    }
    return $Records
}

function Get-PinnedErrorCode {
    param([Parameter(Mandatory)][Collections.IDictionary]$Record)

    $Expected = $Record["expected"]
    $Body = $null
    $Exact = [string]($Expected["stdout_exact"] ?? "")
    if (-not [string]::IsNullOrEmpty($Exact)) {
        $Body = $Exact | ConvertFrom-Json -AsHashtable
    }
    elseif ($Expected["stdout"] -is [Collections.IDictionary]) {
        $Body = $Expected["stdout"]
    }
    if (
        $Body -is [Collections.IDictionary] -and
        $Body["error"] -is [Collections.IDictionary]
    ) {
        return [string]$Body["error"]["code"]
    }
    return "none"
}

# The end-to-end coverage gate. The foundation gate is a claim about ten locked
# stories driven through the real native commands, not about a count of fixtures, so
# the ten are written out here rather than read from the fixture: a locked story that
# quietly leaves the registry would otherwise take its own gate with it. This runs in
# every family for the same reason the capability gate does.
$LockedEndToEndScenarios = @(
    "planning-publication-and-aggregation"
    "read-only-human-refresh"
    "concurrent-equivalent-and-conflicting-publication"
    "blocked-to-ready-and-ready-to-blocked"
    "completion-and-retirement-receipts"
    "positive-afk-classification-and-dispatch"
    "explicit-human-and-attention-stops"
    "terminal-completion"
    "optional-handoff-context"
    "durable-transition-then-publication-failure"
)

function Test-EndToEndCoverageGate {
    $Coverage = $Fixture["end_to_end_coverage"]
    $Locked = $Coverage["locked_scenarios"]
    $Distributions =
        @($Fixture["capability_coverage"]["distributions"]) | Sort-Object -CaseSensitive
    $ScopedRecords = $Fixture["capability_coverage"]["scoped_records"]

    $Workflows = [ordered]@{}
    foreach ($Workflow in @($Fixture["workflows"])) {
        $Workflows[[string]$Workflow["id"]] = $Workflow
    }

    Assert-True (
        (@($Locked.Keys | ForEach-Object { [string]$_ }) -join ",") -ceq
            ($LockedEndToEndScenarios -join ",")
    ) "locked_scenarios names the ten locked end-to-end scenarios in order"

    $Exercised = [Collections.Generic.HashSet[string]]::new()
    foreach ($Entry in $Locked.GetEnumerator()) {
        $Scenario = [string]$Entry.Key
        $Ids = @($Entry.Value | ForEach-Object { [string]$_ })
        Assert-True ($Ids.Count -gt 0) "$Scenario names at least one workflow"

        $Covered = [Collections.Generic.HashSet[string]]::new()
        foreach ($Id in $Ids) {
            Assert-True ($Workflows.Contains($Id)) "$Scenario names known workflow $Id"
            $Workflow = $Workflows[$Id]
            $Narrowed = @($Workflow["distributions"] | ForEach-Object { [string]$_ })
            if (($Narrowed | Sort-Object -CaseSensitive) -join "," -cne ($Distributions -join ",")) {
                Assert-True (
                    [string]$ScopedRecords[$Id]["reason"] -ceq "capability-absent"
                ) "$Id is narrowed for a registered capability reason"
            }
            foreach ($Distribution in $Narrowed) {
                [void]$Covered.Add($Distribution)
            }
            foreach ($Command in @($Workflow["commands"])) {
                [void]$Exercised.Add([string]@($Command["arguments"])[1])
            }
        }
        Assert-True (
            (@($Covered) | Sort-Object -CaseSensitive) -join "," -ceq ($Distributions -join ",")
        ) "$Scenario is asked of every distribution"
    }

    foreach ($Operation in @("publish", "reconcile", "record-dispatch-result")) {
        Assert-True (
            $Exercised.Contains($Operation)
        ) "a locked end-to-end scenario exercises $Operation"
    }

    $Claimed = [Collections.Generic.HashSet[string]]::new(
        [string[]]@($Locked.GetEnumerator() | ForEach-Object {
            $_.Value | ForEach-Object { [string]$_ }
        }),
        [StringComparer]::Ordinal
    )
    foreach ($Id in @($Workflows.Keys)) {
        Assert-True (
            $Claimed.Contains([string]$Id)
        ) "workflow $Id is claimed by a locked scenario"
    }

    $Prefixes = @($Coverage["read_only_call_prefixes"] | ForEach-Object { [string]$_ })
    $Refreshes = 0
    foreach ($Id in @($Workflows.Keys)) {
        $Workflow = $Workflows[$Id]
        $Operations = @(
            @($Workflow["commands"]) |
                ForEach-Object { [string]@($_["arguments"])[1] } |
                Sort-Object -CaseSensitive -Unique
        )
        if ($Operations.Count -ne 1 -or $Operations[0] -cne "reconcile") {
            continue
        }
        $Refreshes++
        foreach ($Call in @($Workflow["expected_github_calls"])) {
            $Text = [string]$Call
            $Allowed = @(
                $Prefixes | Where-Object {
                    $Text.StartsWith($_, [StringComparison]::Ordinal)
                }
            ).Count -gt 0
            Assert-True (
                $Allowed -and -not $Text.Contains("--method", [StringComparison]::Ordinal)
            ) "read-only workflow $Id made only read calls"
        }
    }
    Assert-True (
        $Refreshes -gt 0
    ) "an end-to-end refresh is pinned, so the read-only gate proves something"
}

function Test-CapabilityCoverageGate {
    $Coverage = $Fixture["capability_coverage"]
    $Indexed = Get-CoverageRecords
    $Distributions = @($Coverage["distributions"]) | Sort-Object -CaseSensitive
    $ScopedRecords = $Coverage["scoped_records"]

    $Manifests = [ordered]@{}
    foreach ($Entry in $Coverage["manifest_scenarios"].GetEnumerator()) {
        $Manifests[[string]$Entry.Key] =
            $Indexed[[string]$Entry.Value]["expected"]["stdout"]["capabilities"]
    }

    $Narrowed = @(
        $Indexed.GetEnumerator() |
            Where-Object {
                $_.Value.Contains("distributions") -and
                (
                    (
                        @($_.Value["distributions"]) |
                            Sort-Object -CaseSensitive
                    ) -join ","
                ) -cne ($Distributions -join ",")
            } |
            ForEach-Object { [string]$_.Key } |
            Sort-Object -CaseSensitive
    )
    $Registered = @($ScopedRecords.Keys | Sort-Object -CaseSensitive)
    Assert-True (($Narrowed -join ",") -ceq ($Registered -join ",")) (
        "capability coverage registers exactly the narrowed scopes " +
        "(narrowed: $($Narrowed -join ', '); registered: $($Registered -join ', '))"
    )

    foreach ($Entry in $ScopedRecords.GetEnumerator()) {
        Assert-True (
            [string]$Entry.Value["reason"] -cin @($Coverage["scope_reasons"])
        ) "$($Entry.Key) declares a registered scope reason"
    }

    Assert-True (
        (
            @($Coverage["manifest_scenarios"].Keys | Sort-Object -CaseSensitive) -join ","
        ) -ceq ($Distributions -join ",")
    ) "manifest_scenarios names every distribution"

    foreach ($Entry in $Coverage["manifest_scenarios"].GetEnumerator()) {
        $ScenarioId = [string]$Entry.Value
        Assert-True (
            (@($Indexed[$ScenarioId]["distributions"]) -join ",") -ceq [string]$Entry.Key
        ) "$ScenarioId is scoped to $($Entry.Key) alone"
        Assert-True (
            [string]$ScopedRecords[$ScenarioId]["reason"] -ceq "manifest-identity"
        ) "$ScenarioId is registered as manifest-identity"
    }
    $Identities = @(
        $ScopedRecords.GetEnumerator() |
            Where-Object { [string]$_.Value["reason"] -ceq "manifest-identity" } |
            ForEach-Object { [string]$_.Key } |
            Sort-Object -CaseSensitive
    )
    Assert-True (
        ($Identities -join ",") -ceq (
            (
                @($Coverage["manifest_scenarios"].Values) |
                    ForEach-Object { [string]$_ } |
                    Sort-Object -CaseSensitive
            ) -join ","
        )
    ) "manifest-identity is claimed only by the manifest scenarios"

    foreach ($Entry in $ScopedRecords.GetEnumerator()) {
        if ([string]$Entry.Value["reason"] -cne "capability-absent") {
            continue
        }
        $Capability = [string]$Entry.Value["capability"]
        $Advertises = [bool]$Entry.Value["advertises"]
        $Expected = [Collections.Generic.List[string]]::new()
        foreach ($Manifest in $Manifests.GetEnumerator()) {
            $Optional = $Manifest.Value["optional_capabilities"]
            Assert-True ($Optional.Contains($Capability)) (
                "$($Entry.Key) names capability $Capability that " +
                "$($Manifest.Key) advertises"
            )
            if ([bool]$Optional[$Capability] -eq $Advertises) {
                $Expected.Add([string]$Manifest.Key)
            }
        }
        $ExpectedSorted = @($Expected | Sort-Object -CaseSensitive)
        $Actual = @(
            @($Indexed[[string]$Entry.Key]["distributions"]) |
                ForEach-Object { [string]$_ } |
                Sort-Object -CaseSensitive
        )
        Assert-True (($Actual -join ",") -ceq ($ExpectedSorted -join ",")) (
            "$($Entry.Key) is scoped to the distributions advertising " +
            "$Capability=$Advertises (expected: $($ExpectedSorted -join ', '); " +
            "actual: $($Actual -join ', '))"
        )
    }

    $Groups = [ordered]@{}
    foreach ($Entry in $ScopedRecords.GetEnumerator()) {
        if ([string]$Entry.Value["reason"] -cne "family-local-detail") {
            continue
        }
        $Group = [string]$Entry.Value["variant_group"]
        if (-not $Groups.Contains($Group)) {
            $Groups[$Group] = [Collections.Generic.List[string]]::new()
        }
        $Groups[$Group].Add([string]$Entry.Key)
    }
    Assert-True ($Groups.Count -gt 0) "the fixture declares family-local variant groups"

    foreach ($Group in $Groups.GetEnumerator()) {
        $MemberIds = @($Group.Value)
        $Operations = @(
            $MemberIds |
                ForEach-Object { [string]$ScopedRecords[$_]["operation"] } |
                Sort-Object -CaseSensitive -Unique
        )
        Assert-True ($Operations.Count -eq 1) (
            "variant group $($Group.Key) names exactly one operation"
        )
        $Operation = $Operations[0]
        $Advertising = [Collections.Generic.List[string]]::new()
        foreach ($Manifest in $Manifests.GetEnumerator()) {
            Assert-True ($Manifest.Value["operations"].Contains($Operation)) (
                "variant group $($Group.Key) names operation $Operation that " +
                "$($Manifest.Key) knows"
            )
            if ([bool]$Manifest.Value["operations"][$Operation]) {
                $Advertising.Add([string]$Manifest.Key)
            }
        }
        $Covered = [Collections.Generic.List[string]]::new()
        foreach ($MemberId in $MemberIds) {
            foreach ($Distribution in @($Indexed[$MemberId]["distributions"])) {
                $Covered.Add([string]$Distribution)
            }
        }
        $Unique = @($Covered | Sort-Object -CaseSensitive -Unique)
        Assert-True ($Unique.Count -eq $Covered.Count) (
            "variant group $($Group.Key) scopes each distribution to one member"
        )
        Assert-True (
            ($Unique -join ",") -ceq (
                (@($Advertising | Sort-Object -CaseSensitive)) -join ","
            )
        ) (
            "variant group $($Group.Key) covers every distribution advertising " +
            "$Operation (expected: $($Advertising -join ', '); " +
            "actual: $($Unique -join ', '))"
        )
        $Arguments = @(
            $MemberIds |
                ForEach-Object {
                    @($Indexed[$_]["arguments"]) -join " "
                } |
                Sort-Object -CaseSensitive -Unique
        )
        Assert-True ($Arguments.Count -eq 1) (
            "variant group $($Group.Key) members drive one argument vector"
        )
        $ExitCodes = @(
            $MemberIds |
                ForEach-Object { [int]$Indexed[$_]["expected"]["exit_code"] } |
                Sort-Object -Unique
        )
        Assert-True ($ExitCodes.Count -eq 1) (
            "variant group $($Group.Key) members agree on the exit code"
        )
        $Codes = @(
            $MemberIds |
                ForEach-Object { Get-PinnedErrorCode -Record $Indexed[$_] } |
                Sort-Object -CaseSensitive -Unique
        )
        Assert-True ($Codes.Count -eq 1) (
            "variant group $($Group.Key) members agree on the error code " +
            "(found: $($Codes -join ', '))"
        )
    }
}

function Test-CapabilityVerificationGate {
    # Setup verification (#257). The distribution running setup is the distribution
    # being verified, so the profile it judges against is the only part any other
    # family can see. It lands in the fixture as data and is pinned here against this
    # distribution's own declaration: three members that quietly judged different
    # requirements under one profile name would otherwise never disagree anywhere.
    $Verification = $Fixture["capability_verification"]
    $FixtureProfile = $Verification["profiles"]["foundation"]
    $Declared = Get-GitLoopyContinuationProfile -Name "foundation"

    Assert-True (
        (@($FixtureProfile["requirements"] | ForEach-Object { [string]$_ }) -join ",") -ceq
            (@($Declared["requirements"]) -join ",")
    ) "the PowerShell foundation profile requires exactly what the fixture pins"
    Assert-True (
        [string]$FixtureProfile["continuation_contract_version"] -ceq
            [string]$Declared["continuation_contract_version"]
    ) "the PowerShell foundation profile pins the fixture's contract version"
    Assert-True (
        [int]$FixtureProfile["record_format"] -eq [int]$Declared["record_format"]
    ) "the PowerShell foundation profile pins the fixture's record format"
    Assert-True (
        [string]$FixtureProfile["tracker_adapter"] -ceq
            [string]$Declared["tracker_adapter"]
    ) "the PowerShell foundation profile pins the fixture's tracker Adapter"
    Assert-True (
        (@($FixtureProfile["tracker_operations"] | ForEach-Object { [string]$_ }) -join ",") -ceq
            (@($Declared["tracker_operations"]) -join ",")
    ) "the PowerShell foundation profile pins the fixture's tracker operations"
    Assert-True (
        (@($FixtureProfile["native_operations"] | ForEach-Object { [string]$_ }) -join ",") -ceq
            (@($Declared["native_operations"]) -join ",")
    ) "the PowerShell foundation profile pins the fixture's native operations"
    Assert-True (
        [string]$FixtureProfile["mode_default"] -ceq [string]$Declared["mode_default"]
    ) "the PowerShell foundation profile pins the fixture's default mode"

    # The report profile (#263) additionally requires the `resolve-authority`
    # operation and the `report` mode to be advertised.
    $FixtureReportProfile = $Verification["profiles"]["report"]
    $DeclaredReport = Get-GitLoopyContinuationProfile -Name "report"
    Assert-True (
        (@($FixtureReportProfile["requirements"] | ForEach-Object { [string]$_ }) -join ",") -ceq
            (@($DeclaredReport["requirements"]) -join ",")
    ) "the PowerShell report profile requires exactly what the fixture pins"
    Assert-True (
        [string]$FixtureReportProfile["continuation_contract_version"] -ceq
            [string]$DeclaredReport["continuation_contract_version"]
    ) "the PowerShell report profile pins the fixture's contract version"
    Assert-True (
        [int]$FixtureReportProfile["record_format"] -eq [int]$DeclaredReport["record_format"]
    ) "the PowerShell report profile pins the fixture's record format"
    Assert-True (
        [string]$FixtureReportProfile["tracker_adapter"] -ceq
            [string]$DeclaredReport["tracker_adapter"]
    ) "the PowerShell report profile pins the fixture's tracker Adapter"
    Assert-True (
        (@($FixtureReportProfile["tracker_operations"] | ForEach-Object { [string]$_ }) -join ",") -ceq
            (@($DeclaredReport["tracker_operations"]) -join ",")
    ) "the PowerShell report profile pins the fixture's tracker operations"
    Assert-True (
        (@($FixtureReportProfile["native_operations"] | ForEach-Object { [string]$_ }) -join ",") -ceq
            (@($DeclaredReport["native_operations"]) -join ",")
    ) "the PowerShell report profile pins the fixture's native operations"
    Assert-True (
        [string]$FixtureReportProfile["mode_default"] -ceq [string]$DeclaredReport["mode_default"]
    ) "the PowerShell report profile pins the fixture's default mode"
    Assert-True (
        (@($FixtureReportProfile["required_modes"] | ForEach-Object { [string]$_ }) -join ",") -ceq
            (@($DeclaredReport["required_modes"]) -join ",")
    ) "the PowerShell report profile pins the fixture's required modes"

    # The verdict is taken on the manifest this distribution really advertises, so
    # the chain runs real manifest -> setup verdict with no hand-asserted link.
    # `verdicts` is nested by profile since #263 added a second named profile.
    # The profiles judged are read from the fixture's `profile_distributions`
    # rather than listed here, because `execute-frontier` (#264) is the first
    # requirement set only one member declares --- and this list is exactly what
    # #266 changes when this one does.
    $Manifest = Get-GitLoopyCapabilityManifest
    $DeclaredProfiles = @(
        $Verification["profile_distributions"].Keys |
            Where-Object { @($Verification["profile_distributions"][$_]) -contains "powershell" }
    )
    Assert-True (
        (@($DeclaredProfiles | Sort-Object) -join ",") -ceq "foundation,report"
    ) "the fixture attributes exactly the profiles PowerShell declares"
    foreach ($ProfileName in $DeclaredProfiles) {
        $Expected = $Verification["verdicts"][$ProfileName]["powershell"]
        $Verdict = Get-GitLoopyContinuationVerification `
            -Manifest $Manifest -Name $ProfileName

        Assert-True (
            [string]$Verdict["profile"] -ceq [string]$Expected["profile"]
        ) "the PowerShell $ProfileName verdict names the profile the fixture pins"
        Assert-True (
            [bool]$Verdict["satisfied"] -eq [bool]$Expected["satisfied"]
        ) "the PowerShell $ProfileName verdict on its own manifest matches the fixture"
        Assert-True (
            (@($Verdict["unsatisfied_requirements"]) -join ",") -ceq
                (@($Expected["unsatisfied_requirements"] | ForEach-Object { [string]$_ }) -join ",")
        ) "the PowerShell $ProfileName verdict leaves nothing unsatisfied the fixture does not pin"
        Assert-True (
            (@($Verdict["unsupported_optional_capabilities"]) -join ",") -ceq
                (@($Expected["unsupported_optional_capabilities"] |
                    ForEach-Object { [string]$_ }) -join ",")
        ) "the PowerShell $ProfileName verdict names the optional capabilities the fixture pins"
    }

    # A verifier that answered "satisfied" unconditionally would pass both checks
    # above, so every requirement is shown to fail on its own broken manifest.
    # Each refusal now names the profile it is judged against (#263).
    $Refusals = @($Verification["refusals"])
    Assert-True ($Refusals.Count -gt 0) (
        "the fixture registers at least one capability-verification refusal"
    )
    foreach ($Refusal in $Refusals) {
        $Id = [string]$Refusal["id"]
        # A refusal against a profile this distribution does not declare is
        # skipped rather than asked: an unknown profile is refused outright, so
        # asking would prove the wrong thing.
        if ($DeclaredProfiles -notcontains [string]$Refusal["profile"]) {
            continue
        }
        $Broken = Copy-GitLoopyDeepValue $Manifest
        $Path = @($Refusal["remove"] | ForEach-Object { [string]$_ })
        if ($Path.Count -eq 1) {
            $Broken.Remove($Path[0])
        }
        else {
            $Broken[$Path[0]].Remove($Path[1])
        }

        $RefusalProfile = [string]$Refusal["profile"]
        $Refused = Get-GitLoopyContinuationVerification `
            -Manifest $Broken -Name $RefusalProfile
        Assert-True (-not [bool]$Refused["satisfied"]) "refusal $Id is refused"
        Assert-True (
            (@($Refused["unsatisfied_requirements"]) -join ",") -ceq
                (@($Refusal["unsatisfied_requirements"] |
                    ForEach-Object { [string]$_ }) -join ",")
        ) "refusal $Id names exactly the requirement the fixture pins"
    }

    # An unknown profile is refused rather than silently widened:
    # `execute-frontier` is a requirement set the fixture attributes to Python
    # alone until #266, and answering it here would let a pass be read as
    # readiness for a mode this distribution does not advertise.
    $Widened = $false
    try {
        Get-GitLoopyContinuationProfile -Name "execute-frontier" | Out-Null
        $Widened = $true
    }
    catch {
        $Widened = $false
    }
    Assert-True (-not $Widened) "an unknown capability profile is refused"
}

try {
    Test-CapabilityCoverageGate
    Test-CapabilityVerificationGate
    Test-EndToEndCoverageGate
    Test-ScriptedGitHubTransport
    $CapabilityScenario = @(
        $Fixture["scenarios"] |
            Where-Object { $_["id"] -ceq "capabilities-powershell" }
    )
    Assert-True ($CapabilityScenario.Count -eq 1) (
        "PowerShell Continuation capabilities scenario is unique"
    )
    Assert-True (
        $CapabilityScenario[0]["expected"]["stdout"]["capabilities"]["release_version"] -ceq
            $ReleaseFixture["expected_release_version"]
    ) "PowerShell Continuation capabilities match the shared Release version"
    Test-GitHubFailureBoundaries
    Test-ReconciliationPagination
    Test-CompletionSemanticRejections
    Test-CanonicalJsonRejections
    Test-CanonicalJsonAcceptances
    Test-ValidPublishCases
    Test-FingerprintCases
    Test-TerminalOutcomeCases
    Test-ActionKindSchemas
    Test-ConditionSchemas
    Test-EphemeralPublicationExcludedFromReconciliation
    Test-ProducerRevisionBound
    Test-SemanticBeforeSize
    Test-NoGuidanceDispositions

    foreach ($Scenario in $Fixture["scenarios"]) {
        if (
            $Scenario.Contains("distributions") -and
            "powershell" -notin @($Scenario["distributions"])
        ) {
            continue
        }
        $Result = Invoke-Scenario -Scenario $Scenario
        $Expected = $Scenario["expected"]
        Assert-ScenarioResult `
            -Id $Scenario["id"] `
            -Result $Result `
            -Expected $Expected
        Assert-True (
            (
                $Result.GithubCalls | ConvertTo-Json -Compress
            ) -ceq (
                @($Expected["github_calls"]) | ConvertTo-Json -Compress
            )
        ) "$($Scenario["id"]) scripted GitHub calls match"
        Assert-True (
            $Result.ConsumedSteps -eq @($Scenario["github_script"]).Count
        ) "$($Scenario["id"]) consumed every scripted GitHub call"
    }

    foreach ($Workflow in $Fixture["workflows"]) {
        if ("powershell" -notin @($Workflow["distributions"])) {
            continue
        }
        $WorkflowId = $Workflow["id"]
        $Transport = [ordered]@{
            GithubLog = Join-Path $TempRoot "$WorkflowId-github.log"
            ScriptPath = Join-Path $TempRoot "$WorkflowId-github-script.json"
            StatePath = Join-Path $TempRoot "$WorkflowId-github-state"
        }
        [IO.File]::WriteAllText(
            $Transport.ScriptPath,
            (
                ConvertTo-Json `
                    -InputObject @($Workflow["github_script"]) `
                    -Compress `
                    -Depth 50
            ),
            [Text.UTF8Encoding]::new($false)
        )
        [IO.File]::WriteAllText(
            $Transport.GithubLog,
            "",
            [Text.UTF8Encoding]::new($false)
        )
        [IO.File]::Delete($Transport.StatePath)

        $CommandIndex = 0
        foreach ($Command in $Workflow["commands"]) {
            $CommandIndex++
            $Case = [ordered]@{
                id = "$WorkflowId-$CommandIndex"
                arguments = $Command["arguments"]
                request = $Command["request"]
            }
            $Result = Invoke-Scenario -Scenario $Case -Transport $Transport
            Assert-ScenarioResult `
                -Id $Case.id `
                -Result $Result `
                -Expected $Command["expected"]
        }

        $ActualCalls = @([IO.File]::ReadAllLines($Transport.GithubLog))
        Assert-True (
            (
                $ActualCalls | ConvertTo-Json -Compress
            ) -ceq (
                @($Workflow["expected_github_calls"]) |
                    ConvertTo-Json -Compress
            )
        ) "$WorkflowId scripted GitHub calls match"
        Assert-True (
            (Get-ConsumedSteps $Transport.StatePath) -eq
                @($Workflow["github_script"]).Count
        ) "$WorkflowId consumed every scripted GitHub call"
    }
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

[Console]::Out.WriteLine("PowerShell Continuation conformance: ok")
