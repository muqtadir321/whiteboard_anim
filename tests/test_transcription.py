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
    assert abs(timing.sentences[1].start - 3.3) < 1e-6
    assert timing.sentences[-1].end == 10.0


def test_fake_transcriber_contract(tmp_path):
    t = FakeTranscriber("hello world", 2.0).transcribe(str(tmp_path / "a.wav"), "en")
    assert t.words[1]["start"] == 1.0 and t.engine == "fake"
