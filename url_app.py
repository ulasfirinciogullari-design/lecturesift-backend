"""Compatibility entrypoint for Render.

Render may start this service with `uvicorn url_app:app`. The real application
lives in main.py (or app/main.py in the Docker image), so expose that FastAPI
instance without duplicating routes.
"""

try:
    from app.main import app  # Docker layout: /app/app/main.py
except ModuleNotFoundError:
    from main import app      # Native/root layout: /app/main.py
