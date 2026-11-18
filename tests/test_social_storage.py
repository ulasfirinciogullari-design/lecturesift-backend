from io import BytesIO

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import lecturesift.social_storage as storage


def test_social_content_is_shared_through_object_storage(monkeypatch):
    objects = {}

    class FakeClient:
        def put_object(self, *, Bucket, Key, Body, ContentType):
            assert Bucket == "social-bucket"
            assert ContentType == "application/json"
            objects[Key] = Body

        def get_object(self, *, Bucket, Key):
            assert Bucket == "social-bucket"
            if Key not in objects:
                raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            return {"Body": BytesIO(objects[Key])}

        def list_objects_v2(self, **kwargs):
            assert kwargs["Bucket"] == "social-bucket"
            return {"Contents": [{"Key": key} for key in objects if key.startswith(kwargs["Prefix"])]}

    fake = type("Storage", (), {"bucket": "social-bucket", "_client": FakeClient()})()
    monkeypatch.setattr(storage, "_storage", lambda: fake)
    prefix = "social/instagram/cards/"
    storage.write_json(prefix + "2026-09-22.json", {"title": "One useful idea"})
    assert storage.read_json(prefix + "2026-09-22.json") == {"title": "One useful idea"}
    assert storage.read_json(prefix + "2026-09-23.json") is None
    assert storage.recent_json(prefix) == [{"title": "One useful idea"}]


def test_a_dropped_connection_reads_as_storage_unavailable(monkeypatch):
    """Transport errors are not ClientErrors; callers still get the one RuntimeError they handle."""
    class DownClient:
        def get_object(self, **kwargs):
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        put_object = list_objects_v2 = get_object

    fake = type("Storage", (), {"bucket": "social-bucket", "_client": DownClient()})()
    monkeypatch.setattr(storage, "_storage", lambda: fake)
    with pytest.raises(RuntimeError, match="unavailable"):
        storage.read_json("social/instagram/studio/2026-11-17/post.json")
    with pytest.raises(RuntimeError, match="unavailable"):
        storage.write_json("social/instagram/studio/2026-11-17/post.json", {})
    with pytest.raises(RuntimeError, match="unavailable"):
        storage.recent_json("social/instagram/studio/")
