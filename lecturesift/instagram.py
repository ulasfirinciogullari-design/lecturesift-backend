import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class InstagramConfigurationError(RuntimeError):
    pass


class InstagramAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, error_type: str = "instagram_api_error"):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


@dataclass(frozen=True)
class InstagramClient:
    access_token: str
    account_id: str
    app_secret: str
    api_version: str = "v23.0"
    base_url: str = "https://graph.instagram.com"
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not all((self.access_token, self.account_id, self.app_secret)):
            raise InstagramConfigurationError("Instagram environment variables are incomplete")
        if not self.api_version.startswith("v"):
            raise InstagramConfigurationError("Instagram Graph API version must start with 'v'")

    def _auth(self) -> dict[str, str]:
        proof = hmac.new(
            self.app_secret.encode("utf-8"),
            self.access_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {"access_token": self.access_token, "appsecret_proof": proof}

    def _request(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = {**params, **self._auth()}
        url = f"{self.base_url.rstrip('/')}/{self.api_version}/{path.lstrip('/')}"
        body = None
        if method == "GET":
            url = f"{url}?{urlencode(payload)}"
        else:
            body = urlencode(payload).encode("utf-8")
        request = Request(url, data=body, method=method, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                data = json.loads(exc.read().decode("utf-8"))
                error = data.get("error", {})
                message = error.get("message", "Instagram API request failed")
                error_type = error.get("type", "instagram_api_error")
            except (ValueError, AttributeError):
                message, error_type = "Instagram API request failed", "instagram_api_error"
            raise InstagramAPIError(message, status_code=502, error_type=error_type) from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise InstagramAPIError("Instagram API is temporarily unavailable") from None

    def get_account(self) -> dict[str, Any]:
        return self._request(
            "GET",
            self.account_id,
            {"fields": "id,username,account_type,media_count"},
        )

    def create_media_container(
        self,
        *,
        media_url: str,
        caption: str = "",
        media_type: str = "IMAGE",
        cover_url: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"caption": caption}
        if media_type == "IMAGE":
            params["image_url"] = media_url
        else:
            params.update({"media_type": media_type, "video_url": media_url})
        if cover_url:
            params["cover_url"] = cover_url
        return self._request("POST", f"{self.account_id}/media", params)

    def get_container_status(self, container_id: str) -> dict[str, Any]:
        return self._request("GET", container_id, {"fields": "id,status_code,status"})

    def publish_media(self, container_id: str) -> dict[str, Any]:
        return self._request("POST", f"{self.account_id}/media_publish", {"creation_id": container_id})

