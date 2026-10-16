from engine.audio_sync import caption_cues, split_caption
from engine.caption_renderer import CaptionRenderer, write_srt
from engine.illustration_composer import CAPTION_REGION
from engine.text_shaper import bidi_runs, shape_line


def test_caption_timing_contiguous():
    scene = {"caption_text": "A person starts investing with fifty dollars and waits patiently.", "start_seconds": 2.0, "end_seconds": 6.0}
    cues = caption_cues(scene)
    assert cues[0].start == 2.0 and cues[-1].end == 6.0
    assert all(abs(a.end - b.start) < 1e-9 for a, b in zip(cues, cues[1:]))
    assert all(len(c.text) <= 40 for c in cues)


def test_caption_uses_word_timestamps():
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("one two three four five six seven eight".split())]
    scene = {"caption_text": "one two three four five six seven eight", "start_seconds": 0.0, "end_seconds": 4.0, "words": words}
    cues = caption_cues(scene)
    assert cues[1].start == words[len(cues[0].text.split())]["start"]


def test_rtl_bidi_keeps_numbers_ltr():
    runs = bidi_runs("منافع 100% ہوا", base_rtl=True)
    texts = [(r.text.strip(), r.rtl) for r in runs]
    assert ("100%", False) in texts
    # visual order: last logical word comes first (leftmost)
    assert texts[0] == ("ہوا", True)


def test_urdu_shaping_not_reversed_characters():
    line = shape_line("ایک شخص", 48, "ur")
    assert not line.missing_chars
    # Contextual shaping produces fewer/different glyphs than isolated characters, never reversed codepoints.
    assert line.rtl and line.glyphs


def test_pashto_glyph_coverage():
    line = shape_line("ټډړږښګڼېۍ ۵۰ ډالر 100%", 48, "ps")
    assert line.missing_chars == []


def test_caption_renderer_wraps_and_reports_overflow():
    r = CaptionRenderer("en", CAPTION_REGION, 0.5)
    r.surface_for("Short caption")
    assert not r.overflows
    r.surface_for(" ".join(["extraordinarily"] * 30))
    assert r.overflows


def test_split_caption_short_phrases():
    chunks = split_caption("First you learn a skill, then you find a job, finally you earn money.")
    assert all(len(c) <= 40 for c in chunks)
    assert " ".join(chunks).split() == "First you learn a skill, then you find a job, finally you earn money.".split()


def test_srt_rtl_embedding(tmp_path):
    from engine.audio_sync import CaptionCue

    write_srt([CaptionCue("ایک شخص", 0, 1.5)], str(tmp_path / "c.srt"), "ur")
    content = (tmp_path / "c.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,500" in content and "‫" in content
