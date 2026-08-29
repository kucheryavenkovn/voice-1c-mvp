import json
import os
import pathlib
import re
import urllib.parse
from collections import deque
from datetime import datetime

import metrics
import onec
import requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# START_MODULE_CONTRACT
#   PURPOSE: Оркестрирует голосовой и текстовый диалог с 1С.
#   SCOPE: HTTP API, dialogue FSM, LLM intent, stock/order routing, TTS response.
#   DEPENDS: M-STT, M-TTS, M-1C-ADAPTER, M-MOCK-1C, M-OBSERVABILITY
#   LINKS: M-VOICE-GATEWAY, V-M-VOICE-GATEWAY, DF-VOICE-TURN, DF-PART-ORDER, DF-STOCK-QUERY
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   app - FastAPI application: endpoints /ask, /ask-text, /transcript, /monitor
#   orchestrate - полный голосовой ход: STT → FSM/LLM → 1С → TTS
#   lm_intent - LLM-распознавание намерения
#   build_answer - сборка текстового ответа по данным 1С/mock
#   call_stock_api - остатки через 1С/mock
#   call_order_api - заказ товара через 1С/mock
#   call_part_api - разовая заявка запчасти
#   call_lookup_vehicle - шаг идентификации техники
#   call_lookup_parts - шаг идентификации запчастей
#   chat_history - история чата по chat_id
#   chat_append - добавить ход в историю
#   _norm_quantity - нормализация количества
# END_MODULE_MAP

STT_URL = os.getenv("STT_URL", "http://stt:8000")
TTS_URL = os.getenv("TTS_URL", "http://tts:8000")
STOCK_API_URL = os.getenv("STOCK_API_URL", "http://mock-api:8000/api/stock")
ORDER_API_URL = os.getenv("ORDER_API_URL", "http://mock-api:8000/api/orders")

