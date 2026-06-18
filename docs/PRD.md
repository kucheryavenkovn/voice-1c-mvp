# PRD — voice-1c-mvp

## 1. Постановка задачи

Проверить на реальном железе связку:

- **speech-to-text** (распознавание русской речи, на GPU);
- **вызов LLM** по OpenAI-совместимому API — локальная модель в **LM Studio**,
  доступная из внутренней подсети Docker;
- **вызов API остатков** (имитация 1С);
- **text-to-speech** (синтез русской речи).

Требовался готовый `docker-compose.yml` и способ запуска из PowerShell.

## 2. Цели и вне-цели

**Цели**
- Один `docker compose up` поднимает весь голосовой цикл.
- Голосовой ввод с микрофона и голосовой ответ (веб-чат).
- LLM остаётся на хосте (LM Studio), контейнеры ходят к ней по сети.
- GPU используется для STT.
- Чёткая точка замены mock-API на реальную 1С.

**Вне цели (MVP)**
- Продакшен-авторизация, горизонтальное масштабирование.
- Реальная интеграция с 1CKit/MCP (оставлена точка `call_stock_api`).
- n8n (упрощён до встроенного оркестратора в `voice-gateway`).

## 3. Требования

| ID | Требование | Как выполнено |
|----|------------|---------------|
| FR-1 | Распознать русскую речь из аудио | `stt` (faster-whisper, medium, GPU float16) |
| FR-2 | Синтезировать русскую речь | `tts` (Piper, `ru_RU-dmitri-medium`) |
| FR-3 | Понять намерение и товар | LM Studio через OpenAI-chat, JSON-intent |
| FR-4 | Вернуть остаток товара | 1C MCP Toolkit (`POST /api/execute_query`, регистр `ТоварыНаСкладах`); mock-api — фоллбэк |
| FR-5 | Полный цикл «аудио→ответ» | `voice-gateway` `/ask` |
| FR-6 | Голосовой UI | веб-чат на `http://localhost:8103` |
| NFR-1 | Запуск из pwsh | `docker compose` + PS-скрипты |
| NFR-2 | GPU при наличии | `deploy.resources.reservations.devices` + автодетект |
| NFR-3 | LLM на хосте | `host.docker.internal:1234` + `extra_hosts` |

## 4. Нефункциональные допущения (окружение)

- Windows + Docker Desktop (WSL2), NVIDIA RTX 4090 (24 ГБ), NVIDIA Container Runtime.
- PowerShell 7.x, `curl.exe` (встроен в Windows 10+).
- LM Studio с загруженной моделью `google/gemma-4-e4b`.

---

# 5. Пошаговое воспроизведение развёртывания

> Этот раздел повторяет **ровно то, что делалось при сборке**, включая命中шие
> ошибки и их исправления. Воспроизвести с нуля можно командами ниже.

## 5.1. Проверка окружения

```powershell
docker --version                 # Docker version 29.5.3
docker compose version           # v5.1.4
pwsh --version                   # PowerShell 7.6.2

# GPU виден из контейнера?
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
# -> NVIDIA GeForce RTX 4090, 24564 MiB

# LM Studio отвечает?
Invoke-RestMethod http://127.0.0.1:1234/v1/models
# -> data[0].id = "google/gemma-4-e4b"
```

## 5.2. Структура проекта

```text
voice-1c-mvp/
  docker-compose.yml  .env.example  .env  README.md  .gitignore
  check-lmstudio.ps1  test-pipeline.ps1
  docs/  PRD.md  ARCHITECTURE.md  API.md
  stt/            Dockerfile  app.py  requirements.txt  README.md
  tts/            Dockerfile  app.py  requirements.txt  README.md
  mock-api/       Dockerfile  app.py  requirements.txt  README.md
  voice-gateway/  Dockerfile  app.py  requirements.txt  README.md
                  static/index.html   (веб-чат)
  samples/        question.wav  answer.wav
```

## 5.3. Сборка и подъём

```powershell
Copy-Item .env.example .env
./check-lmstudio.ps1          # хост + контейнер + chat-тест
docker compose build          # ~3-5 мин: faster-whisper, cuDNN, Piper-голос
docker compose up -d
docker compose ps             # все 4 контейнера Up
```

## 5.4. Полный прогон

```powershell
./test-pipeline.ps1
```

Ожидаемый результат:
```text
TTS  -> samples\question.wav: «Скажи, пожалуйста, какой остаток по товару молоко?»
STT  -> «скажи, пожалуйста, какой остаток по товару молоко?»
LM   -> {"action": "get_stock", "item": "молоко"}
1C   -> 42 шт.
TTS  -> samples\answer.wav: «Остаток по товару 'молоко': 42 штук.»
```

---

# 6. Зафиксированные проблемы и исправления

При первом запуске всплыло три бага. Все устранены в коде — раздел для будущего
воспроизведения и понимания «подводных камней».

## 6.1. `ModuleNotFoundError: No module named 'requests'` в `stt`

