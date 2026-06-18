FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# download the Russian Piper voice (dmitri, medium) from rhasspy/piper-voices
ARG VOICE_NAME=ru_RU-dmitri-medium
ARG VOICE_BASE=https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/ru/ru_RU/dmitri/medium
RUN mkdir -p /voices && \
    wget -q --show-progress -O /voices/${VOICE_NAME}.onnx ${VOICE_BASE}/${VOICE_NAME}.onnx && \
    wget -q -O /voices/${VOICE_NAME}.onnx.json ${VOICE_BASE}/${VOICE_NAME}.onnx.json

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app.py .

ENV PIPER_VOICE=/voices/ru_RU-dmitri-medium.onnx
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
