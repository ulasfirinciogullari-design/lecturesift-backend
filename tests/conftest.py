import pytest


@pytest.fixture(autouse=True)
def isolate_legacy_media_tests_from_billing(request, monkeypatch):
    """Keep the pre-existing media/API suite focused on media behavior.

    Account, guest-limit, billing and reward behavior is covered separately in
    test_platform.py. Production endpoints remain strict; only legacy unit tests
    get an isolated usage authorization stub.
    """
    if request.path.name == "test_v4.py":
        monkeypatch.setattr(
            "lecturesift.app.PLATFORM.authorize_minutes",
            lambda *_args, **_kwargs: {"mode": "test", "remaining": 0},
        )
