# stt — Speech-to-Text

Распознавание речи на **faster-whisper** (CTranslate2). Контейнер с поддержкой GPU.

## Эндпоинты
- `GET /health` — статус, модель, устройство, кол-во CUDA-устройств.
- `POST /stt` (=`POST /v1/audio/transcriptions`) — multipart поле `file`,
  возвращает `{"text", "language"}`.

## Переменные окружения
| Переменная | По умолчанию | Значение |
|------------|--------------|----------|
| `WHISPER_MODEL` | `medium` | `tiny`/`base`/`small`/`medium`/`large-v3` (кач-во/скорость) |
| `WHISPER_DEVICE` | `auto` | `auto`/`cuda`/`cpu` |
| `WHISPER_COMPUTE_TYPE` | `auto` | `auto`→`float16` на GPU, `int8` на CPU |
| `WHISPER_LANGUAGE` | `ru` | код языка или пусто (авто) |

## GPU
CUDA-библиотеки тянутся через pip (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) и
пробрасываются в `LD_LIBRARY_PATH` — отдельный CUDA-base-образ не нужен.
При ошибке загрузки на GPU автоматически откат на `cpu/int8`.

Модель (`medium`, ~1.5 ГБ) скачивается с Hugging Face при первом старте.

## Пересобрать
```powershell
docker compose up -d --build stt
docker compose logs -f stt
```
