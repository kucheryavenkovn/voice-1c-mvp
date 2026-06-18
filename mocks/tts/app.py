"""Mock TTS (Piper stand-in) — returns a silent WAV sized to the text length."""
import struct

from fastapi import FastAPI, Response
from pydantic import BaseModel

app = FastAPI(title="mock-tts")


def _silent_wav(text: str, rate: int = 22050) -> bytes:
    secs = min(5.0, max(0.3, len(text or "") * 0.06))
    n = int(rate * secs)
    data = b"\x00\x00" * n
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


class TTSRequest(BaseModel):
    text: str


@app.get("/health")
def health():
    return {"ok": True, "mock": True}


@app.post("/tts")
def tts(req: TTSRequest):
    return Response(content=_silent_wav(req.text), media_type="audio/wav")
