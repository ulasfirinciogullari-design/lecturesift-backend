"""S3-compatible transient source transfer and persistent final-ZIP storage.

Raw uploads may be copied to the object store only long enough for a background
worker to consume them. They are deleted after success/final failure. The only
persistent product artifact is the completed LectureSift ZIP.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import boto3

from .config import S3_ACCESS_KEY_ID, S3_BUCKET, S3_ENDPOINT_URL, S3_REGION, S3_SECRET_ACCESS_KEY


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

    def _require(self):
        if not self.remote or self._client is None:
            raise RuntimeError("Kalıcı çıktı deposu yapılandırılmamış.")
        return self._client

    def source_key(self, job_id: str, role: str, index: int, path: Path) -> str:
        suffix = path.suffix.lower() or ".bin"
        return f"transient/jobs/{job_id}/sources/{role}_{index:03d}{suffix}"

    def output_key(self, job_id: str, path: Path) -> str:
        filename = path.name if path.suffix.casefold() == ".zip" else "LectureSift_Study_Pack.zip"
        return f"outputs/jobs/{job_id}/{filename}"

    def upload_file(self, path: Path, key: str, *, content_type: str = "") -> str:
        client = self._require()
        if content_type:
            client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})
        else:
            client.upload_file(str(path), self.bucket, key)
        return key

    def download_file(self, key: str, destination: Path) -> Path:
        client = self._require()
        destination.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(self.bucket, key, str(destination))
        return destination

    def delete_key(self, key: str) -> None:
        if key and self.remote and self._client is not None:
            self._client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        if not prefix or not self.remote or self._client is None:
            return 0
        paginator = self._client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", []) if item.get("Key")]
            if keys:
                self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                deleted += len(keys)
        return deleted

    def delete_transient_job(self, job_id: str) -> int:
        return self.delete_prefix(f"transient/jobs/{job_id}/")

    def publish_output(self, job_id: str, zip_path: Path) -> dict:
        if not self.remote:
            return {}
        if not zip_path.exists() or not zip_path.is_file() or zip_path.suffix.casefold() != ".zip":
            raise RuntimeError("Kalıcılaştırılacak final ZIP bulunamadı.")
        key = self.output_key(job_id, zip_path)
        self.upload_file(zip_path, key, content_type="application/zip")
        return {
            "remote_download_key": key,
            "remote_file_count": 1,
            "remote_output_size_bytes": zip_path.stat().st_size,
            "storage_policy": "final_zip_only",
        }

    def publish_job(self, job_id: str, job_dir: Path) -> dict:
        candidates = [path for path in job_dir.glob("*.zip") if path.is_file()]
        if not candidates:
            return {}
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return self.publish_output(job_id, candidates[0])

    @staticmethod
    def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
        root = destination.resolve()
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError("ZIP içinde güvenli olmayan dosya yolu bulundu.")
        archive.extractall(destination)

    def materialize_output(self, job_id: str, key: str, destination: Path) -> int:
        if not key:
            return 0
        destination.mkdir(parents=True, exist_ok=True)
        local_zip = destination / Path(key).name
        self.download_file(key, local_zip)
        extracted = destination / "_restored"
        shutil.rmtree(extracted, ignore_errors=True)
        extracted.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(local_zip) as archive:
            self._safe_extract(archive, extracted)
        package_dir = destination / "package"
        shutil.rmtree(package_dir, ignore_errors=True)
        package_dir.mkdir(parents=True, exist_ok=True)
        internal = extracted / "_lecturesift"
        for item in extracted.iterdir():
            if item.name == "_lecturesift":
                continue
            target = package_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        result_source = internal / "result.json"
        if result_source.exists():
            shutil.copy2(result_source, destination / "result.json")
        slides_source = internal / "slides"
        if slides_source.exists():
            shutil.copytree(slides_source, destination / "slides", dirs_exist_ok=True)
        shutil.rmtree(extracted, ignore_errors=True)
        return 1

    def materialize_job(self, job_id: str, destination: Path, key: str = "") -> int:
        return self.materialize_output(job_id, key, destination) if key else 0


STORAGE = ObjectStorage()
