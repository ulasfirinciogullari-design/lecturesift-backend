"""Anonymous requests cannot make the public social media routes render repeatedly."""

from datetime import date, datetime, timedelta, timezone
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import lecturesift.app as app_module
import lecturesift.daily_social as daily_social
import lecturesift.social_routes as social_routes
from lecturesift.daily_social import PUBLIC_MEDIA_DAY_WINDOW, SingleFlightRenderCache

TODAY = date(2026, 10, 21)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        assert tz is timezone.utc
        return cls(2026, 10, 21, 23, 30, tzinfo=tz)


@pytest.fixture
def renders(monkeypatch):
    monkeypatch.setattr(daily_social, "datetime", _FrozenDatetime)
    calls = []

    def counting(name):
        def render(day):
            calls.append((name, day))
            return f"{name}:{day.isoformat()}".encode()

        return SingleFlightRenderCache(render)

    monkeypatch.setattr(app_module, "render_daily_image", counting("daily-image"))
    monkeypatch.setattr(app_module, "render_daily_reel_cover", counting("daily-cover"))
    monkeypatch.setattr(app_module, "render_daily_reel", counting("daily-reel"))
    monkeypatch.setattr(app_module, "_PUBLIC_EVERGREEN_IMAGE", counting("evergreen-image"))
    monkeypatch.setattr(app_module, "_PUBLIC_GENERATED_REEL_COVER", counting("generated-cover"))
    monkeypatch.setattr(app_module, "_PUBLIC_GENERATED_REEL_VIDEO", counting("generated-reel"))
    return calls


ROUTES = (
    "/instagram/daily/image/{day}.jpg",
    "/instagram/daily/reel/{day}.jpg",
    "/instagram/daily/reel/{day}.mp4",
    "/instagram/evergreen/image/{day}.jpg",
    "/instagram/evergreen/reel/{day}.jpg",
    "/instagram/evergreen/reel/{day}.mp4",
)


@pytest.mark.parametrize("route", ROUTES)
def test_days_outside_the_publisher_window_are_refused_without_rendering(renders, route):
    client = TestClient(app_module.app)
    for offset in (-400, -2, 2, 30):
        day = TODAY + timedelta(days=offset)
        response = client.get(route.format(day=day.isoformat()))
        assert response.status_code == 404, (route, day)
        assert response.json()["detail"]["code"] == "LS-IG-06"
    assert client.get(route.format(day="not-a-day")).status_code == 400
    assert renders == []


@pytest.mark.parametrize("route", ROUTES)
def test_accepted_days_render_once_however_often_they_are_requested(renders, route):
    client = TestClient(app_module.app)
    accepted = [TODAY + timedelta(days=offset) for offset in range(-PUBLIC_MEDIA_DAY_WINDOW, PUBLIC_MEDIA_DAY_WINDOW + 1)]
    # Cycling through every accepted day never evicts one of them.
    for _round in range(3):
        for day in accepted:
            response = client.get(route.format(day=day.isoformat()))
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "public, max-age=86400"
    assert sorted(day for _name, day in renders) == accepted


def test_the_window_covers_the_publishers_dates():
    # The API runs in UTC; generated reels and evergreen cards use the
    # Europe/Istanbul date, up to a day ahead of it.
    assert daily_social.public_media_day_allowed(TODAY, today=TODAY)
    assert daily_social.public_media_day_allowed(TODAY + timedelta(days=1), today=TODAY)
    assert daily_social.public_media_day_allowed(TODAY - timedelta(days=1), today=TODAY)
    assert not daily_social.public_media_day_allowed(TODAY + timedelta(days=2), today=TODAY)


def test_every_public_render_cache_holds_the_whole_window():
    window = 2 * PUBLIC_MEDIA_DAY_WINDOW + 1
    for cache in (
        daily_social.render_daily_image,
        daily_social.render_daily_reel_cover,
        daily_social.render_daily_reel,
        app_module._PUBLIC_EVERGREEN_IMAGE,
        app_module._PUBLIC_GENERATED_REEL_COVER,
        app_module._PUBLIC_GENERATED_REEL_VIDEO,
    ):
        assert isinstance(cache, SingleFlightRenderCache)
        assert cache._maxsize >= window


def test_concurrent_misses_wait_for_one_render_and_renders_never_overlap():
    started = threading.Event()
    release = threading.Event()
    active = []
    overlaps = []
    calls = []

    def render(key):
        active.append(key)
        overlaps.append(len(active))
        calls.append(key)
        started.set()
        release.wait(5)
        time.sleep(0.01)
        active.remove(key)
        return f"video:{key}".encode()

    cache = SingleFlightRenderCache(render)
    other = SingleFlightRenderCache(render)
    results = []
    threads = [threading.Thread(target=lambda: results.append(cache("day"))) for _ in range(8)]
    threads.append(threading.Thread(target=lambda: results.append(other("cover"))))
    for thread in threads:
        thread.start()
    assert started.wait(5)
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(5)
    assert sorted(calls) == ["cover", "day"]
    assert max(overlaps) == 1
    assert results.count(b"video:day") == 8 and results.count(b"video:cover") == 1


def test_missing_sources_are_not_cached_and_failures_propagate():
    answers = iter([None, b"card"])
    cache = SingleFlightRenderCache(lambda _key: next(answers))
    assert cache("day") is None
    assert cache("day") == b"card"
    assert cache("day") == b"card"

    def broken(_key):
        raise RuntimeError("ffmpeg unavailable")

    with pytest.raises(RuntimeError):
        SingleFlightRenderCache(broken)("day")


def test_installed_duplicate_social_routes_apply_the_same_window(monkeypatch):
    monkeypatch.setattr(daily_social, "datetime", _FrozenDatetime)
    calls = []
    for name in ("render_daily_image", "render_daily_reel_cover", "render_daily_reel"):
        monkeypatch.setattr(social_routes, name, lambda day, name=name: calls.append((name, day)) or b"media")
    standalone = FastAPI()
    social_routes.install_social_routes(standalone)
    client = TestClient(standalone)
    for route in ROUTES[:3]:
        assert client.get(route.format(day=(TODAY + timedelta(days=5)).isoformat())).status_code == 404
        assert client.get(route.format(day=TODAY.isoformat())).status_code == 200
    assert [day for _name, day in calls] == [TODAY, TODAY, TODAY]


def test_launch_images_are_cached_for_every_launch_post():
    assert isinstance(social_routes._LAUNCH_IMAGE, SingleFlightRenderCache)
    assert social_routes._LAUNCH_IMAGE._maxsize >= len(social_routes.LAUNCH_POSTS)