# Источник остатков: "1c" (1C MCP Toolkit, REST) или "mock" (mock-api контейнер)
STOCK_BACKEND = os.getenv("STOCK_BACKEND", "1c").lower()
STOCK_FALLBACK_TO_MOCK = os.getenv("STOCK_FALLBACK_TO_MOCK", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

LM_BASE_URL = os.getenv("LM_BASE_URL", "http://host.docker.internal:1234/v1")
LM_API_KEY = os.getenv("LM_API_KEY", "lm-studio")
LM_MODEL = os.getenv("LM_MODEL", "auto")
# reasoning-модели (vLLM + Qwen3): отключаем thinking для скорости голосового цикла
LM_ENABLE_THINKING = os.getenv("LM_ENABLE_THINKING", "false").lower() in ("1", "true", "yes", "on")
LM_TIMEOUT = int(os.getenv("LM_TIMEOUT", "120"))
# цена за 1M токенов (если задана) — для оценки стоимости в мониторинге
LM_PRICE_PROMPT = float(os.getenv("LM_PRICE_PROMPT", "0") or 0)
LM_PRICE_COMPLETION = float(os.getenv("LM_PRICE_COMPLETION", "0") or 0)

SYSTEM_PROMPT = (
    "Ты — складской ассистент, интегрированный с 1С. "
    "Определи намерение пользователя и ответь СТРОГО валидным JSON без markdown и пояснений.\n"
    "1) Если спрашивают остаток/количество/наличие товара — верни:\n"
    '{"action": "get_stock", "item": "<товар>"}\n'
    "2) Если просят ПЕРЕЧИСЛИТЬ товары по названию, по которым есть остатки "
    "('по каким товарам ... есть остатки', 'какие ... есть в наличии', 'список товаров ...') — верни:\n"
    '{"action": "list_stock", "item": "<товар>"}\n'
    "В поле item подставь то, чем пользователь обозначил товар: НАИМЕНОВАНИЕ "
    "(например 'молоко') ИЛИ АРТИКУЛ/КОД (например 'Арт-7777', '45463728', '7777'), "
    "в том числе когда артикул назван без слова «артикул» — просто цифрами/кодом. "
    "Передавай код как есть, цифры сохраняй цифрами. "
    "Название приведи к именительному падежу единственного числа "
    "(телевизоры→телевизор, стулья→стул, молока→молоко). "
    "Если пользователь назвал конкретный СКЛАД ('на центральном складе', 'на складе X') "
    '— добавь поле "warehouse": "<склад>"; если склад не назван — "warehouse": null.\n'
    "3) Если просят ЗАКАЗАТЬ или ОФОРМИТЬ заказ на товар ('закажи 5 молока', "
    "'оформи заказ на три телевизора', 'нужно заказать шесть подшипников') — верни:\n"
    '{"action": "order_part", "item": "<товар>", "quantity": <целое число>, "warehouse": null}\n'
    "В поле quantity подставь количество ЦЕЛЫМ ЧИСЛОМ цифрой ('пять' -> 5, 'три' -> 3); "
    "если количество не названо — 1. Товар и склад — по тем же правилам, что выше.\n"
    "4) ЗАКАЗ ЗАПЧАСТИ — ВСЕГДА НАЧИНАЕМ С ТЕХНИКИ И СТРОГО ПО СПРАВОЧНИКУ 1С. "
    "Если пользователь назвал ТЕХНИКУ (марку/модель/госномер), а запчасть ещё не "
    "названа — верни:\n"
    '{"action": "request_part", "item": null, "vehicle": "<техника>"}\n'
    "Шлюз сам проверит технику по справочнику 1С (не найдена — сообщит, что "
    "заказать под неё нельзя) и запросит запчасть. Если названы И техника, И "
    "запчасть ('нужен диск для кировца') — верни:\n"
    '{"action": "request_part", "item": "<запчасть>", "vehicle": "<техника>", '
    '"quantity": <целое число, по умолчанию 1>}\n'
    "В item — только запчасть, в vehicle — только техника. Подтверждения и выбор "
    "вариантов выполняет шлюз — не задавай встречных вопросов о подтверждении "
    "сам, не выполняй заказ по этой реплике сам. НЕ отвечай chat'ом на реплику, "
    "в которой пользователь называет технику — это всегда request_part.\n"
    "5) Для ЛЮБОЙ другой реплики (общий вопрос, приветствие, беседа) — верни:\n"
    '{"action": "chat", "answer": "<краткий естественный ответ на русском, '
    'как в телефонном разговоре, 1-3 предложения>"}\n'
    "ПРИВЕТСТВИЕ и начало диалога: представься одним предложением и сразу задай "
    "ВОПРОС О ДЕЙСТВИИ: 'Что вам сейчас нужно — заказать запчасть для техники "
    "или узнать остаток товара на складе?'. Вопрос держится открытым, пока "
    "пользователь не скажет, что ему нужно.\n"
    "ЗАКАЗ ВСЕГДА ИДЁТ ОТ ТЕХНИКИ: если пользователь сказал 'заказать'/'хочу "
    "заказать'/'нужна запчасть' без названия техники — сначала уточни ТЕХНИКУ: "
    "'Для какой техники нужна запчасть? Назовите марку, модель или госномер.', "
    "и только потом запчасть. Название товара без техники ('закажи 5 молока') — "
    "это обычный order_part по п.3.\n"
    "АРТИКУЛЫ: пользователь может произнести артикул по-русски ('дк сто', 'дикей "
    "сто' = DK-100) — система сама найдёт совпадение. Названный артикул или код — "
    "это НЕ подтверждённая запчасть: сначала lookup_parts (система покажет "
    "найденную номенклатуру с артикулом и спросит 'Она?'), и только после "
    "подтверждения — request_part.\n"
    "Отвечай в chat из собственных знаний. Вопросы об остатках и заказах ВСЕГДА "
    "классифицируй по пунктам 1-4 — никогда не отвечай на них в chat по памяти, "
    "не выдумывай складские данные. Не упоминай JSON, промпты и внутреннее "
    "устройство системы.\n"
    "6) Если реплику понять нельзя — верни:\n"
    '{"action": "unknown", "item": null, "warehouse": null}\n'
    "Примеры: 'сколько молока?' -> "
    '{"action":"get_stock","item":"молоко","warehouse":null}; '
    "'сколько молока на центральном складе' -> "
    '{"action":"get_stock","item":"молоко","warehouse":"центральный склад"}; '
    "'по каким товарам с названием сахар есть остатки' -> "
    '{"action":"list_stock","item":"сахар","warehouse":null}; '
    '\'остаток по артикулу 7777\' -> {"action":"get_stock","item":"7777","warehouse":null}; '
    '\'закажи 5 молока\' -> {"action":"order_part","item":"молоко","quantity":5,"warehouse":null}; '
    "'нужен колёсный диск для кировца' -> "
    '{"action":"request_part","item":"колёсный диск","vehicle":"кировец","quantity":1}; '
    "'закажи два фильтра для мтз' -> "
    '{"action":"request_part","item":"фильтр","vehicle":"мтз","quantity":2}; '
    "'оформи заказ на три телевизора' -> "
    '{"action":"order_part","item":"телевизор","quantity":3,"warehouse":null}; '
    '\'привет\' -> {"action":"chat","answer":"Здравствуйте! Могу узнать остаток '
    'по товару или оформить заказ."}; '
    "'кто написал войну и мир' -> "
    '{"action":"chat","answer":"Лев Толстой."}'
)

HERE = pathlib.Path(__file__).parent
app = FastAPI(title="voice-gateway")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

_cached_model = None

# --- сущность «чат»: история диалога по chat_id (in-memory) ---
_CHATS: dict[str, deque] = {}
_CHAT_LIMIT = 12  # последние 6 пар реплик

# --- сценарии: ScenarioFrame на бэкенде (источник бизнес-состояния) ---------
# legacy _DIALOG_STATES остаётся ТОЛЬКО как проекция совместимости (UI/тесты):
# она выводится из активного ScenarioFrame и не является source of truth.
_DIALOG_STATES: dict[str, dict] = {}

from scenarios import (  # noqa: E402
    Command,
    EntityResolver,
    ScenarioManager,
    build_repair_payload,
    load_definitions,
)

_YES_WORDS = {
    "да",
    "давай",
    "ага",
    "верно",
    "именно",
    "он",
    "она",
    "он самый",
    "она самая",
    "yes",
    "y",
    "ок",
    "хорошо",
    "подтверждаю",
}
_NO_WORDS = {"нет", "не", "не та", "не он", "не она", "неверно", "no", "n"}
_ABORT_WORDS = {"стоп", "отмена", "отмени", "хватит", "отменить"}
# личные/обиходные упоминания складов -> настроенные склады кейса
_WAREHOUSE_ALIASES = (
    ("мой склад", onec.ENGINEER_WAREHOUSE),
    ("моём складе", onec.ENGINEER_WAREHOUSE),
    ("моем складе", onec.ENGINEER_WAREHOUSE),
    ("мой", onec.ENGINEER_WAREHOUSE),
    ("склад инженера", onec.ENGINEER_WAREHOUSE),
    ("у себя", onec.ENGINEER_WAREHOUSE),
    ("текущее оп", onec.CURRENT_OP_WAREHOUSE),
    ("текущего оп", onec.CURRENT_OP_WAREHOUSE),
    ("склад оп", onec.CURRENT_OP_WAREHOUSE),
    ("наше оп", onec.CURRENT_OP_WAREHOUSE),
    ("нашем оп", onec.CURRENT_OP_WAREHOUSE),
    ("другое оп", onec.OTHER_OP_WAREHOUSE),
    ("другого оп", onec.OTHER_OP_WAREHOUSE),
    ("центральный склад", onec.OTHER_OP_WAREHOUSE),
    ("на центральном", onec.OTHER_OP_WAREHOUSE),
)


def _map_warehouse(name: str | None) -> str | None:
    """'мой склад' -> Склад инженера, 'склад текущего ОП' -> ..., иначе как есть."""
    if not name:
        return name
    t = name.lower()
    for alias, target in _WAREHOUSE_ALIASES:
        if alias in t:
            return target
    return name


def _looks_stock_query(text: str) -> bool:
    t = (text or "").lower()
    return "сколько" in t or "остат" in t or "осталось" in t


_STOCK_STOPWORDS = {
    "сколько",
    "остаток",
    "остатки",
    "осталось",
    "на",
    "складе",
    "склад",
    "моём",
    "моем",
    "текущем",
    "другом",
    "оп",
    "товар",
    "есть",
    "ещё",
    "еще",
    "покажи",
    "узнать",
    "мне",
    "нужно",
}


def _strip_stock_words(text: str) -> str:
    """«сколько дисков на моём складе» -> «дисков» (упоминание для resolver)."""
    toks = [
        t
        for t in re.split(r"\s+", (text or "").strip())
        if t and t.lower().strip(".,!?") not in _STOCK_STOPWORDS
    ]
    return " ".join(toks)


def _extract_qty(text: str) -> int | None:
    """Количество из реплики — только явное: '5 штук' / 'пять штук'.
    Голые цифры и числа словами ('дк 100', 'сто') количеством НЕ считаем."""
    if not text:
        return None
    t = text.lower()
    m = re.search(r"(\d+)\s*(?:шт|штук|штуки|штуку)", t)
    if m:
        n = int(m.group(1))
        return n if n > 0 else None
    words = "|".join(onec._WORD_NUMBERS)
    m = re.search(rf"\b({words})\s*шт", t)
    if m:
        return int(onec._WORD_NUMBERS[m.group(1)])
    return None


def stock_at_warehouse_view(warehouse: str) -> dict:
    """Что есть на складе (для 'какие остатки у меня есть'): список позиций
    с количествами. Только чтение."""
    try:
        res = onec.stock_at_warehouse(warehouse)
    except Exception as e:
        return {
            "found": False,
            "message": f"Не удалось получить остатки склада: {e}",
            "source": "1c",
        }
    res["table"] = {
        "title": f"Остатки на складе: {warehouse}",
        "headers": ["Товар", "Артикул", "Количество"],
        "rows": [
            [i["name"], i.get("article", ""), onec._format_qty(i["quantity"])]
            for i in res.get("items", [])
        ],
    }
    return res


_FILLERS = {
    "нужен",
    "нужна",
    "нужны",
    "нужно",
    "хочу",
    "давай",
    "давайте",
    "закажи",
    "оформи",
    "оформить",
    "мне",
    "пожалуйста",
    "плиз",
    "бы",
    "ещё",
    "еще",
    "есть",
}


def chat_history(chat_id: str | None) -> deque | None:
    """История чата (user/assistant пары) или None для анонимных запросов."""
    if not chat_id:
        return None
    return _CHATS.setdefault(chat_id, deque(maxlen=_CHAT_LIMIT))


def chat_append(history: deque | None, user_text: str, answer: str) -> None:
    if history is not None:
        history.append(
            {
                "role": "user",
                "content": user_text,
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
        )
        history.append(
            {
                "role": "assistant",
                "content": answer,
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
        )


def _scenario_registry():
    global _SCENARIO_REGISTRY
    if _SCENARIO_REGISTRY is None:
        _SCENARIO_REGISTRY = load_definitions(HERE / "scenarios" / "definitions")
    return _SCENARIO_REGISTRY


_SCENARIO_REGISTRY = None


def _vehicle_lookup(mention: str) -> dict:
    res = call_lookup_vehicle(mention)
    return {
        "found": res.get("found", False),
        "entities": res.get("entities", []),
        "message": res.get("message", ""),
    }


def _part_lookup(mention: str) -> dict:
    res = call_lookup_parts(mention, None)
    return {
        "found": res.get("found", False),
        "entities": res.get("entities", []),
        "message": res.get("message", ""),
    }


def _nomenclature_lookup(mention: str) -> dict:
    res = onec.find_nomenclature(mention)
    return {
        "found": res.get("found", False),
        "entities": res.get("entities", []),
        "message": res.get("message", ""),
    }


_SCENARIO_RESOLVER = EntityResolver(
    {
        "vehicle": _vehicle_lookup,
        "part": _part_lookup,
        "nomenclature": _nomenclature_lookup,
        # склады данной конфигурации (Справочник.Склады без Код): identity = точное
        # Наименование из настроек кейса (см. docs/1C_METADATA.md)
        "warehouse": lambda mention: {
            "found": True,
            "entities": [
                {
                    "name": _map_warehouse(mention),
                    "code": _map_warehouse(mention),
                    "allow_name_identity": True,
                }
            ],
        },
    }
)


def _scenario_executor(action_type: str, frame, payload: dict) -> dict:
    """Execution gate: вызывается ТОЛЬКО из confirm_pending при валидной версии."""
    if action_type == "create_repair_documents":
        items = [
            {
                "source": (
                    row.fields.get("supply_source").value
                    if row.fields.get("supply_source")
                    else None
                )
                or "S",
                "part": {
                    "name": row.fields["nomenclature"].entity.name,
                    "article": row.fields["nomenclature"].entity.metadata.get("article", ""),
                },
                "qty": int(row.fields["quantity"].value or 1),
            }
            for row in frame.collections.get("items", [])
        ]
        res = onec.create_repair_order(items, frame.fields["vehicle"].entity.name)
        res["frame_payload"] = build_repair_payload(frame)
        return res
    raise ValueError(f"unknown action_type {action_type!r}")


def _scenario_manager() -> ScenarioManager:
    global _SCENARIO_MANAGER
    if _SCENARIO_MANAGER is None:
        _SCENARIO_MANAGER = ScenarioManager(
            _scenario_registry(), _SCENARIO_RESOLVER, executor=_scenario_executor
        )
    return _SCENARIO_MANAGER


_SCENARIO_MANAGER = None


def _session(chat_id: str | None):
    return _scenario_manager().session(chat_id)


def _sync_legacy_state(chat_id: str) -> dict:
    """Проекция совместимости: legacy-слоты выводятся из активного ScenarioFrame.

    stage — НЕ источник истины и НЕ управляет интерпретацией реплик; он нужен
    только UI/тестам на переходный период (compat-проекция).
    """
    st = _DIALOG_STATES.setdefault(
        chat_id,
        {
            "stage": "idle",
            "item": None,
            "vehicle": None,
            "part": None,
            "qty": 1,
            "items": [],
            "docs": None,
            "fails": 0,
            "_chat_id": chat_id,
        },
    )
    session = _session(chat_id)
    frame = session.active if session else None
    st["items"] = []
    st["part"] = None
    if frame is None or frame.status in ("completed", "cancelled"):
        st["stage"] = "idle"
        if frame is not None:
            st["vehicle"] = None
        return st
    if frame.status == "discarding":
        st["stage"] = "await_order_discard"
        st["vehicle"] = (
            frame.fields.get("vehicle").entity.name
            if frame.fields.get("vehicle") and frame.fields["vehicle"].entity
            else None
        )
        return st
    if frame.pending_action is not None:
        st["stage"] = "await_order_confirm"
        st["vehicle"] = frame.fields["vehicle"].entity.name
        st["items"] = _legacy_items(frame)
        return st
    vehicle = frame.fields.get("vehicle")
    if vehicle is not None and vehicle.status == "ambiguous":
        st["stage"] = "await_vehicle_confirm"
        st["vehicle"] = None
        return st
    if vehicle is None or not vehicle.filled:
        st["stage"] = "await_vehicle"
        st["vehicle"] = None
        return st
    st["vehicle"] = vehicle.entity.name if vehicle.entity else None
    st["items"] = _legacy_items(frame)
    focused = _focused_row(frame)
    if focused is not None:
        nom = focused.fields.get("nomenclature")
        if nom is not None and nom.status == "ambiguous":
            st["stage"] = "await_part_confirm"
            if nom.candidates:
                c = nom.candidates[0]
                st["part"] = {"name": c.name, "article": c.metadata.get("article", "")}
            return st
        if nom is not None and nom.status in ("resolving", "missing", "not_found"):
            st["stage"] = "await_part"
            st["item"] = nom.user_mention
            return st
    st["stage"] = "await_part"
    return st


def _legacy_items(frame) -> list[dict]:
    out = []
    for row in frame.collections.get("items", []):
        nom = row.fields.get("nomenclature")
        qty = row.fields.get("quantity")
        src = row.fields.get("supply_source")
        if nom is None or not nom.filled:
            continue  # черновик строки не виден в корзине
        out.append(
            {
                "part": {
                    "name": nom.entity.name,
                    "article": nom.entity.metadata.get("article", ""),
                },
                "qty": int(qty.value or 1) if qty else 1,
                "source": (src.value if src else None) or "S",
            }
        )
    return out


def _focused_row(frame):
    if frame.focus is None or "[" not in frame.focus.path or frame.focus.path.startswith("items["):
        path = frame.focus.path if frame.focus else ""
        if path.startswith("items["):
            item_id = path.split("[", 1)[1].split("]", 1)[0]
            try:
                return frame.collection_item("items", item_id)
            except KeyError:
                return None
    return None


def _dialog_state(chat_id: str | None) -> dict | None:
    """Legacy-проекция состояния (источник истины — ScenarioFrame в сессии)."""
    if not chat_id:
        return None
    return _sync_legacy_state(chat_id)


def _dialog_reset(st: dict) -> None:
    """Сброс сценария сессии (legacy-точка). Источник истины — frame, не dict."""
    chat_id = st.get("_chat_id") or st.get("chat_id")
    session = _session(chat_id) if chat_id else None
    if session is not None:
        for frame in list(session.frames.values()):
            if frame.status == "active" or frame.status == "discarding":
                _scenario_manager().cancel_scenario(session, frame)
    st.update(
        {
            "stage": "idle",
            "item": None,
            "vehicle": None,
            "part": None,
            "qty": 1,
            "items": [],
            "docs": None,
            "fails": 0,
        }
    )


def _norm_short(text: str) -> str:
    return re.sub(r"[^\w\s]", "", (text or "").strip().lower()).strip()


def _strip_fillers(text: str) -> str:
    toks = [
        t
        for t in re.split(r"\s+", (text or "").strip())
        if t and t.lower().strip(".,!?") not in _FILLERS
    ]
    return " ".join(toks)


_ORDER_WORDS = {
    "оформи",
    "оформляй",
    "оформить",
    "достаточно",
    "всё",
    "все",
    "создавай",
    "создать",
    "оформи заказ",
    "хватит",
}
_ORDER_DISCARD_WORDS = {
    "не сохраняем",
    "не сохранять",
    "не сохраняй",
    "сбрось",
    "сбросить",
    "удали",
    "удалить",
    "очисти",
    "очистить",
    "заново",
}
_ORDER_CONTINUE_WORDS = {
    "продолжаем",
    "продолжить",
    "продолжай",
    "работаем",
    "да",
    "давай",
    "ок",
    "хорошо",
}
_INTENT_WORDS = {
    "заказать",
    "закажи",
    "заказ",
    "заказа",
    "купить",
    "хочу",
    "нужно",
    "нужен",
    "нужна",
    "нужны",
    "запчасть",
    "запчасти",
    "деталь",
    "детали",
    "номенклатура",
    "номенклатуру",
    "позицию",
    "позиции",
    "артикул",
}
_META_HINTS = (
    "каки",
    "повтори",
    "напомни",
    "что ты",
    "что за",
    "вариант",
    "предлага",
    "перечисли",
    "почему",
    "зачем",
    "ты понял",
)


def _looks_meta(text: str) -> bool:
    t = (text or "").lower()
    return t.rstrip().endswith("?") or any(h in t for h in _META_HINTS)


def _classify_utterance(text: str) -> str:
    """Что назвал пользователь: article (код/артикул), name (номенклатура),
    meta (вопрос системе), chatter (болтовня/короткое)."""
    t = _norm_short(text)
    if _looks_meta(text):
        return "meta"
    if t in _YES_WORDS or t in _NO_WORDS or t in _ORDER_WORDS or t in _ABORT_WORDS:
        return "command"
    toks = [x for x in t.split() if x]
    if any(any(ch.isdigit() for ch in x) for x in toks):
        return "article"
    for x in toks:
        if onec._article_variants(x):
            return "article"
    if len(t) <= 3 or t in ("ага", "ок", "окей", "ну", "вот", "так", "угу"):
        return "chatter"
    return "name"


def _is_yes(t_short: str) -> bool:
    words = set(t_short.split())
    return t_short in _YES_WORDS or bool(words & _YES_WORDS)


def _is_no(t_short: str) -> bool:
    words = set(t_short.split())
    return t_short in _NO_WORDS or bool(words & _NO_WORDS)


def _render_state(st: dict) -> str:
    """Состояние сценария для промпта: компактная проекция ScenarioFrame
    (бэкенд — источник истины), для legacy-пользователей — слоты проекции."""
    chat_id = st.get("_chat_id")
    session = _session(chat_id) if chat_id else None
    frame = session.active if session else None
    if frame is not None and frame.status in ("active", "discarding"):
        return (
            "СОСТОЯНИЕ СЦЕНАРИЯ (ScenarioFrame ведёт бэкенд; факты не искажай):\n"
            + _scenario_manager().compact_projection(frame)
        )
    lines = ["СОСТОЯНИЕ СЦЕНАРИЯ ЗАКАЗА ЗАПЧАСТИ (ведёт бэкенд; факты не искажай):"]
    lines.append(f"- Техника: {st.get('vehicle') or 'не выбрана'}")
    if st.get("part"):
        p = st["part"]
        lines.append(f"- Запчасть (подтверждена): {p.get('name')} (арт. {p.get('article', '')})")
    elif st.get("item"):
        lines.append(f"- Запчасть (как назвал пользователь): {st['item']}")
    else:
        lines.append("- Запчасть: не названа")
    lines.append(f"- Количество: {st.get('qty', 1)}")
    lines.append(f"- Позиций в заказе: {len(st.get('items') or [])}")
    for i, it in enumerate(st.get("items") or [], 1):
        lines.append(
            f"  {i}) {it['part']['name']} (арт. {it['part'].get('article', '')}) — "
            f"{it['qty']} шт, {onec._SOURCE_NAMES.get(it['source'], it['source'])}"
        )
    return "\n".join(lines)


def lm_phrase(canned: str, state_summary: str) -> str:
    """Озвучить ответ «по-человечески», не искажая факты. Ошибки и проблемы —
    передаются дословно (пересказ ошибок LLM'ом запрещён). При любой проблеме —
    черновик бэкенда (детерминированный текст)."""
    if not canned or "не удалось" in canned.lower() or "ошибк" in canned.lower():
        return canned
    try:
        payload = {
            "model": resolve_model(),
            "temperature": 0.3,
            "max_tokens": 200,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты голосовой складской ассистент. Тебе дано состояние "
                        "сценария и черновик ответа. Переформулируй черновик "
                        "коротко и естественно (1-2 предложения), как человек. "
                        "Номера, артикулы, названия и количества оставь точными. "
                        "Если в черновике есть вопрос — обязательно сохрани его "
                        "(только один). Новых фактов не добавляй. "
                        "Верни только текст ответа."
                    ),
                },
                {"role": "user", "content": f"{state_summary}\n\nЧерновик ответа: {canned}"},
            ],
        }
        if not LM_ENABLE_THINKING:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        r = requests.post(
            f"{LM_BASE_URL}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {LM_API_KEY}"},
            timeout=15,
        )
        content = (r.json()["choices"][0]["message"]["content"] or "").strip()
        if not content or content.startswith("{") or len(content) > 500:
            return canned
        return content
    except Exception:
        return canned


