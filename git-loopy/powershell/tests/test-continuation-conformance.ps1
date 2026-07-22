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
$Fixture = Get-Content -LiteralPath $FixturePath -Raw |
    ConvertFrom-Json -AsHashtable
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
        [Collections.IDictionary]$Scenario
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

try {
    Test-ScriptedGitHubTransport

    foreach ($Scenario in $Fixture["scenarios"]) {
        if (
            $Scenario.Contains("distributions") -and
            "powershell" -notin @($Scenario["distributions"])
        ) {
            continue
        }
        $Result = Invoke-Scenario -Scenario $Scenario
        $Expected = $Scenario["expected"]
        Assert-True (
            $Result.ExitCode -eq $Expected["exit_code"]
        ) "$($Scenario["id"]) exit code"

        if ($null -eq $Expected["stdout"]) {
            Assert-True (
                [string]::IsNullOrEmpty($Result.Stdout)
            ) "$($Scenario["id"]) writes no stdout"
        }
        else {
            $ActualObject = $Result.Stdout | ConvertFrom-Json -AsHashtable
            $ExpectedObject = $Expected["stdout"]
            $ActualJson = $ActualObject | ConvertTo-Json -Compress -Depth 20
            $ExpectedJson = $ExpectedObject | ConvertTo-Json -Compress -Depth 20
            Assert-True (
                $ActualJson -ceq $ExpectedJson
            ) "$($Scenario["id"]) stdout matches the shared fixture"
            $Lines = @(
                $Result.Stdout -split "\r?\n" |
                    Where-Object { $_.Length -gt 0 }
            )
            Assert-True (
                $Lines.Count -eq 1
            ) "$($Scenario["id"]) writes exactly one stdout object"
        }

        $Needle = $Expected["stderr_contains"]
        if ($null -eq $Needle) {
            Assert-True (
                [string]::IsNullOrEmpty($Result.Stderr)
            ) "$($Scenario["id"]) writes no stderr"
        }
        else {
            Assert-True (
                $Result.Stderr.Contains(
                    [string]$Needle,
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) "$($Scenario["id"]) stderr contains '$Needle'"
        }
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
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

[Console]::Out.WriteLine("PowerShell Continuation conformance: ok")
