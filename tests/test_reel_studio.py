from lecturesift import reel_studio as studio


def _narration():
    return studio.narrate(studio.SAMPLE, voice=False)


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
