"""S3-compatible storage for uploaded media and generated LectureSift files."""

from __future__ import annotations

from pathlib import Path

import boto3

from .costs import record_r2_operation
from .config import (
    DOCUMENT_EXTENSIONS,
    S3_ACCESS_KEY_ID,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_REGION,
    S3_SECRET_ACCESS_KEY,
    VIDEO_EXTENSIONS,
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
            )

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

    def health(self) -> dict[str, bool]:
        if not self.remote or self._client is None:
            return {"configured": False, "connected": False}
        try:
            # A bucket-scoped R2 Object Read & Write token can list objects but
            # may not be allowed to perform bucket-management operations. Test
            # the least-privileged operation the application actually needs.
            self._client.list_objects_v2(Bucket=self.bucket, MaxKeys=1)
            return {"configured": True, "connected": True}
        except Exception:
            return {"configured": True, "connected": False}

    @staticmethod
    def _is_source_media(path: Path, job_dir: Path, relative: str) -> bool:
        if relative.startswith(("sources/", "slide_segments/")):
            return True
        if path.parent != job_dir or path.suffix.casefold() not in (VIDEO_EXTENSIONS | DOCUMENT_EXTENSIONS):
            return False
        return path.name.startswith(("part_", "audio_", "visual_", "document_", "remote."))

    def publish_job(self, job_id: str, job_dir: Path) -> dict:
        if not self.remote:
            return {}
        uploaded: list[str] = []
        for path in job_dir.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(job_dir).as_posix()
            if self._is_source_media(path, job_dir, relative):
                continue
            key = f"jobs/{job_id}/{relative}"
            self.upload_file(path, key)
            uploaded.append(key)
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
        count = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = str(item.get("Key", ""))
                if not key or "/sources/" in key:
                    continue
                relative = key[len(prefix):]
                if not relative:
                    continue
                self.download_file(key, destination / relative)
                count += 1
        return count

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
