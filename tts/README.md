# tts — Text-to-Speech (Piper, русский)

Синтез речи на **Piper** (`piper-tts`), голос `ru_RU-dmitri-medium`.
CPU-инференс — для TTS GPU не нужен.

## Эндпоинты
- `GET /health` — статус, путь к голосу, путь к `piper`.
- `POST /tts` — body `{"text", "length_scale"?, "noise_scale"?}`,
  ответ `audio/wav` (22050 Hz, mono, 16-bit).

`length_scale > 1` замедляет речь, `< 1` ускоряет.

## Переменные окружения
| Переменная | По умолчанию |
|------------|--------------|
| `PIPER_VOICE` | `/voices/ru_RU-dmitri-medium.onnx` |

## Голос
Качается при сборке из `rhasspy/piper-voices` (Hugging Face):
```
ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx(.json)
```
Сменить голос — поменяй `VOICE_NAME`/`VOICE_BASE` в `Dockerfile` (есть и `irina`,
`ruslan`; полный список — в репозитории rhasspy/piper-voices).

## Пересобрать
```powershell
docker compose up -d --build tts
```
