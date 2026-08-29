"""Command-generator: small LLM превращает реплику в typed-команды.

Принципы:
- LLM — только интерпретатор семантических операций над ScenarioFrame;
- источник состояния — компактная проекция сессии, а не история чата;
- LLM НЕ придумывает EntityRef/business facts и НЕ исполняет 1С;
- malformed output безопасен: без мутаций, максимум один повтор, затем fallback.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from .commands import Command, get_allowed_kinds, parse_commands

# START_MODULE_CONTRACT
#   PURPOSE: LLM command-generator: реплика + проекция сессии -> typed commands[].
#   SCOPE: COMMAND_SYSTEM_PROMPT, сборка промпта, вызов LLM, JSON->Command, retry(1).
#   DEPENDS: pydantic, scenarios.commands
#   LINKS: M-COMMAND-INTERPRETER, M-SCENARIO-MANAGER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   COMMAND_SYSTEM_PROMPT - system-промпт интерпретатора операций
#   build_command_prompt - user-промпт: проекция сессии + реплика + контекст
#   GenerationResult - команды + метаданные генерации (ok/attempts/raw)
#   generate_commands - вызов LLM (json mode, temperature 0) + валидация + retry
# END_MODULE_MAP

COMMAND_SYSTEM_PROMPT = """Ты — интерпретатор операций складского голосового ассистента.
Ты получаешь: состояние бизнес-сценария (ScenarioFrame), фокус, ожидания и реплику пользователя.
Твоя единственная задача — вернуть JSON-массив семантических операций (typed commands) над этой структурой.

ЖЁСТКИЕ ПРАВИЛА:
1. Состояние сценария — только в ScenarioFrame из промпта. Не выдумывай факты.
2. Не выдумывай идентификаторы 1С (EntityRef/код/ссылку). Для товара/техники/склада
   передавай только mention — СыРОЕ упоминание из слов пользователя.
3. Ты НЕ исполняешь 1С и НЕ решаешь, заполнено ли ссылочное поле: резолвер сделает это сам.
4. Можно вернуть НЕСКОЛЬКО команд для одной реплики (например: добавить строку,
   назвать в ней товар, указать количество).
5. Если реплика неоднозначна (неясно: менять текущую строку или создавать новую,
   неясно, о каком поле речь) — верни одну команду clarify вместо угадывания.
6. Пользователь может менять ЛЮБЫЕ ранее заполненные поля и возвращаться к ним.
   focus — подсказка, но не ограничение.
7. Реплика может открыть временный другой сценарий (например вопрос об остатках
   внутри заказа) — start_scenario.
8. Обычный разговорный вопрос/фраза — chitchat (обязательно с answer_text).
   chitchat не меняет состояние.
9. Не выдумывай новые типы команд. Только типы из списка ниже.
10. Формат ответа: ТОЛЬКО JSON вида {"commands": [...]} без пояснений.

Типы команд:
- start_scenario {scenario_type: "repair_order"|"stock_query"} — начать сценарий
- set_field {path, mention?|value?} — заполнить поле сценария
  (entity-поля: только mention; скаляры: value)
- clear_field {path} — очистить поле
- append_collection_item {collection} — добавить строку (БЕЗ поиска товара)
- select_collection_item {collection, item_ref} — выбрать строку в focus
  (item_ref: "1","2"... | "first"|"last" | "this" | "prev" | "new")
- set_collection_field {collection, item_ref, field, mention?|value?} — поле строки
- remove_collection_item {collection, item_ref} — удалить строку
- switch_focus {focus | collection+item_ref} — перевести фокус ("back" = назад)
- query_scenario {query?} — прочитать состояние/задать вопрос по данным frame
- propose_pending — пользователь хочет завершить («оформляй», «сохраняй»):
  показать подтверждение создания документов (проверки сделает менеджер)
- confirm_pending — подтвердить ранее показанное действие («да, создавай»)
  ЛИБО выбрать предложенного кандидата товара/техники («да, она»)
