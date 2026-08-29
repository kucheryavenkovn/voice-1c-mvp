# Архитектура

## Архитектура диалога: ScenarioFrame (с C-SCENARIO-FRAMES)

Бизнес-состояние диалога — персистентный **ScenarioFrame** на бэкенде
(`voice-gateway/scenarios/`), а не память LLM и не линейная FSM-«лестница».

```text
audio/text → STT → интерпретация реплики (typed commands)
          → ScenarioManager → ScenarioFrame → EntityResolver → 1C
          → подтверждение (execution gate) → ответ → TTS
```

Ключевые элементы:

| Элемент | Где | Смысл |
|---------|-----|-------|
| `ScenarioFrame` | `scenarios/models.py` | поля, коллекции (стабильные `item_id`), статусы разрешения, `focus`, `pending_action`, версия |
| Определения сценариев | `scenarios/definitions/*.yaml` | декларативные схемы (обязательность, зависимости/инвалидация), валидируются при загрузке |
| `ScenarioManager` | `scenarios/manager.py` | применяет закрытый набор typed-команд; централизованная инвалидация; PendingAction |
| `EntityResolver` | `scenarios/resolver.py` | mention → 1С → точный `EntityRef` (код/ссылка); ambiguous/not_found ≠ заполнено |
| Проекция | `scenarios/projection.py` | компактное состояние frame для маленького context window LLM (без истории чата) |
| Execution gate | `scenarios/execution.py` + `PendingAction` | документы 1С — только после явного подтверждения актуальной версии frame |

Пример frame и правки без переходов по FSM:

```text
REPAIR_ORDER
  vehicle:  resolved  Трактор Кировец К-744Р [ref ТР-0000008]
  items:
    item-A: nomenclature=resolved (Диск DK-300), quantity=5
    item-B: nomenclature=missing, quantity=2
  focus: items[item-B].nomenclature

реплика: «во второй строке поставь три штуки»
→ set_collection_field(items[item-B].quantity = 3)
→ версия frame++, PendingAction (если был) инвалидируется
```

Историческая FSM-«лестница» (`stage = await_*`) осталась только как
legacy-проекция для UI/тестов (`_sync_legacy_state`) — она не определяет
семантику реплик.

## Компоненты

```
┌─────────────────────────── ХОСТ (Windows) ───────────────────────────┐
│                                                                       │
│   LM Studio  ──:1234──►  OpenAI-compatible API  (google/gemma-4-e4b)  │
│   1C:ERP + MCP Toolkit ──:6003──► REST /api/execute_query             │
│                                                                       │
└──────────────────────────────────┬────────────────────────────────────┘
                                   │ host.docker.internal:{1234,6003}
┌──────────────────────── СЕТЬ DOCKER (voice-1c-mvp_default) ───────────┐
│                                                                       │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐            │
│   │  voice-gw    │───►│     stt      │    │     tts      │            │
│   │  :8000/8103  │    │ faster-      │    │   Piper      │            │
│   │              │───►│ whisper(GPU) │    │   ru         │            │
│   │  веб-чат /   │    └──────────────┘    └──────────────┘            │
│   │  /ask /ask-  │           ▲                   ▲                     │
│   │  text        │           │                   │                     │
│   │              │──► 1С остатки (:6003)         │                     │
│   │              │──► mock-api (фоллбэк)         │                     │
│   └──────┬───────┘                                │                     │
│          │ :8103                                  │                     │
└──────────┼────────────────────────────────────────┼────────────────────┘
        браузер                            wav-ответ
```

## Поток запроса `POST /ask` (голосовой цикл)

```
браузер ──audio.webm──► gateway /ask
                            │
                            │ 1) multipart field "file"
                            ▼
                          stt /stt ──► faster-whisper (cuda/float16, vad_filter)
                            │ ◄── {"text": "...", "language": "ru"}
                            │
                            │ 2) chat/completions (system-prompt → JSON-intent)
                            ▼
                       LM Studio :1234
                            │ ◄── {"action":"get_stock","item":"молоко"}
                            │
                            │ 3) get_stock(item):  STOCK_BACKEND=1c →
                            │    POST :6003/api/execute_query (ТоварыНаСкладах.Остатки,
                            │    группировка по Склад, ресурс ВНаличииОстаток);
                            │    при ошибке → mock-api GET /api/stock
                            ▼
                       mock-api (1C фоллбэк) / 1C MCP :6003
                            │ ◄── {"found":true,"quantity":42,"warehouses":[…],"message":"…","source":"1c"}
                            │
                            │ 4) build_answer(text)
                            │ 5) POST /tts {"text": answer}
                            ▼
                          tts (Piper) ──► wav (22050 Hz, mono, 16-bit)
                            │
                            ▼
браузер ◄──audio/wav + X-Question/X-Intent/X-Answer── gateway
```

## Порты

| Сервис | В контейнере | На хосте | Назначение |
|--------|--------------|----------|------------|
| stt    | 8000 | 8100 | `/health`, `/stt`, `/v1/audio/transcriptions` |
| tts    | 8000 | 8101 | `/health`, `/tts` |
| mock-api | 8000 | 8102 | `/health`, `/api/stock` |
| voice-gateway | 8000 | 8103 | `/` (чат), `/ask`, `/ask-text`, `/transcribe`, `/speak`, `/health` |

На хосте (вне compose): LM Studio `:1234`, 1C MCP Toolkit `:6003` — оба через
`host.docker.internal`.

## LLM-контракт (шлюз ↔ LM Studio)

Шлюз шлёт system-prompt с требованием вернуть **строго JSON**:
```json
{"action": "get_stock", "item": "<товар>"}
```
`extract_json()` срезает markdown-обрамление (```` ```json ... ``` ````) и
берёт первую `{...}`. Это сделало схему устойчивой на reasoning-модели без
надёжного нативного function-calling (как `gemma`).

## GPU

- `stt` запрашивает устройство через `deploy.resources.reservations.devices`
  (`driver: nvidia`, `count: ${GPU_COUNT:-all}`).
- Библиотеки CUDA: в образе `python:3.11-slim` ставятся `nvidia-cublas-cu12`
  и `nvidia-cudnn-cu12`, путь к ним пробрасывается в `LD_LIBRARY_PATH` — ctranslate2
  находит cuDNN/cuBLAS без CUDA base image.
- Автодетект: `WHISPER_DEVICE=auto` → `ctranslate2.get_cuda_device_count()`.
- Fallback: при ошибке загрузки на GPU модель грузится на `cpu/int8`.

## Отказоустойчивость / таймауты

- STT: 180 c (распознавание + возможная первая загрузка модели).
- LM: 60 c на chat-completion.
- TTS/mock: 60/10 c.
- `/health` у каждого сервиса; шлюз в `/health` зондирует `stt` и `tts`.
