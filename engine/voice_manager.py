"""Voice-over preparation: supplied audio first, then the free TTS fallback chain.

Priority: supplied audio > configured online provider > local engines in the
configured order. A voice is only used for a language it actually supports;
wrong-language fallback is disabled unless explicitly enabled, and is then
reported as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .audio_processor import concat_with_pauses, detect_silences, normalize, probe, trim_clip
from .audio_timing import Timing, exact_timing
from .results import StageError, Status
from .retry import with_retries
from .transliteration import roman_to_urdu, spell_numbers
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
    clone: dict | None = None


def build_chain(cfg: dict, reference: Path | None = None) -> list[TTSProvider]:
    chain: list[TTSProvider] = []
    v = cfg["voice"]
    if reference is not None and v["clone"]["engine"] == "omnivoice":
        from .tts.omnivoice import OmniVoiceProvider

        # Speaks directly in the reference voice; other engines follow as fallbacks.
        chain.append(OmniVoiceProvider(cfg, reference))
    if v["allow_online_free_tts"]:
        for name in v.get("online_providers", []):
            if name in ONLINE:
                chain.append(ONLINE[name](cfg))
    if v["allow_local_tts"]:
        for name in v.get("local_engines", []):
            if name in LOCAL:
                chain.append(LOCAL[name](cfg))
    return chain


def _convert(text: str, conversion: str | None, tts_lang: str = "", pronunciations: dict | None = None) -> str:
    if conversion == "roman_to_urdu":
        return roman_to_urdu(text, pronunciations)
    if tts_lang == "ur":
        return spell_numbers(text)  # digits in Urdu-script narration are spoken as Urdu words
    return text


def speech_units(sentences: list[str], paragraphs: list[int] | None, whole_paragraphs: bool, max_words: int = 60) -> list[list[int]]:
    """Sentence indices synthesised together: one per sentence, or whole paragraphs (split if very long)."""
    if not whole_paragraphs or not paragraphs or len(paragraphs) != len(sentences):
        return [[i] for i in range(len(sentences))]
    units: list[list[int]] = []
    cur: list[int] = []
    words = 0
    for i, s in enumerate(sentences):
        n = len(s.split())
        if cur and (paragraphs[i] != paragraphs[cur[-1]] or words + n > max_words):
            units.append(cur)
            cur, words = [], 0
        cur.append(i)
        words += n
    if cur:
        units.append(cur)
    return units


def tighten_clip(path: Path, dst: Path, tokens: list[str], aligner=None, lang: str = "", log=print) -> Path:
    """Cut a generated clip right before its first and right after its last spoken word.

    Neural voices often add a breath or a short "aaf" sound and silence after the last
    word; with word timing (forced alignment) that tail is removed exactly. Without an
    aligner, edge silence is trimmed tightly instead.
    """
    dur = probe(path).duration
    start, end = 0.0, dur
    done = False
    if aligner is not None and tokens:
        try:
            spans = aligner.align(str(path), tokens, lang)
            known = [sp for sp in spans if sp]
            if len(known) >= max(1, len(tokens) // 2):
                start = max(0.0, known[0][0] - 0.06)
                last = next((sp for sp in reversed(spans[-2:]) if sp), None)  # last word(s) must be found
                if last is not None:
                    end = min(dur, last[1] + 0.12)
                done = True
        except Exception as exc:  # timing is optional; fall back to silence trimming
            log(f"  clip alignment failed ({exc}); trimming silence instead")
    if not done:
        quiet = detect_silences(path, noise_db=-42.0, min_duration=0.12)
        if quiet and quiet[0][0] <= 0.05:
            start = max(0.0, quiet[0][1] - 0.05)
        if quiet and quiet[-1][1] >= dur - 0.05 and quiet[-1][0] > start:
            end = min(dur, quiet[-1][0] + 0.1)
    if end - start < 0.2 or (start <= 0.01 and end >= dur - 0.01):
        return path
    trim_clip(path, dst, start, end)
    return dst


def _split_span(unit: list[int], span: tuple[float, float], sentences: list[str]) -> list[tuple[float, float]]:
    """Share a paragraph's time between its sentences by length (used when no aligner exists)."""
    a, b = span
    weights = [max(1, len(sentences[i])) for i in unit]
    total = sum(weights)
    out, t = [], a
    for w in weights:
        d = (b - a) * w / total
        out.append((t, t + d))
        t += d
    return out