def _cart_summary(st: dict) -> str:
    lines = []
    for i, it in enumerate(st["items"], 1):
        lines.append(
            f"{i}) {it['part']['name']} (арт. {it['part'].get('article', '')}) — "
            f"{it['qty']} шт, {onec._SOURCE_NAMES.get(it['source'], it['source'])}"
        )
    return f"Техника: {st['vehicle']}. Позиции: " + "; ".join(lines) + "."


# START_CONTRACT: _dialog_turn
#   PURPOSE: Один семантический ход активного ScenarioFrame: интерпретация
#            реплики -> typed-команды -> ScenarioManager -> ответ.
#   INPUTS: { st: dict - legacy-проекция (источник истины - frame), text,
#             t_short, qty, history }
#   OUTPUTS: { dict - found/message/table/source текущего шага }
#   SIDE_EFFECTS: Мутирует frame только через ScenarioManager (typed-команды).
#   LINKS: M-VOICE-GATEWAY, M-SCENARIO-MANAGER, M-COMMAND-INTERPRETER, DF-PART-ORDER
# END_CONTRACT: _dialog_turn
# START_BLOCK_DIALOG_FSM
def _dialog_turn(st: dict, text: str, t_short: str, qty: int, history=None) -> dict:
    """Один ход сценария (ScenarioFrame). Реплика сначала интерпретируется
    семантически относительно frame/focus/pending, затем ScenarioManager
    применяет typed-команды. stage не используется для понимания реплики."""
    chat_id = st.get("_chat_id")
    session = _session(chat_id)
    manager = _scenario_manager()
    frame = session.active if session else None
    if frame is None:
        return {
            "found": False,
            "message": "Нет активного сценария. Скажите, что нужно.",
            "source": "1c",
        }

    # 0) подтверждение создания документов: только явное «да» исполняет.
    #    Любая другая реплика — обычная интерпретация данных; правка после
    #    показа подтверждения инвалидирует PendingAction (Case 6).
    if frame.pending_action is not None:
        if _is_yes(t_short):
            try:
                r = manager.apply(session, Command(kind="confirm_pending"))
            except Exception as e:
                # legacy-паритет: ошибка 1С — понятный ответ, подтверждение остаётся
                return {
                    "found": False,
                    "message": f"Не удалось создать документы: {e}",
                    "source": "1c",
                }
            if r.ok and r.status == "executed":
                docs = dict(r.data.get("docs", {}))
                docs.pop("frame_payload", None)
                st["docs"] = docs
                table = build_cart_table(st, done=True)
                _dialog_reset(st)
                st["docs"] = docs  # ссылки на документы остаются видимыми после сброса
                return {
                    "found": True,
                    "message": r.data.get("message", ""),
                    "table": table,
                    "source": "1c",
                }
            return {
                "found": False,
                "message": r.message or "Не удалось создать документы.",
                "source": "1c",
            }
        if _is_no(t_short):
            manager.apply(session, Command(kind="reject_pending"))
            frame.status = "discarding"
            _sync_legacy_state(chat_id)
            return {
                "found": False,
                "message": "Хорошо. Продолжаем работать с этим заказом или не сохраняем его?",
                "source": "1c",
            }
        # иначе — реплика НЕ подтверждение: идём в обычную интерпретацию ниже;
        # любое изменение данных само инвалидирует pending_action (версия frame).

    # 1) статус «продолжаем или не сохраняем?»
    if frame.status == "discarding":
        if t_short in _ORDER_CONTINUE_WORDS:
            frame.status = "active"
            _sync_legacy_state(chat_id)
            return {
                "found": False,
                "message": "Хорошо, продолжаем. Можно назвать ещё запчасть или скажите «оформляй».",
                "source": "1c",
            }
        if t_short in _ORDER_DISCARD_WORDS or t_short in _NO_WORDS:
            _dialog_reset(st)
            return {
                "found": False,
                "message": (
                    "Заказ не сохранён, корзина очищена. Я ваш складской ассистент. "
                    "Что вам сейчас нужно — заказать запчасть для техники или узнать "
                    "остаток товара на складе?"
                ),
                "source": "llm",
            }
        return {
            "found": False,
            "message": "Уточните: продолжаем работать с этим заказом или не сохраняем?",
            "source": "1c",
        }

    # 2) ожидание выбора/подтверждения кандидата (ambiguous ссылочное поле)
    res_path = frame.pending_resolution
    if res_path:
        return _interpret_resolution(session, frame, st, text, t_short, qty, history)

    # 3) общая интерпретация реплики относительно frame
    return _interpret_free(session, frame, st, text, t_short, qty, history)