- **Симптом:** контейнер `v1c-stt` падал в рестарт (exit 1) при импорте
  `faster_whisper` (он внутри импортирует `requests` для скачивания модели).
- **Причина:** `requests` отсутствовал в `stt/requirements.txt`.
- **Фикс:** добавлен `requests>=2.31`.
- **Команда:** `docker compose up -d --build stt`.

## 6.2. `422 Field required: audio` на `POST /ask`

- **Симптом:** `answer.wav` содержал JSON-ошибку; curl слал multipart-поле `file`,
  а эндпоинт ждал параметр `audio`.
- **Причина:** несовпадение имени поля формы (`file` vs `audio`).
- **Фикс:** параметр `/ask` переименован в `file` (как в `/transcribe` и в `stt`).
- Проверка: `curl.exe -F "file=@samples\question.wav" http://127.0.0.1:8103/ask`.

## 6.3. `UnicodeEncodeError: 'latin-1'` в заголовках ответа

- **Симптом:** `POST /ask` возвращал 500 на кириллическом ответе.
- **Причина:** значения заголовков `X-Intent` содержали сырой JSON с кириллицей;
  HTTP-заголовки обязаны кодироваться latin-1.
- **Фикс:** все `X-*` заголовки проходят `urllib.parse.quote(...)` (percent-encoding),
  клиент декодирует `decodeURIComponent`.

## 6.4. Рестарт зависимостей при `--build <svc>`

- **Наблюдение:** `docker compose up -d --build voice-gateway` пересоздаёт `stt`
  и `tts` (из-за `depends_on`), поэтому сразу после сборки `/ask` мог вернуть 502.
- **Фикс:** `test-pipeline.ps1` и `check-lmstudio.ps1` начинают с health-check и
  ждут готовности; ручную проверку тоже начинать с `/health` каждого сервиса.

---

# 7. Критерии приёмки (Definition of Done)

- [x] `docker compose ps` — 4 контейнера `Up`.
- [x] `GET /health` всех сервисов возвращает `{"ok": true,...}`.
- [x] `STT` грузится на GPU (`device: cuda`, `cuda_devices: 1`).
- [x] `./test-pipeline.ps1` проходит, `samples\answer.wav` — валидное аудио.
- [x] Веб-чат `http://localhost:8103`: микрофон → голосовой ответ.
- [x] `call_stock_api()` — изолированная точка замены на реальную 1С.

# 8. Дальнейшие шаги (post-MVP)

- Заменить `call_stock_api` на реальный 1CKit/MCP `get_stock`.
- Перейти с JSON-prompt на нативный function-calling (qwen2.5/llama3.1).
- Потоковое TTS (chunked) для меньшей задержки первого слова.
- Аутентификация на шлюзе и wss вместо fetch.
- Заменить `WHISPER_MODEL=medium` на `large-v3` при запасе по VRAM.

# 9. Интеграция с 1С:ERP через MCP Toolkit

Реальные остатки берутся через [1C MCP Toolkit](https://github.com/ROCTUP/1c-mcp-toolkit)
(встроенный HTTP-сервер обработки `MCP_Toolkit.epf` на `:6003`, REST `/api/execute_query`).
Подробно: [docs/1C_INTEGRATION.md](1C_INTEGRATION.md).

**Что сделано при интеграции (воспроизведение):**
1. Прочитан README тулкита: обнаружены REST `/api/execute_query` и список инструментов
   (`execute_query`, `execute_code`, `get_metadata`, …).
2. Проверена доступность `:6003` — сначала `netstat` показал «ничего не слушает»
   (сервер не был запущен); после старта обработки — `0.0.0.0:6003 LISTENING`.
3. Из контейнера: `host.docker.internal:6003` отвечает (404 на `/`, валидация на `/api/...`).
4. Через `SELECT *` выяснена схема: каталог `Справочник.Склады` (без `Код`),
   регистр `РегистрНакопления.ТоварыНаСкладах.Остатки` с ресурсом **`ВНаличииОстаток`**
   (не `КоличествоОстаток`), измерения `Номенклатура`/`Склад`/`Помещение`.
5. Составлен агрегирующий запрос (сумма `ВНаличииОстаток` по `Склад.Наименование`,
   фильтр `ПОДОБНО` через `ВРЕГ`), проверен на «молоко» (480), «сахар» (590), not-found (`[0]:`).
6. Реализован `voice-gateway/onec.py` (REST-клиент + парсер текстового формата таблицы),
   `call_stock_api()` переключается `STOCK_BACKEND=1c|mock` с фоллбэком.

**Проверка:**
```powershell
Invoke-RestMethod http://127.0.0.1:8103/health    # -> "stock_backend":"1c","onec":true
# /ask-text «сколько у нас молока?» -> «Остаток 'молоко': всего 480 единиц. По складам: …»
```

**Нюансы:** единицы хранения у разных номенклатур могут различаться (кг/шт/упак) —
сумма по ним смешанная; для MVP допустимо, при необходимости — группировка по единице.
