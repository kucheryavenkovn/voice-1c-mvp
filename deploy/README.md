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

## Деплой на Ubuntu из клона репозитория (без GHCR и бандла)

Рабочий путь, проверенный на Ubuntu 24.04 (VMware, CPU-only, ~4 ГБ RAM):

```bash
# Compose-плагин: в Ubuntu 24.04 пакет называется docker-compose-v2
# (docker-compose-plugin — имя из репозитория Docker, в ubuntu-архивах его нет)
sudo apt-get install -y docker-compose-v2
sudo usermod -aG docker $USER && exit   # перелогиниться для группы docker

git clone <репозиторий> && cd voice-1c-mvp

# CPU-машина: убрать nvidia-колёса из stt (балласт ~1.3 ГБ в образе,
# нужны только для GPU; ctranslate2 CPU-ядра уже в комплекте)
sed -i '/^nvidia-/d' stt/requirements.txt

cp .env.example .env
# отредактировать: STOCK_BACKEND=mock (или 1c+адрес), WHISPER small/cpu/int8
# LLM: LM_BASE_URL=http://<ip-машины-с-llm>:1234/v1 (пусто = deterministic fallback)

# Сборка — dev-compose (там build-контексты); GPU-секция на build не влияет
docker compose -f docker-compose.yml build

# Запуск — bundle-compose (CPU, без NVIDIA-секции: dev-compose упадёт на
# машине без nvidia runtime с "could not select device driver nvidia")
docker compose -f scripts/deploy/docker-compose.bundle.yml --project-directory . up -d
```

Грабли, на которые наступили:

- **`--project-directory .` обязателен**: без него compose берёт проектной
  директорией каталог compose-файла (`scripts/deploy/`) и не находит корневой
  `.env` — контейнеры стартуют на дефолтах (`STOCK_BACKEND=1c` вместо mock).
- **Whisper-модель** (~460 МБ для small) скачивается с HuggingFace при первом
  старте контейнера stt — нужен интернет или терпение при health-check.
- **RAM**: small/int8 + tts + gateway + mock ≈ 1.5–2 ГБ; на машине 3.8 ГБ
 Medium уже не влезает. Проверь свободные порты 8100–8103 (`ss -tlnp`).
- **Gitea**: ветка по умолчанию должна быть `master` (была `feat/order-parts` —
  свежий клон попадал не туда; меняется в настройках репозитория).

## Микрофон в браузере без HTTPS

`getUserMedia` (микрофон) работает только в secure context — `localhost` или
HTTPS. При открытии по прямому IP (`http://<server-ip>:8103`) будет ошибка
«Микрофон недоступен». Варианты без HTTPS:

1. **Флаг Chrome/Edge** (проще всего):
   - открыть `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
     (Edge: `edge://flags/#unsafely-treat-insecure-origin-as-secure`)
   - вписать `http://<server-ip>:8103` → Enabled → перезапустить браузер
   - микрофон на прямом IP заработает
2. **SSH-туннель** (без настроек браузера): `ssh -N -L 8103:localhost:8103
   <user>@<server>` и открывать `http://localhost:8103` — localhost
   secure context «из коробки»

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