_ORDINAL_WORDS = {
    "первый": 0,
    "первая": 0,
    "первое": 0,
    "1": 0,
    "второй": 1,
    "вторая": 1,
    "второе": 1,
    "2": 1,
    "третий": 2,
    "третья": 2,
    "3": 2,
}

_FINALIZE_WORDS = (
    "оформи",
    "оформля",
    "оформим",
    "оформить",
    "создава",
    "создай",
    "создать",
    "достаточно",
    "хватит",
    "сохраня",
    "сохрани",
    "сохраняем",
)

_ADD_ROW_RE = re.compile(r"(добав|созда)\w*\s+(ещ[её]\s+)?(строку|позици\w+)", re.IGNORECASE)
_DEL_ROW_RE = re.compile(
    r"удал\w*\s+(перв\w+|втор\w+|трет\w+|последн\w+)?\s*(строк\w+|позици\w+)?", re.IGNORECASE
)
_ORDINAL_ROW_RE = re.compile(r"(перв|втор|трет|четв|последн)\w*\s*(строк|позици)", re.IGNORECASE)
_ROW_QTY_RE = re.compile(r"(строк\w*|позици\w*)", re.IGNORECASE)
_BACK_VEHICLE_RE = re.compile(r"верн\w+\s+к\s+(техник|машин)", re.IGNORECASE)
_VEHICLE_CORRECT_RE = re.compile(
    r"(?:нет,?\s*)?техник\w*\s+(?:вс[её][-\s]?таки\s+)?(?:теперь\s+)?(.+)$", re.IGNORECASE
)


def _row_ordinal_from_text(t_short: str) -> str | None:
    m = _ORDINAL_ROW_RE.search(t_short)
    if not m:
        return None
    root = m.group(1)
    if root.startswith("перв"):
        return "1"
    if root.startswith("втор"):
        return "2"
    if root.startswith("трет"):
        return "3"
    if root.startswith("последн"):
        return "last"
    return None


def _candidate_message(field) -> str:
    """Текст-подсказка по текущим кандидатам ссылочного поля."""
    if not field.candidates:
        return "В 1С ничего не нашлось. Попробуйте назвать иначе."
    return "; ".join(c.name for c in field.candidates[:3]) + "."


def _parts_table_with_stock_compat(field, vehicle: str | None):
    return _parts_table_with_stock(
        [{"name": c.name, "article": c.metadata.get("article", "")} for c in field.candidates],
        vehicle,
    )


def _apply_row_source(session, frame, row, use_qty: int):
    """Рассчитать источник обеспечения (E/C/O/S) по остаткам складов кейса."""
    nom = row.fields["nomenclature"]
    try:
        stocks = onec.stock_for_item(nom.entity.metadata.get("article") or nom.entity.name)
    except Exception:
        stocks = {"E": 0, "C": 0, "O": 0}
    if stocks["E"] >= use_qty:
        source = "E"
    elif stocks["C"] >= use_qty:
        source = "C"
    elif stocks["O"] >= use_qty:
        source = "O"
    else:
        source = "S"
    row.fields["supply_source"].set_value(source)
    return source


def _confirm_part_added(session, frame, st, row, use_qty: int) -> dict:
    manager = _scenario_manager()
    manager.apply(
        session,
        Command(
            kind="set_collection_field",
            path="items",
            item_ref=row.item_id,
            focus="quantity",
            value=use_qty,
        ),
    )
    r = manager.confirm_resolution(frame, f"items[{row.item_id}].nomenclature", 0)
    if not r.ok:
        return {
            "found": False,
            "message": "Не удалось подтвердить запчасть по справочнику 1С. Назовите её ещё раз.",
            "source": "1c",
        }
    source = _apply_row_source(session, frame, row, use_qty)
    _sync_legacy_state(st.get("_chat_id"))
    p = row.fields["nomenclature"].entity
    table = build_cart_table(st)
    return {
        "found": True,
        "message": (
            f"Добавил в заказ: {p.name} (арт. {p.metadata.get('article', '')}) — "
            f"{use_qty} шт, {onec._SOURCE_NAMES[source]}. "
            "Добавить ещё позицию или сохраняем заказ?"
        ),
        "table": table,
        "source": "1c",
    }


def _interpret_resolution(session, frame, st, text, t_short, qty, history) -> dict:
    """Ожидается выбор/подтверждение кандидата ссылочного поля (ambiguous)."""
    manager = _scenario_manager()
    res_path = frame.pending_resolution
    field = frame.field(res_path)
    is_vehicle = res_path == "vehicle"
    chat_id = st.get("_chat_id")

    # финализация важнее выбора кандидата: «оформи заказ» в контексте
    # подтверждения запчасти означает «сохраняй», а не «ищи номенклатуру»
    if any(k in t_short for k in _FINALIZE_WORDS) and not (
        _ADD_ROW_RE.search(t_short) or _DEL_ROW_RE.search(t_short)
    ):
        return _interpret_free(session, frame, st, text, t_short, qty, history)

    if _is_yes(t_short) and field.candidates:
        if is_vehicle:
            r = manager.confirm_resolution(frame, res_path, 0)
            if not r.ok:
                return {
                    "found": False,
                    "message": "Не удалось получить идентичность техники из 1С. Назовите другую.",
                    "source": "1c",
                }
            _sync_legacy_state(chat_id)
            named_item = (st.get("item") or "").strip()
            if named_item:
                st["item"] = None
                return _resolve_part_mention(session, frame, st, named_item, qty, history)
            return {
                "found": True,
                "message": (
                    f"Техника подтверждена: {field.entity.name}. Какая именно запчасть "
                    "нужна? Назовите название или артикул."
                ),
                "source": "1c",
            }
        row = _focused_row(frame)
        use_qty = qty or st.get("qty") or 1
        return _confirm_part_added(session, frame, st, row, use_qty)

    if t_short in _NO_WORDS and not _VEHICLE_CORRECT_RE.search(t_short):
        manager.apply(session, Command(kind="clear_field", path=res_path))
        _sync_legacy_state(chat_id)
        if is_vehicle:
            return {
                "found": False,
                "message": "Хорошо. Назовите другую технику — марку, модель или госномер.",
                "source": "1c",
            }
        return {
            "found": False,
            "message": "Хорошо. Назовите другую запчасть — вид или артикул.",
            "source": "1c",
        }

    # выбор по номеру/порядку: «второй», «2»
    ordinal = _ORDINAL_WORDS.get(t_short)
    if ordinal is not None and ordinal < len(field.candidates):
        manager.confirm_resolution(frame, res_path, ordinal)
        _sync_legacy_state(chat_id)
        if is_vehicle:
            return _interpret_resolution(
                session, frame, {**st, "_force_yes": True}, "да", "да", qty, history
            )
        row = _focused_row(frame)
        return _confirm_part_added(session, frame, st, row, qty or st.get("qty") or 1)

    # коррекция названия: новое упоминание вместо кандидатов
    if is_vehicle:
        m = _VEHICLE_CORRECT_RE.search((text or "").strip())
        mention = m.group(1).strip() if m else None
        if mention and not _is_no(t_short):
            r = manager.apply(session, Command(kind="set_field", path="vehicle", mention=mention))
            _sync_legacy_state(chat_id)
            if r.status == "ambiguous":
                return {
                    "found": r.ok,
                    "message": "Нашёл технику: " + field.candidates[0].name + ". Это она?",
                    "source": "1c",
                }
            if r.status == "resolved":
                return {
                    "found": True,
                    "message": f"Техника подтверждена: {field.entity.name}. Какая именно запчасть нужна?",
                    "source": "1c",
                }
            return {
                "found": False,
                "message": "Техника в 1С не найдена — назовите другую.",
                "source": "1c",
            }
    else:
        core = _strip_fillers(text)
        if core and not _is_no(t_short):
            return _resolve_part_mention(
                session, frame, st, core, qty, history, re_resolve=res_path
            )

    # мета/болтовня при выборе — выход к LLM (как в legacy)
    kind = _classify_utterance(text)
    if kind in ("meta", "chatter") or st.get("fails", 0) >= 2:
        st["fails"] = 0
        intent, _raw = lm_intent(text, history, extra_system=_render_state(st))
        ans = build_answer(text, intent, None)
        return {"found": False, "message": ans, "source": "llm"}
    return {"found": False, "message": "Уточните: да или нет?", "source": "1c"}


def _resolve_part_mention(
    session, frame, st, mention, qty, history, re_resolve: str | None = None
) -> dict:
    """Разрешить упоминание номенклатуры в строке корзины (строго, через 1С)."""
    manager = _scenario_manager()
    row = None
    if re_resolve:
        row_id = re_resolve.split("[", 1)[1].split("]", 1)[0]
        row = frame.collection_item("items", row_id)
    else:
        row = _focused_row(frame)
        if row is not None and row.fields["nomenclature"].filled:
            row = None
    if row is None:
        r = manager.apply(session, Command(kind="append_collection_item", path="items"))
        row_id = r.data["item_id"]
        row = frame.collection_item("items", row_id)
    path = f"items[{row.item_id}].nomenclature"
    r = manager.apply(session, Command(kind="set_field", path=path, mention=mention))
    _sync_legacy_state(st.get("_chat_id"))
    if r.status == "ambiguous":
        nom = frame.field(path)
        table = _parts_table_with_stock_compat(nom, st.get("vehicle"))
        if len(nom.candidates) == 1:
            c = nom.candidates[0]
            art = c.metadata.get("article", "")
            msg = f"Нашёл запчасть: {c.name}" + (f", артикул {art}" if art else "") + ". Она?"
        else:
            lst = "; ".join(
                c.name
                + (f" (арт. {c.metadata.get('article', '')})" if c.metadata.get("article") else "")
                for c in nom.candidates
            )
            tail = f" для техники {st.get('vehicle')}" if st.get("vehicle") else ""
            msg = f"Есть варианты{tail}: {lst}. Какой нужен?"
        return {"found": True, "message": msg, "table": table, "source": "1c"}
    if r.status == "not_found":
        st["fails"] = st.get("fails", 0) + 1
        return {
            "found": False,
            "message": (
                f"По '{mention}' номенклатуры в базе нет — такую запчасть мы не обрабатываем. "
                "Назовите артикул или другую запчасть."
            ),
            "source": "1c",
        }
    if r.status == "resolved":
        return _confirm_part_added(session, frame, st, row, qty or st.get("qty") or 1)
    return {"found": False, "message": "Уточните запчасть.", "source": "1c"}


