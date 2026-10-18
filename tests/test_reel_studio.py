from lecturesift import reel_studio as studio


def _narration(row=studio.SAMPLE):
    return studio.narrate(row, voice=False)


def test_scenes_follow_the_script_order_with_a_quiz_pause():
    narration = _narration()
    starts = [narration.scenes[name][0] for name in ("hook", "problem", "product", "quiz", "answer", "cta")]
    assert starts == sorted(starts)
    assert narration.scenes["answer"][0] - narration.scenes["quiz"][1] >= studio.QUIZ_PAUSE
    assert 15 <= narration.duration <= 45


def test_caption_phrases_fit_one_line():
    narration = _narration()
    f = studio.font(studio.CAPTION_SIZE, "black")
    groups = studio.caption_groups(narration.words)
    assert [w.text for g in groups for w in g] == [w.text for w in narration.words]
    assert all(f.getlength(" ".join(w.text for w in g)) <= studio.CAPTION_WIDTH or len(g) == 1 for g in groups)


def test_alignment_maps_characters_to_words():
    text = "Cevap: metafaz."
    starts = [i * 0.1 for i in range(len(text))]
    alignment = {
        "characters": list(text),
        "character_start_times_seconds": starts,
        "character_end_times_seconds": [s + 0.1 for s in starts],
    }
    words = studio._words_from_alignment(text, alignment, 2.0)
    assert [w.text for w in words] == ["Cevap:", "metafaz."]
    assert words[0].start == 2.0 and round(words[1].start, 2) == 2.7


def test_frames_and_carousel_have_instagram_sizes():
    narration = _narration()
    for name in ("hook", "problem", "product", "quiz", "cta"):
        start, end = narration.scenes[name]
        assert studio.render_frame(studio.SAMPLE, narration, (start + end) / 2).size == (1080, 1920)
    slides = studio.render_carousel(studio.SAMPLE)
    assert len(slides) == 5 and all(s.size == (1080, 1350) for s in slides)


_LONGEST = {
    **studio.SAMPLE,
    # Longest text the lesson validator lets through, with long unbreakable Turkish words.
    "hook": ["Mikrobiyolojiyi hâlâ mı", "Elektronegatifliklerinin"],
    "hook_highlight": "Elektronegatifliklerinin",
    "question": "Elektronegatifliklerinin kıyaslanmasında periyodik tablonun hangi bölgesi genelde en yüksek?",
    "options": ["Elektronegatifliklerinin", "Sağ üst köşedeki ametal", "Sol alt köşedeki metal", "Soygazlar her zaman en"],
    "answer": 0,
}


def _bright_pixels(image, box):
    region = image.crop(box).convert("RGB")
    return sum(1 for r, g, b in region.getdata() if r > 200 and g > 140)


def test_longest_text_stays_inside_the_safe_area():
    slides = studio.render_carousel(_LONGEST)
    for slide in slides:
        # Nothing bright (text, highlight, cards) in the side margins.
        assert _bright_pixels(slide, (0, 0, 52, studio.CH)) == 0
        assert _bright_pixels(slide, (studio.CW - 52, 0, studio.CW, studio.CH)) == 0
    # The question card ends above the swipe hint.
    assert _bright_pixels(slides[2], (72, studio.CH - 140, 700, studio.CH)) == 0
    narration = _narration(_LONGEST)
    for name in ("hook", "quiz"):
        start, end = narration.scenes[name]
        frame = studio.render_frame(_LONGEST, narration, end - 0.3, groups=[])
        assert _bright_pixels(frame, (0, 0, 52, studio.H)) == 0
        assert _bright_pixels(frame, (studio.W - 52, 0, studio.W, studio.H)) == 0
        # The caption band stays free for the subtitles.
        assert _bright_pixels(frame, (52, 1450, studio.W - 52, 1700)) == 0


def test_short_hook_is_vertically_centred():
    slide = studio.render_carousel(studio.SAMPLE)[0].convert("L")
    rows = [y for y in range(170, studio.CH - 150) if max(slide.crop((72, y, studio.CW - 72, y + 1)).getdata()) > 200]
    middle = (rows[0] + rows[-1]) / 2
    assert abs(middle - (170 + studio.CH - 150) / 2) < 60
