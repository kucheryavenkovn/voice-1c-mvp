#requires -Version 7
<#
.SYNOPSIS
  Verifies the LLM server (OpenAI-compatible: vLLM / LM Studio / …) is reachable
  from the host and from inside the docker network (host.docker.internal), and
  that the loaded model answers a chat completion.
  Uses LM_BASE_URL / LM_API_KEY from .env (fallback: http://127.0.0.1:1234/v1).
#>
$ErrorActionPreference = 'Stop'

$envFile = Join-Path $PSScriptRoot '.env'
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            Set-Variable -Name $Matches[1] -Value $Matches[2] -Scope Script
        }
    }
}

$baseUrl = if ($LM_BASE_URL) { $LM_BASE_URL } else { 'http://127.0.0.1:1234/v1' }
$hostUrl = $baseUrl -replace 'host\.docker\.internal', '127.0.0.1'

Write-Host "==> [1/3] host  -> $hostUrl/models" -ForegroundColor Cyan
$headers = @{}
if ($LM_API_KEY) { $headers['Authorization'] = "Bearer $LM_API_KEY" }
try {
    $models = Invoke-RestMethod -Uri "$hostUrl/models" -TimeoutSec 5 -Headers $headers
    if ($models.data) {
        Write-Host ("    OK. models: " + ($models.data.id -join ', ')) -ForegroundColor Green
        $modelId = $models.data[0].id
    } else {
        throw "no models loaded"
    }
} catch {
    Write-Host "    FAIL: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    Start the LLM server (see LM_BASE_URL in .env) and load a model." -ForegroundColor Yellow
    exit 1
}

Write-Host "`n==> [2/3] docker container -> $baseUrl (as the gateway sees it)" -ForegroundColor Cyan
$authArgs = @()
if ($LM_API_KEY) { $authArgs = @('-H', "Authorization: Bearer $LM_API_KEY") }
docker run --rm --add-host=host.docker.internal:host-gateway `
    curlimages/curl:latest -s -m 10 @authArgs `
    "$baseUrl/models" `
    | Out-String | Write-Host

Write-Host "`n==> [3/3] chat completion smoke test (model: $modelId)" -ForegroundColor Cyan
$body = @{
    model      = $modelId
    temperature = 0
    max_tokens  = 256
    messages    = @(
        @{ role = 'system'; content = 'Reply with a single JSON object.' }
        @{ role = 'user';   content = 'Return exactly: {"action":"get_stock","item":"молоко"}' }
    )
} | ConvertTo-Json -Depth 5

try {
    $resp = Invoke-RestMethod -Uri "$hostUrl/chat/completions" -Method Post `
        -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 90 `
        -Headers $headers
    $answer = $resp.choices[0].message.content
    Write-Host "    OK. model answered: $answer" -ForegroundColor Green
} catch {
    Write-Host "    FAIL: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host "`nLLM server is ready." -ForegroundColor Green
