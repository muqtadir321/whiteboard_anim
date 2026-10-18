from pathlib import Path

from engine.audio_timing import align_words, estimate_timing
from engine.transcription_manager import finalize_transcript

from .conftest import FakeTranscriber


def test_roman_urdu_transcript_is_transliterated():
    t = finalize_transcript([{"start": 0, "end": 2, "text": "یہ ہے", "avg_logprob": -0.3}], [{"word": "یہ", "start": 0, "end": 1}], [-0.3], "roman_urdu", "x")
    assert t.text.lower().startswith("yeh")
    assert t.notes and t.confidence < 0.8


def test_alignment_maps_sentences_to_word_times():
    sentences = ["Money grows slowly.", "Then it grows fast."]
    words = [{"word": w, "start": i, "end": i + 0.8} for i, w in enumerate("money grows slowly then it grows fast".split())]
    timing, sim = align_words(sentences, words, 8.0)
    assert sim > 0.9
    assert timing.source == "words"
    assert timing.sentences[1].start == 3.0


def test_alignment_flags_mismatch():
    words = [{"word": w, "start": i, "end": i + 0.8} for i, w in enumerate("completely different speech here".split())]
    _, sim = align_words(["Money grows slowly."], words, 5.0)
    assert sim < 0.6


def test_estimated_timing_snaps_to_pauses():
    sentences = ["Short one.", "This is a considerably longer second sentence here."]
    timing = estimate_timing(sentences, 10.0, [(3.0, 3.6)])
    assert timing.source == "estimated"
    assert abs(timing.sentences[1].start - 3.54) < 1e-6  # where speech resumes after the pause
    assert timing.sentences[-1].end == 10.0


def test_fake_transcriber_contract(tmp_path):
    t = FakeTranscriber("hello world", 2.0).transcribe(str(tmp_path / "a.wav"), "en")
    assert t.words[1]["start"] == 1.0 and t.engine == "fake"


def test_dp_alignment_handles_many_sentences_without_drift():
    """30 sentences with uneven pace: every boundary must land on its own pause."""
    import random

    rng = random.Random(3)
    sentences, silences, true_starts = [], [], []
    t = 0.3
    for i in range(30):
        words = rng.randint(4, 18)
        sentences.append(" ".join(["word"] * words) + ".")
        true_starts.append(t)
        t += words * rng.uniform(0.25, 0.45)  # speaking rate varies per sentence
        gap = rng.uniform(0.25, 0.7)
        silences.append((t, t + gap))
        # a short comma pause inside some sentences
        t += gap
    duration = t
    timing = estimate_timing(sentences, duration, silences)
    errs = [abs(s.start - (tr - 0.06)) for s, tr in zip(timing.sentences[1:], true_starts[1:])]
    assert max(errs) < 0.05


def test_forced_alignment_timing_interpolates_gaps():
    from engine.forced_alignment import romanize_token, timing_from_alignment

    sentences = ["Money grows in 2026.", "Then it doubles."]
    spans = [(0.5, 0.9), (0.9, 1.3), (1.3, 1.4), None, (2.0, 2.3), (2.3, 2.5), (2.5, 3.0)]
    tm = timing_from_alignment(sentences, spans, 3.5)
    assert tm.source == "forced_alignment"
    assert tm.sentences[1].start == 1.95 and tm.sentences[0].start == 0.0 and tm.sentences[1].end == 3.5
    w = tm.sentences[0].words[3]
    assert w["estimated"] and 1.4 <= w["start"] < 2.0
    assert romanize_token("$100,000") == "onezerozerozerozerozero"
    assert romanize_token("What's") == "what's"
    assert romanize_token("درهم") == ""  # needs uroman, not available here
