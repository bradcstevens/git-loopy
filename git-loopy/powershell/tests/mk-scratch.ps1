$src = 'test-orchestrator-boundary.ps1'
$text = [IO.File]::ReadAllText($src)
$anchor = '    $AgentClosedEvents = Read-Events -Path $AgentClosedStdout'
if (-not $text.Contains($anchor)) { throw 'anchor not found' }
$debug = $anchor + "`n" + @'
    Write-Host "=== EVENTS ==="
    Write-Host ($AgentClosedEvents | ConvertTo-Json -Depth 12)
    Write-Host "=== STDERR ==="
    Write-Host ([IO.File]::ReadAllText($AgentClosedStderr))
    Write-Host "=== GH LOG ==="
    if ([IO.File]::Exists($env:FAKE_GH_LOG)) { Write-Host ([IO.File]::ReadAllText($env:FAKE_GH_LOG)) }
    exit 0
'@
[IO.File]::WriteAllText('scratch-agent-closed.ps1', $text.Replace($anchor, $debug))
Write-Host 'written'
