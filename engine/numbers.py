"""Multilingual extraction of quantities: numbers, percentages, currency, multipliers.

Values are taken only from the narration. Nothing here invents data: every
Quantity records the exact span of source text it came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")

EN_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
EN_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000, "trillion": 1_000_000_000_000,
             "lakh": 100_000, "crore": 10_000_000}

# Roman Urdu number words (common spellings).
RU_WORDS = {
    "aik": 1, "ek": 1, "ik": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "panch": 5, "paanch": 5,
    "chay": 6, "chhe": 6, "che": 6, "saat": 7, "sat": 7, "aath": 8, "aat": 8, "nau": 9, "no": None, "das": 10, "dus": 10,
    "gyarah": 11, "barah": 12, "baara": 12, "pandrah": 15, "bees": 20, "pachees": 25, "tees": 30,
    "chalees": 40, "chaalees": 40, "pachas": 50, "pachaas": 50, "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90, "nawway": 90,
    "chhabbees": 26, "pachpan": 55, "painsath": 65, "pachattar": 75, "pachhattar": 75, "atthattar": 78,
    "sau": 100, "so": None, "hazar": 1000, "hazaar": 1000, "lakh": 100_000, "laakh": 100_000, "crore": 10_000_000, "karor": 10_000_000,
    "arab": 1_000_000_000, "kharab": 100_000_000_000,
}
UR_WORDS = {
    "ایک": 1, "دو": 2, "تین": 3, "چار": 4, "پانچ": 5, "چھ": 6, "سات": 7, "آٹھ": 8, "نو": 9, "دس": 10,
    "گیارہ": 11, "بارہ": 12, "پندرہ": 15, "بیس": 20, "پچیس": 25, "تیس": 30, "چالیس": 40, "پچاس": 50,
    "ساٹھ": 60, "پچپن": 55, "پینسٹھ": 65, "پچھتر": 75, "ستر": 70, "اسی": 80, "نوے": 90, "سو": 100, "ہزار": 1000, "لاکھ": 100_000, "کروڑ": 10_000_000,
}
PS_WORDS = {
    "یو": 1, "دوه": 2, "درې": 3, "څلور": 4, "پنځه": 5, "شپږ": 6, "اووه": 7, "اته": 8, "نهه": 9, "لس": 10,
    "شل": 20, "دېرش": 30, "څلوېښت": 40, "پنځوس": 50, "شپېته": 60, "اويا": 70, "اتیا": 80, "نوي": 90,
    "سل": 100, "زر": 1000, "لک": 100_000, "ويشت": 20, "دېرش": 30, "اويا": 70, "سوه": 100, "زره": 1000, "زرو": 1000, "لکه": 100_000,
    "يو": 1, "درې": 3, "اووه": 7, "شپږ": 6,
}
SCALE_WORDS = {"sau": 100, "hazar": 1000, "hazaar": 1000, "lakh": 100_000, "laakh": 100_000, "crore": 10_000_000, "karor": 10_000_000,
               "arab": 1_000_000_000, "kharab": 100_000_000_000,
               "سو": 100, "ہزار": 1000, "لاکھ": 100_000, "کروڑ": 10_000_000, "سل": 100, "زر": 1000, "لک": 100_000,
               "سوه": 100, "زره": 1000, "زرو": 1000, "لکه": 100_000}

PERCENT_WORDS = ("percent", "per cent", "percentage", "fisad", "feesad", "fees ad", "فیصد", "سلنه", "%", "٪")
CURRENCY_WORDS = {
    "$": "$", "₹": "Rs", "dollar": "$", "dollars": "$", "usd": "$", "ڈالر": "$", "ډالر": "$",
    "rupee": "Rs", "rupees": "Rs", "rs": "Rs", "rs.": "Rs", "pkr": "Rs", "rupay": "Rs", "rupaye": "Rs", "rupe": "Rs", "روپے": "Rs", "روپیہ": "Rs", "روپۍ": "Rs", "روپۍ": "Rs",
    "dirham": "AED", "dirhams": "AED", "aed": "AED", "درهم": "AED", "درهمه": "AED", "درہم": "AED",
    "afghani": "AFN", "afghanis": "AFN", "افغانۍ": "AFN", "£": "£", "pound": "£", "pounds": "£", "€": "€", "euro": "€", "euros": "€",
}
MULTIPLIER_WORDS = {
    "double": 2, "doubles": 2, "doubled": 2, "doubling": 2, "twice": 2, "triple": 3, "triples": 3, "tripled": 3,
    "quadruple": 4, "dugna": 2, "dogna": 2, "doguna": 2, "teen guna": 3, "tigna": 3, "دگنا": 2, "دوگنا": 2, "تگنا": 3, "دوه چنده": 2, "دوه برابره": 2, "half": 0.5, "halved": 0.5, "aadha": 0.5, "آدھا": 0.5, "نیم": 0.5,
}
YEAR_RE = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
# Scales shown by name on screen (smaller ones are written out in full, e.g. 50,000).
LARGE_SCALES = {"lakh": 100_000, "laakh": 100_000, "million": 1_000_000, "crore": 10_000_000, "karor": 10_000_000,
                "billion": 1_000_000_000, "arab": 1_000_000_000, "kharab": 100_000_000_000, "trillion": 1_000_000_000_000}
_SCALE_AFTER = re.compile(r"\s+(" + "|".join(sorted(set(EN_SCALES) | set(SCALE_WORDS) - {"sau", "hundred"}, key=len, reverse=True)) + r")\b", re.I)


@dataclass
class Quantity:
    value: float
    kind: str  # percent | currency | count | multiplier | year | plain
    unit: str
    span: tuple[int, int]
    source: str
    scale_word: str = ""  # "lakh", "million", ... when the source names a large scale

    @property
    def display(self) -> str:
        v = self.value
        num = _fmt(v)
        if self.scale_word:  # written the way it is said: $1 trillion, Rs 51 lakh, 1.33 million
            num = f"{_fmt(v / LARGE_SCALES[self.scale_word])} {self.scale_word}"
        if self.kind == "percent":
            return f"{num}%"
        if self.kind == "currency":
            if self.unit in ("$", "£", "€"):
                return f"{self.unit}{num}"
            return f"{self.unit} {num}"
        if self.kind == "multiplier":
            return f"{num}x"
        return num

    def to_dict(self) -> dict:
        return {"value": self.value, "kind": self.kind, "unit": self.unit, "display": self.display, "span": list(self.span), "source": self.source}


def _fmt(v: float) -> str:
    if abs(v - round(v)) < 1e-9:
        iv = int(round(v))
        if abs(iv) >= 10_000:
            return f"{iv:,}"
        return str(iv)
    return (f"{v:,.2f}" if abs(v) >= 1000 else f"{v:.2f}").rstrip("0").rstrip(".")


def normalize_digits(text: str) -> str:
    return text.translate(DIGIT_MAP)


_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?:\s?([kKmM])\b)?")


def _word_tokens(text: str):
    for m in re.finditer(r"[^\s.,!?;:()\"'،۔؟]+", text):
        yield m.group(0), m.start(), m.end()


def _parse_english_words(tokens, i):
    """Parse an English number phrase starting at token i. Returns (value, next_index) or None."""
    total = 0
    current = 0
    j = i
    consumed = False
    while j < len(tokens):
        w = tokens[j][0].lower().replace("-", " ")
        parts = w.split()
        ok = True
        for p in parts:
            if p in EN_UNITS:
                current += EN_UNITS[p]
            elif p in EN_TENS:
                current += EN_TENS[p]
            elif p in EN_SCALES and consumed:
                scale = EN_SCALES[p]
                if scale == 100:
                    current *= 100
                else:
                    total += current * scale
                    current = 0
            elif p == "and" and consumed:
                pass
            else:
                ok = False
                break
        if not ok:
            break
        consumed = True
        j += 1
    if not consumed:
        return None
    # Trailing "and" is not part of the number.
    while j > i and tokens[j - 1][0].lower() == "and":
        j -= 1
    value = total + current
    if tokens[i][0].lower() == "and":
        return None
    return value, j


def _parse_table_words(tokens, i, table):
    value = 0
    current = 0
    j = i
    consumed = False
    while j < len(tokens):
        w = tokens[j][0].lower()
        if w in SCALE_WORDS and consumed:
            current = (current or 1) * SCALE_WORDS[w]
            if SCALE_WORDS[w] >= 1000:
                value += current
                current = 0
        elif w in table and table[w] is not None and w not in SCALE_WORDS:
            current += table[w]
        elif w in SCALE_WORDS and not consumed and w in table and table[w] is not None:
            current = SCALE_WORDS[w]
        else:
            break
        consumed = True
        j += 1
    if not consumed:
        return None
    return value + current, j


def extract_quantities(text: str, lang: str) -> list[Quantity]:
    norm = normalize_digits(text)
    lower = norm.lower()
    found: list[Quantity] = []
    taken: list[tuple[int, int]] = []

    def overlaps(a, b):
        return any(not (b <= s or a >= e) for s, e in taken)

    # 1. Digits
    for m in _NUM_RE.finditer(norm):
        raw = m.group(1).replace(",", "")
        value = float(raw + ("." + m.group(2) if m.group(2) else ""))
        suffix = (m.group(3) or "").lower()
        if suffix == "k":
            value *= 1000
        elif suffix == "m":
            value *= 1_000_000
        end, scale_word = m.end(), ""
        sm = _SCALE_AFTER.match(lower, end) if not suffix else None
        if sm:  # "51 lakh", "1.33 million", "4,057.7 crore"
            w = sm.group(1).lower()
            value *= {**EN_SCALES, **SCALE_WORDS}[w]
            end, scale_word = sm.end(), (w if w in LARGE_SCALES else "")
        found.append(Quantity(value, "plain", "", (m.start(), end), text[m.start():end], scale_word))
        taken.append((m.start(), end))

    # 2. Number words
    tokens = list(_word_tokens(norm))
    i = 0
    while i < len(tokens):
        tok, s, e = tokens[i]
        res = None
        if lang == "en":
            res = _parse_english_words(tokens, i)
        elif lang == "roman_urdu":
            res = _parse_table_words(tokens, i, RU_WORDS) or _parse_english_words(tokens, i)
        elif lang == "ur":
            res = _parse_table_words(tokens, i, UR_WORDS)
        elif lang == "ps":
            res = _parse_table_words(tokens, i, PS_WORDS)
        if res and res[1] > i:
            value, j = res
            span = (tokens[i][1], tokens[j - 1][2])
            # Ambiguous single short words ("do" = two / to do) need a unit nearby.
            single = j - i == 1
            ambiguous = single and tokens[i][0].lower() in ("do", "so", "no", "sat", "che", "tin", "ik", "ek", "یو", "سو", "نو", "دو", "ایک", "one", "aik", "a")
            if not overlaps(*span) and (not ambiguous or _unit_follows(lower, span[1])):
                last = tokens[j - 1][0].lower()
                found.append(Quantity(float(value), "plain", "", span, text[span[0]:span[1]], last if last in LARGE_SCALES and j - i > 1 else ""))
                taken.append(span)
            i = j
        else:
            i += 1

    # 3. Classify by neighbouring words
    for q in found:
        after = lower[q.span[1]: q.span[1] + 14].strip()
        before = lower[max(0, q.span[0] - 10): q.span[0]].strip()
        if after.startswith(("%", "٪")) or any(after.startswith(w) for w in PERCENT_WORDS):
            q.kind = "percent"
            end = q.span[1] + (len(lower[q.span[1]:]) - len(lower[q.span[1]:].lstrip()))
            for w in PERCENT_WORDS:
                if lower[end:].startswith(w):
                    q.span = (q.span[0], end + len(w))
                    break
            q.source = text[q.span[0]:q.span[1]]
            continue
        cur = None
        for w, sym in CURRENCY_WORDS.items():
            arabic_word = any("\u0600" <= ch <= "\u06ff" for ch in w)
            # Arabic-script currency words inflect (ډالر -> ډالرو, روپے/روپیہ), so allow suffixes there.
            if before.endswith(w) or after.startswith(w + " ") or after == w or after.startswith(w + ".") or after.startswith(w + ",") or (arabic_word and after.startswith(w)):
                cur = sym
                break
        if cur:
            q.kind, q.unit = "currency", cur
            continue
        if YEAR_RE.fullmatch(q.source.strip()) and 1800 <= q.value <= 2100:
            q.kind = "year"
            continue
        if after.startswith(("x ", "x.", "x,")) or after == "x" or after.startswith(("times", "guna", "گنا", "چنده")):
            q.kind = "multiplier"
            continue
        q.kind = "count"

    # 4. Multiplier words ("doubles", "twice", "dugna")
    for word, mult in MULTIPLIER_WORDS.items():
        for m in re.finditer(r"(?<!\w)" + re.escape(word) + r"(?!\w)", lower):
            if not overlaps(m.start(), m.end()):
                kind = "multiplier" if mult >= 1 else "percent"
                value = mult if mult >= 1 else 50.0
                found.append(Quantity(float(value), kind, "", (m.start(), m.end()), text[m.start():m.end()]))
                taken.append((m.start(), m.end()))
    found.sort(key=lambda q: q.span[0])
    return found


def _unit_follows(lower: str, pos: int) -> bool:
    after = lower[pos: pos + 14].strip()
    return any(after.startswith(w) for w in list(PERCENT_WORDS) + list(CURRENCY_WORDS)) or after.startswith(("saal", "years", "سال", "کال", "din", "دن"))
