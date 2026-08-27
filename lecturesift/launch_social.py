"""Launch-grid content and rendering for the official LectureSift Instagram account."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class LaunchPost:
    index: int
    title: str
    subtitle: str
    kicker: str
    caption: str
    marker: str


_POSTS = (
    (
        "Follow @lecturesift",
        "Smarter study workflows are coming.",
        "LAUNCHING SOON",
        "We’re building a smarter way to study. 🚀\n\nFollow @lecturesift for AI-powered study workflows, product updates, and practical ways to turn lectures into useful study material.\n\nLaunching soon.\n\n#LectureSiftLaunch01 #LectureSift #EdTech #AIForStudents #StudySmarter #StudentTech #LearningTools",
    ),
    (
        "Before / After",
        "Hours of lecture → a clean study pack.",
        "LESS CLUTTER. MORE LEARNING.",
        "Before: hours of lecture video and scattered notes.\nAfter: organized slides, transcript, notes, quizzes and flashcards in one study pack.\n\nThat’s the workflow LectureSift is built to simplify.\n\n#LectureSiftLaunch02 #LectureSift #StudySmarter #EdTech #AIForStudents #StudyTools #StudentLife",
    ),
    (
        "Everything in one flow",
        "Slides. Transcript. Notes. Quiz. Flashcards.",
        "ONE WORKFLOW",
        "A lecture should not turn into five separate study chores.\n\nLectureSift brings slides, transcript, structured notes, quizzes and flashcards into one workflow so you can spend more time learning and less time organizing.\n\n#LectureSiftLaunch03 #LectureSift #EdTech #StudyWorkflow #AIForStudents #StudySmarter #LearningTools",
    ),
    (
        "Built for exam prep",
        "Turn passive watching into active recall.",
        "STUDY WITH INTENT",
        "Exam prep works better when you actively retrieve what you learned.\n\nLectureSift is designed to turn lecture material into usable notes, questions and flashcards you can review instead of endlessly rewatching.\n\n#LectureSiftLaunch04 #LectureSift #ExamPrep #ActiveRecall #StudySmarter #EdTech #Students",
    ),
    (
        "Study faster",
        "Keep the useful parts. Skip the busywork.",
        "FOCUS ON LEARNING",
        "The goal is not to study less seriously. It’s to waste less time on repetitive work.\n\nLectureSift helps organize lecture content so your attention can stay on understanding, recalling and practicing.\n\n#LectureSiftLaunch05 #LectureSift #StudySmarter #Productivity #EdTech #AIForStudents #Learning",
    ),
    (
        "Use any source",
        "Upload video, combine sources, or start from a link.",
        "FLEXIBLE INPUT",
        "Your study material does not always arrive in one perfect file.\n\nLectureSift is being built for flexible inputs: lecture videos, multiple sources, separate audio/visual material and supported video links — all feeding the same study workflow.\n\n#LectureSiftLaunch06 #LectureSift #StudyTools #EdTech #LectureNotes #AIForStudents #StudentTech",
    ),
    (
        "From lecture to study pack",
        "One source. Multiple ways to learn.",
        "READY TO REVIEW",
        "Turn lecture material into something you can actually revise.\n\nSlides, transcript, smart notes, quizzes and flashcards can work together as one reusable study pack.\n\n#LectureSiftLaunch07 #LectureSift #StudyPack #Flashcards #Quiz #EdTech #StudySmarter",
    ),
    (
        "How it works",
        "1. Add a lecture  2. Let LectureSift process it  3. Study the output",
        "THREE SIMPLE STEPS",
        "How LectureSift works:\n\n1️⃣ Add your lecture material.\n2️⃣ LectureSift processes and organizes it.\n3️⃣ Review the outputs in the format that fits your study session.\n\nSimple input. Useful output.\n\n#LectureSiftLaunch08 #LectureSift #HowItWorks #EdTech #AIForStudents #StudyTools #StudySmarter",
    ),
    (
        "What is LectureSift?",
        "An AI-powered study companion for lecture-heavy learning.",
        "MEET LECTURESIFT",
        "LectureSift is an AI-powered study companion built to turn lecture-heavy learning into a cleaner, more useful workflow.\n\nUpload your material and transform it into structured study outputs you can review, test and reuse.\n\n#LectureSiftLaunch09 #LectureSift #EdTech #AIForStudents #StudySmarter #LearningTechnology #StudentTech",
    ),
)

LAUNCH_POSTS = tuple(
    LaunchPost(
        index=index,
        title=title,
        subtitle=subtitle,
        kicker=kicker,
        caption=caption,
        marker=f"#LectureSiftLaunch{index:02d}",
    )
    for index, (title, subtitle, kicker, caption) in enumerate(_POSTS, 1)
)


def post_for_index(index: int) -> LaunchPost:
    if not 1 <= index <= len(LAUNCH_POSTS):
        raise ValueError("launch post index must be between 1 and 9")
    return LAUNCH_POSTS[index - 1]


def completed_indices(media_items: list[dict]) -> list[int]:
    captions = "\n".join((item.get("caption") or "") for item in media_items)
    return [post.index for post in LAUNCH_POSTS if post.marker in captions]


def next_pending_post(media_items: list[dict]) -> LaunchPost | None:
    completed = set(completed_indices(media_items))
    return next((post for post in LAUNCH_POSTS if post.index not in completed), None)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ("DejaVuSans-Bold.ttf", "Arial Bold.ttf") if bold else ("DejaVuSans.ttf", "Arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, min_size: int, *, bold: bool) -> ImageFont.ImageFont:
    size = start_size
    while size > min_size:
        font = _font(size, bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
        size -= 2
    return _font(min_size, bold)


def render_launch_image(index: int) -> bytes:
    post = post_for_index(index)
    image = Image.new("RGB", (1080, 1080), "#071429")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((58, 58, 1022, 1022), radius=54, fill="#0b1d3a", outline="#243d68", width=3)
    if index % 3 == 1:
        draw.ellipse((710, -90, 1170, 370), fill="#17376d")
        draw.ellipse((790, -25, 1125, 310), outline="#39a6ff", width=5)
    elif index % 3 == 2:
        draw.rounded_rectangle((735, 60, 1018, 395), radius=70, fill="#1b315e")
        draw.line((760, 360, 1010, 110), fill="#4ce0a3", width=8)
    else:
        draw.polygon(((800, 55), (1018, 55), (1018, 335), (900, 265)), fill="#24316a")
        draw.arc((735, 40, 1065, 370), 40, 280, fill="#39a6ff", width=7)

    draw.rounded_rectangle((98, 102, 365, 166), radius=27, fill="#142d55")
    draw.text((130, 120), "LECTURESIFT", fill="#4ce0a3", font=_font(27, True))
    draw.text((98, 250), post.kicker, fill="#88b9f2", font=_font(28, True))

    title_font = _fit_text(draw, post.title, 865, 82, 54, bold=True)
    draw.text((98, 318), post.title, fill="white", font=title_font)

    words = post.subtitle.split()
    lines: list[str] = []
    current = ""
    font = _font(42)
    for word in words:
        candidate = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= 820:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    draw.multiline_text((98, 455), "\n".join(lines[:3]), fill="#d9e9ff", font=font, spacing=16)

    draw.rounded_rectangle((98, 817, 982, 935), radius=34, fill="#102a50", outline="#1d4f84", width=2)
    draw.text((132, 846), "AI-powered study workflows", fill="#b8d8ff", font=_font(31, True))
    draw.text((132, 890), "lecturesift.com", fill="#4ce0a3", font=_font(25))
    draw.rounded_rectangle((858, 842, 950, 912), radius=24, fill="#39a6ff")
    draw.text((879, 859), f"{index:02d}", fill="#061022", font=_font(30, True))

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=93, optimize=True)
    return output.getvalue()
