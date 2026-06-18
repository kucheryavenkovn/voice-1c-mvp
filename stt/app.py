import os
import tempfile

import ctranslate2
from fastapi import FastAPI, File, UploadFile, HTTPException
from faster_whisper import WhisperModel
from pydantic import BaseModel

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ru") or None


def resolve_device() -> str:
    d = os.getenv("WHISPER_DEVICE", "auto").lower()
    if d == "auto":
        try:
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda"
        except Exception:
            pass
        return "cpu"
    return d


def resolve_compute_type(device: str) -> str:
    c = os.getenv("WHISPER_COMPUTE_TYPE", "auto").lower()
    if c == "auto":
        return "float16" if device == "cuda" else "int8"
    return c


def load_model() -> WhisperModel:
    device = resolve_device()
    compute = resolve_compute_type(device)
    try:
        print(f"[STT] loading '{WHISPER_MODEL}' on {device}/{compute}", flush=True)
        return WhisperModel(WHISPER_MODEL, device=device, compute_type=compute)
    except Exception as e:
        print(f"[STT] load on {device}/{compute} failed: {e}; retry cpu/int8", flush=True)
        return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")


model = load_model()
app = FastAPI(title="faster-whisper STT")


class TextOut(BaseModel):
    text: str
    language: str | None = None


@app.get("/health")
def health():
    return {
        "ok": True,
        "model": WHISPER_MODEL,
        "language": WHISPER_LANGUAGE,
        "device": resolve_device(),
        "compute_type": resolve_compute_type(resolve_device()),
        "cuda_devices": ctranslate2.get_cuda_device_count(),
    }


@app.post("/stt", response_model=TextOut)
@app.post("/v1/audio/transcriptions", response_model=TextOut)
def transcribe(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(file.file.read())
        tmp.close()
        try:
            segments, info = model.transcribe(
                tmp.name,
                language=WHISPER_LANGUAGE,
                vad_filter=True,
                beam_size=5,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"transcribe error: {e}")
        text = " ".join(s.text.strip() for s in segments).strip()
        return TextOut(text=text, language=info.language)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
