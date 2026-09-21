"""A second, original daily study card for the official Instagram account.

The text is generated once, validated, and stored before Meta fetches the image.
Published rows and recent captions prevent retries from duplicating a post.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from openai import OpenAI, OpenAIError
from sqlalchemy import Column, Date, DateTime, MetaData, String, Table, Text, create_engine, select, update
from sqlalchemy.exc import IntegrityError

from .config import DATABASE_URL, INSTAGRAM_DAILY_AUTOMATION_ENABLED, OPENAI_API_KEY, PUBLIC_BASE_URL
from .daily_social import DailyTip, _assert_target_account, _client, _wait_until_ready, render_tip_image
from .instagram import InstagramAPIError, InstagramConfigurationError


_META = MetaData()
_POSTS = Table(
    "instagram_evergreen_posts", _META,
    Column("day", Date, primary_key=True),
    Column("title", String(100), nullable=False),
    Column("body", String(180), nullable=False),
    Column("title_tr", String(100), nullable=False),
    Column("body_tr", String(180), nullable=False),
    Column("steps_json", Text, nullable=False),
    Column("keyword", String(80), nullable=False),
    Column("caption", Text, nullable=False),
    Column("published_media_id", String(100)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

_PILLARS = (
    ("active recall after class", "#ActiveRecall #LectureNotes #StudyMethods #ExamPrep #UniversityStudy #StudyRoutine #DersÇalışma #LectureSift"),
    ("practice questions and quizzes", "#PracticeQuestions #QuizYourself #ExamPrep #LectureNotes #StudyMethods #StudentTips #SınavHazırlığı #LectureSift"),
    ("flashcards and spaced review", "#Flashcards #SpacedRepetition #ActiveRecall #ExamPrep #StudyRoutine #LearningTips #DersÇalışma #LectureSift"),
    ("understanding difficult concepts", "#StudyMethods #ConceptLearning #LectureNotes #UniversityStudy #LearningTips #StudySmarter #DersNotları #LectureSift"),
    ("lecture note organisation", "#LectureNotes #StudyWorkflow #UniversityStudy #StudyTools #StudentProductivity #ExamPrep #DersNotları #LectureSift"),
    ("finding and fixing study mistakes", "#ExamPrep #MistakeLog #ActiveRecall #PracticeQuestions #StudyRoutine #StudentTips #SınavHazırlığı #LectureSift"),
)

_SCHEMA = {
    "type": "object",
    "properties": {name: {"type": "string"} for name in ("title", "body", "title_tr", "body_tr", "keyword")}
    | {"steps": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "body", "title_tr", "body_tr", "keyword", "steps"],
    "additionalProperties": False,
}


@lru_cache(maxsize=1)
def _engine():
    if not DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise InstagramConfigurationError("Shared Postgres database is required for evergreen publishing")
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    engine = create_engine(url, pool_pre_ping=True)
    _META.create_all(engine, tables=[_POSTS])
    return engine


def _row(day: date):
    with _engine().connect() as connection:
        return connection.execute(select(_POSTS).where(_POSTS.c.day == day)).mappings().first()


def _tip_from_row(row) -> DailyTip:
    steps = tuple(json.loads(row["steps_json"]))
    return DailyTip(
        title=row["title"], body=row["body"], title_tr=row["title_tr"],
        body_tr=row["body_tr"], caption=row["caption"], steps=steps,
    )


def image_for_day(day: date) -> bytes | None:
    row = _row(day)
    return render_tip_image(_tip_from_row(row)) if row else None


def status_for_day(day: date) -> dict:
    row = _row(day)
    return {"date": day.isoformat(), "prepared": bool(row), "published": bool(row and row["published_media_id"])}


def _recent_titles() -> list[str]:
    with _engine().connect() as connection:
        return list(connection.execute(select(_POSTS.c.title).order_by(_POSTS.c.day.desc()).limit(90)).scalars())


def _validate(data: dict, recent_titles: list[str]) -> dict:
    limits = {
        "title": (12, 72), "body": (25, 125), "title_tr": (12, 72),
        "body_tr": (25, 125), "keyword": (10, 60),
    }
    cleaned = {}
    for field, (minimum, maximum) in limits.items():
        value = " ".join(str(data.get(field) or "").split())
        if not minimum <= len(value) <= maximum or "#" in value or "@" in value:
            raise ValueError("Generated study card failed its text quality check")
        cleaned[field] = value
    steps = data.get("steps")
    if not isinstance(steps, list) or len(steps) != 3:
        raise ValueError("Generated study card needs three steps")
    cleaned["steps"] = [" ".join(str(step).split()) for step in steps]
    if any(not 20 <= len(step) <= 105 for step in cleaned["steps"]):
        raise ValueError("Generated study steps are too short or too long")
    if len({step.casefold() for step in cleaned["steps"]}) != 3:
        raise ValueError("Generated study steps repeat")
    title = cleaned["title"].casefold()
    if any(title == old.casefold() for old in recent_titles):
        raise ValueError("Generated study title repeats a recent post")
    title_words = set(title.split())
    if any(len(title_words & set(old.casefold().split())) / max(len(title_words | set(old.casefold().split())), 1) > 0.66
           for old in recent_titles):
        raise ValueError("Generated study title is too similar to a recent post")
    if any(term in " ".join((cleaned["title"], cleaned["body"])).casefold()
           for term in ("guaranteed", "100%", "secret algorithm", "viral hack")):
        raise ValueError("Generated study card makes an unsupported claim")
    return cleaned


def _generate(day: date, recent_titles: list[str], *, pillar_offset: int = 0) -> dict:
    if not OPENAI_API_KEY:
        raise InstagramConfigurationError("OpenAI key is required for evergreen publishing")
    pillar, _ = _PILLARS[(day.toordinal() + pillar_offset) % len(_PILLARS)]
    client = OpenAI(api_key=OPENAI_API_KEY, timeout=45, max_retries=1)
    for _ in range(3):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.7,
                max_tokens=650,
                response_format={"type": "json_schema", "json_schema": {"name": "study_card", "strict": True, "schema": _SCHEMA}},
                messages=[
                    {"role": "system", "content": (
                        "You are the editorial writer for LectureSift, a study tool. Create one original, practical "
                        "Instagram study card on the requested topic. Write a specific, useful micro-workflow: "
                        "a strong hook, one clear benefit, and exactly three actionable steps. English is primary; "
                        "include accurate natural Turkish translations. The keyword is a natural search phrase, "
                        "not a hashtag. Use only common study advice; do not invent research, statistics, product "
                        "capabilities, customer stories, or promises of exam results. No clickbait, spam, or filler. "
                        "The steps must teach something a student can try without LectureSift."
                    )},
                    {"role": "user", "content": json.dumps({
                        "topic": pillar, "avoid_titles": recent_titles[:90],
                        "format": "title <= 8 words; body one sentence; three short concrete steps; Turkish title and body",
                    })},
                ],
            )
        except OpenAIError as exc:
            raise InstagramConfigurationError("Study card generation service is unavailable") from exc
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal:
            continue
        try:
            return _validate(json.loads(choice.message.content or "{}"), recent_titles)
        except (ValueError, TypeError):
            continue
    raise InstagramConfigurationError("No study card passed the editorial checks")


def _ensure_post(day: date):
    existing = _row(day)
    if existing:
        return existing
    recent_titles = _recent_titles()
    data = _generate(day, recent_titles)
    _, hashtags = _PILLARS[day.toordinal() % len(_PILLARS)]
    marker = f"LectureSift · idea · {day.isoformat()}"
    caption = (
        f"{data['title']} | {data['keyword']}\n\n{data['body']}\n\n"
        + "\n".join(f"{index}. {step}" for index, step in enumerate(data["steps"], 1))
        + f"\n\n🇹🇷 {data['title_tr']}: {data['body_tr']}\n\n"
        + "Save this and try it after your next lecture. More study methods at lecturesift.com.\n\n"
        + f"{hashtags}\n{marker}"
    )
    try:
        with _engine().begin() as connection:
            connection.execute(_POSTS.insert().values(
                day=day, title=data["title"], body=data["body"], title_tr=data["title_tr"],
                body_tr=data["body_tr"], steps_json=json.dumps(data["steps"], ensure_ascii=False),
                keyword=data["keyword"], caption=caption, created_at=datetime.now(ZoneInfo("UTC")),
            ))
    except IntegrityError:
        pass
    return _row(day)


def publish_evergreen_post(day: date | None = None) -> dict:
    if not INSTAGRAM_DAILY_AUTOMATION_ENABLED:
        return {"status": "disabled"}
    selected_day = day or datetime.now(ZoneInfo("Europe/Istanbul")).date()
    client = _client()
    _assert_target_account(client)
    post = _ensure_post(selected_day)
    if post["published_media_id"]:
        return {"status": "already_published", "kind": "evergreen", "date": selected_day.isoformat()}
    marker = f"LectureSift · idea · {selected_day.isoformat()}"
    recent = client.get_recent_media(limit=50).get("data", [])
    if any(marker in (item.get("caption") or "") for item in recent):
        return {"status": "already_published", "kind": "evergreen", "date": selected_day.isoformat()}
    base_url = PUBLIC_BASE_URL or "https://api.lecturesift.com"
    container = client.create_media_container(
        media_url=f"{base_url.rstrip('/')}/instagram/evergreen/image/{selected_day.isoformat()}.jpg",
        caption=post["caption"],
    )
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    with _engine().begin() as connection:
        connection.execute(update(_POSTS).where(_POSTS.c.day == selected_day).values(published_media_id=published.get("id")))
    return {"status": "published", "kind": "evergreen", "date": selected_day.isoformat(), "media_id": published.get("id")}


def main() -> int:
    try:
        result = publish_evergreen_post()
    except (InstagramAPIError, InstagramConfigurationError, RuntimeError, ValueError, KeyError) as exc:
        print(f"Instagram evergreen post failed: {exc}", file=sys.stderr)
        return 1
    print(result.get("status", "unknown"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
