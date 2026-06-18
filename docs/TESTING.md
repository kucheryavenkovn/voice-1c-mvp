# Тестирование

Три уровня защиты от регрессий.

## 1. Unit + integration (pytest, in-process)

Тестируют логику шлюза и клиента 1С **без Docker/GPU/1С/LM Studio**. Все внешние
зависимости (STT, TTS, LM Studio, 1C MCP Toolkit) подменяются одной фейковой
транспортной реализацией (`FakeRequests` в `tests/conftest.py`), маршрутизируемой
по URL.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r voice-gateway\requirements.txt -r requirements-dev.txt
pytest --cov --cov-report=term-missing
```

Покрытие:
- `test_parser.py` — парсер текстовых таблиц 1C (`[N]{...}:`, кавычки/`\"`, числа,
  пусто `[0]:`), `_sanitize`, `_build_query` (наименование **ИЛИ** артикул, без инъекций).
- `test_messages.py` — построитель голосового ответа: 1 позиция / несколько, артикул,
  плюрализация (`единица/единицы/единиц`, `позиция/позиции/позиций`, `складе/складах`),
  десятичные количества, «и ещё на N складах».
- `test_extract_json.py` — извлечение JSON из ответа LLM (```` ```json ````, текст вокруг,
  мусор) и `build_answer`.
- `test_onec.py` — `onec.query_stock` целиком через фейковый 1C: группировка по позициям,
  один/несколько/пусто, десятичные, ошибка 1C → `RuntimeError`.
- `test_gateway.py` — эндпоинты `/`, `/diag`, `/health`, `/speak`, `/transcribe`,
  `/ask`, `/ask-text` (артикул/наименование/unknown), пустой STT → 400, падение TTS → 502,
  fallback к mock-api при недоступности 1C.

Голден-данные `ONEC_*` в `conftest.py` повторяют реальные ответы 1C MCP Toolkit.

## 2. Mock-стек (docker-compose, без GPU)

Поднимает весь пайплайн, заменяя тяжелые STT/TTS на микро-заглушки (`mocks/stt`,
`mocks/tts`) — те же имена сервисов/контейнеров, без GPU. 1C и LM Studio тоже не нужны
(включён fallback на mock-api):

```powershell
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build
./test-mock-stack.ps1
docker compose -f docker-compose.yml -f docker-compose.mock.yml down
```

## 3. Live (реальные сервисы)

`./test-pipeline.ps1` — полный цикл через реальные STT (GPU), TTS, mock-api,
LM Studio (`samples/question.wav` → `samples/answer.wav`).

## CI

`.github/workflows/test.yml` прогоняет pytest с покрытием на каждый push/PR.
