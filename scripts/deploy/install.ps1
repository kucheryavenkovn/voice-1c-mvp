[CmdletBinding()]
param(
    [string]$LmUrl = "",
    [string]$OnecUrl = "",
    [string]$Model = "small",
    [switch]$Yes
)

# Установка voice-1c-mvp offline-бандла (Windows, PowerShell 7).
# Запускать из каталога бандла: pwsh ./install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Загрузка образов" -ForegroundColor Cyan
if (Test-Path images.tar) {
    docker load -i images.tar
    if ($LASTEXITCODE -ne 0) { throw "docker load завершился с ошибкой" }
}
elseif (Get-ChildItem -Filter 'images.tar.part*' -ErrorAction SilentlyContinue) {
    # двоичная склейка частей (нарезка под FAT32/USB)
    $parts = Get-ChildItem -Filter 'images.tar.part*' | Sort-Object Name
    $out = [IO.File]::Create((Join-Path $PWD 'images.tar'))
    try {
        foreach ($p in $parts) {
            $in = [IO.File]::OpenRead($p.FullName)
            try { $in.CopyTo($out) } finally { $in.Dispose() }
        }
    } finally { $out.Dispose() }
    docker load -i images.tar
    if ($LASTEXITCODE -ne 0) { throw "docker load завершился с ошибкой" }
}
else {
    throw "images.tar (или images.tar.part*) не найден в $PWD"
}

Write-Host "==> Конфигурация .env" -ForegroundColor Cyan
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}

function Get-Env([string]$Key) {
    $line = Select-String -Path .env -Pattern "^$Key=" | Select-Object -Last 1
    if ($line) { return ($line.Line -replace "^$Key=", "") }
    return ""
}
function Set-Env([string]$Key, [string]$Value) {
    $content = Get-Content .env -Raw
    $content = $content -replace "(?m)^$Key=.*", "$Key=$Value"
    Set-Content .env $content -NoNewline
}

if (-not $Yes) {
    if (-not $LmUrl) {
        $default = Get-Env "LM_BASE_URL"
        $ans = Read-Host "LLM (LM Studio/vLLM, напр. http://192.168.1.50:1234/v1) [$default]"
        $LmUrl = if ($ans) { $ans } else { $default }
    }
    if (-not $OnecUrl) {
        $default = Get-Env "ONEC_BASE_URL"
        $ans = Read-Host "1С MCP Toolkit (напр. http://192.168.1.60:6003/api) [$default]"
        $OnecUrl = if ($ans) { $ans } else { $default }
    }
    $ans = Read-Host "Whisper-модель на CPU: tiny|base|small|medium|large-v3 [$Model]"
    $Model = if ($ans) { $ans } else { $Model }
}

if ($LmUrl) { Set-Env "LM_BASE_URL" $LmUrl }
if ($OnecUrl) { Set-Env "ONEC_BASE_URL" $OnecUrl }
Set-Env "WHISPER_MODEL" $Model
Set-Env "WHISPER_DEVICE" "cpu"
Set-Env "WHISPER_COMPUTE_TYPE" "int8"

Write-Host "==> docker compose up -d" -ForegroundColor Cyan
docker compose up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up завершился с ошибкой" }

Write-Host "==> Ожидание шлюза" -ForegroundColor Cyan
$port = Get-Env "PORT_GATEWAY"; if (-not $port) { $port = "8103" }
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-RestMethod "http://localhost:$port/health" -TimeoutSec 2 | Out-Null
        Write-Host ""
        Write-Host "Готово: http://localhost:$port" -ForegroundColor Green
        docker compose ps
        exit 0
    } catch { Start-Sleep 2 }
}
throw "Шлюз не ответил за 60 с. Логи: docker compose logs voice-gateway"
