"""Privacy-safe scheduled Instagram publishing for LectureSift.

The daily creative system is English-first for the global feed, while every
creative and caption also carries a concise Turkish localization. No customer
content, private data, follower scraping or engagement automation is used.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

from .config import (
    INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_ACCOUNT_ID,
    INSTAGRAM_APP_SECRET,
    INSTAGRAM_DAILY_AUTOMATION_ENABLED,
    INSTAGRAM_DAILY_MEDIA_TYPE,
    INSTAGRAM_GRAPH_API_VERSION,
    PUBLIC_BASE_URL,
)
from .instagram import InstagramAPIError, InstagramClient, InstagramConfigurationError
from .launch_social import next_pending_post


@dataclass(frozen=True)
class DailyTip:
    title: str
    body: str
    title_tr: str
    body_tr: str
    caption: str
    steps: tuple[str, str, str]


# A rotating set of specific, reusable study workflows. Each post gives the
# viewer something to try before mentioning the product.
_TIPS = (
    (
        "Stop rewatching the whole lecture",
        "Find the gap. Review that moment. Test yourself.",
        "Dersin tamamını tekrar izleme",
        "Eksik konuyu bul, o kısmı izle, kendini test et.",
        ("Write one question you cannot answer.", "Find that moment in your notes or transcript.", "Answer from memory before replaying."),
        "how to review a lecture",
        "#LectureNotes #StudyRoutine #ActiveRecall #ExamPrep #StudyTips #UniversityStudy #DersÇalışma #LectureSift",
    ),
    (
        "Turn one lecture into 5 questions",
        "Questions reveal what a summary can hide.",
        "Bir dersten 5 soru çıkar",
        "Sorular, özetin gizlediği eksikleri gösterir.",
        ("List five ideas the lecturer repeats.", "Turn each idea into a why or how question.", "Answer without opening your notes."),
        "lecture notes to quiz questions",
        "#LectureNotes #QuizYourself #ActiveRecall #StudyMethods #ExamPreparation #StudentTips #Üniversite #LectureSift",
    ),
    (
        "The 2-minute active recall test",
        "Recall first. Check later. Fix only the gaps.",
        "2 dakikalık aktif hatırlama testi",
        "Önce hatırla, sonra kontrol et, eksikleri düzelt.",
        ("Close your notes and set a two-minute timer.", "Explain the topic aloud in plain language.", "Check your notes and mark missing ideas."),
        "active recall study method",
        "#ActiveRecall #StudyMethods #ExamPrep #LearningScience #StudyRoutine #StudentLife #DersÇalışma #LectureSift",
    ),
    (
        "Your slides can be a practice test",
        "Change headings into questions before revising.",
        "Slaytlarını deneme testine çevir",
        "Tekrardan önce başlıkları soruya dönüştür.",
        ("Choose a slide with one main idea.", "Hide the slide and write a question about it.", "Answer, then compare with the slide."),
        "study from lecture slides",
        "#LectureSlides #ExamPrep #QuizYourself #ActiveRecall #StudyTools #UniversityStudy #DersNotları #LectureSift",
    ),
    (
        "Find the exact minute you missed",
        "Search the concept, then replay only its explanation.",
        "Kaçırdığın dakikayı bul",
        "Kavramı ara, sadece açıklamasını yeniden izle.",
        ("Write the term that still feels unclear.", "Search it in the lecture transcript.", "Replay that segment and make one flashcard."),
        "search a lecture transcript",
        "#LectureTranscript #StudySmarter #Flashcards #LectureNotes #ExamPrep #StudentProductivity #DersÇalışma #LectureSift",
    ),
    (
        "The 3-pass lecture review",
        "Understand. Recall. Test. Keep each pass short.",
        "Dersi 3 turda tekrar et",
        "Anla, hatırla, test et. Her tur kısa olsun.",
        ("Pass 1: skim the key ideas.", "Pass 2: recall them without looking.", "Pass 3: answer practice questions."),
        "how to revise lecture notes",
        "#LectureReview #StudyRoutine #ActiveRecall #PracticeQuestions #ExamPrep #StudyMethods #Üniversite #LectureSift",
    ),
    (
        "Make flashcards that actually test you",
        "One idea per card makes the answer clear.",
        "İşe yarayan flashcard hazırla",
        "Her karta tek fikir koy; cevap net olsun.",
        ("Put one question on the front.", "Write a short, checkable answer on the back.", "Split any card you keep missing."),
        "how to make effective flashcards",
        "#Flashcards #ActiveRecall #StudyMethods #ExamPrep #LearningTips #StudentLife #DersÇalışma #LectureSift",
    ),
    (
        "Review a lecture in 10 minutes",
        "Use the time to find gaps, not reread everything.",
        "Dersi 10 dakikada tekrar et",
        "Her şeyi okumak yerine eksikleri bul.",
        ("Spend 2 minutes scanning the outline.", "Spend 5 minutes recalling key ideas.", "Spend 3 minutes fixing what you missed."),
        "10 minute lecture review",
        "#StudyRoutine #LectureNotes #ActiveRecall #ExamPrep #TimeManagement #StudyTips #Üniversite #LectureSift",
    ),
    (
        "Keep a mistake log for exams",
        "The errors you repeat are your best revision plan.",
        "Sınav için hata defteri tut",
        "Tekrarlanan hatalar en iyi tekrar planındır.",
        ("Record every missed practice question.", "Write why the wrong answer felt right.", "Retry the question after two days."),
        "exam mistake log",
        "#ExamPrep #PracticeQuestions #StudyMethods #MistakeLog #ActiveRecall #StudentTips #SınavHazırlığı #LectureSift",
    ),
    (
        "Use a one-page topic map",
        "See how the key ideas connect before memorizing details.",
        "Konuyu tek sayfada haritala",
        "Ayrıntılardan önce ana fikirlerin bağını gör.",
        ("Write the topic in the center.", "Add three to five main branches.", "Explain every connection aloud."),
        "concept map for studying",
        "#ConceptMap #StudyMethods #LectureNotes #ExamPrep #LearningTools #UniversityStudy #DersNotları #LectureSift",
    ),
    (
        "Try a closed-book brain dump",
        "Write what you know before opening your notes.",
        "Notu açmadan bildiklerini yaz",
        "Eksikleri görmek için önce hafızanı yokla.",
        ("Choose one lecture topic.", "Write everything you recall for three minutes.", "Compare with your notes and fill the gaps."),
        "brain dump study technique",
        "#ActiveRecall #BrainDump #StudyRoutine #ExamPrep #LectureReview #StudentTips #DersÇalışma #LectureSift",
    ),
    (
        "Make a confusion list",
        "A precise question is easier to solve than vague doubt.",
        "Anlamadıklarını listele",
        "Net soru, belirsiz şüpheden daha kolay çözülür.",
        ("Note each term that still feels fuzzy.", "Turn each one into a specific question.", "Ask, search or replay only those parts."),
        "how to understand difficult lectures",
        "#LectureNotes #StudyQuestions #ExamPrep #StudySmarter #UniversityStudy #LearningTips #DersÇalışma #LectureSift",
    ),
    (
        "Explain it like a tutor",
        "Simple explanations expose shaky understanding.",
        "Öğretmen gibi anlat",
        "Basit anlatım, eksik anlamayı ortaya çıkarır.",
        ("Pick one concept from today's lecture.", "Explain it without technical words.", "Revisit the step where you got stuck."),
        "teach back study method",
        "#StudyMethods #ActiveRecall #ExplainToLearn #LectureReview #ExamPrep #StudentLife #DersÇalışma #LectureSift",
    ),
    (
        "Study before the lecture starts",
        "Five minutes of preview makes questions easier to notice.",
        "Dersten önce 5 dakika bak",
        "Kısa ön bakış, önemli soruları fark ettirir.",
        ("Scan the lecture title and headings.", "Write two things you expect to learn.", "Listen for answers during the lecture."),
        "how to prepare for a lecture",
        "#LecturePrep #StudyRoutine #UniversityStudy #LectureNotes #StudentTips #LearningMethods #DersÇalışma #LectureSift",
    ),
    (
        "Review within a day",
        "A short check soon after class catches confusion early.",
        "Dersi bir gün içinde gözden geçir",
        "Kısa tekrar, belirsizlikleri erken yakalar.",
        ("Skim your notes after class.", "Write three questions from memory.", "Resolve one confusing point today."),
        "after lecture review routine",
        "#LectureReview #StudyRoutine #ActiveRecall #ExamPrep #UniversityStudy #StudentTips #DersÇalışma #LectureSift",
    ),
    (
        "Compare two similar concepts",
        "The difference is often what the exam asks.",
        "Benzer iki kavramı karşılaştır",
        "Sınav çoğu zaman aradaki farkı sorar.",
        ("Write each concept as a column.", "Add one similarity and two differences.", "Create a question that tests the contrast."),
        "compare concepts for exams",
        "#ExamPrep #ConceptLearning #StudyMethods #PracticeQuestions #LectureNotes #StudentTips #SınavHazırlığı #LectureSift",
    ),
    (
        "Turn headings into a quiz",
        "A table of contents can become practice questions.",
        "Başlıkları mini quize çevir",
        "İçindekiler listesi soru bankası olabilir.",
        ("Copy three headings from your notes.", "Rewrite each as a how or why question.", "Answer without reading the section."),
        "make a quiz from notes",
        "#QuizYourself #LectureNotes #ActiveRecall #StudyMethods #ExamPrep #UniversityStudy #DersNotları #LectureSift",
    ),
    (
        "Space out your review",
        "Short returns beat one long final-night reread.",
        "Tekrarı günlere yay",
        "Kısa tekrarlar son gece maratonundan iyidir.",
        ("Review the topic today.", "Test yourself again in a few days.", "Revisit only what you missed later."),
        "spaced repetition study plan",
        "#SpacedRepetition #StudyRoutine #ActiveRecall #ExamPrep #Flashcards #LearningTips #DersÇalışma #LectureSift",
    ),
    (
        "Start with practice questions",
        "Let questions show you what deserves another look.",
        "Önce soru çöz",
        "Sorular hangi konuya dönmen gerektiğini gösterir.",
        ("Try three questions without notes.", "Mark every step you guessed.", "Review those steps, then retry."),
        "practice questions before revision",
        "#PracticeQuestions #ExamPrep #ActiveRecall #StudyMethods #StudentTips #UniversityStudy #SınavHazırlığı #LectureSift",
    ),
    (
        "Build a one-page exam sheet",
        "Keep the ideas you must be able to explain.",
        "Tek sayfalık sınav özeti yap",
        "Açıklayabilmen gereken fikirleri seç.",
        ("List the five most important concepts.", "Add one example beside each concept.", "Cover the page and explain it back."),
        "one page exam revision sheet",
        "#ExamPrep #RevisionNotes #StudyMethods #ActiveRecall #LectureNotes #StudentTips #SınavHazırlığı #LectureSift",
    ),
    (
        "Use a question parking lot",
        "Capture confusion without losing the lecture thread.",
        "Soruları kenara not et",
        "Dersin akışını kaçırmadan belirsizliği yakala.",
        ("Mark a question with the lecture timestamp.", "Keep listening instead of searching mid-class.", "Return to the marked moment afterward."),
        "how to take lecture notes",
        "#LectureNotes #StudyQuestions #UniversityStudy #StudentProductivity #LearningTips #DersNotları #LectureSift",
    ),
    (
        "Draw it from memory",
        "A quick diagram can reveal a missing link.",
        "Şemayı hafızandan çiz",
        "Küçük bir çizim eksik bağlantıyı gösterir.",
        ("Choose a process with three or more stages.", "Draw the arrows without looking.", "Check where your diagram breaks."),
        "visual active recall method",
        "#ActiveRecall #VisualLearning #StudyMethods #ExamPrep #LectureReview #UniversityStudy #DersÇalışma #LectureSift",
    ),
    (
        "Mix two related topics",
        "Practice choosing the right idea, not just repeating one.",
        "İki konuyu karıştırarak çalış",
        "Doğru yöntemi seçmeyi de pratik et.",
        ("Pick two topics you often confuse.", "Shuffle practice questions from both.", "Explain why each answer fits its topic."),
        "interleaving study practice",
        "#Interleaving #ExamPrep #PracticeQuestions #StudyMethods #ActiveRecall #LearningTips #DersÇalışma #LectureSift",
    ),
    (
        "End with one takeaway",
        "A clear closing note makes the next review easier.",
        "Dersi tek sonuçla bitir",
        "Net bir kapanış notu sonraki tekrarı kolaylaştırır.",
        ("Ask what changed in your understanding.", "Write one sentence in your own words.", "Turn that sentence into a question."),
        "summarize a lecture effectively",
        "#LectureSummary #StudyRoutine #ActiveRecall #LectureNotes #UniversityStudy #StudentTips #DersNotları #LectureSift",
    ),
)


def _index(day: date) -> int:
    return day.toordinal() % len(_TIPS)


def daily_marker(day: date) -> str:
    return f"LectureSift · {day.isoformat()}"


def daily_tip(day: date) -> DailyTip:
    title, body, title_tr, body_tr, steps, keyword, hashtags = _TIPS[_index(day)]
    # The publishing marker stays readable and does not consume a hashtag slot.
    marker = daily_marker(day)
    selected_hashtags = " ".join(hashtags.split()[:5])
    caption = (
        f"{title} | {keyword}\n\n{body}\n\n"
        + "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
        + f"\n\n🇹🇷 {title_tr}: {body_tr}\n\n"
        + "Save this for your next study session. LectureSift helps turn lectures into notes, quizzes and flashcards.\n\n"
        + f"{selected_hashtags}\n{marker}"
    )
    return DailyTip(title=title, body=body, title_tr=title_tr, body_tr=body_tr, caption=caption, steps=steps)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ("DejaVuSans-Bold.ttf", "Arial Bold.ttf") if bold else ("DejaVuSans.ttf", "Arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= max_width or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) >= max_lines - 1:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return "\n".join(lines)


def _fit_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    start_size: int,
    min_size: int,
    *,
    max_lines: int,
    bold: bool,
) -> tuple[ImageFont.ImageFont, str]:
    for size in range(start_size, min_size - 1, -2):
        font = _font(size, bold)
        wrapped = _wrap(draw, text, font, max_width, max_lines)
        box = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=max(8, size // 6))
        if box[2] <= max_width and box[3] <= max_height:
            return font, wrapped
    font = _font(min_size, bold)
    return font, _wrap(draw, text, font, max_width, max_lines)


def render_daily_image(day: date) -> bytes:
    """Render an API-compatible 4:5 lesson card with the full three-step method."""
    tip = daily_tip(day)
    image = Image.new("RGB", (1080, 1350), "#050b1f")
    draw = ImageDraw.Draw(image)
    draw.ellipse((650, -100, 1160, 410), fill="#18376c")
    draw.rounded_rectangle((72, 72, 1008, 1298), radius=58, fill="#08172f", outline="#2c6dff", width=3)
    draw.rounded_rectangle((118, 130, 430, 204), radius=30, fill="#10294d")
    draw.text((153, 149), "LECTURESIFT", fill="#4ce0d4", font=_font(30, True))
    draw.text((118, 265), "SAVE THIS STUDY METHOD", fill="#8dbdff", font=_font(30, True))

    title_font, title = _fit_wrapped(draw, tip.title, 830, 180, 72, 50, max_lines=3, bold=True)
    draw.multiline_text((118, 325), title, fill="white", font=title_font, spacing=12)
    title_box = draw.multiline_textbbox((118, 325), title, font=title_font, spacing=12)

    body_y = max(510, title_box[3] + 26)
    body_font, body = _fit_wrapped(draw, tip.body, 820, 90, 37, 30, max_lines=2, bold=False)
    draw.multiline_text((118, body_y), body, fill="#c5daf4", font=body_font, spacing=10)

    for index, step in enumerate(tip.steps, 1):
        top = 625 + (index - 1) * 145
        draw.rounded_rectangle((118, top, 962, top + 125), radius=26, fill="#10294d")
        draw.ellipse((144, top + 31, 206, top + 93), fill="#386fff")
        draw.text((164, top + 44), str(index), fill="white", font=_font(30, True))
        font, wrapped = _fit_wrapped(draw, step, 700, 88, 35, 28, max_lines=3, bold=False)
        draw.multiline_text((237, top + 24), wrapped, fill="#edf6ff", font=font, spacing=7)

    draw.line((118, 1078, 962, 1078), fill="#254b78", width=3)
    draw.text((118, 1118), "TR", fill="#4ce0d4", font=_font(28, True))
    tr_font, tr_title = _fit_wrapped(draw, tip.title_tr, 740, 95, 42, 32, max_lines=2, bold=True)
    draw.multiline_text((190, 1108), tr_title, fill="#eef5ff", font=tr_font, spacing=8)
    draw.text((118, 1234), "lecturesift.com  /  Study smarter", fill="#4ce0a3", font=_font(27, True))

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=93, optimize=True)
    return output.getvalue()


def render_daily_reel_cover(day: date) -> bytes:
    """Render a 9:16 bilingual hook card with a conservative profile-safe center."""
    tip = daily_tip(day)
    image = Image.new("RGB", (1080, 1920), "#050b1f")
    draw = ImageDraw.Draw(image)
    draw.ellipse((620, -170, 1220, 430), fill="#18376c")
    draw.ellipse((-260, 1280, 360, 1900), fill="#28175a")
    draw.rounded_rectangle((70, 90, 1010, 1830), radius=60, fill="#08172f", outline="#386fff", width=4)

    draw.rounded_rectangle((118, 150, 435, 226), radius=30, fill="#10294d")
    draw.text((153, 170), "LECTURESIFT", fill="#4ce0d4", font=_font(31, True))
    draw.text((118, 352), "STUDY SMARTER", fill="#8dbdff", font=_font(34, True))

    title_font, title = _fit_wrapped(draw, tip.title, 825, 310, 84, 54, max_lines=3, bold=True)
    draw.multiline_text((118, 440), title, fill="white", font=title_font, spacing=15)
    title_box = draw.multiline_textbbox((118, 440), title, font=title_font, spacing=15)

    body_y = max(790, title_box[3] + 65)
    body_font, body = _fit_wrapped(draw, tip.body, 820, 225, 49, 36, max_lines=4, bold=False)
    draw.multiline_text((118, body_y), body, fill="#d9e8ff", font=body_font, spacing=20)

    draw.line((118, 1110, 962, 1110), fill="#285184", width=3)
    draw.text((118, 1180), "TÜRKÇE", fill="#4ce0d4", font=_font(30, True))
    tr_title_font, tr_title = _fit_wrapped(draw, tip.title_tr, 820, 190, 49, 36, max_lines=3, bold=True)
    draw.multiline_text((118, 1245), tr_title, fill="#f4f8ff", font=tr_title_font, spacing=12)
    tr_box = draw.multiline_textbbox((118, 1245), tr_title, font=tr_title_font, spacing=12)
    tr_body_y = max(1435, tr_box[3] + 34)
    tr_body_font, tr_body = _fit_wrapped(draw, tip.body_tr, 820, 150, 35, 30, max_lines=3, bold=False)
    draw.multiline_text((118, tr_body_y), tr_body, fill="#bed6f6", font=tr_body_font, spacing=12)

    draw.rounded_rectangle((118, 1660, 810, 1748), radius=31, fill="#386fff")
    draw.text((153, 1684), "Turn lectures into study packs", fill="white", font=_font(31, True))
    draw.text((118, 1780), "lecturesift.com", fill="#4ce0a3", font=_font(29, True))

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=93, optimize=True)
    return output.getvalue()


def _render_reel_step_slide(day: date, slide: int) -> bytes:
    """Two readable 9:16 teaching frames following the opening hook."""
    tip = daily_tip(day)
    image = Image.new("RGB", (720, 1280), "#050b1f")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((44, 72, 676, 1208), radius=42, fill="#0b1d3b", outline="#386fff", width=3)
    draw.text((82, 110), "LECTURESIFT / STUDY BETTER", fill="#4ce0d4", font=_font(23, True))
    draw.text((82, 213), f"{slide + 1:02d} / 03", fill="#8dbdff", font=_font(33, True))
    if slide == 1:
        heading = "TRY THIS TODAY"
        lines = (tip.steps[0], tip.steps[1])
    else:
        heading = "MAKE IT STICK"
        lines = (tip.steps[2], tip.body_tr)
    draw.text((82, 290), heading, fill="white", font=_font(46, True))
    for index, line in enumerate(lines):
        top = 395 + index * 315
        draw.rounded_rectangle((82, top, 638, top + 255), radius=30, fill="#12305b")
        draw.text((108, top + 24), f"0{index + 1}" if slide == 1 else ("03" if index == 0 else "TR"), fill="#4ce0d4", font=_font(29, True))
        font, wrapped = _fit_wrapped(draw, line, 494, 155, 42, 30, max_lines=4, bold=index == 0)
        draw.multiline_text((108, top + 82), wrapped, fill="#f5f9ff", font=font, spacing=10)
    draw.text((82, 1135), "Save this for your next study session", fill="#9abfed", font=_font(24))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


@lru_cache(maxsize=4)
def render_daily_reel(day: date) -> bytes:
    """Render a three-scene, bounded-memory MP4 without CPU-heavy zoom filters."""
    work = tempfile.mkdtemp(prefix="lecturesift-reel-")
    try:
        output_path = f"{work}/reel.mp4"
        slides = (
            Image.open(io.BytesIO(render_daily_reel_cover(day))).resize((720, 1280), Image.Resampling.LANCZOS),
            Image.open(io.BytesIO(_render_reel_step_slide(day, 1))),
            Image.open(io.BytesIO(_render_reel_step_slide(day, 2))),
        )
        for index, slide in enumerate(slides):
            slide.save(f"{work}/slide-{index}.jpg", format="JPEG", quality=88)
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            *[argument for index in range(3) for argument in (
                "-loop", "1", "-framerate", "24", "-t", "3.5", "-i", f"{work}/slide-{index}.jpg",
            )],
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-r", "24", "-threads", "1",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
            "-movflags", "+faststart", "-an", output_path,
        ]
        completed = subprocess.run(command, capture_output=True, timeout=45, check=False)
        if completed.returncode != 0:
            raise RuntimeError("Instagram Reel could not be rendered")
        with open(output_path, "rb") as reel:
            data = reel.read()
        if len(data) < 10_000:
            raise RuntimeError("Instagram Reel output is invalid")
        return data
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Instagram Reel renderer is unavailable") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def is_already_published(client: InstagramClient, day: date) -> bool:
    marker = daily_marker(day)
    return any(marker in (item.get("caption") or "") for item in client.get_recent_media().get("data", []))


def media_type_for_day(day: date) -> str:
    configured = INSTAGRAM_DAILY_MEDIA_TYPE.upper()
    if configured == "MIXED":
        return "IMAGE" if day.toordinal() % 3 == 0 else "REELS"
    if configured in {"IMAGE", "REELS"}:
        return configured
    raise InstagramConfigurationError("INSTAGRAM_DAILY_MEDIA_TYPE must be IMAGE, REELS or MIXED")


def _verify_public_video(url: str) -> None:
    """Fail before creating a Meta container when the public Reel is unavailable."""
    try:
        with urlopen(Request(url, headers={"Range": "bytes=0-31"}), timeout=30) as response:
            if response.status not in {200, 206} or response.headers.get_content_type() != "video/mp4":
                raise InstagramAPIError("Public Instagram Reel endpoint is not serving MP4")
            if response.read(12)[4:8] != b"ftyp":
                raise InstagramAPIError("Public Instagram Reel endpoint returned invalid MP4")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise InstagramAPIError("Public Instagram Reel endpoint is unavailable") from exc


def _wait_until_ready(client: InstagramClient, container_id: str) -> None:
    for _ in range(48):
        status = client.get_container_status(container_id).get("status_code")
        if status == "FINISHED":
            return
        if status in {"ERROR", "EXPIRED"}:
            raise InstagramAPIError("Instagram media processing failed")
        time.sleep(5)
    raise InstagramAPIError("Instagram media processing timed out")


def _client() -> InstagramClient:
    return InstagramClient(
        access_token=INSTAGRAM_ACCESS_TOKEN,
        account_id=INSTAGRAM_ACCOUNT_ID,
        app_secret=INSTAGRAM_APP_SECRET,
        api_version=INSTAGRAM_GRAPH_API_VERSION,
    )


def _assert_target_account(client: InstagramClient) -> None:
    account = client.get_account()
    if (account.get("username") or "").lower() != "lecturesift":
        raise InstagramConfigurationError("Configured Instagram account is not @lecturesift")


def publish_next_launch_post(*, only_if_none_completed: bool = False, force: bool = False) -> dict:
    """Publish the first missing launch-grid card, with marker-based idempotency."""
    if not force and not INSTAGRAM_DAILY_AUTOMATION_ENABLED:
        return {"status": "disabled"}

    base_url = PUBLIC_BASE_URL or "https://api.lecturesift.com"
    client = _client()
    _assert_target_account(client)
    recent = client.get_recent_media(limit=50).get("data", [])
    captions = "\n".join((item.get("caption") or "") for item in recent)
    completed = [idx for idx in range(1, 10) if f"#LectureSiftLaunch{idx:02d}" in captions]
    if only_if_none_completed and completed:
        return {"status": "launch_already_started", "completed": completed}

    post = next_pending_post(recent)
    if post is None:
        return {"status": "launch_complete", "completed": completed}

    media_url = f"{base_url}/instagram/launch/image/{post.index}.jpg"
    container = client.create_media_container(media_url=media_url, caption=post.caption)
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    return {"status": "published", "kind": "launch", "index": post.index, "media_id": published.get("id")}


def publish_daily_post(day: date | None = None) -> dict:
    """Publish one useful daily study lesson in the configured format."""
    if not INSTAGRAM_DAILY_AUTOMATION_ENABLED:
        return {"status": "disabled"}

    selected_day = day or date.today()
    client = _client()
    _assert_target_account(client)
    if is_already_published(client, selected_day):
        return {"status": "already_published", "kind": "daily", "date": selected_day.isoformat()}
    tip = daily_tip(selected_day)
    base_url = PUBLIC_BASE_URL or "https://api.lecturesift.com"
    media_type = media_type_for_day(selected_day)
    if media_type == "REELS":
        media_url = f"{base_url}/instagram/daily/reel/{selected_day.isoformat()}.mp4"
        cover_url = f"{base_url}/instagram/daily/reel/{selected_day.isoformat()}.jpg"
        _verify_public_video(media_url)
        container = client.create_media_container(
            media_url=media_url,
            caption=tip.caption,
            media_type="REELS",
            cover_url=cover_url,
        )
    elif media_type == "IMAGE":
        container = client.create_media_container(
            media_url=f"{base_url}/instagram/daily/image/{selected_day.isoformat()}.jpg",
            caption=tip.caption,
        )
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    return {
        "status": "published",
        "kind": "daily",
        "media_type": media_type,
        "date": selected_day.isoformat(),
        "media_id": published.get("id"),
    }


def main() -> int:
    try:
        result = publish_daily_post()
    except (InstagramAPIError, InstagramConfigurationError, RuntimeError, KeyError) as exc:
        print(f"Instagram scheduled post failed: {exc}", file=sys.stderr)
        return 1
    print(result.get("status", "unknown"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