def synthesize_sentences(sentences: list[str], lang: str, cfg: dict, work_dir: Path, log=print, chain: list[TTSProvider] | None = None,
                         reference: Path | None = None, cloner=None, paragraphs: list[int] | None = None, aligner=None) -> VoiceResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    chain = chain if chain is not None else build_chain(cfg, reference)
    attempts: list[dict] = []
    routes = list(TTS_ROUTES.get(lang, []))
    wrong = False
    if cfg["voice"]["allow_wrong_language_fallback"] and lang in WRONG_LANGUAGE_FALLBACK:
        routes.append(WRONG_LANGUAGE_FALLBACK[lang])
    for ri, (tts_lang, conversion) in enumerate(routes):
        is_wrong = ri >= len(TTS_ROUTES.get(lang, []))
        texts = [_convert(s, conversion, tts_lang, cfg["voice"].get("pronunciations")) for s in sentences]
        for provider in chain:
            if not provider.is_available(tts_lang):
                entry = {"provider": provider.name, "language": tts_lang, "status": "unavailable"}
                if getattr(provider, "unavailable_reason", None):
                    entry["reason"] = provider.unavailable_reason
                    log(f"  {provider.name} unavailable: {provider.unavailable_reason}")
                attempts.append(entry)
                continue
            parts = []
            units = speech_units(sentences, paragraphs, getattr(provider, "speaks_paragraphs", False))
            utexts = [" ".join(texts[i] for i in u) for u in units]
            try:
                raws = [work_dir / f"tts_{provider.name}_{i:03d}.wav" for i in range(len(utexts))]
                retries = cfg["automation"]["retry_limit"] if provider.online else 0
                if hasattr(provider, "synthesize_batch"):
                    with_retries(lambda: provider.synthesize_batch(utexts, tts_lang, [str(r) for r in raws]), max(retries, 1),
                                 cfg["automation"]["retry_base_seconds"], log)
                else:
                    for text, raw in zip(utexts, raws):
                        with_retries(lambda: provider.synthesize(text, tts_lang, str(raw)), retries, cfg["automation"]["retry_base_seconds"], log)
                for i, raw in enumerate(raws):
                    norm = work_dir / f"tts_{provider.name}_{i:03d}_norm.wav"
                    normalize(raw, norm, cfg["video"]["audio_sample_rate"], trim_silence=True)
                    tokens = [t for k in units[i] for t in sentences[k].split()]
                    parts.append(tighten_clip(norm, work_dir / f"tts_{provider.name}_{i:03d}_tight.wav", tokens, aligner, lang, log))
            except (TTSError, StageError, OSError) as exc:
                attempts.append({"provider": provider.name, "language": tts_lang, "status": "failed", "error": str(exc)[:300]})
                log(f"  TTS provider {provider.name} failed for {tts_lang}: {exc}")
                continue
            clone_info = None
            if reference is not None and getattr(provider, "clones_voice", False):
                clone_info = {"requested": True, "reference": str(reference), "applied": True, "engine": provider.name,
                              "license": provider.license, "method": "zero-shot voice-cloning TTS"}
            elif reference is not None:
                from .voice_clone import apply_clone

                converted, clone_info = apply_clone(parts, reference, cfg, work_dir / "clone", cloner, log)
                if clone_info.get("applied"):
                    parts = []
                    for i, c in enumerate(converted):
                        norm = work_dir / f"clone_{i:03d}_norm.wav"
                        normalize(c, norm, cfg["video"]["audio_sample_rate"], trim_silence=False)
                        parts.append(norm)
            out = work_dir / "narration.wav"
            v = cfg["voice"]
            gaps = []
            for k, u in enumerate(units):
                nxt = units[k + 1][0] if k + 1 < len(units) else None
                new_par = nxt is not None and paragraphs is not None and paragraphs[nxt] != paragraphs[u[-1]]
                gaps.append(0.0 if nxt is None else v.get("paragraph_pause_seconds", 0.3) if new_par else v["sentence_pause_seconds"])
            uspans = concat_with_pauses(parts, out, gaps, cfg["video"]["audio_sample_rate"])
            duration = probe(out).duration
            spans = [sp for u, us in zip(units, uspans) for sp in _split_span(u, us, sentences)]
            timing = exact_timing(sentences, spans, duration)
            if aligner is not None:
                # exact word timing of the generated narration, so drawings land on the spoken word
                try:
                    from .forced_alignment import timing_from_alignment

                    tokens = [t for s_ in sentences for t in s_.split()]
                    timing = timing_from_alignment(sentences, aligner.align(str(out), tokens, lang), duration)
                except Exception as exc:
                    log(f"  word alignment of generated narration failed ({exc}); using sentence timing")
            attempts.append({"provider": provider.name, "language": tts_lang, "status": "success"})
            wrong = is_wrong
            desc = provider.describe(tts_lang)
            desc["conversion"] = conversion
            desc["wrong_language_fallback"] = wrong
            desc["speech_units"] = "paragraphs" if len(units) < len(sentences) else "sentences"
            return VoiceResult(str(out), timing, desc, texts if (conversion or texts != sentences) else None, attempts, wrong, clone_info)
    raise StageError(
        f"No suitable free voice available for '{lang}'. Tried: "
        + (", ".join(f"{a['provider']}({a['language']}): {a['status']}" for a in attempts) or "no providers configured"),
        Status.PERMANENT_FAILURE,
    )
