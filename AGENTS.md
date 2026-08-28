# AGENTS.md — voice-1c-mvp

Локальный голосовой ассистент для работы с данными 1С: распознавание русской
речи, определение намерения, проверка остатков, подбор запчастей для техники,
создание документов обеспечения и голосовой ответ.

Ключевые слова: voice, 1c, stt, tts, llm, fastapi, mcp, inventory, parts, dialogue-fsm, erp

## GRACE 4

Проект управляется фреймворком GRACE 4 (каталог `.grace/`, CLI `@osovv/grace-cli`).

- **Источник истины по архитектуре** — `.grace/graph/main.xml` (модули `M-*`,
  потоки `DF-*`). Индекс якорей — `.grace/graph/index.xml`.
- **Требования/технологии/принципы/деплой/UX** — `.grace/context/*.xml`.
- **Verification** — `.grace/verification/main.xml` (`V-M-*` привязаны к
  реальным тестам и прогонам; для LM- и stock-изменений запускайте
  `pytest -q tests/...` из соответствующих записей).
- **Изменения** — `.grace/changes/active/C-GRACE-RETROFIT/` (текущий ретрофит,
  status: approved). Завершённые бандлы переносите в `archive/` со сменой
  статуса на `applied`.

### Правила для агентов
1. Любое изменение публичной поверхности модуля отражайте в MODULE_CONTRACT
   файла и в `.grace/graph/main.xml` (CrossLinks через LINKS).
2. Новые ключевые функции снабжайте контрактом `START_CONTRACT: имя … END_CONTRACT: имя`.
3. Крупные смысловые участки оборачивайте уникальными парами
   `START_BLOCK_<ИМЯ>` / `END_BLOCK_<ИМЯ>` (~500 токенов на блок).
4. Runtime-маркеры вида `[VoiceGateway][функция][BLOCK_<ИМЯ>]` — стабильны;
   verification-записи могут требовать их как доказательство.
5. Перед коммитом: `grace lint --path .`, `ruff check .`, `pytest -m "not ui"`.

### CLI
```bash
grace lint --path .          # целостность каталога и разметки
grace status --path .        # здоровье, активные changes, next action
grace file show voice-gateway/app.py --contracts --blocks   # навигация по файлу
```

Примечание: CLI 4.0.5 переходный — `grace lint/status` работают с каталогом
`.grace/`, а `grace module/verification/file`-запросы требуют классических
`docs/*.xml`. В проекте источником истины является `.grace/`.

## Быстрая шпаргалка по проекту

- Оркестратор и FSM диалога: `voice-gateway/app.py` (M-VOICE-GATEWAY)
- Интеграция с 1С (MCP Toolkit): `voice-gateway/onec.py` (M-1C-ADAPTER)
- Метрики/трейсы: `voice-gateway/metrics.py` (M-OBSERVABILITY)
- Сервисы: `stt/app.py`, `tts/app.py`, `mock-api/app.py`
- Веб-чат (голос+текст+корзина): `voice-gateway/static/index.html`
- Данные кейса: `fixtures/case-parts/`, `fixtures/voice/`, `scripts/1c-case/`
- Запуск стека: `docker compose up -d`; 1С-обработка MCP_Toolkit на :6003;
  vLLM на :18020 (`enable_thinking=false`).
