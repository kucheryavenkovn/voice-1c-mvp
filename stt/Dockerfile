FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DOWNLOAD_TIMEOUT=60

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# expose nvidia cuDNN/cuBLAS libs to ctranslate2 for GPU inference
ENV LD_LIBRARY_PATH="/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib:/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:${LD_LIBRARY_PATH}"

COPY app.py .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
