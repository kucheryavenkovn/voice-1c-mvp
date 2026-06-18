#requires -Version 7
<#
  End-to-end smoke test against a MOCK stack (no GPU, no 1C, no LM Studio needed).
  Brings up stt/tts stubs + mock-api + gateway, waits for health, runs a turn.
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSCommandPath
Set-Location $root

Write-Host "==> docker compose (mock) up" -ForegroundColor Cyan
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build
Start-Sleep -Seconds 4

function Wait-Health($port, $name) {
  for ($i = 0; $i -lt 40; $i++) {
    try { $h = Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 5; if ($h.ok) { return } }
    catch { }
    Start-Sleep -Seconds 4
  }
  throw "$name not healthy on $port"
}
foreach ($p in @(@('stt', 8100), @('tts', 8101), @('mock', 8102), @('gateway', 8103))) {
  Wait-Health $p[1] $p[0]; Write-Host "  $($p[0]) healthy" -ForegroundColor Green
}

Write-Host "`n==> /transcribe (mock STT)" -ForegroundColor Cyan
$tr = curl.exe -s -F "file=@$PSCommandPath;type=application/octet-stream" http://127.0.0.1:8103/transcribe
$tr

Write-Host "`n==> /ask-text via mock stack (STT mocked text -> LM? -> mock-api fallback)" -ForegroundColor Cyan
$jf = Join-Path $env:TEMP 'mock_ask.json'
@{ text = 'сколько молока?' } | ConvertTo-Json -Compress | Set-Content -Path $jf -Encoding utf8
$hf = Join-Path $env:TEMP 'mock_h.txt'
curl.exe -s -m 60 -D $hf -H "Content-Type: application/json; charset=utf-8" --data-binary "@$jf" http://127.0.0.1:8103/ask-text -o "$env:TEMP\mock_answer.wav"
Get-Content $hf | Where-Object { $_ -match 'X-Answer:' } | ForEach-Object {
  "ANS: " + [uri]::UnescapeDataString(($_ -replace '^X-Answer:\s*', '').Trim())
}

Write-Host "`nMock stack left running. Stop with:" -ForegroundColor Green
Write-Host "  docker compose -f docker-compose.yml -f docker-compose.mock.yml down"
