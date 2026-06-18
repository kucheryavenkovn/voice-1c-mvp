# voice-gateway — оркестратор + веб-чат

Связывает STT → LM Studio → mock 1C → TTS и отдаёт голосовой веб-чат на `/`.

## Эндпоинты
- `GET /` — HTML голосового чата (микрофон + текстовый ввод).
- `GET /health` — статус + зондирует `stt`/`tts`.
- `POST /ask` — полный цикл из аудио (multipart, поле `file`).
- `POST /ask-text` — полный цикл из текста (`{"text": "..."}`).
- `POST /transcribe` — только STT.
- `POST /speak` — только TTS.

## Переменные окружения
| Переменная | По умолчанию |
|------------|--------------|
| `STT_URL` | `http://stt:8000` |
| `TTS_URL` | `http://tts:8000` |
| `STOCK_API_URL` | `http://mock-api:8000/api/stock` |
| `LM_BASE_URL` | `http://host.docker.internal:1234/v1` |
| `LM_API_KEY` | `lm-studio` |
| `LM_MODEL` | `auto` (берёт первую из `/v1/models`) |

## Точка интеграции с 1С
Функция `call_stock_api(item)` — единственное место, где ходят за остатком.
Замени тело на вызов своего 1CKit/MCP `get_stock`, верни совместимый JSON
(`found`, `quantity`, опц. `message`). Смотреть `mock-api/README.md`.

## Веб-чат (`static/index.html`)
- кнопка **«Говорить»** — `MediaRecorder` → `audio/webm` → `POST /ask` → автоплей ответа;
- текстовое поле — `POST /ask-text`;
- распознанный вопрос / intent / ответ выводятся в чат и читаются из заголовков
  ответа (`X-Question`, `X-Intent`, `X-Answer`, percent-encoded).

## Пересобрать
```powershell
docker compose up -d --build voice-gateway
```
