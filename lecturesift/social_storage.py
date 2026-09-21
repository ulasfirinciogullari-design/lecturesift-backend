"""Private object storage for public social creatives and publication receipts."""

from __future__ import annotations

import json
from functools import lru_cache

from botocore.exceptions import ClientError

from .instagram import InstagramConfigurationError
from .storage import ObjectStorage


@lru_cache(maxsize=1)
def _storage() -> ObjectStorage:
    storage = ObjectStorage()
    if not storage.remote or storage._client is None:
        raise InstagramConfigurationError("Shared object storage is required for social publishing")
    return storage


def read_bytes(key: str) -> bytes | None:
    storage = _storage()
    try:
        result = storage._client.get_object(Bucket=storage.bucket, Key=key)
        return result["Body"].read()
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "").lower()
        if code in {"nosuchkey", "notfound", "404"}:
            return None
        raise RuntimeError("Social content storage is unavailable") from None


def write_bytes(key: str, content: bytes, content_type: str) -> None:
    storage = _storage()
    try:
        storage._client.put_object(
            Bucket=storage.bucket, Key=key, Body=content, ContentType=content_type,
        )
    except ClientError:
        raise RuntimeError("Social content storage is unavailable") from None


def read_json(key: str) -> dict | None:
    content = read_bytes(key)
    return json.loads(content) if content is not None else None


def write_json(key: str, value: dict) -> None:
    write_bytes(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), "application/json")


def recent_json(prefix: str, limit: int = 30) -> list[dict]:
    storage = _storage()
    keys = []
    token = None
    try:
        while True:
            args = {"Bucket": storage.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                args["ContinuationToken"] = token
            page = storage._client.list_objects_v2(**args)
            keys.extend(item["Key"] for item in page.get("Contents", []) if item["Key"].endswith(".json"))
            token = page.get("NextContinuationToken")
            if not token:
                break
    except ClientError:
        raise RuntimeError("Social content storage is unavailable") from None
    return [row for key in sorted(keys, reverse=True)[:limit] if (row := read_json(key)) is not None]
