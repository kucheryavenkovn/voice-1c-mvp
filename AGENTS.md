# AGENTS.md — voice-1c-mvp

Локальный голосовой ассистент для работы с данными 1С: распознавание русской
речи, определение намерения, проверка остатков, подбор запчастей для техники,
создание документов обеспечения и голосовой ответ.

Ключевые слова: voice, 1c, stt, tts, llm, fastapi, mcp, inventory, parts, scenario-frame, erp

## Секреты и инфраструктура (строго)

1. НЕ коммитить и НЕ сохранять в файлах репозитория: пароли, токены, API-ключи,
   логины, внутренние IP, имена хостов, URL внутренних сервисов (Gitea, 1С,
   LLM-хосты). В документации, скриптах и примерах — только плейсхолдеры:
   `<server-ip>`, `<token>`, `<user>@<host>`.
2. Секреты для локального запуска — в `.env` (gitignored) или в системном
   Credential Manager. Пароли для SSH/сервисов в автоматизации — только во
   временных файлах ВНЕ репозитория и удалять сразу после использования.
3. Перед каждым коммитом проверять диф на утечки:
   `git diff --cached | grep -iE "passw|token|secret|api[_-]?key|\\b10\\.[0-9]+\\.[0-9]+\\.[0-9]+\\b"`
   — совпадения (кроме плейсхолдеров и `lm-studio`) — блокер коммита.
4. Не логировать и не выводить в трейсы значения секретов, попавшие в runtime.

## GRACE 4

Проект управляется фреймворком GRACE 4 (каталог `.grace/`, CLI `@osovv/grace-cli`,
закреплено: 4.0.5). Все команды GRACE работают с каталогом `.grace/`.

- **Текущие требования и ограничения** — `.grace/context/*.xml`
  (requirements, technology, principles, deployment, ux-guidelines).
- **Источник истины по модулям и зависимостям** — `.grace/graph/main.xml`
  (модули `M-*`, потоки `DF-*`); индекс — `.grace/graph/index.xml`.
- **Связь модулей с проверками** — `.grace/verification/main.xml`
  (`V-M-*` привязаны к реальным тестам; `Command` содержит только исполняемую
  команду, требования живого окружения — в `Notes`/`Scenario`).
- **Изменения** — `.grace/changes/active/C-*/` (spec.xml + plan.xml);
  завершённые бандлы переносятся в `.grace/changes/archive/` со статусом
  `applied` у spec и plan.

### Правила для агентов

1. `.grace/context` — текущие требования и ограничения.
2. `.grace/graph` — источник истины по модулям и зависимостям.
3. `.grace/verification` — связь модулей с проверками.
4. Изменение архитектуры сопровождается изменением graph.
5. Изменение поведения сопровождается изменением verification.
6. Значимые новые функции получают контракт `START_CONTRACT: имя … END_CONTRACT: имя`.
7. Значимые смысловые области получают пары `START_BLOCK_<ИМЯ>` / `END_BLOCK_<ИМЯ>`
   (ориентир — несколько сотен токенов; без микроблоков).
8. Старые semantic anchor IDs (`M-*`, `DF-*`, `V-M-*`, `BLOCK_*`) без
   необходимости не переименовываются.
9. Любое изменение публичной поверхности модуля отражается в MODULE_CONTRACT,
   `__all__`/MODULE_MAP файла и в `.grace/graph/main.xml` (LINKS).
10. Runtime-маркеры вида `[VoiceGateway][функция][BLOCK_<ИМЯ>]` /
    `[OneCAdapter][функция][BLOCK_<ИМЯ>]` соответствуют реальным semantic
    blocks; verification-записи могут требовать их как доказательство.
11. Перед коммитом: `grace lint --path .`, `ruff check .`, `ruff format --check .`,
    `pytest -m "not ui"`.

### CLI (проверено на 4.0.5)

```bash
grace lint --path .            # целостность каталога, разметки и maps
grace status --path .          # здоровье, active/archive changes, next action
grace file show voice-gateway/app.py --contracts --blocks   # разметка файла
grace module show M-VOICE-GATEWAY --path .                  # карточка модуля
grace module health M-OBSERVABILITY --path .                # здоровье модуля
grace verification show V-M-1C-ADAPTER --path .             # запись проверки
grace lint --path . --change C-ИМЯ --assertions target --runCommands  # ассерты активного C-*
```

## Быстрая шпаргалка по проекту

- Оркестратор и интерпретация реплик: `voice-gateway/app.py` (M-VOICE-GATEWAY,
  M-COMMAND-INTERPRETER)
- Сценарная подсистема: `voice-gateway/scenarios/` — ScenarioFrame, менеджер,
  resolver, проекция, execution gate (M-SCENARIO-MANAGER, M-ENTITY-RESOLVER)
- Декларативные определения сценариев: `voice-gateway/scenarios/definitions/*.yaml`
  (repair_order, stock_query; M-SCENARIO-DEFINITIONS)
- Интеграция с 1С (MCP Toolkit): `voice-gateway/onec.py` (M-1C-ADAPTER)
- Метрики/трейсы: `voice-gateway/metrics.py` (M-OBSERVABILITY)
- Сервисы: `stt/app.py`, `tts/app.py`, `mock-api/app.py`
- Веб-чат (голос+текст+корзина): `voice-gateway/static/index.html`
- Данные кейса: `fixtures/case-parts/`, `fixtures/voice/`, `scripts/1c-case/`
- Запуск стека: `docker compose up -d`; 1С-обработка MCP_Toolkit на :6003;
  vLLM на :18020 (`enable_thinking=false`).

## Архитектурный инвариант: ScenarioFrame, а не FSM

Бизнес-состояние — персистентный `ScenarioFrame` на бэкенде
(`voice-gateway/scenarios/`), а НЕ линейная лестница (историческая FSM-«лестница»
осталась только как legacy-проекция `stage` для UI/тестов) и НЕ память LLM:

```text
REPAIR_ORDER (ScenarioFrame)
  vehicle:  resolved  Трактор Кировец К-744Р [ref ТР-0000008]
  items:
    item-A: nomenclature=resolved (Диск DK-300), quantity=5
    item-B: nomenclature=missing, quantity=2
  focus: items[item-B].nomenclature
  pending_action: нет
```

Правила:
1. Реплика сначала интерпретируется семантически (frame + focus + pending +
   проекция), затем ScenarioManager применяет typed-команды; `stage` семантику
   реплики не определяет.
2. Ссылочное поле заполняется только EntityRef из 1С (mention → resolver →
   код/ссылка); ambiguous/not_found — не «заполнено».
3. Смена влияющего поля (техники) инвалидирует зависимые разрешения по схеме
   YAML; независимые данные (количество) сохраняются.
4. Write-эффекты (документы 1С) — только через PendingAction, подтверждённый
   для актуальной версии frame; любая правка после «Создаём документы?»
   инвалидирует подтверждение.
5. Параллельный сценарий (STOCK_QUERY внутри REPAIR_ORDER) не уничтожает
   активный frame; полная история чата не нужна для продолжения сценария.
