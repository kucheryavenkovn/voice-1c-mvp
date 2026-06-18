# voice-1c-mvp

Голосовой MVP для интеграции с 1С: **говоришь в микрофон → распознавание речи →
LLM понимает намерение → запрос остатков в 1С → синтез речи → слышишь ответ**.
Всё разворачивается одной командой в Docker Compose.

> Цель проекта — быстро проверить связку STT + LLM (LM Studio) + 1C-API + TTS
> на реальном железе (NVIDIA GPU) перед тем, как строить продакшен-интеграцию.

---

## Что внутри

| Сервис          | Контейнер      | Порт | Назначение                                  | Стек                       |
|-----------------|----------------|------|---------------------------------------------|----------------------------|
| `stt`           | `v1c-stt`      | 8100 | распознавание речи (GPU)                    | faster-whisper + FastAPI   |
| `tts`           | `v1c-tts`      | 8101 | синтез русской речи                         | Piper (`ru_RU-dmitri-medium`) + FastAPI |
| `mock-api`      | `v1c-mock-api` | 8102 | заглушка API остатков 1С                    | FastAPI, in-memory         |
| `voice-gateway` | `v1c-gateway`  | 8103 | оркестратор + **веб-чат** (UI на `/`)       | FastAPI + статика          |

**LM Studio** работает на хосте Windows на порту `1234` и отдаёт OpenAI-совместимый
API. Контейнеры обращаются к нему через `host.docker.internal` — это и есть
«доступ к LLM по OpenAI-совместимому API через внутреннюю подсеть Docker».

```
                 ┌─────────────┐  host.docker.internal:1234
  voice-gateway ─┤  LM Studio  │  (google/gemma-4-e4b)
                 └─────────────┘
        │
   ┌────┼──────────────┬───────────────┐
   ▼    ▼              ▼               ▼
  stt  tts         mock-api(1C)    браузер (чат на /)
```

---

## Быстрый старт (PowerShell 7)

```powershell
# 0. LM Studio: Developer → Start Server на :1234, загрузить модель

# 1. конфиг
Copy-Item .env.example .env

# 2. проверка LM Studio (хост + из контейнера + чат-тест)
./check-lmstudio.ps1

# 3. собрать и поднять
docker compose build
docker compose up -d
docker compose ps

# 4. автотест пайплайна (генерит question.wav → полный цикл → answer.wav)
./test-pipeline.ps1
```

---

## Как поговорить с системой (голосовой чат)

Открой в браузере:

```
http://localhost:8103
```

- нажми **«🎙 Говорить»** — браузер запросит доступ к микрофону;
- говори вопрос, например: *«какой остаток по молоку?»*;
- нажми кнопку ещё раз чтобы остановить — система ответит голосом;
- ответ также виден текстом в чате и проигрывается в плеере.

Есть и текстовый режим (поле ввода + «Отправить») — если не хочется говорить вслух.

> Автовоспроизведение звука работает, потому что кнопка — это жест пользователя.
> Если браузер всё же блокирует звук — разреши звук для сайта `localhost:8103`.

---

## Документация

| Файл | Описание |
|------|----------|
| [docs/PRD.md](docs/PRD.md) | Продуктовые требования + **пошаговое воспроизведение развёртывания** (то, что делалось при сборке) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Архитектура, потоки данных, последовательность вызовов `/ask` |
| [docs/API.md](docs/API.md) | Все HTTP-эндпоинты всех сервисов |
| [stt/README.md](stt/README.md) | Сервис распознавания речи |
| [tts/README.md](tts/README.md) | Сервис синтеза речи |
| [mock-api/README.md](mock-api/README.md) | Заглушка API 1С |
| [voice-gateway/README.md](voice-gateway/README.md) | Оркестратор + веб-чат |

---

## Где заменить mock на реальную 1С

`voice-gateway/app.py`, функция `call_stock_api(item)` — единственная точка интеграции.
Сейчас идёт в `STOCK_API_URL=http://mock-api:8000/api/stock`. Замени на вызов своего
1CKit / MCP-tool `get_stock`. Контракт — в [mock-api/README.md](mock-api/README.md).

---

## Если GPU не подхватился

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

CPU-режим (в `.env`):
```env
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
GPU_COUNT=0
```
затем `docker compose up -d --build`.

---

## Управление

```powershell
docker compose logs -f voice-gateway   # логи шлюза
docker compose restart stt             # перезапуск сервиса
docker compose down                    # остановить всё
docker compose up -d --build           # пересобрать после правок
```

## Известные особенности

- Модель `google/gemma-4-e4b` — **reasoning-модель** (часть токенов уходит на
  рассуждение), поэтому в шлюзе `max_tokens=800`. Для обычных моделей (qwen/llama)
  можно вернуть `200`.
- Тестовые скрипты — **чистый PowerShell**, локальный Python / venv не нужны.
- Контейнеры после `docker compose up --build <svc>` пересоздают зависимости —
  `test-pipeline.ps1` начинает с health-check и подождёт готовности STT.
