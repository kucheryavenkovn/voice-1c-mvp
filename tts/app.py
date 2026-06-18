import os
import shutil
import subprocess
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

VOICE = os.getenv("PIPER_VOICE", "/voices/ru_RU-dmitri-medium.onnx")
PIPER = shutil.which("piper") or os.getenv("PIPER_BIN", "piper")

app = FastAPI(title="piper TTS (ru)")


class TTSRequest(BaseModel):
    text: str
    length_scale: float | None = None
    noise_scale: float | None = None


@app.get("/health")
def health():
    return {"ok": os.path.exists(VOICE), "voice": VOICE, "piper": PIPER}


@app.post("/tts")
def tts(req: TTSRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    if not os.path.exists(VOICE):
        raise HTTPException(status_code=500, detail=f"voice not found: {VOICE}")

    out = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    out.close()
    try:
        cmd = [PIPER, "-m", VOICE, "-f", out.name]
        if req.length_scale is not None:
            cmd += ["--length-scale", str(req.length_scale)]
        if req.noise_scale is not None:
            cmd += ["--noise-scale", str(req.noise_scale)]
        proc = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True,
        )
        if proc.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"piper failed: {proc.stderr.decode('utf-8', 'ignore')}",
            )
        with open(out.name, "rb") as f:
            data = f.read()
        return Response(content=data, media_type="audio/wav")
    finally:
        try:
            os.unlink(out.name)
        except OSError:
            pass


@app.post("/tts-file")
def tts_file(req: TTSRequest):
    from fastapi.responses import FileResponse

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    out.close()
    subprocess.run(
        [PIPER, "-m", VOICE, "-f", out.name],
        input=text.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    return FileResponse(out.name, media_type="audio/wav", filename="tts.wav")
