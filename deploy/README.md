# Деплой на тестовый сервер (pull из GHCR)

Образы публикует CI (`.github/workflows/publish.yaml`) при каждом пуше в `master`:
`ghcr.io/kucheryavenkovn/voice-1c-mvp/{stt,tts,mock-api,gateway}`

## Альтернатива без GHCR: offline-бандл

Если тянуть из реестра нельзя — собери переносимый бандл на машине с образами
(Windows: `pwsh scripts/deploy/make-bundle.ps1 -Build -Zip [-SplitGB 3]`).
Получится каталог/zip с images.tar, CPU-compose, .env.example и установщиками
`install.sh` (Ubuntu) / `install.ps1` (Windows). Установка на целевой машине:
`chmod +x install.sh && ./install.sh` — сам загрузит образы, спросит адреса
LLM/1С и запустит стек. Подробности — в BUNDLE-README.txt внутри бандла.


## Первый запуск на сервере

```bash
# 1. Скопировать на сервер: deploy/docker-compose.server.yml, deploy/.env.server.example
#    (или весь каталог deploy/)

# 2. Каталог деплоя
mkdir -p ~/voice-1c && cd ~/voice-1c
# положить сюда docker-compose.server.yml и .env.server.example

# 3. Конфиг
cp .env.server.example .env
# отредактировать под окружение (LM_BASE_URL, ONEC_BASE_URL, WHISPER_MODEL)

# 4. Доступ к GHCR (пакеты приватные)
#    Создать PAT (classic) со scope read:packages:
#    https://github.com/settings/tokens
echo "TOKEN" | docker login ghcr.io -u <github-username> --password-stdin

# 5. Запуск
docker compose -f docker-compose.server.yml pull
docker compose -f docker-compose.server.yml up -d

# 6. Проверка
curl http://localhost:8103/health  # или docs: http://localhost:8103/docs
docker compose -f docker-compose.server.yml ps
```

## Обновление до новой версии

```bash
cd ~/voice-1c
docker compose -f docker-compose.server.yml pull
docker compose -f docker-compose.server.yml up -d
```

Вариант: фиксировать конкретную версию вместо `latest` — тег = short SHA коммита,
например `ghcr.io/kucheryavenkovn/voice-1c-mvp/gateway:bb1b192...` (см. вкладку
Packages репозитория или `gh run list --workflow publish`).

## Альтернатива: сделать пакеты публичными

Если сервер не должен знать токен: Settings → Packages → выбрать пакет →
Package settings → Change visibility → Public. Тогда `docker login` не нужен.

## Что нужно на хосте (вне контейнеров)

- LLM-инференс (vLLM :18020 или LM Studio :1234) — контейнеры ходят через
  `host.docker.internal`
- 1С: обработка `MCP_Toolkit.epf` с HTTP-сервером на :6003
- Если LLM/1С на другой машине — заменить `host.docker.internal` на её IP в `.env`

## Замечания по CPU

- STT: `WHISPER_MODEL=small` + `int8` — разумный компромисс на CPU;
  `medium` заметно медленнее
- GPU-секция из dev-compose в server-compose убрана (иначе compose упадёт
  без NVIDIA runtime)
