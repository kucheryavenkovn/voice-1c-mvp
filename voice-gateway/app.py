import json
import os
import pathlib
import re
import urllib.parse
from collections import deque

import metrics
import onec
import requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
    "4) Если просят запчасть ДЛЯ ТЕХНИКИ или запчасть в контексте ремонта "
    "('нужен диск', 'нужен диск для кировца', 'закажи фильтр для мтз') — верни:\n"
    '{"action": "request_part", "item": "<запчасть>", "vehicle": "<техника '
    'как назвали или null>", "quantity": <целое число, по умолчанию 1>}\n'
    "В item — только запчасть, в vehicle — только техника. Подтверждения и выбор "
    "вариантов выполняет шлюз — не задавай встречных вопросов о подтверждении "
    "сам, не выполняй заказ по этой реплике сам.\n"
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

# --- детерминированная лестница подтверждений (автомат состояний диалога) ---
_DIALOG_STATES: dict[str, dict] = {}
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
}


def chat_history(chat_id: str | None) -> deque | None:
    """История чата (user/assistant пары) или None для анонимных запросов."""
    if not chat_id:
        return None
    return _CHATS.setdefault(chat_id, deque(maxlen=_CHAT_LIMIT))


def chat_append(history: deque | None, user_text: str, answer: str) -> None:
    if history is not None:
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": answer})


def _dialog_state(chat_id: str | None) -> dict | None:
    if not chat_id:
        return None
    return _DIALOG_STATES.setdefault(
        chat_id, {"stage": "idle", "item": None, "vehicle": None, "part": None, "qty": 1}
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


def _dialog_reset(st: dict) -> None:
    st.update({"stage": "idle", "item": None, "vehicle": None, "part": None, "qty": 1})


def _dialog_turn(st: dict, text: str, t_short: str, qty: int) -> dict:
    """Один ход детерминированной лестницы. Возвращает dict как от бэкенда."""
    stage = st["stage"]
    if stage == "await_vehicle":
        res = call_lookup_vehicle(_strip_fillers(text))
        if res.get("found") and len(res.get("vehicles", [])) == 1:
            st["vehicle"] = res["vehicles"][0]
            st["stage"] = "await_vehicle_confirm"
        return res
    if stage == "await_vehicle_confirm":
        if t_short in _NO_WORDS:
            st["stage"] = "await_vehicle"
            return {
                "found": False,
                "message": "Хорошо. Назовите другую технику — марку, модель или госномер.",
                "source": "1c",
            }
        res = call_lookup_parts(st["item"], st["vehicle"])
        if res.get("found") and len(res.get("parts", [])) == 1:
            st["part"] = res["parts"][0]
            st["stage"] = "await_part_confirm"
        else:
            st["stage"] = "await_part"
        return res
    if stage == "await_part":
        res = call_lookup_parts(_strip_fillers(text), st["vehicle"])
        if not res.get("found") and st["item"]:
            res = call_lookup_parts(f"{st['item']} {_strip_fillers(text)}", st["vehicle"])
        if res.get("found") and len(res.get("parts", [])) == 1:
            st["part"] = res["parts"][0]
            st["stage"] = "await_part_confirm"
        return res
    if stage == "await_part_confirm":
        if t_short in _NO_WORDS:
            st["stage"] = "await_part"
            return {
                "found": False,
                "message": "Хорошо. Назовите другую запчасть — вид или артикул.",
                "source": "1c",
            }
        p = st["part"] or {}
        res = call_part_api(p.get("article") or p.get("name") or text, st["vehicle"], qty)
        if res.get("found"):
            _dialog_reset(st)
        return res
    return {"found": False, "message": "Неизвестный шаг диалога.", "source": "1c"}


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


def lm_intent(text: str, history: deque | None = None) -> tuple[dict | None, str]:
    model = resolve_model()
    headers = {"Authorization": f"Bearer {LM_API_KEY}"}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages += list(history)
    messages.append({"role": "user", "content": text})
    content = ""
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
            content = r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return None, f"<LM error: {e}>"
        if extract_json(content) is not None:
            break
    return extract_json(content), content


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

    # 1) активная лестница: ход обрабатывает автомат состояний (без LLM)
    if st is not None and st["stage"] in (
        "await_vehicle",
        "await_vehicle_confirm",
        "await_part",
        "await_part_confirm",
    ):
        t_stock = metrics.ms()
        stock = _dialog_turn(st, text, t_short, st.get("qty") or 1)
        stock_ms = metrics.ms() - t_stock
        answer = stock.get("message") or "Уточните."
        chat_append(history, text, answer)
        intent = {"action": f"dialog:{st['stage']}"}
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
            },
        )

    # 2) обычный NLU-путь
    t_lm = metrics.ms()
    intent, raw = lm_intent(text, history)
    lm_ms = metrics.ms() - t_lm

    stock = None
    item = (intent or {}).get("item")
    warehouse = (intent or {}).get("warehouse")
    action = (intent or {}).get("action")
    if action == "request_part" and item:
        # старт лестницы: подтверждаем технику и запчасть из базы, не заказываем сразу
        t_stock = metrics.ms()
        st_item = item
        st_vehicle = str((intent or {}).get("vehicle") or "") or None
        st_qty = _norm_quantity((intent or {}).get("quantity"))
        if st is None:
            # анонимный запрос (без чата) — прежний прямой цикл
            stock = call_part_api(st_item, st_vehicle or "", st_qty)
        else:
            st.update({"stage": "await_vehicle", "item": st_item, "qty": st_qty})
            if st_vehicle:
                stock = call_lookup_vehicle(st_vehicle)
                if stock.get("found") and len(stock.get("vehicles", [])) == 1:
                    st["vehicle"] = stock["vehicles"][0]
                    st["stage"] = "await_vehicle_confirm"
            else:
                stock = {
                    "found": False,
                    "message": "Для какой техники нужна запчасть? Назовите марку, модель или госномер.",
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
    elif action in ("get_stock", "list_stock") and item:
        t_stock = metrics.ms()
        try:
            stock = call_stock_api(item, warehouse)
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

    headers = {
        "X-Question": urllib.parse.quote(text),
        "X-Intent": urllib.parse.quote(json.dumps(intent or {}, ensure_ascii=False)),
        "X-Answer": urllib.parse.quote(answer),
        "X-LM-Raw": urllib.parse.quote((raw or "")[:500]),
    }
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
    }
    return tts_r.content, headers, extra


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
def ask(file: UploadFile = File(...)):
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
        audio, headers, extra = orchestrate(text, file.headers.get("x-chat-id"))
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


@app.get("/monitor", response_class=HTMLResponse)
def monitor():
    return HTMLResponse(content=(HERE / "static" / "monitor.html").read_text(encoding="utf-8"))
