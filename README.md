# voice-1c-mvp

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-05998B?logo=fastapi&logoColor=white)
![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![NVIDIA CUDA](https://img.shields.io/badge/NVIDIA-CUDA%20GPU-76B900?logo=nvidia&logoColor=white)
![STT](https://img.shields.io/badge/STT-faster--whisper-FF6F00)
![TTS](https://img.shields.io/badge/TTS-Piper-8E24AA)
![LLM](https://img.shields.io/badge/LLM-OpenAI--compatible-7C3AED) ![vLLM](https://img.shields.io/badge/vLLM-%3A18020-B010FB) ![LM Studio](https://img.shields.io/badge/LM_Studio-%3A1234-7C3AED) ![Ollama](https://img.shields.io/badge/Ollama-supported-FFFFFF?logo=ollama&logoColor=black)
![1C:ERP](https://img.shields.io/badge/1C-ERP%20%2B%20MCP%20Toolkit-D52B1E)
![PowerShell](https://img.shields.io/badge/scripts-PowerShell%207-5391FE?logo=powershell&logoColor=white)
![pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)
![License](https://img.shields.io/badge/license-GPLv3-blue)

Голосовой MVP для интеграции с 1С: **говоришь в микрофон → распознавание речи →
LLM понимает намерение → запрос остатков в 1С или оформление заказа → синтез речи →
слышишь ответ**. Всё разворачивается одной командой в Docker Compose.

> Цель проекта — быстро проверить связку STT + LLM + 1C-API + TTS на реальном
> железе (NVIDIA GPU) перед тем, как строить продакшен-интеграцию. LLM — любой
> инференс с OpenAI-совместимым API: **vLLM**, **LM Studio**, **Ollama**,
> llama.cpp-server и т.п.

## Демо

**Голосовой диалог (авто-режим VAD):** вопрос остатков по молоку, затем подбор
товаров под рецепт (морковь, яблоки, свёкла…) — полный цикл STT → LLM → 1С → TTS:

![Голосовой диалог: остатки и подбор по рецепту](docs/assets/demo-voice-stock.mp4)

**Заказ запчастей голосом:** «закажи три уплотнителя 77777…» → шлюз создаёт
документ **«Заказ поставщику»** в 1С через 1C MCP Toolkit (справа видно 1С:ERP),
затем добавляется вторая позиция заказа:

![Заказ запчастей голосом с созданием документа в 1С](docs/assets/demo-order-1c.mp4)

---

## Что внутри

| Сервис          | Контейнер      | Порт | Назначение                                  | Стек                       |
|-----------------|----------------|------|---------------------------------------------|----------------------------|
| `stt`           | `v1c-stt`      | 8100 | распознавание речи (GPU)                    | faster-whisper + FastAPI   |
| `tts`           | `v1c-tts`      | 8101 | синтез русской речи                         | Piper (`ru_RU-dmitri-medium`) + FastAPI |
| `mock-api`      | `v1c-mock-api` | 8102 | заглушка API остатков 1С                    | FastAPI, in-memory         |
| `voice-gateway` | `v1c-gateway`  | 8103 | оркестратор + **веб-чат** (UI на `/`)       | FastAPI + статика          |

**LLM-инференс** работает на хосте Windows и отдаёт OpenAI-совместимый API
(`/v1/chat/completions`). Подойдёт **любой** сервер такого типа — шлюз настраивается
через переменные `LM_BASE_URL` / `LM_API_KEY` / `LM_MODEL` в `.env`:

| Инференс   | Пример `LM_BASE_URL`                      | Примечание                                    |
|------------|-------------------------------------------|-----------------------------------------------|
| vLLM       | `http://host.docker.internal:18020/v1`    | для reasoning-моделей задай `enable_thinking=false` |
| LM Studio  | `http://host.docker.internal:1234/v1`     | Developer → Start Server                      |
| Ollama     | `http://host.docker.internal:11434/v1`    | OpenAI-совместимый эндпоинт из коробки        |

Контейнеры обращаются к LLM на хосте через `host.docker.internal` (в compose уже
прописан `extra_hosts`) — это и есть «доступ к LLM по OpenAI-совместимому API
через внутреннюю подсеть Docker». Для reasoning-моделей (Qwen3, gemma и т.п.)
шлюз умеет отключать «размышления» через `LM_ENABLE_THINKING=false`
(передаёт `chat_template_kwargs.enable_thinking`).

```
                  ┌──────────────────────────┐  host.docker.internal
   voice-gateway ─┤  LLM: vLLM / LM Studio / │  (LM_BASE_URL из .env)
                  │      Ollama / llama.cpp  │
                  └──────────────────────────┘
         │
    ┌────┼──────────────┬───────────────┐
    ▼    ▼              ▼               ▼
   stt  tts         mock-api(1C)    браузер (чат на /)
```

---

## Быстрый старт (PowerShell 7)

```powershell
# 0. Стартуй внешний LLM-инференс и 1С на хосте:
#    - LLM (любой OpenAI-совместимый): LM Studio → Developer → Start Server (:1234),
#      vLLM → `vllm serve <model> --port 18020 --enable_thinking false` (или LM_ENABLE_THINKING=false),
#      Ollama → `ollama serve` (:11434);
#      укажи выбранный сервер в .env → LM_BASE_URL / LM_MODEL;
#    - 1С: открой MCP_Toolkit.epf в нужной базе → «Встроенный сервер» → «Запустить сервер» (:6003).

# 1. конфиг
Copy-Item .env.example .env

# 2. проверка LLM-сервера (хост + из контейнера + чат-тест)
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

**Два режима** (переключатель «Авто-диалог» внизу):

- **Push-to-talk** (по умолчанию): **«🎙»** → говори (*«какой остаток по молоку?»*) → ещё раз для ответа.
- **Авто-диалог (VAD)**: включи тумблер, нажми кружок — и говори свободно.
  Система сама ловит конец фразы (пауза ~0.8 c), отвечает голосом и **снова слушает** —
  идёт полноценный диалог без повторных нажатий. Кружок — остановить сессию.

Ответ также виден текстом в чате. Есть текстовый режим (поле ввода + «Отправить»).

Как работает авто-режим: VAD на Web Audio API (сглаженный RMS, авто-калибровка и
подстройка фона шума, **двухпороговый** детектор: «старт речи» и «конец речи»).
Внизу есть регуляторы **порог** и **пауза до ответа** + живые цифры
`RMS / старт / стоп / шум` — если не останавливается из-за шума, подними «порог»
так, чтобы шум был ниже значения «старт». На время ответа слух выключается, чтобы
система не «слышала» сама себя; для сложной акустики лучше гарнитура. Это
pure-frontend, внешних зависимостей нет.

> Автовоспроизведение звука работает, потому что кнопка — это жест пользователя.
> Если браузер блокирует звук — разреши звук для сайта `localhost:8103`.

## Мониторинг этапов и задержек

Открой `http://localhost:8103/monitor` (или «📊 мониторинг» в шапке чата):
- **по этапам** (мс): STT, LM, 1C/stock, TTS, total — avg / p50 / p95 / max + полоса p95;
- **сводка**: turns, errors, error rate;
- **последние ходы**: тип, тайминги, запрос/товар, результат (✓ items=N / не найдено / ошибка).
Обновление каждые 2 с. Источник — `GET /metrics`.

Каждый ответ `/ask`, `/ask-text`, `/transcribe` несёт заголовок `X-Timings`
(`stt=..,lm=..,stock=..,tts=..,total=..`), а под репликой в чате появляется
`⏱ stt … · lm … · stock … · tts … · ∑ …`. В логе контейнера — строки `[trace] …`
(`docker compose logs voice-gateway`). Так видно узкие места — обычно это TTS/STT/LM.

**Отладка микрофона/VAD:** открой `http://localhost:8103/diag` (или ссылка «🎤 диагностика»
в шапке чата). Запиши фразу (авто-стоп 10 с) — увидишь график RMS, пик/среднее/шум,
**рекомендованный порог** и как VAD разметит речь на этой записи (с теми же ползунками
«порог»/«пауза»). Помогает подобрать значения под свой микрофон/помещение.

---

## Документация

| Файл | Описание |
|------|----------|
| [docs/PRD.md](docs/PRD.md) | Продуктовые требования + **пошаговое воспроизведение развёртывания** (то, что делалось при сборке) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Архитектура, потоки данных, последовательность вызовов `/ask` |
| [docs/API.md](docs/API.md) | Все HTTP-эндпоинты всех сервисов |
| [docs/1C_INTEGRATION.md](docs/1C_INTEGRATION.md) | **Интеграция с 1С:ERP через 1C MCP Toolkit** (установка `.epf`, запрос, парсер) |
| [docs/1C_METADATA.md](docs/1C_METADATA.md) | Справочник по объектам ИБ: ЗаказПоставщику, ЗаказНаРемонт, ПриобретениеТоваровУслуг, ПеремещениеТоваров, Склады, Номенклатура |
| [docs/1C_FINDINGS.md](docs/1C_FINDINGS.md) | Находки по базе для голосовых сценариев (что уже реализовано, что этап 2) |
| [docs/TESTING.md](docs/TESTING.md) | Тесты: pytest (unit+integration), mock-стек, live |
| [stt/README.md](stt/README.md) | Сервис распознавания речи |
| [tts/README.md](tts/README.md) | Сервис синтеза речи |
| [mock-api/README.md](mock-api/README.md) | Заглушка API 1С |
| [voice-gateway/README.md](voice-gateway/README.md) | Оркестратор + веб-чат |

---

## Источник остатков: реальная 1С или mock

По умолчанию шлюз берёт остатки из **1С:ERP через [1C MCP Toolkit](https://github.com/ROCTUP/1c-mcp-toolkit)** (REST `/api/execute_query`), mock-API используется как фоллбэк для тестов.

Переключатель — в `.env`:
```env
STOCK_BACKEND=1c                 # 1c | mock
STOCK_FALLBACK_TO_MOCK=true      # при ошибке 1С — откат на mock-api
ONEC_BASE_URL=http://host.docker.internal:6003/api
```

### Запуск 1C MCP Toolkit (MCP-сервер внутри 1С)

Остатки отдаёт сторонний MCP-сервер **[1C MCP Toolkit](https://github.com/ROCTUP/1c-mcp-toolkit)**
(автор ROCTUP, лицензия GPL-3.0). Он запускается **внутри 1С** как внешняя обработка и поднимает
HTTP-сервер — без изменения конфигурации и без публикации 1С на веб-сервере. Наш проект лишь
обращается к его REST API (`/api/execute_query`), поэтому отдельной MPL/GPL-зависимости в коде нет.

**Установка в нужной базе 1С:**

1. Скачай обработку со страницы релизов тулкита:
   [build/MCP_Toolkit.epf](https://github.com/ROCTUP/1c-mcp-toolkit/blob/main/build/MCP_Toolkit.epf)
   (или из [Releases](https://github.com/ROCTUP/1c-mcp-toolkit/releases)).
2. В нужной базе 1С (где есть регистр `ТоварыНаСкладах`, напр. 1С:ERP / КА / УТ):
   **Файл → Открыть…** → выбери `MCP_Toolkit.epf`.
3. В открывшейся форме выбери режим **«Встроенный сервер»** (без Python).
4. При необходимости укажи порт (по умолчанию `6003`) и ID канала (`ONEC_CHANNEL`).
5. Нажми **«Запустить сервер»**. В окне сообщений должно появиться:
   `Встроенный HTTP-сервер запущен на порту 6003`.
6. Не закрывай обработку, пока нужен голосовой цикл — сервер живёт в её сеансе.

Проверка, что 1С видна из Docker (шлюз ходит через `host.docker.internal`):
```powershell
# из хоста
Invoke-RestMethod http://127.0.0.1:6003/api/execute_query -Method Post `
  -ContentType 'application/json' -Body '{"query":"ВЫБРАТЬ 1"}'
# в /health шлюза должно быть "onec": true
Invoke-RestMethod http://127.0.0.1:8103/health
```

> Если в `netstat` порт `6003` не слушает — обработка не запущена или нажата не «Запустить сервер».
> Файервол Windows: для подключений с других машин добавь правило для `6003`; `localhost` и
> `host.docker.internal` обычно работают без правила.

Шлюз выполняет запрос к регистру `РегистрНакопления.ТоварыНаСкладах.Остатки`
(ресурс `ВНаличииОстаток`), ищет по **наименованию ИЛИ артикулу** (`Номенклатура.Артикул`)
и группирует сумму по складам. Перед поиском слова **лемматизируются** в именительный
падеж ед.ч. (*телевизоры→телевизор*, *стулья→стул*, *молока→молоко*), а пробелы между
словами становятся wildcard `%`. Спрашивать можно по-разному:
*«какой остаток по молоку?»*, *«по артикулу 7777»*, *«сколько по 45463728»*,
*«сколько телевизоров SHARP»*, а также **перечислить** позиции — *«по каким товарам
с названием сахар есть остатки»*, *«какие телевизоры в наличии»* (intent `list_stock`
выдаёт список имя + артикул + остаток).
Ответ: «Барбарис (конфеты) (арт. Арт-7777): всего 210 единиц. По складам: …» либо
перечень «Товары с остатком по 'сахар' (3 позиции): …». Подробности: [docs/1C_INTEGRATION.md](docs/1C_INTEGRATION.md).

`call_stock_api(item)` в `voice-gateway/app.py` — точка замены/расширения логики
(например, учитывать склад,_series, единицы измерения). Контракт остаточного JSON —
в [mock-api/README.md](mock-api/README.md).

### Заказ запчастей (intent `order_part`)

*«Закажи три уплотнителя»*, *«оформи заказ на 5 подшипников»* → LLM возвращает
`{"action":"order_part","item":"уплотнитель","quantity":3}` → шлюз создаёт
документ **Заказ поставщику** в 1С через `POST /api/execute_code` того же
MCP Toolkit (шапка копируется с последнего существующего заказа) и озвучивает:
«Создан заказ № ТД00-000012: Уплотнитель — 3 штуки. Потребность зарегистрирована.»

> Слово `Записать` по умолчанию в чёрном списке тулкита — уберите его в форме
> обработки, иначе заказ оформится в mock-фоллбэке (номер `ЗР-NNNNNNN`, видно
> в `source` ответа и логах). Аналогично остаткам: при любой ошибке 1С сценарий
> продолжается на mock-api (`POST /api/orders`).

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

- **Reasoning-модели** (Qwen3, gemma, deepseek-r1 и т.п.) тратят часть токенов на
  «размышление» — шлюз по умолчанию передаёт `chat_template_kwargs.enable_thinking=false`
  (управляется `LM_ENABLE_THINKING`, см. «Что внутри»). Для обычных моделей менять
  ничего не нужно.
- Тестовые скрипты — **чистый PowerShell**, локальный Python / venv не нужны.
- Контейнеры после `docker compose up --build <svc>` пересоздают зависимости —
  `test-pipeline.ps1` начинает с health-check и подождёт готовности STT.

---

## Roadmap

- **STT: GigaAM v3 (CTC-голова, ONNX int8)** как альтернатива faster-whisper
  для CPU-контуров — заметно точнее на русском (Golos: ~2–3% WER против
  ~7–8% у whisper-small), легче (~124M параметров против 244M) и быстрее
  на CPU, а CTC не «галлюцинирует» на коротких командах. План:
  `STT_BACKEND=gigaam`. Нюансы: экспорт ONNX + свой log-mel frontend;
  лицензия моделей — Salute (внутренний MVP ок, для продукта проверить).
- **stt: cpu/gpu-сплит requirements** — на CPU-машинах nvidia-колёса
  (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, ~1.3 ГБ) сейчас вырезаются
  вручную (`sed -i '/^nvidia-/d' stt/requirements.txt`), оформить
  build-аргументом `STT_GPU=true|false`.

---

## Лицензия

Код этого репозитория распространяется под **GNU GPL v3** — см. [LICENSE](LICENSE).
© 2026 Vladimir Kucheryavenko.

Проект обращается по сети к стороннему MCP-серверу
[1C MCP Toolkit](https://github.com/ROCTUP/1c-mcp-toolkit) (лицензия GPL-3.0), но не
включает его исходный код, поэтому GPL-обязательства тулкита на данный репозиторий
не распространяются.
