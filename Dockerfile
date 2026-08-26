FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./app/main.py
COPY lowmem_patch.py ./lowmem_patch.py
COPY url_app.py ./url_app.py

CMD ["sh","-c","PYTHONPATH=/app uvicorn url_app:app --host 0.0.0.0 --port ${PORT:-10000}"]
