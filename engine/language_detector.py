"""Deterministic language identification for English, Urdu, Roman Urdu and Pashto.

Combines Unicode script analysis, language-specific letters and function-word
lexicons. The language implied by the input field is used as a prior and wins
whenever the evidence is ambiguous. Roman Urdu is never classified as English
merely because it is written in Latin characters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text_shaper import is_arabic_char

PASHTO_LETTERS = set("ټډړږښګڼېۍځڅ")
URDU_LETTERS = set("ٹڈڑںےۓھہۂۃ")

EN_WORDS = set(
    "the a an and of to in is are was were it that this with for on as at by from be have has had not but or they you we he she "
    "their his her its will would can could money people when what which there more over time grows".split()
)
RU_WORDS = set(
    "hai hain ka ki ke ko se mein main mai aur nahi nahin kya yeh ye woh wo ap aap hum tum jo bhi tha thi thay ho hota hoti "
    "kar karna karte kiya liye lye sath saath agar lekin magar phir pehle paisa paise apna apne apni koi kuch bohat bahut "
    "zyada kam din saal waqt log logon bacha shakhs ne gaya gayi raha rahi rahe sakta sakte chahiye wala wali wale jab tab "
    "kyun kyunke isliye matlab acha theek hamesha".split()
)
UR_WORDS = set("ہے ہیں کا کی کے کو سے میں اور نہیں کیا یہ وہ آپ ہم تم جو بھی تھا تھی تھے ہو کر نے لیے ساتھ اگر لیکن پھر".split())
PS_WORDS = set("دی ده دې چې او په د ته کې دا هغه یې وو شو شوه نه دي څه ولې کوي لپاره سره هم".split())


@dataclass
class Detection:
    code: str  # en | ur | roman_urdu | ps | unknown
    confidence: float
    evidence: dict

    def to_dict(self) -> dict:
        return {"code": self.code, "confidence": round(self.confidence, 3), "evidence": self.evidence}


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)]


def detect_language(text: str, expected: str | None = None) -> Detection:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return Detection(expected or "unknown", 0.0, {"reason": "no letters"})
    arabic = sum(1 for c in letters if is_arabic_char(c))
    arabic_ratio = arabic / len(letters)
    toks = _tokens(text)
    evidence: dict = {"arabic_ratio": round(arabic_ratio, 3), "tokens": len(toks)}

    if arabic_ratio >= 0.5:
        ps_letters = sum(1 for c in letters if c in PASHTO_LETTERS)
        ur_letters = sum(1 for c in letters if c in URDU_LETTERS)
        ps_words = sum(1 for t in toks if t in PS_WORDS)
        ur_words = sum(1 for t in toks if t in UR_WORDS)
        ps_score = ps_letters * 1.0 + ps_words * 2.0
        ur_score = ur_letters * 1.0 + ur_words * 2.0
        evidence.update(ps_score=ps_score, ur_score=ur_score)
        total = ps_score + ur_score
        if total == 0:
            code = expected if expected in ("ur", "ps") else "ur"
            return Detection(code, 0.4, {**evidence, "reason": "arabic script without distinguishing features"})
        code = "ps" if ps_score > ur_score else "ur"
        conf = 0.5 + 0.5 * abs(ps_score - ur_score) / total
        if conf < 0.65 and expected in ("ur", "ps"):
            code = expected
        return Detection(code, conf, evidence)

    en_hits = sum(1 for t in toks if t in EN_WORDS)
    ru_hits = sum(1 for t in toks if t in RU_WORDS)
    evidence.update(en_hits=en_hits, roman_urdu_hits=ru_hits)
    total = en_hits + ru_hits
    if expected == "ps_latn":
        return Detection("ps_latn", 0.6, {**evidence, "reason": "explicitly configured Pashto transliteration"})
    if total == 0:
        code = expected if expected in ("en", "roman_urdu") else "en"
        return Detection(code, 0.3, {**evidence, "reason": "no function words recognised"})
    code = "roman_urdu" if ru_hits > en_hits else "en"
    conf = 0.5 + 0.5 * abs(ru_hits - en_hits) / total
    if conf < 0.65 and expected in ("en", "roman_urdu"):
        code = expected
    return Detection(code, conf, evidence)