def _interpret_free(session, frame, st, text, t_short, qty, history) -> dict:
    """Семантическая интерпретация реплики относительно ScenarioFrame.

    Порядок: строковые операции (адресные) -> finalize/нет -> коррекция техники
    -> количество -> упоминание запчасти -> мета/болтовня. stage НЕ участвует.
    """
    manager = _scenario_manager()
    chat_id = st.get("_chat_id")

    # 0) строковые операции коллекции — НЕ поиск номенклатуры (Case 1-3)
    if _ADD_ROW_RE.search(t_short):
        r = manager.apply(session, Command(kind="append_collection_item", path="items"))
        _sync_legacy_state(chat_id)
        return {
            "found": True,
            "message": "Добавил новую строку. Какая запчасть нужна — название или артикул?",
            "table": build_cart_table(st),
            "source": "1c",
        }
    if _DEL_ROW_RE.search(t_short):
        ordinal = _row_ordinal_from_text(t_short) or ("last" if "последн" in t_short else None)
        r = manager.apply(
            session,
            Command(kind="remove_collection_item", path="items", item_ref=ordinal or "last"),
        )
        _sync_legacy_state(chat_id)
        if r.ok:
            return {
                "found": True,
                "message": "Удалил строку. Что дальше — добавим ещё или сохраняем заказ?",
                "table": build_cart_table(st),
                "source": "1c",
            }
        return {"found": False, "message": "Такой строки нет в заказе.", "source": "1c"}
    # «во второй строке поставь три штуки» — адресное количество
    row_ord = _row_ordinal_from_text(t_short)
    qty_val = _extract_qty(text)
    if qty_val and row_ord is not None:
        r = manager.apply(
            session,
            Command(
                kind="set_collection_field",
                path="items",
                item_ref=row_ord,
                focus="quantity",
                value=qty_val,
            ),
        )
        _sync_legacy_state(chat_id)
        if r.ok:
            return {
                "found": True,
                "message": f"Готово: в строке {row_ord} количество {qty_val} шт.",
                "table": build_cart_table(st),
                "source": "1c",
            }
    if _BACK_VEHICLE_RE.search(t_short):
        manager.apply(session, Command(kind="switch_focus", focus="vehicle"))
        v = frame.fields.get("vehicle")
        name = v.entity.name if v and v.entity else "ещё не выбрана"
        return {
            "found": True,
            "message": f"Вернулся к технике: {name}. Что изменить?",
            "source": "1c",
        }

    # 1) финализация заказа -> PendingAction (требует явного подтверждения)
    if any(k in t_short for k in _FINALIZE_WORDS):
        if st["items"]:
            manager.propose_pending_action(frame, "create_repair_documents")
            _sync_legacy_state(chat_id)
            return {
                "found": True,
                "message": _cart_summary(st) + " Создаём документы?",
                "table": build_cart_table(st),
                "source": "1c",
            }
        return {
            "found": False,
            "message": "В заказе пока нет позиций. Назовите запчасть.",
            "source": "1c",
        }

    # 2) «нет» без контекста выбора = завершение набора позиций
    if _is_no(t_short) and not _VEHICLE_CORRECT_RE.search(t_short):
        if st["items"]:
            manager.propose_pending_action(frame, "create_repair_documents")
            _sync_legacy_state(chat_id)
            return {
                "found": True,
                "message": _cart_summary(st) + " Создаём документы?",
                "table": build_cart_table(st),
                "source": "1c",
            }
        return {
            "found": False,
            "message": "Позиций в заказе пока нет. Назовите запчасть — название или артикул.",
            "source": "1c",
        }

    # 2а) «да/подтверждаю» без ожидаемого подтверждения — документов не создаёт.
    # Если после yes-слов осталось содержимое («да, диск задний») — это упоминание.
    yes_remainder = " ".join(t for t in t_short.split() if t not in _YES_WORDS)
    if yes_remainder and _is_yes(t_short):
        return _resolve_part_mention(session, frame, st, yes_remainder, qty, history)
    if _is_yes(t_short):
        return {
            "found": False,
            "message": "Сейчас нечего подтверждать. Скажите «оформляй», чтобы сохранить заказ.",
            "source": "1c",
        }

    # 3) коррекция техники (Case 4): «нет, техника всё-таки МТЗ-82»
    if "техник" in t_short:
        m = _VEHICLE_CORRECT_RE.search((text or "").strip())
        mention = (m.group(1) if m else "").strip()
        if mention:
            r = manager.apply(session, Command(kind="set_field", path="vehicle", mention=mention))
            _sync_legacy_state(chat_id)
            v = frame.fields["vehicle"]
            if r.status == "ambiguous":
                return {
                    "found": r.ok,
                    "message": f"Нашёл технику: {v.candidates[0].name}. Это она?",
                    "source": "1c",
                }
            if r.status == "resolved":
                return {
                    "found": True,
                    "message": f"Техника теперь: {v.entity.name}. Запчасть подберу заново — назовите её.",
                    "source": "1c",
                }
            return {
                "found": False,
                "message": "Такой техники в 1С нет. Назовите другую.",
                "source": "1c",
            }

    # 4) количество к текущей строке
    if qty_val and not row_ord:
        row = _focused_row(frame)
        if row is not None and row.fields["nomenclature"].filled:
            r = manager.apply(
                session,
                Command(
                    kind="set_collection_field",
                    path="items",
                    item_ref=row.item_id,
                    focus="quantity",
                    value=qty_val,
                ),
            )
            _sync_legacy_state(chat_id)
            if r.ok:
                return {
                    "found": True,
                    "message": f"Количество: {qty_val} шт.",
                    "table": build_cart_table(st),
                    "source": "1c",
                }

    # 7а) «сколько … на складе» при промахе LLM — детерминированный остаток
    # (параллельный STOCK_QUERY, без создания мусорных строк в заказе)
    if _looks_stock_query(text):
        stock_item = _strip_stock_words(text)
        wh = None
        for alias, target in _WAREHOUSE_ALIASES:
            if alias in text.lower():
                wh = target
                break
        if stock_item or wh:
            action = "list_stock" if not stock_item else "get_stock"
            try:
                res = _run_stock_scenario(chat_id, stock_item or None, wh, action)
            except Exception as e:
                res = {"found": False, "message": f"Не удалось получить остаток: {e}"}
            st["fails"] = st.get("fails", 0)
            res.setdefault("table", build_stock_table(res, action))
            return res
        return {
            "found": False,
            "message": "По какому товару узнать остаток? Назовите название или артикул.",
            "source": "1c",
        }

    # 5)schema-driven: незаполненная обязательная техника — голое существительное
    # это упоминание ТЕХНИКИ (заказ всегда от техники), а не запчасти
    vehicle = frame.fields.get("vehicle")
    if vehicle is not None and not vehicle.filled:
        search_text = _strip_fillers(text)
        core = " ".join(
            t
            for t in search_text.split()
            if t.lower() not in _INTENT_WORDS and t.lower() not in onec._SERVICE_WORDS
        )
        if not core:
            return {
                "found": False,
                "message": "Для какой техники нужна запчасть? Назовите марку, модель или госномер.",
                "source": "1c",
            }
        r = manager.apply(session, Command(kind="set_field", path="vehicle", mention=core))
        _sync_legacy_state(chat_id)
        if r.status == "ambiguous":
            return {
                "found": r.ok,
                "message": f"Нашёл технику: {vehicle.candidates[0].name}. Это она?",
                "source": "1c",
            }
        if r.status == "resolved":
            return {
                "found": True,
                "message": f"Техника подтверждена: {vehicle.entity.name}. Какая именно запчасть нужна?",
                "source": "1c",
            }
        st["fails"] = st.get("fails", 0) + 1
        res = call_lookup_vehicle(core)
        return {
            "found": False,
            "message": res.get("message") or f"Техника '{core}' в 1С не найдена. Назовите другую.",
            "source": "1c",
        }

    # 6) мета-вопрос: повторить варианты / состояние заказа
    kind = _classify_utterance(text)
    if kind == "meta":
        return {
            "found": False,
            "message": (
                f"Мы оформляем заказ для техники {st.get('vehicle')}. "
                "Назовите запчасть — название или артикул, либо скажите «оформляй»."
            ),
            "source": "1c",
        }

    # 7) болтовня / 2 подряд неудачи -> LLM (с проекцией frame)
    if kind == "chatter" or st.get("fails", 0) >= 2:
        st["fails"] = 0
        intent, _raw = lm_intent(text, history, extra_system=_render_state(st))
        ans = build_answer(text, intent, None)
        return {"found": False, "message": ans, "source": "llm"}

    # 8) содержательное упоминание запчасти -> строгий lookup
    search_text = _strip_fillers(text)
    core = " ".join(
        t
        for t in search_text.split()
        if t.lower() not in _INTENT_WORDS and t.lower() not in onec._SERVICE_WORDS
    )
    if not core:
        return {
            "found": False,
            "message": (
                f"Мы уже оформляем заказ для техники {st.get('vehicle')}. "
                "Назовите конкретную запчасть — название или артикул."
            ),
            "source": "1c",
        }
    st["fails"] = st.get("fails", 0)
    return _resolve_part_mention(session, frame, st, core, qty, history)


def _run_stock_scenario(
    chat_id: str, item: str | None, warehouse: str | None, action: str | None
) -> dict:
    """STOCK_QUERY как параллельный ScenarioFrame: активный frame не теряется.

    Строгое разрешение ссылки: без resolved-идентичности запрос не исполняется;
    в этом случае вызываемая сторона может откатиться на прямой запрос."""
    session = _session(chat_id)
    manager = _scenario_manager()
    stock_frame = manager.start_scenario(session, "stock_query")
    try:
        if item:
            r = manager.apply(session, Command(kind="set_field", path="nomenclature", mention=item))
            if r.status != "resolved":
                nom = stock_frame.fields["nomenclature"]
                if r.status == "ambiguous":
                    return {
                        "found": False,
                        "message": "Уточните, какой именно товар: " + _candidate_message(nom),
                        "source": "1c",
                    }
                return {
                    "found": False,
                    "message": f"Товар '{item}' в 1С не найден.",
                    "source": "1c",
                }
        if warehouse:
            manager.apply(session, Command(kind="set_field", path="warehouse", mention=warehouse))
        nom = stock_frame.fields["nomenclature"]
        wh = stock_frame.fields.get("warehouse")
        wh_name = wh.entity.name if (wh and wh.entity and wh.filled) else None
        if action == "list_stock" and not item and wh_name:
            return stock_at_warehouse_view(wh_name)
        res = onec.query_stock(nom.entity.name, wh_name)
        return res
    finally:
        manager.cancel_scenario(session, stock_frame)
        _sync_legacy_state(chat_id)


