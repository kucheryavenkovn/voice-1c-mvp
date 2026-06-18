#requires -Version 7
<#
  End-to-end voice pipeline test (pure PowerShell, no local Python needed):
    1) TTS  -> samples\question.wav   (synthesize a known question)
    2) /ask -> STT -> LM Studio -> mock 1C -> TTS  (full loop)
    3) save samples\answer.wav and print STT/intent/answer
#>
$ErrorActionPreference = 'Stop'

$GATEWAY = 'http://127.0.0.1:8103'
$TTS     = 'http://127.0.0.1:8101'
$STT     = 'http://127.0.0.1:8100'

$samples = Join-Path $PSScriptRoot 'samples'
New-Item -ItemType Directory -Force -Path $samples | Out-Null
$questionWav = Join-Path $samples 'question.wav'
$answerWav   = Join-Path $samples 'answer.wav'
$headersFile = Join-Path $samples '.headers'

$questionText = 'Скажи, пожалуйста, какой остаток по товару молоко?'

Write-Host "==> [1/3] health checks" -ForegroundColor Cyan
foreach ($pair in @(@('gateway', $GATEWAY), @('tts', $TTS), @('stt', $STT))) {
    $name, $url = $pair
    try {
        $h = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 10
        Write-Host "    $name : OK  ($($h | ConvertTo-Json -Compress -Depth 3))" -ForegroundColor Green
    } catch {
        Write-Host "    $name : DOWN ($($_.Exception.Message))" -ForegroundColor Red
        exit 1
    }
}

Write-Host "`n==> [2/3] TTS -> question.wav" -ForegroundColor Cyan
Write-Host "    text: $questionText"
$body = @{ text = $questionText } | ConvertTo-Json -Compress
Invoke-WebRequest -Uri "$TTS/tts" -Method Post -ContentType 'application/json' `
    -Body $body -OutFile $questionWav | Out-Null
$len = (Get-Item $questionWav).Length
Write-Host "    saved: $questionWav ($len bytes)" -ForegroundColor Green

Write-Host "`n==> [3/3] full pipeline /ask (STT -> LM Studio -> mock 1C -> TTS)" -ForegroundColor Cyan
if (Test-Path $headersFile) { Remove-Item $headersFile -Force }
& curl.exe -s -m 180 -D $headersFile -F "file=@$questionWav;type=audio/wav" "$GATEWAY/ask" -o $answerWav
if ($LASTEXITCODE -ne 0) { throw "curl /ask failed (exit $LASTEXITCODE)" }

function Get-Header($name) {
    $line = Get-Content $headersFile | Where-Object { $_ -match "^$($name):" } | Select-Object -First 1
    if (-not $line) { return '' }
    $val = ($line -replace "^$($name):\s*", '').Trim()
    if ($val -match '^=') { $val = $val.Substring(1) }
    return [uri]::UnescapeDataString($val)
}

$sttText = Get-Header 'X-Question'
$intent  = Get-Header 'X-Intent'
$answer  = Get-Header 'X-Answer'
$raw     = Get-Header 'X-LM-Raw'

Write-Host "    STT text : $sttText"
Write-Host "    Intent   : $intent"
Write-Host "    LM raw   : $raw"
Write-Host "    Answer   : $answer"
$len = (Get-Item $answerWav).Length
Write-Host "    audio    : $answerWav ($len bytes)" -ForegroundColor Green

Write-Host "`nDone. Play the answer with:" -ForegroundColor Green
Write-Host "    start $answerWav"
