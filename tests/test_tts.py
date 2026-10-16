import pytest

from engine.results import Status, StageError
from engine.retry import with_retries
from engine.tts.base import RetryableTTSError
from engine.tts.espeak import EspeakProvider
from engine.tts.mms import MMSProvider
from engine.voice_manager import build_chain, synthesize_sentences

from .conftest import HAS_ESPEAK, ToneTTS


def test_chain_order_and_online_opt_in(cfg):
    names = [p.name for p in build_chain(cfg)]
    assert names == ["piper", "mms", "espeak"]  # no online provider unless configured
    cfg["voice"]["online_providers"] = ["edge_tts"]
    assert [p.name for p in build_chain(cfg)][0] == "edge_tts"
    cfg["voice"]["allow_online_free_tts"] = False
    assert "edge_tts" not in [p.name for p in build_chain(cfg)]


def test_noncommercial_models_can_be_disabled(cfg):
    cfg["voice"]["allow_noncommercial_models"] = False
    assert not MMSProvider(cfg).is_available("ps")


@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak-ng not installed")
def test_espeak_supports_en_ur_not_ps(cfg):
    p = EspeakProvider(cfg)
    assert p.is_available("en") and p.is_available("ur")
    assert not p.is_available("ps")


def test_pashto_with_compatible_test_voice(cfg, tmp_path):
    voice = ToneTTS(cfg, languages=("ps",))
    r = synthesize_sentences(["یو سړی.", "پیسې زیاتې شوې."], "ps", cfg, tmp_path, log=lambda m: None, chain=[voice])
    assert r.provider["provider"] == "test_tone"
    assert r.timing.source == "exact" and len(r.timing.sentences) == 2
    assert r.timing.sentences[1].start > r.timing.sentences[0].start


def test_no_wrong_language_voice_by_default(cfg, tmp_path):
    english_only = ToneTTS(cfg, languages=("en",))
    with pytest.raises(StageError) as exc:
        synthesize_sentences(["یو سړی."], "ps", cfg, tmp_path, log=lambda m: None, chain=[english_only])
    assert "No suitable free voice" in str(exc.value)


def test_roman_urdu_uses_derived_urdu_text(cfg, tmp_path):
    urdu_voice = ToneTTS(cfg, languages=("ur",))
    r = synthesize_sentences(["Aik shakhs ne pachas dollar lagaye."], "roman_urdu", cfg, tmp_path, log=lambda m: None, chain=[urdu_voice])
    assert urdu_voice.calls[0] == ("ایک شخص نے پچاس ڈالر لگائے۔", "ur")
    assert r.derived_text and r.provider["conversion"] == "roman_to_urdu"


def test_falls_through_failing_provider(cfg, tmp_path):
    class Broken(ToneTTS):
        name = "broken"

        def synthesize(self, text, language, output_path):
            raise RetryableTTSError("network down")

    r = synthesize_sentences(["Hello."], "en", cfg, tmp_path, log=lambda m: None, chain=[Broken(cfg, ("en",)), ToneTTS(cfg, ("en",))])
    assert r.provider["provider"] == "test_tone"
    assert r.attempts[0]["status"] == "failed"


def test_bounded_retry_with_backoff():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        raise StageError("timeout", Status.RETRYABLE_FAILURE)

    with pytest.raises(StageError):
        with_retries(flaky, limit=3, base=2.0, sleep=sleeps.append)
    assert len(calls) == 4 and sleeps == [2.0, 4.0, 8.0]
    permanent = []

    def bad_input():
        permanent.append(1)
        raise StageError("invalid", Status.PERMANENT_FAILURE)

    with pytest.raises(StageError):
        with_retries(bad_input, limit=3, base=0, sleep=lambda s: None)
    assert len(permanent) == 1
