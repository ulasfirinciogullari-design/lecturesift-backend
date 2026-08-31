FROM python:3.12-slim

ARG LECTURESIFT_BUILD_REVISION=unknown

LABEL org.opencontainers.image.revision="${LECTURESIFT_BUILD_REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LECTURESIFT_BUILD_REVISION="${LECTURESIFT_BUILD_REVISION}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    fonts-dejavu-core \
    tesseract-ocr \
    tesseract-ocr-osd \
    tesseract-ocr-ara \
    tesseract-ocr-chi-sim \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    tesseract-ocr-hin \
    tesseract-ocr-ita \
    tesseract-ocr-jpn \
    tesseract-ocr-kor \
    tesseract-ocr-por \
    tesseract-ocr-rus \
    tesseract-ocr-spa \
    tesseract-ocr-tur \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./main.py
COPY url_app.py ./url_app.py
COPY lecturesift ./lecturesift
COPY deploy/image_smoke.py /usr/local/bin/lecturesift-image-smoke.py

RUN groupadd --gid 10001 lecturesift \
    && useradd --uid 10001 --gid lecturesift --create-home --shell /usr/sbin/nologin lecturesift \
    && mkdir -p /var/lib/lecturesift \
    && chown -R lecturesift:lecturesift /var/lib/lecturesift

ENV LECTURESIFT_WORK_DIR=/var/lib/lecturesift

USER lecturesift

EXPOSE 8000

# Prove the shared API/worker/job image has every native and Python capability
# before it can be deployed. Runtime health checks remain role-specific in
# Compose/platform configuration; the shared image must not assume an HTTP API.
RUN python /usr/local/bin/lecturesift-image-smoke.py

# Social automation runs in its own one-shot service/timer. Keeping the API
# process single-purpose prevents restarts from creating duplicate posts.
CMD ["sh","-c","exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
