FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    fonts-dejavu-core \
    fonts-noto-core \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./main.py
COPY url_app.py ./url_app.py
COPY lecturesift ./lecturesift

# Start the API normally and bootstrap only the very first launch-grid post in
# a detached helper. Marker checks make deploy retries and later deploys safe.
CMD ["sh","-c","python -m lecturesift.launch_once & exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
