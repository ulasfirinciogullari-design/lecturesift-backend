from datetime import date

from lecturesift.daily_social import daily_tip, render_daily_image


def test_daily_tip_is_stable_and_has_idempotency_marker():
    selected_day = date(2026, 8, 27)
    assert daily_tip(selected_day) == daily_tip(selected_day)
    assert "#LectureSiftGununNotu20260827" in daily_tip(selected_day).caption


def test_daily_image_is_a_jpeg():
    image = render_daily_image(date(2026, 8, 27))
    assert image.startswith(b"\xff\xd8\xff")
    assert len(image) > 10_000
