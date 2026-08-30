"""S3-compatible storage for uploaded media and generated LectureSift files."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)

from .costs import record_r2_operation
from .config import (
    DOCUMENT_EXTENSIONS,
    MEDIA_EXTENSIONS,
    S3_ACCESS_KEY_ID,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_REGION,
    S3_SECRET_ACCESS_KEY,
    STORAGE_TRANSFER_PARALLELISM,
)


class ObjectStorage:
    def __init__(self) -> None:
        self.bucket = S3_BUCKET
        self.remote = bool(
            self.bucket and S3_ENDPOINT_URL and S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY
        )
        self._client = None
        if self.remote:
            self._client = boto3.client(
                "s3",
                endpoint_url=S3_ENDPOINT_URL,
                region_name=S3_REGION or "auto",
                aws_access_key_id=S3_ACCESS_KEY_ID,
                aws_secret_access_key=S3_SECRET_ACCESS_KEY,
                config=Config(
                    retries={"mode": "standard", "total_max_attempts": 5},
                    connect_timeout=5,
                    read_timeout=60,
                    tcp_keepalive=True,
                ),
            )

    @staticmethod
    def error_code(exc: Exception) -> str:
        """Return a safe diagnostic code without leaking credentials or URLs."""
        if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
            return "credentials_missing"
        if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError)):
            return "storage_timeout"
        if isinstance(exc, EndpointConnectionError):
            return "endpoint_unreachable"
        if isinstance(exc, ClientError):
            response = exc.response or {}
            error = response.get("Error") or {}
            code = str(error.get("Code") or "").casefold()
            status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
            if code in {
                "invalidaccesskeyid",
                "invalidtoken",
                "signaturedoesnotmatch",
                "tokenrefreshrequired",
                "expiredtoken",
            }:
                return "credentials_invalid"
            if code in {"accessdenied", "forbidden", "unauthorized"} or status in {401, 403}:
                return "access_denied"
            if code in {"nosuchbucket", "notfound"} or status == 404:
                return "bucket_not_found"
            if code in {"requesttimeout", "requesttimeoutexception", "slowdown"} or status in {408, 429}:
                return "storage_timeout"
        return "storage_error"

    def source_key(self, job_id: str, role: str, index: int, path: Path) -> str:
        suffix = path.suffix.lower() or ".bin"
        return f"jobs/{job_id}/sources/{role}_{index:03d}{suffix}"

    def upload_file(self, path: Path, key: str) -> str:
        if not self.remote or self._client is None:
            raise RuntimeError("Kalıcı dosya deposu yapılandırılmamış.")
        self._client.upload_file(str(path), self.bucket, key)
        parts = key.split("/")
        record_r2_operation(
            "write",
            bytes_count=path.stat().st_size,
            job_id=parts[1] if len(parts) > 2 and parts[0] == "jobs" else None,
        )
        return key

    def download_file(self, key: str, destination: Path) -> Path:
        if not self.remote or self._client is None:
            raise RuntimeError("Kalıcı dosya deposu yapılandırılmamış.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(destination))
        parts = key.split("/")
        record_r2_operation(
            "read",
            bytes_count=destination.stat().st_size,
            job_id=parts[1] if len(parts) > 2 and parts[0] == "jobs" else None,
        )
        return destination

    def health(self) -> dict[str, bool | str]:
        if not self.remote or self._client is None:
            return {
                "configured": False,
                "connected": False,
                "diagnostic": "not_configured",
            }
        try:
            # A bucket-scoped R2 Object Read & Write token can list objects but
            # may not be allowed to perform bucket-management operations. Test
            # the least-privileged operation the application actually needs.
            self._client.list_objects_v2(Bucket=self.bucket, MaxKeys=1)
            return {"configured": True, "connected": True}
        except Exception as exc:
            return {
                "configured": True,
                "connected": False,
                "diagnostic": self.error_code(exc),
            }

    @staticmethod
    def _is_source_media(path: Path, job_dir: Path, relative: str) -> bool:
        if relative.startswith(("sources/", "slide_segments/")):
            return True
        if path.parent != job_dir or path.suffix.casefold() not in (MEDIA_EXTENSIONS | DOCUMENT_EXTENSIONS):
            return False
        return path.name.startswith(("part_", "audio_", "visual_", "document_", "remote."))

    def publish_job(self, job_id: str, job_dir: Path) -> dict:
        if not self.remote:
            return {}
        paths = []
        for path in sorted(job_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(job_dir).as_posix()
            if self._is_source_media(path, job_dir, relative):
                continue
            paths.append(path)

        def publish(path: Path) -> str:
            relative = path.relative_to(job_dir).as_posix()
            key = f"jobs/{job_id}/{relative}"
            self.upload_file(path, key)
            return key

        workers = min(STORAGE_TRANSFER_PARALLELISM, len(paths))
        if workers <= 1:
            uploaded = [publish(path) for path in paths]
        else:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix=f"lecturesift-publish-{job_id[:8]}",
            ) as executor:
                uploaded = list(executor.map(publish, paths))
        result_key = f"jobs/{job_id}/result.json"
        zip_keys = [key for key in uploaded if key.casefold().endswith(".zip")]
        return {
            "remote_prefix": f"jobs/{job_id}/",
            "remote_result_key": result_key if result_key in uploaded else "",
            "remote_download_key": zip_keys[0] if zip_keys else "",
            "remote_file_count": len(uploaded),
        }

    def materialize_job(self, job_id: str, destination: Path) -> int:
        if not self.remote or self._client is None:
            return 0
        prefix = f"jobs/{job_id}/"
        paginator = self._client.get_paginator("list_objects_v2")
        downloads: list[tuple[str, Path]] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = str(item.get("Key", ""))
                if not key or "/sources/" in key:
                    continue
                relative = key[len(prefix):]
                if not relative:
                    continue
                downloads.append((key, destination / relative))

        def download(item: tuple[str, Path]) -> Path:
            key, path = item
            return self.download_file(key, path)

        workers = min(STORAGE_TRANSFER_PARALLELISM, len(downloads))
        if workers <= 1:
            for item in downloads:
                download(item)
        else:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix=f"lecturesift-restore-{job_id[:8]}",
            ) as executor:
                list(executor.map(download, downloads))
        return len(downloads)

    def delete_job(self, job_id: str) -> int:
        """Delete every stored object for one job without exposing object keys."""
        if not self.remote or self._client is None:
            return 0
        prefix = f"jobs/{job_id}/"
        paginator = self._client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [
                {"Key": str(item.get("Key", ""))}
                for item in page.get("Contents", [])
                if str(item.get("Key", ""))
            ]
            for offset in range(0, len(keys), 1000):
                batch = keys[offset:offset + 1000]
                if batch:
                    self._client.delete_objects(
                        Bucket=self.bucket,
                        Delete={"Objects": batch, "Quiet": True},
                    )
                    deleted += len(batch)
        return deleted

    def delete_keys(self, keys: list[str]) -> int:
        """Delete an explicit set of private objects in API-sized batches."""
        if not self.remote or self._client is None:
            return 0
        normalized = [{"Key": str(key)} for key in dict.fromkeys(keys) if str(key)]
        deleted = 0
        for offset in range(0, len(normalized), 1000):
            batch = normalized[offset:offset + 1000]
            if not batch:
                continue
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": batch, "Quiet": True},
            )
            deleted += len(batch)
        return deleted


STORAGE = ObjectStorage()
