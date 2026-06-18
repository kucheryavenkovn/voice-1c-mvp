"""Mock STT (faster-whisper stand-in) — no GPU, no model.

Returns a fixed (configurable) transcript for any uploaded audio, so the full
voice pipeline can be exercised in CI / on machines without a GPU.
"""

import os

from fastapi import FastAPI, File, UploadFile

app = FastAPI(title="mock-stt")
TEXT = os.getenv("MOCK_STT_TEXT", "сколько у нас молока?")


@app.get("/health")
def health():
    return {"ok": True, "mock": True, "text": TEXT}


@app.post("/stt")
@app.post("/v1/audio/transcriptions")
def stt(file: UploadFile = File(...)):
    return {"text": TEXT, "language": "ru"}
