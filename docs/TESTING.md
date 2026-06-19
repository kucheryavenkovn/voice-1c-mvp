# Тестирование

Четыре уровня защиты от регрессий + линтер/CI.

## 0. Линт и формат (ruff)
```powershell
ruff check .          # линтер
ruff format --check . # форматер (только ruff — без второго форматера)
```
Конфиг — `pyproject.toml` (`[tool.ruff]`), git-hooks — `.pre-commit-config.yaml`
(`pip install pre-commit && pre-commit install`).

## 1. Unit + integration (pytest, in-process)
Логика шлюза и клиента 1С **без Docker/GPU/1С/LM Studio**. Все внешние зависимости
(STT, TTS, LM Studio, 1C MCP Toolkit) подменяются одной фейковой реализацией
`FakeRequests` (`tests/conftest.py`), маршрутизируемой по URL.

```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r voice-gateway\requirements.txt -r requirements-dev.txt
pytest -m "not ui" --cov --cov-report=term-missing
```

Покрытие:
- `test_parser.py` — парсер таблиц 1C, `_sanitize`, `_build_query` (наименование **ИЛИ** артикул, без инъекций);
- `test_messages.py` — построитель реплик: 1/несколько позиций, артикул, плюрализация, десятичные;
- `test_extract_json.py` — извлечение JSON из ответа LLM и `build_answer`;
- `test_onec.py` — `onec.query_stock` через фейк 1C (группировка, один/несколько/пусто, ошибка);
- `test_gateway.py` — эндпоинты `/`, `/diag`, `/health`, `/speak`, `/transcribe`, `/ask`, `/ask-text`, пустой STT→400, TTS-fail→502, fallback к mock-api;
- `test_nonfunctional.py` — тайм-аут 1C→fallback без зависания, тайм-аут LM→корректная деградация, 20 параллельных `/ask-text`.

## 2. Контрактные golden-тесты 1C (`@pytest.mark.contract`)
Файлы `tests/fixtures/1c/*.txt` — **реальные** ответы `/api/execute_query`,
снятые скриптом `scripts/record_1c_fixtures.ps1`. Тесты гоняют через парсер и
`query_stock`, фиксируя столбцы/строки/экранирование кавычек/десятичные/пустой
артикул. Ловят любое изменение сериализации тулкита. 1С для прогона не нужна.

Переснять под свою базу: `./scripts/record_1c_fixtures.ps1` (нужен запущенный MCP-сервер на `:6003`).

## 3. Снапшоты реплик (`tests/__snapshots__/test_snapshots.ambr`)
Дословная фиксация голосовых формулировок (`syrupy`). Любое изменение
текста → падение и ревью. Легитимное изменение:
```powershell
pytest tests/test_snapshots.py --snapshot-update
```

## 4. Контракт mock-api (`test_mock_api_contract.py`)
Паритет формы ответа mock-api и 1C-бэкенда (`item/found/quantity/items/warehouses/message/source`).
Ловит расхождение (раньше mock-api не возвращал `items` — починено).

## 5. UI-тесты Playwright (`@pytest.mark.ui`, локально)
Покрывают клиентский VAD и DOM (`tests/test_ui.py`): автодиалог, переходы статусов
`слушаю→говорите→распознаю→отвечаю→✓ готов слушать`, появление пузырьков, реакция
readout на ползунок порога. Headless-микрофона нет — поэтому MicStream/AnalyserNode/
MediaRecorder подменяются скриптовой RMS-кривой.
```powershell
pip install -r requirements-ui.txt
playwright install chromium
pytest -m ui
```
В CI не запускаются (`pytest -m "not ui"`), чтобы не тянуть chromium.

## 6. Mock-стек docker-compose (без GPU)
`docker-compose.mock.yml` подменяет STT/TTS микро-заглушками (те же имена сервисов):
```powershell
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build
./test-mock-stack.ps1
```

## 7. Live
`./test-pipeline.ps1` — полный цикл через реальные STT (GPU), TTS, mock-api, LM Studio.

## CI
`.github/workflows/test.yml` — job `lint` (ruff) + job `pytest` с матрицей
Python 3.11/3.12/3.13/3.14 (`-m "not ui"`), покрытие и артефакт `coverage.xml`.
