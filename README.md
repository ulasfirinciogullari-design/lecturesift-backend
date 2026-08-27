# LectureSift Backend V4.1

LectureSift turns lecture video into a complete study pack: transcript, optional translation, structured notes, summary, slides, quiz, flashcards, and exportable files.

## Local development

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Without durable-infrastructure environment variables, LectureSift keeps the existing in-process background execution path for local development.

## Durable background execution

Production can offload long-running lecture jobs to a dedicated Celery worker. When `CELERY_BROKER_URL` and the S3-compatible storage settings are configured, the web service uploads source media to object storage, queues the job, and the worker processes it independently of the browser connection and web process lifecycle.

Required production environment variables:

- `OPENAI_API_KEY`
- `CELERY_BROKER_URL`
- `REDIS_URL` (can use the same Render Key Value connection string)
- `S3_ENDPOINT_URL`
- `S3_REGION`
- `S3_BUCKET`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`

The worker should set `LECTURESIFT_WORKER=1` and start with:

```bash
celery -A lecturesift.queue.celery_app worker --loglevel=INFO --concurrency=1
```

The repository's `render.yaml` defines a Render Key Value instance and a dedicated background worker. Object storage remains S3-compatible so Cloudflare R2, AWS S3, Backblaze B2 S3, or another compatible provider can be used without changing the processing pipeline.

## Safety of rollout

`main` is the live deployment branch. Durable execution is being introduced behind configuration-based fallback so the current in-process path continues to work until the queue and object-storage services are provisioned. Automated pytest checks should pass before deployment.
