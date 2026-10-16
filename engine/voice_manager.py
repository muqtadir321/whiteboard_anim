"""Voice-over preparation: supplied audio first, then the free TTS fallback chain.

Priority: supplied audio > configured online provider > local engines in the
configured order. A voice is only used for a language it actually supports;
wrong-language fallback is disabled unless explicitly enabled, and is then
reported as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .audio_processor import concat_with_pauses, normalize
from .audio_timing import Timing, exact_timing
from .results import StageError, Status
from .retry import with_retries
from .transliteration import roman_to_urdu
from .tts.base import TTSError, TTSProvider
from .tts.edge import EdgeTTSProvider
from .tts.espeak import EspeakProvider
from .tts.mms import MMSProvider
from .tts.piper import PiperProvider

LOCAL = {"piper": PiperProvider, "mms": MMSProvider, "espeak": EspeakProvider}
ONLINE = {"edge_tts": EdgeTTSProvider}

# Which TTS language(s) can speak each script language, and whether the text
# must be converted (derived representation) first.
TTS_ROUTES = {
    "en": [("en", None)],
    "ur": [("ur", None)],
    "roman_urdu": [("ur", "roman_to_urdu")],
    "ps": [("ps", None)],
    "ps_latn": [],
}
WRONG_LANGUAGE_FALLBACK = {"roman_urdu": ("en", None), "ps": ("ur", None), "ps_latn": ("en", None)}


@dataclass
class VoiceResult:
    audio_path: str
    timing: Timing
    provider: dict
    derived_text: list[str] | None = None
    attempts: list[dict] = field(default_factory=list)
    wrong_language: bool = False


def build_chain(cfg: dict) -> list[TTSProvider]:
    chain: list[TTSProvider] = []
    v = cfg["voice"]
    if v["allow_online_free_tts"]:
        for name in v.get("online_providers", []):
            if name in ONLINE:
                chain.append(ONLINE[name](cfg))
    if v["allow_local_tts"]:
        for name in v.get("local_engines", []):
            if name in LOCAL:
                chain.append(LOCAL[name](cfg))
    return chain


def _convert(text: str, conversion: str | None) -> str:
    if conversion == "roman_to_urdu":
        return roman_to_urdu(text)
    return text


def synthesize_sentences(sentences: list[str], lang: str, cfg: dict, work_dir: Path, log=print, chain: list[TTSProvider] | None = None) -> VoiceResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    chain = chain if chain is not None else build_chain(cfg)
    attempts: list[dict] = []
    routes = list(TTS_ROUTES.get(lang, []))
    wrong = False
    if cfg["voice"]["allow_wrong_language_fallback"] and lang in WRONG_LANGUAGE_FALLBACK:
        routes.append(WRONG_LANGUAGE_FALLBACK[lang])
    for ri, (tts_lang, conversion) in enumerate(routes):
        is_wrong = ri >= len(TTS_ROUTES.get(lang, []))
        texts = [_convert(s, conversion) for s in sentences]
        for provider in chain:
            if not provider.is_available(tts_lang):
                attempts.append({"provider": provider.name, "language": tts_lang, "status": "unavailable"})
                continue
            parts = []
            try:
                for i, text in enumerate(texts):
                    raw = work_dir / f"tts_{provider.name}_{i:03d}.wav"
                    with_retries(lambda: provider.synthesize(text, tts_lang, str(raw)), cfg["automation"]["retry_limit"] if provider.online else 0,
                                 cfg["automation"]["retry_base_seconds"], log)
                    norm = work_dir / f"tts_{provider.name}_{i:03d}_norm.wav"
                    normalize(raw, norm, cfg["video"]["audio_sample_rate"], trim_silence=True)
                    parts.append(norm)
            except (TTSError, StageError, OSError) as exc:
                attempts.append({"provider": provider.name, "language": tts_lang, "status": "failed", "error": str(exc)[:300]})
                log(f"  TTS provider {provider.name} failed for {tts_lang}: {exc}")
                continue
            out = work_dir / "narration.wav"
            spans = concat_with_pauses(parts, out, cfg["voice"]["sentence_pause_seconds"], cfg["video"]["audio_sample_rate"])
            from .audio_processor import probe

            duration = probe(out).duration
            attempts.append({"provider": provider.name, "language": tts_lang, "status": "success"})
            wrong = is_wrong
            desc = provider.describe(tts_lang)
            desc["conversion"] = conversion
            desc["wrong_language_fallback"] = wrong
            return VoiceResult(str(out), exact_timing(sentences, spans, duration), desc, texts if conversion else None, attempts, wrong)
    raise StageError(
        f"No suitable free voice available for '{lang}'. Tried: "
        + (", ".join(f"{a['provider']}({a['language']}): {a['status']}" for a in attempts) or "no providers configured"),
        Status.PERMANENT_FAILURE,
    )