- reject_pending — отклонить показанное действие («нет, не сохраняем»)
- cancel_scenario — отменить сценарий целиком
- chitchat {answer_text} — разговор вне сценария
- clarify {answer_text} — задать уточняющий вопрос пользователю

ПОДТВЕРЖДЕНИЕ: «да» при показанном действии = confirm_pending.
«да, но <правка>» = правка (мутация) БЕЗ confirm_pending — подтверждение придёт заново.
"""

_ALLOWED = ", ".join(get_allowed_kinds())


def build_command_prompt(
    projection: str,
    utterance: str,
    recent_turns: list[dict] | None = None,
    pending_question: str | None = None,
) -> str:
    """User-промпт: операционный контекст + реплика (+ минимальный разговорный контекст)."""
    lines = [
        "СОСТОЯНИЕ СЦЕНАРИЯ (ScenarioFrame — единственный источник состояния):",
        projection or "(нет активного сценария)",
        "",
        f"ДОПУСТИМЫЕ ТИПЫ КОМАНД: {_ALLOWED}",
    ]
    if pending_question:
        lines += ["", f"ПОСЛЕДНИЙ ВОПРОС ПОЛЬЗОВАТЕЛЮ: {pending_question}"]
    if recent_turns:
        lines += ["", "НЕДАВНИЙ ДИАЛОГ (только контекст, не источник состояния):"]
        for turn in recent_turns[-4:]:
            role = "П" if turn.get("role") == "user" else "А"
            lines.append(f"{role}: {turn.get('content', '')}")
    lines += ["", f"РЕПЛИКА ПОЛЬЗОВАТЕЛЯ: {utterance}", "", 'Верни JSON {{"commands": [...]}}.']
    return "\n".join(lines)


class GenerationResult(BaseModel):
    """Результат генерации: команды + служебные метаданные (без reasoning)."""

    commands: list[Command] = Field(default_factory=list)
    ok: bool = False
    error: str | None = None
    attempts: int = 0
    raw_preview: str = ""

    @property
    def malformed(self) -> bool:
        return not self.ok


def _extract_json(text: str) -> Any:
    """Строгий JSON -> (fallback) первый {...} в тексте."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (ValueError, TypeError):
                return None
        return None


def generate_commands(
    utterance: str,
    projection: str,
    call_llm: Callable[[dict], str],
    recent_turns: list[dict] | None = None,
    pending_question: str | None = None,
    max_attempts: int = 2,
) -> GenerationResult:
    """Реплика -> typed commands. call_llm(payload)->str инжектируется приложением
    (OpenAI-compatible, temperature 0, json mode при поддержке бэкенда).

    malformed output: повтор один раз, затем безопасный отказ (без мутаций).
    """
    prompt = build_command_prompt(projection, utterance, recent_turns, pending_question)
    raw = ""
    error = None
    for attempt in range(1, max_attempts + 1):
        payload: dict[str, Any] = {
            "temperature": 0,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": COMMAND_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        if attempt == 1:
            # json mode, если бэкенд поддерживает; иначе бэкенд вернёт ошибку —
            # повтор пойдёт без response_format
            payload["response_format"] = {"type": "json_object"}
        try:
            raw = call_llm(payload)
        except Exception as e:
            error = f"LLM unavailable: {e.__class__.__name__}"
            continue
        data = _extract_json(raw)
        if isinstance(data, dict) and isinstance(data.get("commands"), list):
            data = data["commands"]
        commands = parse_commands(data)
        if commands:
            return GenerationResult(
                commands=commands,
                ok=True,
                attempts=attempt,
                raw_preview=raw[:200],
            )
        error = "malformed or empty command output"
    return GenerationResult(ok=False, error=error, attempts=max_attempts, raw_preview=raw[:200])


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "COMMAND_SYSTEM_PROMPT",
    "GenerationResult",
    "build_command_prompt",
    "generate_commands",
]
