"""A second, original daily study card for the official Instagram account.

The text is generated once, validated, and stored before Meta fetches the image.
Stored receipts and recent captions prevent retries from duplicating a post.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from openai import OpenAI, OpenAIError

from .config import INSTAGRAM_DAILY_AUTOMATION_ENABLED, OPENAI_API_KEY, PUBLIC_BASE_URL
from .daily_social import DailyTip, _TIPS, _assert_target_account, _client, _wait_until_ready, render_tip_image
from .instagram import InstagramAPIError, InstagramConfigurationError
from .social_storage import read_json, recent_json, write_json

_PREFIX = "social/instagram/cards/"

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


def _key(day: date) -> str:
    return f"{_PREFIX}{day.isoformat()}.json"


def _row(day: date) -> dict | None:
    return read_json(_key(day))


def _tip_from_row(row) -> DailyTip:
    steps = tuple(row["steps"])
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
    return [row["title"] for row in recent_json(_PREFIX, limit=60)]


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
    editorial_text = " ".join((cleaned["title"], cleaned["body"], *cleaned["steps"])).casefold()
    if any(term in editorial_text for term in (
        "guaranteed", "%", "secret algorithm", "viral hack", "research proves",
        "studies show", "garanti", "mucize",
    )):
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
                        "The steps must teach something a student can try without LectureSift. "
                        "Choose a narrow problem, not a broad topic summary. Give one concrete mini-example "
                        "in a step, and end with a check the student can do from memory. Avoid generic hooks "
                        "such as 'study smarter', 'break down concepts', or 'improve your learning'."
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
    recent_titles = _recent_titles() + [tip[0] for tip in _TIPS]
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
    write_json(_key(day), {
        **data, "caption": caption, "published_media_id": None,
        "created_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    })
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
    published_item = next((item for item in recent if marker in (item.get("caption") or "")), None)
    if published_item:
        if published_item.get("id"):
            write_json(_key(selected_day), {**post, "published_media_id": published_item["id"]})
        return {"status": "already_published", "kind": "evergreen", "date": selected_day.isoformat()}
    base_url = PUBLIC_BASE_URL or "https://api.lecturesift.com"
    container = client.create_media_container(
        media_url=f"{base_url.rstrip('/')}/instagram/evergreen/image/{selected_day.isoformat()}.jpg",
        caption=post["caption"],
    )
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    write_json(_key(selected_day), {**post, "published_media_id": published.get("id")})
    return {"status": "published", "kind": "evergreen", "date": selected_day.isoformat(), "media_id": published.get("id")}


def main() -> int:
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "prepare":
            selected_day = date.fromisoformat(sys.argv[2])
            post = _ensure_post(selected_day)
            result = {"status": "prepared" if post else "unavailable"}
        elif len(sys.argv) == 1:
            result = publish_evergreen_post()
        else:
            print("Usage: python -m lecturesift.evergreen_social [prepare YYYY-MM-DD]", file=sys.stderr)
            return 2
    except (InstagramAPIError, InstagramConfigurationError, RuntimeError, ValueError, KeyError) as exc:
        print(f"Instagram evergreen post failed: {exc}", file=sys.stderr)
        return 1
    print(result.get("status", "unknown"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
