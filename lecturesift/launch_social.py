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


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int = 3) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) >= max_lines - 1:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    return "\n".join(lines)


def render_launch_image(index: int) -> bytes:
    """Render a 3:4 feed card with a conservative Instagram grid-safe area."""
    post = post_for_index(index)
    image = Image.new("RGB", (1080, 1440), "#050b1f")
    draw = ImageDraw.Draw(image)

    # Keep meaningful content well inside the profile-thumbnail crop area.
    draw.rounded_rectangle((72, 72, 1008, 1368), radius=58, fill="#08162f", outline="#223b68", width=3)
    draw.ellipse((690, 120, 1080, 510), fill="#132b61")
    draw.ellipse((770, 165, 1030, 425), outline="#8f52ff", width=5)

    draw.rounded_rectangle((118, 132, 408, 198), radius=28, fill="#10284f")
    draw.text((150, 150), "LECTURESIFT", fill="#42d8ff", font=_font(28, True))
    draw.text((118, 286), post.kicker, fill="#a8c7ef", font=_font(30, True))

    title_font = _fit_text(draw, post.title, 830, 82, 54, bold=True)
    title = _wrap(draw, post.title, title_font, 830, max_lines=2)
    draw.multiline_text((118, 352), title, fill="white", font=title_font, spacing=12)

    title_box = draw.multiline_textbbox((118, 352), title, font=title_font, spacing=12)
    subtitle_y = max(520, title_box[3] + 54)
    subtitle_font = _font(40)
    subtitle = _wrap(draw, post.subtitle, subtitle_font, 820, max_lines=3)
    draw.multiline_text((118, subtitle_y), subtitle, fill="#d9e8ff", font=subtitle_font, spacing=18)

    panel_top = max(760, subtitle_y + 190)
    panel_bottom = 1160
    draw.rounded_rectangle((118, panel_top, 962, panel_bottom), radius=42, fill="#0b2148", outline="#1d5a92", width=3)

    # A simple study-pack flow that remains legible as a profile thumbnail.
    draw.rounded_rectangle((156, panel_top + 54, 386, panel_top + 250), radius=28, fill="#0d2e5a", outline="#2ca8ff", width=3)
    draw.text((197, panel_top + 91), "LECTURE", fill="#7fd6ff", font=_font(26, True))
    draw.rounded_rectangle((196, panel_top + 148, 345, panel_top + 198), radius=18, fill="#1f6ec0")
    draw.polygon(((245, panel_top + 157), (245, panel_top + 190), (280, panel_top + 174)), fill="white")

    draw.line((414, panel_top + 150, 510, panel_top + 150), fill="#4ce0ff", width=8)
    draw.polygon(((510, panel_top + 150), (482, panel_top + 133), (482, panel_top + 167)), fill="#4ce0ff")

    outputs = (("TRANSCRIPT", "#2ca8ff"), ("NOTES", "#4f7dff"), ("QUIZ", "#8b5cff"), ("FLASHCARDS", "#c049ff"))
    out_y = panel_top + 38
    for label, color in outputs:
        draw.rounded_rectangle((540, out_y, 914, out_y + 70), radius=24, fill="#101f48", outline=color, width=3)
        draw.text((576, out_y + 20), label, fill="white", font=_font(24, True))
        out_y += 82

    draw.rounded_rectangle((118, 1220, 962, 1322), radius=34, fill="#0b2246", outline="#214f82", width=2)
    draw.text((154, 1248), "AI-powered study workflows", fill="#c4ddff", font=_font(31, True))
    draw.text((154, 1287), "lecturesift.com", fill="#4ce0a3", font=_font(24))
    draw.rounded_rectangle((842, 1245, 925, 1308), radius=22, fill="#386fff")
    draw.text((860, 1260), f"{index:02d}", fill="white", font=_font(27, True))

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=94, optimize=True)
    return output.getvalue()
