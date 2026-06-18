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
- **Push-to-talk**: кнопка **«🎙»** — старт/стоп записи.
- **Авто-диалог (VAD)**: тумблер «Авто-диалог» + кружок. VAD на Web Audio API
  (RMS + авто-калибровка шума, чувствительность низкая/средняя/высокая) сам ловит
  конец фразы (пауза ~0.8 c), шлёт `audio/webm` → `POST /ask`, проигрывает ответ и
  снова слушает — диалог без повторных нажатий. На время ответа слух выключается
  (чтобы не ловить собственный голос). Распознанный вопрос / intent / ответ читаются
  из заголовков ответа (`X-Question`, `X-Intent`, `X-Answer`, percent-encoded).
- Текстовый режим: поле ввода → `POST /ask-text`.

## Пересобрать
```powershell
docker compose up -d --build voice-gateway
```
