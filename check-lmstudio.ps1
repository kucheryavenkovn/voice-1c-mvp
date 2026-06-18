#requires -Version 7
<#
  Verifies LM Studio (OpenAI-compatible) is reachable from the host and
  from inside the docker network (host.docker.internal), and that the
  loaded model answers a chat completion.
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
try {
    $models = Invoke-RestMethod -Uri "$hostUrl/models" -TimeoutSec 5
    if ($models.data) {
        Write-Host ("    OK. models: " + ($models.data.id -join ', ')) -ForegroundColor Green
        $modelId = $models.data[0].id
    } else {
        throw "no models loaded in LM Studio"
    }
} catch {
    Write-Host "    FAIL: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    Open LM Studio -> Developer -> Start Server on :1234 and load a model." -ForegroundColor Yellow
    exit 1
}

Write-Host "`n==> [2/3] docker container -> host.docker.internal:1234" -ForegroundColor Cyan
docker run --rm --add-host=host.docker.internal:host-gateway `
    curlimages/curl:latest -s -m 10 `
    "http://host.docker.internal:1234/v1/models" `
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
        -ContentType 'application/json' -Body $body -TimeoutSec 60 `
        -Headers @{ Authorization = "Bearer $LM_API_KEY" }
    $answer = $resp.choices[0].message.content
    Write-Host "    OK. model answered: $answer" -ForegroundColor Green
} catch {
    Write-Host "    FAIL: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host "`nLM Studio is ready." -ForegroundColor Green
