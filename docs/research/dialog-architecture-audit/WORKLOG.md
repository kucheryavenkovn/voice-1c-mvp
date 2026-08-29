# WORKLOG — рабочий журнал исследования (сырые факты, пишется инкрементально)

Этот файл — страховка от потери контекста. Сюда заносятся факты по мере их обнаружения.
Финальные документы: 00-EXECUTIVE-SUMMARY.md … FINAL-REPORT.md в этом же каталоге.

## Статус исследования

- [x] Старт, структура каталогов, git log
- [ ] Ядро диалога (app.py, scenarios/) — агент A → raw/01-core-dialog.md
- [ ] Tools/1C/search (onec.py, tools/, mock-api) — агент B → raw/02-tools-1c-search.md
- [ ] Voice pipeline/frontend/infra — агент C → raw/03-voice-infra.md
- [ ] Тесты — агент D → raw/04-tests.md
- [ ] Собственное чтение scenarios/*.py (малые файлы)
- [ ] Внешний research → 12-EXTERNAL-RESEARCH.md
- [ ] Синтез и написание финальных документов

## Начальные факты (git + FS)

- Repo: https://github.com/kucheryavenkovn/voice-1c-mvp, локально E:\t2s, git repo.
- HEAD: d45a6c4 "fix(scenarios): sanitize bracket-decorated item from LLM in stock scenario"
- Ключевой коммит: 1dff7d3 "feat(scenarios): ScenarioFrame architecture (C-SCENARIO-FRAMES) — persistent frames, typed commands, strict EntityRef from 1C, execution gate with PendingAction; migrate STOCK_QUERY/REPAIR_ORDER; legacy stage kept as compat projection only"
- История эволюции (снизу вверх): 1C-интеграция → chat dialog entity с DB-backed confirmation steps → confirmation state machine (a14717e) → cart multi-position (cc58192) → stock queries inside order flow (728873c) → GRACE 4 retrofit (1c94aee, 3c1ddec) → ScenarioFrame arch (1dff7d3) → robustness fixes (d705145, d45a6c4).
- Т.е. FSM ("ladder") — исторический слой, ScenarioFrame — недавняя миграция; stage оставлен как compat-проекция. Transitional code подтверждён историей.
- Незакоммичено: M voice-gateway/scenarios/commands.py, M voice-gateway/scenarios/manager.py, ?? .grace/changes/active/C-LLM-TYPED-COMMANDS/ (активная работа над typed-командами LLM!)

## Файлы и размеры (байты)

- voice-gateway/app.py — 100679 (ГИГАНТ, оркестратор)
- voice-gateway/onec.py — 132264 (ГИГАНТ, 1С-адаптер)
- voice-gateway/metrics.py — 5782
- voice-gateway/scenarios/: __init__ 1683, commands.py 5510, execution.py 3219, manager.py 24073, models.py 10408, projection.py 3839, registry.py 5856, resolver.py 4525
- definitions: repair_order.yaml 1230, stock_query.yaml 928
- static/index.html — веб-чат (голос+текст+корзина)
- stt/app.py, tts/app.py, mock-api/app.py — сервисы

## Структура каталогов (верхний уровень)

.coverage, .coveragerc, .env, .env.example, .github/, .grace/, .pre-commit-config.yaml,
docker-compose.yml, docker-compose.mock.yml, docs/, fixtures/ (case-parts, voice),
mock-api/, mocks/, pyproject.toml, pytest.ini, requirements-dev.txt, requirements-ui.txt,
samples/, scripts/ (1c-case), stt/, tests/ (+fixtures/1c, __snapshots__), tools/, tts/, voice-gateway/
check-lmstudio.ps1, q1c.ps1, q1cf.ps1, test-mock-stack.ps1, test-pipeline.ps1

(дополняется…)
