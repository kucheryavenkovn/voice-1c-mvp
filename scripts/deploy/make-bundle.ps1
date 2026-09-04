[CmdletBinding()]
param(
    [string]$OutputDir = "",
    [switch]$Build,
    [switch]$Zip,
    [int]$SplitGB = 0
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $OutputDir) { $OutputDir = Join-Path $repoRoot "dist\voice-1c-bundle" }

$images = @("v1c/stt", "v1c/tts", "v1c/mock-api", "v1c/gateway")

Write-Host "==> Проверка образов" -ForegroundColor Cyan
$missing = @()
foreach ($img in $images) {
    if (-not (docker image inspect $img 2>$null)) { $missing += $img }
}
if ($missing.Count -gt 0 -and -not $Build) {
    throw "Образы не найдены: $($missing -join ', '). Запусти с -Build или собери: docker compose build"
}

if ($Build) {
    Write-Host "==> Сборка образов (docker compose build)" -ForegroundColor Cyan
    docker compose -f (Join-Path $repoRoot "docker-compose.yml") build
    if ($LASTEXITCODE -ne 0) { throw "docker compose build завершился с ошибкой" }
}

if (Test-Path $OutputDir) { Remove-Item -Recurse -Force $OutputDir }
New-Item -ItemType Directory -Path $OutputDir | Out-Null

Write-Host "==> docker save -> images.tar" -ForegroundColor Cyan
$tarPath = Join-Path $OutputDir "images.tar"
docker save -o $tarPath @images
if ($LASTEXITCODE -ne 0) { throw "docker save завершился с ошибкой" }

if ($SplitGB -gt 0) {
    Write-Host "==> Нарезка images.tar на части по $SplitGB ГБ" -ForegroundColor Cyan
    $chunkSize = [long]$SplitGB * 1GB
    $buffer = New-Object byte[] (64MB)
    $reader = [IO.File]::OpenRead($tarPath)
    try {
        $partIndex = 0
        while ($true) {
            $partPath = "{0}.part{1:000}" -f $tarPath, $partIndex
            $writer = [IO.File]::Create($partPath)
            try {
                $remaining = $chunkSize
                while ($remaining -gt 0) {
                    $toRead = [Math]::Min($remaining, $buffer.Length)
                    $read = $reader.Read($buffer, 0, $toRead)
                    if ($read -eq 0) { break }
                    $writer.Write($buffer, 0, $read)
                    $remaining -= $read
                }
            } finally { $writer.Dispose() }
            if ((Get-Item $partPath).Length -eq 0) {
                Remove-Item $partPath
                break
            }
            $partIndex++
        }
    } finally { $reader.Dispose() }
    if ($partIndex -gt 0) { Remove-Item $tarPath }
    Write-Host ("    частей: {0}" -f $partIndex)
}

Write-Host "==> Копирование compose/env/install" -ForegroundColor Cyan
# CPU-compose с локальными образами v1c/* (без NVIDIA-секции: на машине без GPU
# dev-compose падает с "could not select device driver nvidia" — stt/tts не стартуют)
Copy-Item (Join-Path $PSScriptRoot "docker-compose.bundle.yml") (Join-Path $OutputDir "docker-compose.yml")
Copy-Item (Join-Path $repoRoot ".env.example") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "install.ps1") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "install.sh") $OutputDir

$readmeText = @"
voice-1c-mvp — offline bundle
=============================

Содержимое: images.tar (4 образа: stt, tts, mock-api, gateway),
docker-compose.yml (CPU, без GPU), .env.example, install.sh (Linux), install.ps1 (Windows).

Установка на Ubuntu/Debian (Docker + Compose v2):

    chmod +x install.sh
    ./install.sh                      # спросит адреса LLM и 1С, модель whisper
    sudo ./install.sh --install-docker  # если Docker ещё не установлен

Установка на Windows (PowerShell 7):

    pwsh ./install.ps1

Установщик сам: загрузит образы (docker load), создаст .env с опросом
LM_BASE_URL / ONEC_BASE_URL (LLM и 1С обычно на другой машине в сети —
указывай прямой IP: http://192.168.1.50:1234/v1), выставит CPU-режим
whisper (small/int8) и запустит стек.

После установки: открой http://localhost:8103
Правка конфигурации: .env, затем docker compose up -d (пересоздаст контейнеры).

Если images.tar нарезан на части (.part000, .part001, ...) — установщики
склеят их автоматически.

Вариант с интернетом: вместо бандла можно тянуть образы из GHCR — см.
deploy/README.md в репозитории (pull-based compose).
"@
Set-Content -Path (Join-Path $OutputDir "BUNDLE-README.txt") -Value $readmeText -Encoding utf8

if ($Zip) {
    Write-Host "==> Упаковка в zip" -ForegroundColor Cyan
    $zipPath = "$OutputDir.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath }
    Compress-Archive -Path "$OutputDir/*" -DestinationPath $zipPath
}

Write-Host ""
Write-Host "Готово: $OutputDir" -ForegroundColor Green
Get-ChildItem $OutputDir | ForEach-Object { "  {0}  {1:N1} MB" -f $_.Name, ($_.Length / 1MB) }
