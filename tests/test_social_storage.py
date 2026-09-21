from io import BytesIO

from botocore.exceptions import ClientError

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
