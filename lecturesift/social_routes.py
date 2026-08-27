"""Small public, read-only social endpoints plus launch image serving."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from .config import (
    INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_ACCOUNT_ID,
    INSTAGRAM_APP_SECRET,
    INSTAGRAM_GRAPH_API_VERSION,
)
from .instagram import InstagramAPIError, InstagramClient, InstagramConfigurationError
from .launch_social import LAUNCH_POSTS, completed_indices, next_pending_post, render_launch_image


def _client() -> InstagramClient:
    return InstagramClient(
        access_token=INSTAGRAM_ACCESS_TOKEN,
        account_id=INSTAGRAM_ACCOUNT_ID,
        app_secret=INSTAGRAM_APP_SECRET,
        api_version=INSTAGRAM_GRAPH_API_VERSION,
    )


def install_social_routes(app: FastAPI) -> None:
    @app.get("/instagram/launch/image/{index}.jpg")
    def instagram_launch_image(index: int) -> Response:
        try:
            content = render_launch_image(index)
        except ValueError as exc:
            raise HTTPException(404, detail={"code": "LS-IG-06", "message": "Launch görseli bulunamadı."}) from exc
        return Response(
            content=content,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/instagram/launch/status")
    def instagram_launch_status() -> dict:
        try:
            client = _client()
            account = client.get_account()
            if (account.get("username") or "").lower() != "lecturesift":
                raise InstagramConfigurationError("Configured account mismatch")
            recent = client.get_recent_media(limit=50).get("data", [])
            completed = completed_indices(recent)
            pending = next_pending_post(recent)
        except InstagramConfigurationError as exc:
            raise HTTPException(503, detail={"code": "LS-IG-01", "message": "Instagram entegrasyonu yapılandırılmamış."}) from exc
        except InstagramAPIError as exc:
            raise HTTPException(502, detail={"code": "LS-IG-04", "message": "Instagram API isteği tamamlanamadı.", "type": exc.error_type}) from exc
        return {
            "ok": True,
            "account": "lecturesift",
            "completed": completed,
            "completed_count": len(completed),
            "total": len(LAUNCH_POSTS),
            "next_index": pending.index if pending else None,
            "complete": pending is None,
        }
