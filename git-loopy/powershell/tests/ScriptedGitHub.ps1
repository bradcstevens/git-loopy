Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-RequiredEnvironmentPath {
    param([Parameter(Mandatory)][string]$Name)

    $Value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        [Console]::Error.WriteLine(
            "missing scripted GitHub environment variable: $Name"
        )
        exit 98
    }
    return $Value
}

$LogPath = Get-RequiredEnvironmentPath "GIT_LOOPY_SCRIPTED_GITHUB_LOG"
$ScriptPath = Get-RequiredEnvironmentPath "GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT"
$StatePath = Get-RequiredEnvironmentPath "GIT_LOOPY_SCRIPTED_GITHUB_STATE"
$Command = $args -join " "
$Utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::AppendAllText($LogPath, "$Command`n", $Utf8)

$Script = @(
    Get-Content -LiteralPath $ScriptPath -Raw |
        ConvertFrom-Json -AsHashtable
)
$Index = if ([IO.File]::Exists($StatePath)) {
    [int][IO.File]::ReadAllText($StatePath)
}
else {
    0
}
if ($Index -ge $Script.Count) {
    [Console]::Error.WriteLine("unlisted GitHub call: $Command")
    exit 98
}

$Step = $Script[$Index]
if ($Command -cne $Step["command"]) {
    [Console]::Error.WriteLine(
        "expected GitHub call '$($Step["command"])', got '$Command'"
    )
    exit 98
}

if ($Step.Contains("expected_stdin_json")) {
    try {
        $ActualStdin = [Console]::In.ReadToEnd() |
            ConvertFrom-Json -AsHashtable
    }
    catch {
        [Console]::Error.WriteLine("GitHub call stdin was not valid JSON")
        exit 98
    }
    $ActualJson = $ActualStdin | ConvertTo-Json -Compress -Depth 50
    $ExpectedJson = $Step["expected_stdin_json"] |
        ConvertTo-Json -Compress -Depth 50
    if ($ActualJson -cne $ExpectedJson) {
        [Console]::Error.WriteLine(
            "GitHub call stdin did not match fixture"
        )
        exit 98
    }
}
elseif ($Step.Contains("expected_stdin")) {
    if ([Console]::In.ReadToEnd() -cne $Step["expected_stdin"]) {
        [Console]::Error.WriteLine(
            "GitHub call stdin did not match fixture"
        )
        exit 98
    }
}

[IO.File]::WriteAllText($StatePath, [string]($Index + 1), $Utf8)
if ($Step.Contains("stdout_json")) {
    [Console]::Out.WriteLine(
        ($Step["stdout_json"] | ConvertTo-Json -Compress -Depth 50)
    )
}
else {
    [Console]::Out.Write([string]($Step["stdout"] ?? ""))
}
[Console]::Error.Write([string]($Step["stderr"] ?? ""))
exit [int]$Step["exit_code"]
