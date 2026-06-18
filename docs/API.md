# HTTP API

Все сервисы — JSON-over-HTTP, если не указано иное. Базовые порты на хосте.

## voice-gateway — `http://localhost:8103`

### `GET /`
HTML-страница голосового веб-чата.

### `GET /health`
```json
{ "ok": true, "stt": true, "tts": true, "lm_base_url": "...", "lm_model": "auto" }
```

### `POST /ask` — полный голосовой цикл
multipart/form-data, поле **`file`** = аудио (wav/webm/ogg/mp3).

Ответ: `audio/wav` + заголовки (percent-encoded, UTF-8):
- `X-Question` — распознанный текст;
- `X-Intent` — JSON-намерение от LLM;
- `X-Answer` — текст ответа;
- `X-LM-Raw` — сырой ответ LLM (до 500 символов).

```
curl.exe -F "file=@samples\question.wav;type=audio/wav" http://127.0.0.1:8103/ask -o answer.wav
```

### `POST /ask-text` — цикл из текста
```json
{ "text": "какой остаток по молоку?" }
```
Ответ: `audio/wav` + те же заголовки, что у `/ask`.

### `POST /transcribe` — только STT
multipart, поле `file`. Возвращает `{"text": "...", "language": "ru"}`.

### `POST /speak` — только TTS
```json
{ "text": "Остаток: 42 шт." }
```
Ответ: `audio/wav`.

---

## stt — `http://localhost:8100`

### `GET /health`
```json
{ "ok": true, "model": "medium", "language": "ru", "device": "cuda",
  "compute_type": "float16", "cuda_devices": 1 }
```

### `POST /stt` (он же `POST /v1/audio/transcriptions`)
multipart, поле `file`. Возвращает `{"text": "...", "language": "ru"}`.

---

## tts — `http://localhost:8101`

### `GET /health`
```json
{ "ok": true, "voice": "/voices/ru_RU-dmitri-medium.onnx", "piper": "/usr/local/bin/piper" }
```

### `POST /tts`
```json
{ "text": "...", "length_scale": 1.0, "noise_scale": 0.667 }
```
Ответ: `audio/wav` (22050 Hz, mono, 16-bit PCM). `length_scale` > 1 — медленнее.

---

## mock-api (1C) — `http://localhost:8102`

### `GET /health`
```json
{ "ok": true, "items": 14 }
```

### `GET /api/stock?item=<товар>`
### `POST /api/stock`  body `{"item": "<товар>"}`
```json
{ "item": "молоко", "found": true, "quantity": 42,
  "message": "Остаток по товару 'молоко': 42 штук." }
```
Справочник товаров (lowercase-ключи): молоко, хлеб, сахар, соль, кофе, чай,
вода, мука, масло, сыр (+ англ. алиасы milk/bread/sugar/water). Незнакомый
товар → `found: false`.

---

## LM Studio — `http://localhost:1234/v1` (на хосте)

Из контейнеров: `http://host.docker.internal:1234/v1`.
- `GET /models` — список загруженных моделей;
- `POST /chat/completions` — OpenAI-совместимый чат (использует шлюз).
