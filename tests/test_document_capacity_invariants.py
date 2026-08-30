from __future__ import annotations

from pathlib import Path

import lecturesift.billing as billing_module
import lecturesift.storage as storage_module


MEBIBYTE = 1024 * 1024


def test_public_catalog_document_limits_never_exceed_runtime_cap(monkeypatch):
    runtime_bytes = 63 * MEBIBYTE + 512
    monkeypatch.setattr(billing_module, "MAX_DOCUMENT_BYTES", runtime_bytes)

    catalog = billing_module.public_catalog()

    for plan in catalog["plans"]:
        top_level_mb = plan["max_document_upload_mb"]
        entitlement_mb = plan["entitlements"]["limits"]["max_document_upload_mb"]
        assert top_level_mb == entitlement_mb
        assert top_level_mb * MEBIBYTE <= runtime_bytes
        assert top_level_mb == min(
            billing_module.PLAN_BY_CODE[plan["code"]].max_document_upload_mb,
            runtime_bytes // MEBIBYTE,
        )


class _TransferClient:
    def __init__(self) -> None:
        self.upload_call = None
        self.download_call = None

    def upload_file(self, filename, bucket, key, **kwargs):
        self.upload_call = (filename, bucket, key, kwargs)

    def download_file(self, bucket, key, filename, **kwargs):
        self.download_call = (bucket, key, filename, kwargs)
        Path(filename).write_bytes(b"restored")


def test_storage_passes_bounded_transfer_config_to_upload_and_download(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(storage_module, "record_r2_operation", lambda *args, **kwargs: None)
    client = _TransferClient()
    monkeypatch.setattr(storage_module, "S3_BUCKET", "lecture-materials")
    monkeypatch.setattr(storage_module, "S3_ENDPOINT_URL", "https://storage.example.test")
    monkeypatch.setattr(storage_module, "S3_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setattr(storage_module, "S3_SECRET_ACCESS_KEY", "test-secret-key")
    monkeypatch.setattr(storage_module, "STORAGE_FILE_TRANSFER_CONCURRENCY", 2)
    monkeypatch.setattr(storage_module.boto3, "client", lambda *args, **kwargs: client)
    storage = storage_module.ObjectStorage()
    source = tmp_path / "source.pdf"
    destination = tmp_path / "restored.pdf"
    source.write_bytes(b"document")

    storage.upload_file(source, "jobs/job-1/sources/document_000.pdf")
    storage.download_file("jobs/job-1/sources/document_000.pdf", destination)

    assert client.upload_call is not None
    assert client.upload_call[3]["Config"] is storage._transfer_config
    assert client.download_call is not None
    assert client.download_call[3]["Config"] is storage._transfer_config
    assert storage._transfer_config.max_concurrency == 2
    assert storage._transfer_config.multipart_threshold == 16 * MEBIBYTE
    assert storage._transfer_config.multipart_chunksize == 8 * MEBIBYTE
    assert destination.read_bytes() == b"restored"