# END_BLOCK_DIALOG_FSM
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=(HERE / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/diag", response_class=HTMLResponse)
def diag():
    return HTMLResponse(content=(HERE / "static" / "diag.html").read_text(encoding="utf-8"))


def resolve_model() -> str:
    global _cached_model
    if LM_MODEL and LM_MODEL.lower() not in ("", "auto"):
        return LM_MODEL
    if _cached_model:
        return _cached_model
    try:
        r = requests.get(f"{LM_BASE_URL}/models", timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if data:
            _cached_model = data[0]["id"]
            return _cached_model
    except Exception as e:
        print(f"[gateway] cannot list LM models: {e}", flush=True)
    return LM_MODEL if LM_MODEL and LM_MODEL.lower() != "auto" else "local-model"


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        start = t.find("{")
        end = t.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(t[start : end + 1])
            except Exception:
                return None
        return None


# START_CONTRACT: lm_intent
#   PURPOSE: Распознать намерение из реплики через OpenAI-compatible LLM.
#   INPUTS: { text: str, history: deque|None, extra_system: str|None }
#   OUTPUTS: { (intent dict|None, raw str) }
#   SIDE_EFFECTS: Метрики record_lm (токены, кэш, стоимость, ток/с).
#   LINKS: M-VOICE-GATEWAY, DF-VOICE-TURN
# END_CONTRACT: lm_intent
# START_BLOCK_LLM_INTENT
def lm_intent(
    text: str,
    history: deque | None = None,
    extra_system: str | None = None,
) -> tuple[dict | None, str]:
    model = resolve_model()
    headers = {"Authorization": f"Bearer {LM_API_KEY}"}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if extra_system:
        messages.append({"role": "system", "content": extra_system})
    if history:
        messages += list(history)
    messages.append({"role": "user", "content": text})
    content = ""
    body: dict = {}
    t0 = metrics.ms()
    for _attempt in (1, 2):  # один повтор: LLM иногда отвечает без JSON
        payload = {
            "model": model,
            "temperature": 0.0,
            "max_tokens": 2048,
            "messages": messages,
        }
        if not LM_ENABLE_THINKING:
            # vLLM/Qwen3: мгновенный ответ без цепочки рассуждений
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            r = requests.post(
                f"{LM_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=LM_TIMEOUT
            )
            r.raise_for_status()
            body = r.json()
            content = body["choices"][0]["message"]["content"]
        except Exception as e:
            return None, f"<LM error: {e}>"
        if extract_json(content) is not None:
            break
    lm_ms = metrics.ms() - t0
    usage = body.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    cost = (pt / 1e6) * LM_PRICE_PROMPT + (ct / 1e6) * LM_PRICE_COMPLETION
    metrics.record_lm(model, pt, ct, cached, lm_ms, cost)
    return extract_json(content), content


# END_BLOCK_LLM_INTENT


def call_stock_api(item: str, warehouse: str | None = None) -> dict:
    """Получить остатки товара (опц. на конкретном складе). Источник определяется
    STOCK_BACKEND: '1c' — 1C MCP Toolkit (REST), 'mock' — заглушка mock-api.
    При STOCK_FALLBACK_TO_MOCK=true и ошибке 1С — откат на mock."""
    if STOCK_BACKEND == "1c":
        try:
            return onec.query_stock(item, warehouse)
        except Exception as e:
            print(f"[gateway] 1C stock failed: {e}", flush=True)
            if STOCK_FALLBACK_TO_MOCK:
                result = _mock_stock(item)
                result["source"] = f"mock(fallback: {e.__class__.__name__})"
                return result
            return {
                "item": item,
                "found": False,
                "quantity": None,
                "items": [],
                "warehouses": [],
                "message": f"Не удалось получить остаток из 1С: {e}",
                "source": "1c",
            }
    return _mock_stock(item)


def _mock_stock(item: str) -> dict:
    r = requests.get(STOCK_API_URL, params={"item": item}, timeout=10)
    r.raise_for_status()
    data = r.json()
    data.setdefault("source", "mock")
    return data


def call_order_api(item: str, quantity: int, warehouse: str | None = None) -> dict:
    """Оформить заказ на товар (кейс «заказ частей»). Бэкенд как у остатков:
    STOCK_BACKEND='1c' — документ в 1С через /api/execute_code, при ошибке
    (напр., «Записать» в чёрном списке тулкита) — откат на mock."""
    if STOCK_BACKEND == "1c":
        try:
            return onec.create_order(item, quantity)
        except Exception as e:
            print(f"[gateway] 1C order failed: {e}", flush=True)
            if STOCK_FALLBACK_TO_MOCK:
                result = _mock_order(item, quantity, warehouse)
                result["source"] = f"mock(fallback: {e.__class__.__name__})"
                return result
            return {
                "item": item,
                "found": False,
                "quantity": quantity,
                "order_number": None,
                "status": None,
                "message": f"Не удалось оформить заказ в 1С: {e}",
                "source": "1c",
            }
    return _mock_order(item, quantity, warehouse)


def _mock_order(item: str, quantity: int, warehouse: str | None = None) -> dict:
    r = requests.post(
        ORDER_API_URL,
        json={"item": item, "quantity": quantity, "warehouse": warehouse},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    data.setdefault("source", "mock")
    return data


def _norm_quantity(q) -> int:
    """LLM может вернуть число, '5' или null → целое >= 1."""
    try:
        n = int(float(q))
    except (TypeError, ValueError):
        return 1
    return n if n > 0 else 1


def build_stock_table(stock: dict | None, action: str | None) -> dict | None:
    """Таблица для веб-чата из результата остатков:
    одна позиция — строки-склады; несколько — строки-товары."""
    if not stock:
        return None
    items = stock.get("items") or []
    if action in ("get_stock", "list_stock") and items:
        if len(items) == 1:
            it = items[0]
            art = f" (арт. {it['article']})" if it.get("article") else ""
            rows = [[w["name"], onec._format_qty(w["quantity"])] for w in it.get("warehouses", [])]
            rows.append(["ИТОГО", onec._format_qty(it["quantity"])])
            return {
                "title": f"{it['name']}{art}",
                "headers": ["Склад", "Количество"],
                "rows": rows,
            }
        return {
            "title": "Товары с остатком",
            "headers": ["Товар", "Артикул", "Количество"],
            "rows": [
                [i["name"], i.get("article", ""), onec._format_qty(i["quantity"])] for i in items
            ],
        }
    return None


def build_parts_table(parts: list, vehicle: str | None) -> dict | None:
    if not parts:
        return None
    title = f"Варианты для техники {vehicle}" if vehicle else "Варианты запчастей"
    return {
        "title": title,
        "headers": ["Запчасть", "Артикул"],
        "rows": [[p["name"], p.get("article", "")] for p in parts],
    }


def _parts_table_with_stock(parts: list, vehicle: str | None) -> dict | None:
    """Варианты + остатки по трём складам кейса (колонки «У вас / Тек. ОП / Др. ОП»)."""
    table = build_parts_table(parts, vehicle)
    if not table:
        return None
    table["headers"] = table["headers"] + ["У вас", "Тек. ОП", "Др. ОП"]
    for row, p in zip(table["rows"], parts, strict=False):
        try:
            s = onec.stock_for_item(p.get("article") or p["name"])
            row += [s["E"], s["C"], s["O"]]
        except Exception:
            row += ["?", "?", "?"]
    return table


def build_cart_table(st: dict, done: bool = False) -> dict | None:
    """Таблица корзины: техника в заголовке, позиции со складами-источниками."""
    items = st.get("items") or []
    if not items:
        return None
    vehicle = st.get("vehicle") or ""
    title = "Заказ для техники: " + vehicle
    if done and st.get("docs"):
        title += " — создан ремонт № " + str(st["docs"].get("repair", ""))
    rows = [
        [
            i + 1,
            it["part"]["name"],
            it["part"].get("article", ""),
            it["qty"],
            onec._SOURCE_NAMES.get(it["source"], it["source"]),
        ]
        for i, it in enumerate(items)
    ]
    table: dict = {
        "title": title,
        "headers": ["№", "Запчасть", "Артикул", "Кол-во", "Источник"],
        "rows": rows,
    }
    docs = st.get("docs") or {}
    labels = {
        "repair_link": "Заказ на ремонт",
        "cmove_link": "Перемещение (текущее ОП)",
        "zorder_link": "Заказ на перемещение",
        "zmove_link": "Перемещение (другое ОП)",
        "order_link": "Заказ поставщику",
    }
    links = [{"label": label, "url": docs[key]} for key, label in labels.items() if docs.get(key)]
    if links:
        table["links"] = links
    return table


def call_part_api(item: str, vehicle: str, quantity: int) -> dict:
    """Кейс «запчасть для техники»: подбор техники, остатки по складам
    (инженер → текущее ОП → другое ОП) и создание нужных документов
    (ЗаказНаРемонт / Перемещение / ЗаказНаПеремещение / ЗаказПоставщику)."""
    try:
        return onec.request_part(item, vehicle, quantity)
    except Exception as e:
        print(f"[gateway] 1C request_part failed: {e}", flush=True)
        return {
            "found": False,
            "vehicle": vehicle,
            "branch": "ERROR",
            "docs": [],
            "quantity": quantity,
            "message": f"Не удалось выполнить запрос запчасти в 1С: {e}",
            "source": "1c",
        }


def _lookup_error(prefix: str, e: Exception) -> dict:
    return {
        "found": False,
        "message": f"Не удалось найти {prefix} в 1С: {e}",
        "source": "1c",
    }


def call_lookup_vehicle(vehicle: str) -> dict:
    """Шаг диалога: найти технику в базе и попросить подтверждение."""
    try:
        return onec.find_vehicles(vehicle)
    except Exception as e:
        print(f"[gateway] 1C find_vehicles failed: {e}", flush=True)
        return _lookup_error("технику", e)


def call_lookup_parts(item: str, vehicle: str | None) -> dict:
    """Шаг диалога: предложить варианты запчастей из базы для выбора."""
    try:
        return onec.find_parts(item, vehicle)
    except Exception as e:
        print(f"[gateway] 1C find_parts failed: {e}", flush=True)
        return _lookup_error("запчасти", e)


def _cart_view(st: dict) -> dict:
    """JSON-вид корзины для X-Cart / панели UI."""
    return {
        "stage": st.get("stage"),
        "vehicle": st.get("vehicle"),
        "items": [
            {
                "name": it["part"]["name"],
                "article": it["part"].get("article", ""),
                "qty": it["qty"],
                "source": it["source"],
                "source_name": onec._SOURCE_NAMES.get(it["source"], it["source"]),
            }
            for it in st.get("items") or []
        ],
        "docs": st.get("docs"),
    }


def call_stock(item: str | None, warehouse: str | None, action: str | None = None) -> dict:
    """Единая точка остатков: список всего склада (list_stock без товара)
    или остаток по товару."""
    if action == "list_stock" and not item and warehouse:
        return stock_at_warehouse_view(warehouse)
    return call_stock_api(item, warehouse)


# START_CONTRACT: build_answer
#   PURPOSE: Собрать текстовый ответ по интенту и результату бэкенда.
#   INPUTS: { text, intent dict|None, stock dict|None }
#   OUTPUTS: { str - текст для озвучки }
#   LINKS: M-VOICE-GATEWAY
# END_CONTRACT: build_answer
# START_BLOCK_ANSWER_BUILD
def build_answer(text: str, intent: dict | None, stock: dict | None) -> str:
    action = (intent or {}).get("action")
    item = (intent or {}).get("item")
    if action in ("get_stock", "list_stock"):
        if action == "list_stock":
            if stock and stock.get("items") is not None:
                return onec._build_list_message(stock["items"], item, stock.get("warehouse"))
            return (stock or {}).get("message") or f"По '{item}' товаров с остатком нет."
        if stock and stock.get("found"):
            return stock.get("message") or f"Остаток: {stock.get('quantity')} штук."
        return (stock or {}).get("message") or f"Товар '{item}' не найден."
    if action == "order_part":
        if stock and stock.get("found"):
            return stock.get("message") or f"Заказ на '{item}' оформлен."
        return (stock or {}).get("message") or f"Товар '{item}' не найден."
    if action == "request_part":
        if stock and stock.get("found"):
            return stock.get("message") or "Запчасть запрошена."
        return (stock or {}).get("message") or f"Не удалось найти запчасть '{item}'."
    if action in ("lookup_vehicle", "lookup_parts"):
        return (stock or {}).get("message") or "Не удалось найти в базе. Уточните."
    if action in ("order_fallback", "stock_fallback"):
        return (stock or {}).get("message") or "Уточните запрос."
    if action == "clarify":
        q = str((intent or {}).get("question") or "").strip()
        if q:
            return q
    if action == "chat":
        ans = str((intent or {}).get("answer") or "").strip()
        if ans:
            return ans
    return (
        "Я умею узнавать остатки по товарам и оформлять заказы. "
        "Что вам сейчас нужно — заказать запчасть для техники или узнать остаток "
        "товара на складе?"
    )


# END_BLOCK_ANSWER_BUILD
# START_CONTRACT: orchestrate
#   PURPOSE: Полный ход диалога: FSM корзины -> LLM NLU -> бэкенд -> TTS.
#   INPUTS: { text: str - реплика, chat_id: str|None - идентификатор чата }
#   OUTPUTS: { audio bytes, headers (X-Answer/X-Table/X-Cart), trace metrics }
#   SIDE_EFFECTS: Обновляет _DIALOG_STATES/_CHATS; обращается к 1С/LM/TTS.
#   LINKS: M-VOICE-GATEWAY, M-1C-ADAPTER, DF-VOICE-TURN, DF-PART-ORDER
# END_CONTRACT: orchestrate
# START_BLOCK_ROUTE_INTENT
def orchestrate(text: str, chat_id: str | None = None) -> tuple[bytes, dict, dict]:
    """Run LM → stock → TTS and return (audio, headers, trace_extra).

    При заданном chat_id работает сущность «чат»: история подмешивается в LLM,
    а лестница подтверждений заказа запчасти ведётся детерминированным
    автоматом состояний (без LLM на шагах «да»/выбора варианта)."""
    history = chat_history(chat_id)
    st = _dialog_state(chat_id)
    t_short = _norm_short(text)

    # 0) отмена диалога
    if st is not None and st["stage"] != "idle" and t_short in _ABORT_WORDS:
        _dialog_reset(st)
        answer = (
            "Хорошо, отменил. Что вам сейчас нужно — заказать запчасть для техники "
            "или узнать остаток товара на складе?"
        )
        chat_append(history, text, answer)
        intent = {"action": "dialog_abort"}
        t_tts = metrics.ms()
        tts_r = requests.post(f"{TTS_URL}/tts", json={"text": answer}, timeout=60)
        tts_r.raise_for_status()
        headers = {
            "X-Question": urllib.parse.quote(text),
            "X-Intent": urllib.parse.quote(json.dumps(intent, ensure_ascii=False)),
            "X-Answer": urllib.parse.quote(answer),
        }
        return (
            tts_r.content,
            headers,
            {"lm_ms": 0.0, "stock_ms": None, "tts_ms": metrics.ms() - t_tts},
        )

    # 1) активная лестница: ход обрабатывает автомат состояний (без LLM),
    #    кроме мета-вопросов/болтовни/2 подряд неудач — там LLM с состоянием.
    #    Вопросы об остатках внутри маршрута — разрешены, этап сохраняется.
    if st is not None and st["stage"] in (
        "await_vehicle",
        "await_vehicle_confirm",
        "await_part",
        "await_part_confirm",
        "await_order_confirm",
        "await_order_discard",
    ):
        if _looks_stock_query(text):
            intent, raw = lm_intent(text, history, extra_system=_render_state(st))
            if (intent or {}).get("action") in ("get_stock", "list_stock"):
                item_s = (intent or {}).get("item")
                wh_s = _map_warehouse((intent or {}).get("warehouse"))
                t_stock = metrics.ms()
                try:
                    # параллельный STOCK_QUERY frame: активный сценарий не теряется
                    stock = _run_stock_scenario(chat_id, item_s, wh_s, (intent or {}).get("action"))
                except Exception as e:
                    stock = {"found": False, "message": f"Не удалось получить остаток: {e}"}
                stock_ms = metrics.ms() - t_stock
                answer = build_answer(text, intent, stock)
                table = build_stock_table(stock, (intent or {}).get("action"))
                chat_append(history, text, answer)
                t_tts = metrics.ms()
                tts_r = requests.post(f"{TTS_URL}/tts", json={"text": answer}, timeout=60)
                tts_r.raise_for_status()
                headers = {
                    "X-Question": urllib.parse.quote(text),
                    "X-Intent": urllib.parse.quote(json.dumps(intent or {}, ensure_ascii=False)),
                    "X-Answer": urllib.parse.quote(answer),
                    "X-Table": urllib.parse.quote(json.dumps(table or {}, ensure_ascii=False)),
                    "X-Cart": urllib.parse.quote(json.dumps(_cart_view(st), ensure_ascii=False)),
                }
                return (
                    tts_r.content,
                    headers,
                    {
                        "lm_ms": metrics.ms() - t_stock - stock_ms,
                        "stock_ms": stock_ms,
                        "tts_ms": metrics.ms() - t_tts,
                        "stock_src": stock.get("source"),
                        "item": item_s,
                        "warehouse": wh_s,
                        "found": stock.get("found"),
                        "items": len(stock.get("items", [])) if stock else 0,
                        "answer_len": len(answer),
                    },
                )
            # не похоже на остатки — обычный ход машины (ниже)
        t_stock = metrics.ms()
        stock = _dialog_turn(st, text, t_short, st.get("qty") or 1, history)
        stock_ms = metrics.ms() - t_stock
        answer = lm_phrase(stock.get("message") or "Уточните.", _render_state(st))
        chat_append(history, text, answer)
        dialog_trace = {
            "component": "VoiceGateway",
            "function": "_dialog_turn",
            "block": "[BLOCK_DIALOG_FSM]",
            "stage": st["stage"],
        }
        intent = {"action": f"dialog:{st['stage']}"}
        t_tts = metrics.ms()
        tts_r = requests.post(f"{TTS_URL}/tts", json={"text": answer}, timeout=60)
        tts_r.raise_for_status()
        headers = {
            "X-Question": urllib.parse.quote(text),
            "X-Intent": urllib.parse.quote(json.dumps(intent, ensure_ascii=False)),
            "X-Answer": urllib.parse.quote(answer),
            "X-Table": urllib.parse.quote(json.dumps(stock.get("table") or {}, ensure_ascii=False)),
            "X-Cart": urllib.parse.quote(json.dumps(_cart_view(st), ensure_ascii=False)),
        }
        return (
            tts_r.content,
            headers,
            {
                "lm_ms": 0.0,
                "stock_ms": stock_ms,
                "tts_ms": metrics.ms() - t_tts,
                "stock_src": stock.get("source"),
                "item": st.get("item"),
                "warehouse": st.get("vehicle"),
                "found": stock.get("found"),
                "items": 0,
                "answer_len": len(answer),
                **dialog_trace,
            },
        )

    # 2) обычный NLU-путь
    t_lm = metrics.ms()
    intent, raw = lm_intent(text, history)
    lm_ms = metrics.ms() - t_lm

    # 2а) LLM не распознал — детерминированные триггеры намерений по словам
    # (защита от цикла «не понял — повторяю приветствие»)
    fallback_stock = None
    if st is not None and (intent is None or (intent or {}).get("action") == "unknown"):
        t_low = text.lower()
        if any(k in t_low for k in ("заказ", "заказать", "закажи", "запчаст")):
            st.update({"stage": "await_vehicle", "item": None, "qty": 1})
            # сценарий REPAIR_ORDER стартует и в fallback-ветке (frame — истина)
            _fallback_session = _session(chat_id)
            if _fallback_session.active is None:
                _scenario_manager().start_scenario(_fallback_session, "repair_order")
                _sync_legacy_state(chat_id)
            intent = {"action": "order_fallback"}
            fallback_stock = {
                "found": False,
                "message": "Для какой техники нужна запчасть? Назовите марку, модель или госномер.",
            }
        elif any(k in t_low for k in ("остат", "сколько")):
            intent = {"action": "stock_fallback"}
            fallback_stock = {
                "found": False,
                "message": "По какому товару узнать остаток? Назовите название или артикул.",
            }

    stock = fallback_stock
    item = (intent or {}).get("item")
    warehouse = _map_warehouse((intent or {}).get("warehouse"))
    action = (intent or {}).get("action")
    if fallback_stock is not None:
        stock_ms = 0.0
    if action == "request_part" and (item or (intent or {}).get("vehicle")):
        # старт сценария REPAIR_ORDER: строгое разрешение техники по 1С (EntityRef)
        t_stock = metrics.ms()
        st_item = _strip_fillers(item) or None
        st_vehicle = str((intent or {}).get("vehicle") or "") or None
        st_qty = _norm_quantity((intent or {}).get("quantity"))
        if st is None:
            # анонимный запрос (без чата) — прежний прямой цикл
            stock = call_part_api(st_item, st_vehicle or "", st_qty)
        else:
            session = _session(chat_id)
            manager = _scenario_manager()
            frame = session.active
            if frame is None or frame.scenario_type != "repair_order":
                frame = manager.start_scenario(session, "repair_order")
            st.update({"stage": "await_vehicle", "item": st_item, "qty": st_qty})
            if st_vehicle:
                stock = call_lookup_vehicle(st_vehicle)
                manager.apply(
                    session, Command(kind="set_field", path="vehicle", mention=st_vehicle)
                )
                if stock.get("found") and len(stock.get("vehicles", [])) == 1:
                    st["stage"] = "await_vehicle_confirm"
            else:
                stock = {
                    "found": False,
                    "message": "Для какой техники нужна запчасть? Назовите марку, модель или госномер.",
                }
            _sync_legacy_state(chat_id)
            if (
                st_vehicle
                and st.get("stage") == "await_vehicle_confirm"
                and len(session.active.fields["vehicle"].candidates) == 1
            ):
                stock = {
                    "found": False,
                    "message": f"Нашёл технику: {session.active.fields['vehicle'].candidates[0].name}. Это она?",
                }
        stock_ms = metrics.ms() - t_stock
    elif action == "lookup_vehicle":
        t_stock = metrics.ms()
        stock = call_lookup_vehicle(str((intent or {}).get("vehicle") or item or ""))
        stock_ms = metrics.ms() - t_stock
    elif action == "lookup_parts" and item:
        t_stock = metrics.ms()
        stock = call_lookup_parts(item, (intent or {}).get("vehicle"))
        stock_ms = metrics.ms() - t_stock
    elif action == "order_part" and item:
        t_stock = metrics.ms()
        try:
            stock = call_order_api(
                item,
                _norm_quantity((intent or {}).get("quantity")),
                warehouse,
            )
        except Exception as e:
            stock = {"found": False, "message": f"Не удалось оформить заказ: {e}"}
        stock_ms = metrics.ms() - t_stock
        if st is not None:
            _dialog_reset(st)
    elif action in ("get_stock", "list_stock") and (item or (action == "list_stock" and warehouse)):
        t_stock = metrics.ms()
        try:
            stock = call_stock(item, warehouse, action)
        except Exception as e:
            stock = {"found": False, "message": f"Не удалось получить остаток: {e}"}
        stock_ms = metrics.ms() - t_stock
    else:
        stock_ms = None
        if st is not None and action in ("get_stock", "list_stock"):
            _dialog_reset(st)

    answer = build_answer(text, intent, stock)
    chat_append(history, text, answer)

    t_tts = metrics.ms()
    try:
        tts_r = requests.post(f"{TTS_URL}/tts", json={"text": answer}, timeout=60)
        tts_r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TTS failed: {e}") from e
    tts_ms = metrics.ms() - t_tts

    table = build_stock_table(stock, action) or (stock or {}).get("table")
    headers = {
        "X-Question": urllib.parse.quote(text),
        "X-Intent": urllib.parse.quote(json.dumps(intent or {}, ensure_ascii=False)),
        "X-Answer": urllib.parse.quote(answer),
        "X-LM-Raw": urllib.parse.quote((raw or "")[:500]),
        "X-Table": urllib.parse.quote(json.dumps(table or {}, ensure_ascii=False)),
    }
    if chat_id and st is not None:
        headers["X-Cart"] = urllib.parse.quote(json.dumps(_cart_view(st), ensure_ascii=False))
    extra = {
        "lm_ms": lm_ms,
        "stock_ms": stock_ms,
        "tts_ms": tts_ms,
        "stock_src": (stock or {}).get("source") if stock else None,
        "item": item,
        "warehouse": warehouse,
        "found": (stock or {}).get("found") if stock else None,
        "items": len((stock or {}).get("items", [])) if stock else 0,
        "answer_len": len(answer),
        "component": "VoiceGateway",
        "function": "orchestrate",
        "block": "[BLOCK_ROUTE_INTENT]",
    }
    return tts_r.content, headers, extra


# END_BLOCK_ROUTE_INTENT
def _finish(headers: dict, trace: dict) -> Response:
    trace["total_ms"] = max(0.0, metrics.ms() - trace.pop("_t0", metrics.ms()))
    headers["X-Timings"] = metrics.fmt_timings(trace)
    metrics.record(trace)
    print(metrics.log_line(trace), flush=True)
    return Response(content=headers.pop("_audio"), media_type="audio/wav", headers=headers)


@app.get("/health")
def health():
    def probe(url: str) -> bool:
        try:
            requests.get(f"{url}/health", timeout=5)
            return True
        except Exception:
            return False

    return {
        "ok": True,
        "stt": probe(STT_URL),
        "tts": probe(TTS_URL),
        "stock_backend": STOCK_BACKEND,
        "onec": onec.ping() if STOCK_BACKEND == "1c" else None,
        "onec_base_url": onec.ONEC_BASE_URL,
        "lm_base_url": LM_BASE_URL,
        "lm_model": LM_MODEL,
    }


class SpeakRequest(BaseModel):
    text: str


@app.post("/speak")
def speak(req: SpeakRequest):
    r = requests.post(f"{TTS_URL}/tts", json={"text": req.text}, timeout=60)
    r.raise_for_status()
    return Response(content=r.content, media_type="audio/wav")


@app.post("/transcribe")
def transcribe(file: UploadFile = File(...)):
    files = {"file": (file.filename or "audio.wav", file.file, file.content_type)}
    t0 = metrics.ms()
    r = requests.post(f"{STT_URL}/stt", files=files, timeout=180)
    r.raise_for_status()
    stt_ms = metrics.ms() - t0
    data = r.json()
    trace = {"kind": "transcribe", "_t0": t0, "stt_ms": stt_ms, "total_ms": stt_ms}
    metrics.record(trace)
    print(metrics.log_line(trace), flush=True)
    return JSONResponse(content=data, headers={"X-Timings": metrics.fmt_timings(trace)})


class AskTextRequest(BaseModel):
    text: str
    chat_id: str | None = None


@app.post("/ask-text")
def ask_text(req: AskTextRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    t0 = metrics.ms()
    try:
        audio, headers, extra = orchestrate(text, req.chat_id)
    except HTTPException as e:
        _err(t0, "ask-text", str(e.detail))
        raise
    trace = {"kind": "ask-text", "_t0": t0, **extra}
    headers["_audio"] = audio
    return _finish(headers, trace)


@app.post("/ask")
def ask(
    file: UploadFile = File(...),
    chat_id: str | None = None,
):
    data = file.file.read()
    files = {
        "file": (
            file.filename or "audio.wav",
            data,
            file.content_type or "application/octet-stream",
        )
    }
    t0 = metrics.ms()
    t_stt = metrics.ms()
    try:
        stt_r = requests.post(f"{STT_URL}/stt", files=files, timeout=180)
        stt_r.raise_for_status()
    except HTTPException:
        raise
    except Exception as e:
        _err(t0, "ask", f"STT failed: {e}")
        raise HTTPException(status_code=502, detail=f"STT failed: {e}") from e
    stt_ms = metrics.ms() - t_stt
    text = (stt_r.json().get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="STT returned empty text")
    try:
        audio, headers, extra = orchestrate(text, chat_id or file.headers.get("x-chat-id"))
    except HTTPException as e:
        _err(t0, "ask", str(e.detail), stt_ms=stt_ms)
        raise
    trace = {"kind": "ask", "_t0": t0, "stt_ms": stt_ms, **extra}
    headers["_audio"] = audio
    return _finish(headers, trace)


def _err(t0: float, kind: str, msg: str, **extra) -> None:
    trace = {"kind": kind, "_t0": t0, "error": msg[:200], "total_ms": metrics.ms() - t0, **extra}
    metrics.record(trace)
    print(metrics.log_line(trace), flush=True)


@app.get("/metrics")
def get_metrics():
    return metrics.snapshot()


class CartItemRequest(BaseModel):
    chat_id: str
    index: int = 0
    qty: int = 1


def _cart_or_404(chat_id: str) -> dict:
    st = _DIALOG_STATES.get(chat_id)
    if st is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return st


@app.post("/cart/update")
def cart_update(req: CartItemRequest):
    st = _cart_or_404(req.chat_id)
    if req.index < 0 or req.index >= len(st["items"]):
        raise HTTPException(status_code=404, detail="cart item not found")
    st["items"][req.index]["qty"] = max(1, int(req.qty))
    return _cart_view(st)


@app.post("/cart/delete")
def cart_delete(req: CartItemRequest):
    st = _cart_or_404(req.chat_id)
    if req.index < 0 or req.index >= len(st["items"]):
        raise HTTPException(status_code=404, detail="cart item not found")
    del st["items"][req.index]
    return _cart_view(st)


@app.post("/cart/clear")
def cart_clear(req: CartItemRequest):
    st = _cart_or_404(req.chat_id)
    _dialog_reset(st)
    return _cart_view(st)


@app.get("/cart")
def cart_get(chat_id: str):
    """Состояние корзины для восстановления панели после обновления страницы."""
    st = _DIALOG_STATES.get(chat_id)
    if st is None:
        return {"stage": "idle", "vehicle": None, "items": [], "docs": None}
    return _cart_view(st)


@app.get("/transcript")
def transcript(chat_id: str):
    h = _CHATS.get(chat_id) or []
    lines = [
        f"[{m.get('ts', '')}] П: {m['content']}"
        if m["role"] == "user"
        else f"[{m.get('ts', '')}] А: {m['content']}"
        for m in h
    ]
    return {"chat_id": chat_id, "lines": lines}


@app.get("/monitor", response_class=HTMLResponse)
def monitor():
    return HTMLResponse(content=(HERE / "static" / "monitor.html").read_text(encoding="utf-8"))


@app.get("/scenario")
def scenario_view(chat_id: str):
    """Read-only debug: активные frame, focus, resolved/unresolved, pending."""
    session = _session(chat_id)
    if session is None:
        return {"chat_id": chat_id, "frames": []}
    manager = _scenario_manager()
    frames = []
    for frame in session.frames.values():
        frames.append(
            {
                "id": frame.id,
                "scenario_type": frame.scenario_type,
                "status": frame.status,
                "version": frame.version,
                "active": frame.id == session.active_frame_id,
                "projection": manager.compact_projection(frame),
                "pending_action": (
                    {
                        "id": frame.pending_action.id,
                        "type": frame.pending_action.type,
                        "frame_version": frame.pending_action.frame_version,
                        "stale": not frame.pending_action.matches_version(frame),
                    }
                    if frame.pending_action
                    else None
                ),
            }
        )
    return {"chat_id": chat_id, "active_frame_id": session.active_frame_id, "frames": frames}


# GRACE: стабильный публичный экспорт (для точной проверки поверхности)
__all__ = [
    "_norm_quantity",
    "app",
    "build_answer",
    "call_lookup_parts",
    "call_lookup_vehicle",
    "call_order_api",
    "call_part_api",
    "call_stock_api",
    "chat_append",
    "chat_history",
    "lm_intent",
    "orchestrate",
]
