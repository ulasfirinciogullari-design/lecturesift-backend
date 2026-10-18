from datetime import datetime, timedelta, timezone

from lecturesift import instagram_replies as replies


class FakeClient:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.sent = []
        self.media = [{"id": "m1", "caption": "Biyoloji notlarını 3. kez mi okuyorsun?", "timestamp": now.isoformat()}]
        self.comments = {"m1": [
            {"id": "c1", "text": "B şıkkı metafaz!", "username": "ayse", "from": {"id": "u1"}},
            {"id": "c2", "text": "Teşekkürler", "username": "lecturesift", "from": {"id": "acc"}},
            {"id": "c3", "text": "Zaten cevapladınız", "username": "ali", "from": {"id": "u2"},
             "replies": {"data": [{"id": "r1", "from": {"id": "acc"}}]}},
        ]}
        self.dms = {"conv1": [
            {"id": "d1", "message": "Ücretsiz deneme var mı?", "from": {"id": "u3"}, "created_time": now.isoformat()},
        ], "conv2": [
            {"id": "d2", "message": "Merhaba", "from": {"id": "u4"}, "created_time": (now - timedelta(days=2)).isoformat()},
        ]}

    def get_account(self):
        return {"id": "acc", "username": "lecturesift"}

    def get_recent_media(self, limit=25):
        return {"data": self.media}

    def get_comments(self, media_id, limit=50):
        return {"data": self.comments[media_id]}

    def get_conversations(self, limit=20):
        return {"data": [{"id": key} for key in self.dms]}

    def get_conversation_messages(self, conversation_id, limit=10):
        return {"messages": {"data": self.dms[conversation_id]}}

    def reply_to_comment(self, comment_id, message):
        self.sent.append(("comment", comment_id, message))

    def send_message(self, recipient_id, text):
        self.sent.append(("dm", recipient_id, text))


def _setup(monkeypatch, drafted):
    client, stored = FakeClient(), {}
    monkeypatch.setattr(replies, "INSTAGRAM_REPLIES_ENABLED", True)
    monkeypatch.setattr(replies, "_client", lambda: client)
    monkeypatch.setattr(replies, "_assert_target_account", lambda c: None)
    monkeypatch.setattr(replies, "read_json", lambda key: stored.get(key))
    monkeypatch.setattr(replies, "write_json", lambda key, value: stored.__setitem__(key, value))
    seen = []

    def fake_draft(items):
        seen.append([i["id"] for i in items])
        return {i["id"]: drafted.get(i["id"], "") for i in items}

    monkeypatch.setattr(replies, "draft", fake_draft)
    return client, stored, seen


def test_answers_new_comments_and_fresh_dms_once(monkeypatch):
    client, stored, seen = _setup(monkeypatch, {"c:c1": "Doğru, metafaz! 🎉", "m:d1": "Evet, 30 dakika ücretsiz."})
    result = replies.run()
    assert seen == [["c:c1", "m:d1"]]  # own comment, already answered comment and stale DM skipped
    assert client.sent == [("comment", "c1", "Doğru, metafaz! 🎉"), ("dm", "u3", "Evet, 30 dakika ücretsiz.")]
    assert result["sent"] == 2
    replies.run()
    assert len(client.sent) == 2  # nothing is answered twice


def test_skipped_items_are_remembered(monkeypatch):
    client, stored, seen = _setup(monkeypatch, {})
    assert replies.run()["skipped"] == 2
    replies.run()
    assert seen[-1] == [] and client.sent == []


def test_disabled_does_nothing(monkeypatch):
    monkeypatch.setattr(replies, "INSTAGRAM_REPLIES_ENABLED", False)
    assert replies.run() == {"status": "disabled"}


def test_replies_with_foreign_links_or_hashtags_are_dropped():
    assert replies.clean_reply("Detaylar için lecturesift.com/contact adresine yaz.") != ""
    assert replies.clean_reply("Bak: example.com") == ""
    assert replies.clean_reply("https://lecturesift.com") == ""
    assert replies.clean_reply("Harika #LectureSift") == ""
    assert len(replies.clean_reply("a " * 400)) <= replies.MAX_REPLY_CHARS
