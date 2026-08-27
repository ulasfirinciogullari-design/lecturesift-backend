import json
from pathlib import Path

import boto3

from .config import (
    S3_ACCESS_KEY_ID,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_REGION,
    S3_SECRET_ACCESS_KEY,
)


class ObjectStorage:
    def __init__(self) -> None:
        self.bucket = S3_BUCKET
        self.remote = bool(self.bucket and S3_ENDPOINT_URL and S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY)
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
        return f"jobs/{job_id}/sources/{role}_{index:03d}{path.suffix.lower()}"

    def upload_file(self, path: Path, key: str) -> str:
        if not self.remote or self._client is None:
            raise RuntimeError("Remote object storage is not configured")
        self._client.upload_file(str(path), self.bucket, key)
        return key

    def download_file(self, key: str, destination: Path) -> Path:
        if not self.remote or self._client is None:
            raise RuntimeError("Remote object storage is not configured")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(destination))
        return destination

    def read_json(self, key: str) -> dict:
        if not self.remote or self._client is None:
            raise RuntimeError("Remote object storage is not configured")
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))

    def presign(self, key: str, expires_seconds: int = 900) -> str:
        if not self.remote or self._client is None:
            raise RuntimeError("Remote object storage is not configured")
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )

    def publish_job(self, job_id: str, job_dir: Path) -> dict:
        uploaded: list[str] = []
        for path in job_dir.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(job_dir).as_posix()
            key = f"jobs/{job_id}/{relative}"
            self.upload_file(path, key)
            uploaded.append(key)
        result_key = f"jobs/{job_id}/result.json"
        zip_candidates = [key for key in uploaded if key.lower().endswith(".zip")]
        return {
            "remote_prefix": f"jobs/{job_id}/",
            "remote_result_key": result_key if result_key in uploaded else "",
            "remote_download_key": zip_candidates[0] if zip_candidates else "",
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
                key = item.get("Key", "")
                if not key or "/sources/" in key:
                    continue
                relative = key[len(prefix):]
                if not relative:
                    continue
                self.download_file(key, destination / relative)
                count += 1
        return count


STORAGE = ObjectStorage()
