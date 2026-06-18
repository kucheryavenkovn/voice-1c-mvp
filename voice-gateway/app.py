import json
import os
import pathlib
import re
import urllib.parse

import requests
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import onec

STT_URL = os.getenv("STT_URL", "http://stt:8000")
TTS_URL = os.getenv("TTS_URL", "http://tts:8000")
STOCK_API_URL = os.getenv("STOCK_API_URL", "http://mock-api:8000/api/stock")

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

SYSTEM_PROMPT = (
    "Ты — складской ассистент, интегрированный с 1С. "
    "Определи намерение пользователя и ответь СТРОГО валидным JSON без markdown и пояснений.\n"
    "Если спрашивают об остатке, количестве, наличии товара — верни:\n"
    '{"action": "get_stock", "item": "<название товара, 1-2 слова, существительное>"}\n'
    "В остальных случаях верни:\n"
    '{"action": "unknown", "item": null}\n'
    "Пример: 'сколько у нас молока?' -> {\"action\":\"get_stock\",\"item\":\"молоко\"}"
)

HERE = pathlib.Path(__file__).parent
app = FastAPI(title="voice-gateway")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

_cached_model = None


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


def lm_intent(text: str) -> tuple[dict | None, str]:
    model = resolve_model()
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 800,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    headers = {"Authorization": f"Bearer {LM_API_KEY}"}
    try:
        r = requests.post(
            f"{LM_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=60
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return None, f"<LM error: {e}>"
    return extract_json(content), content


def call_stock_api(item: str) -> dict:
    """Получить остатки товара. Источник определяется STOCK_BACKEND:
    '1c' — 1C MCP Toolkit (REST /api/execute_query), 'mock' — заглушка mock-api.
    При STOCK_FALLBACK_TO_MOCK=true и ошибке 1С — откат на mock.
    """
    if STOCK_BACKEND == "1c":
        try:
            return onec.query_stock(item)
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


def build_answer(text: str, intent: dict | None, stock: dict | None) -> str:
    action = (intent or {}).get("action")
    item = (intent or {}).get("item")
    if action == "get_stock":
        if stock and stock.get("found"):
            return stock.get("message") or f"Остаток: {stock.get('quantity')} штук."
        return (stock or {}).get("message") or f"Товар '{item}' не найден."
    return (
        "Я умею узнавать остатки по товарам. "
        "Спросите, например: какой остаток по молоку?"
    )


def orchestrate(text: str) -> tuple[bytes, dict]:
    intent, raw = lm_intent(text)
    stock = None
    if (intent or {}).get("action") == "get_stock" and (intent or {}).get("item"):
        try:
            stock = call_stock_api(intent["item"])
        except Exception as e:
            stock = {"found": False, "message": f"Не удалось получить остаток: {e}"}
    answer = build_answer(text, intent, stock)

    try:
        tts_r = requests.post(f"{TTS_URL}/tts", json={"text": answer}, timeout=60)
        tts_r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TTS failed: {e}")

    headers = {
        "X-Question": urllib.parse.quote(text),
        "X-Intent": urllib.parse.quote(json.dumps(intent or {}, ensure_ascii=False)),
        "X-Answer": urllib.parse.quote(answer),
        "X-LM-Raw": urllib.parse.quote((raw or "")[:500]),
    }
    return tts_r.content, headers


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
    r = requests.post(f"{STT_URL}/stt", files=files, timeout=180)
    r.raise_for_status()
    return r.json()


class AskTextRequest(BaseModel):
    text: str


@app.post("/ask-text")
def ask_text(req: AskTextRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    audio, headers = orchestrate(text)
    return Response(content=audio, media_type="audio/wav", headers=headers)


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
    try:
        stt_r = requests.post(f"{STT_URL}/stt", files=files, timeout=180)
        stt_r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"STT failed: {e}")
    text = (stt_r.json().get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="STT returned empty text")
    audio, headers = orchestrate(text)
    return Response(content=audio, media_type="audio/wav", headers=headers)
