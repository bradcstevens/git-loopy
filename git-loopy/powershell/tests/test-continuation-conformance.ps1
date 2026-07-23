Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$Entrypoint = Join-Path $PortDir "git-loopy.ps1"
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

    if ($null -eq $Expected["stdout"]) {
        Assert-True (
            [string]::IsNullOrEmpty($Result.Stdout)
        ) "$Id writes no stdout"
    }
    elseif ($Expected.Contains("stdout_exact")) {
        Assert-True (
            $Result.Stdout -ceq [string]$Expected["stdout_exact"]
        ) "$Id exact stdout matches the shared fixture"
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

try {
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
