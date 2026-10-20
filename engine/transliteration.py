"""Rule-based Roman Urdu <-> Urdu script transliteration.

Used only to create *derived* representations:
  * Roman Urdu script -> Urdu script, when a TTS adapter needs Urdu script.
  * Urdu-script transcript -> Roman Urdu, when Roman Urdu audio was transcribed
    by a model that outputs Urdu script.
The canonical text is always kept separately. Output is approximate and is
reported as such.
"""

from __future__ import annotations

import re

# High-frequency words: exact, hand-checked spellings.
LEXICON = {
    "hai": "ہے", "hain": "ہیں", "ka": "کا", "ki": "کی", "ke": "کے", "ko": "کو", "se": "سے", "mein": "میں", "main": "میں",
    "mai": "میں", "aur": "اور", "nahi": "نہیں", "nahin": "نہیں", "kya": "کیا", "yeh": "یہ", "ye": "یہ", "woh": "وہ", "wo": "وہ",
    "aap": "آپ", "ap": "آپ", "hum": "ہم", "tum": "تم", "jo": "جو", "bhi": "بھی", "tha": "تھا", "thi": "تھی", "thay": "تھے",
    "the": "تھے", "ho": "ہو", "hota": "ہوتا", "hoti": "ہوتی", "hotay": "ہوتے", "kar": "کر", "karna": "کرنا", "karte": "کرتے",
    "kiya": "کیا", "liye": "لیے", "lye": "لیے", "sath": "ساتھ", "saath": "ساتھ", "agar": "اگر", "lekin": "لیکن", "magar": "مگر",
    "phir": "پھر", "pehle": "پہلے", "paisa": "پیسہ", "paise": "پیسے", "apna": "اپنا", "apne": "اپنے", "apni": "اپنی",
    "koi": "کوئی", "kuch": "کچھ", "bohat": "بہت", "bahut": "بہت", "zyada": "زیادہ", "ziada": "زیادہ", "kam": "کم",
    "din": "دن", "saal": "سال", "waqt": "وقت", "log": "لوگ", "logon": "لوگوں", "shakhs": "شخص", "ne": "نے",
    "gaya": "گیا", "gayi": "گئی", "gaye": "گئے", "raha": "رہا", "rahi": "رہی", "rahe": "رہے", "sakta": "سکتا", "sakte": "سکتے",
    "chahiye": "چاہیے", "wala": "والا", "wali": "والی", "wale": "والے", "jab": "جب", "tab": "تب", "kyun": "کیوں",
    "kyunke": "کیونکہ", "kyunki": "کیونکہ", "isliye": "اس لیے", "matlab": "مطلب", "acha": "اچھا", "achha": "اچھا",
    "theek": "ٹھیک", "hamesha": "ہمیشہ", "aik": "ایک", "ek": "ایک", "do": "دو", "teen": "تین", "char": "چار", "panch": "پانچ",
    "das": "دس", "bees": "بیس", "pachas": "پچاس", "sau": "سو", "hazar": "ہزار", "hazaar": "ہزار", "lakh": "لاکھ",
    "crore": "کروڑ", "dollar": "ڈالر", "dollars": "ڈالر", "rupay": "روپے", "rupaye": "روپے", "rupees": "روپے",
    "fisad": "فیصد", "feesad": "فیصد", "percent": "فیصد", "barh": "بڑھ", "barhta": "بڑھتا", "barhti": "بڑھتی",
    "kamai": "کمائی", "kamana": "کمانا", "bachat": "بچت", "qarz": "قرض", "bank": "بینک", "dukaan": "دکان", "dukan": "دکان",
    "ghar": "گھر", "kaam": "کام", "naukri": "نوکری", "tankhwah": "تنخواہ", "karobar": "کاروبار", "karobaar": "کاروبار",
    "sarmaya": "سرمایہ", "sarmayakari": "سرمایہ کاری", "munafa": "منافع", "nuqsan": "نقصان", "nuksan": "نقصان",
    "mehnat": "محنت", "zindagi": "زندگی", "dunya": "دنیا", "duniya": "دنیا", "insan": "انسان", "bacha": "بچہ", "bache": "بچے",
    "school": "اسکول", "kitab": "کتاب", "mobile": "موبائل", "phone": "فون", "computer": "کمپیوٹر", "gaari": "گاڑی", "gari": "گاڑی",
    "pani": "پانی", "khana": "کھانا", "sehat": "صحت", "time": "ٹائم", "business": "بزنس", "invest": "انویسٹ",
    "lagaye": "لگائے", "lagaya": "لگایا", "lagana": "لگانا", "dekho": "دیکھو", "dekhen": "دیکھیں", "socho": "سوچو",
    "samjho": "سمجھو", "pata": "پتا", "aaj": "آج", "kal": "کل", "ab": "اب", "abhi": "ابھی", "sirf": "صرف", "har": "ہر",
    "sab": "سب", "baat": "بات", "tarah": "طرح", "mushkil": "مشکل", "hal": "حل", "masla": "مسئلہ", "wajah": "وجہ",
    "nateeja": "نتیجہ", "dugna": "دگنا", "gunna": "گنا", "guna": "گنا", "hoga": "ہوگا", "hogi": "ہوگی", "honge": "ہوں گے",
    "garib": "غریب", "gareeb": "غریب", "ghareeb": "غریب", "ameer": "امیر", "amir": "امیر", "kyu": "کیوں", "kyon": "کیوں",
    "rehta": "رہتا", "rehti": "رہتی", "rehte": "رہتے", "saat": "سات", "chhe": "چھ", "chay": "چھ", "chaar": "چار", "paanch": "پانچ",
    "aadat": "عادت", "aadatein": "عادتیں", "aadaten": "عادتیں", "aadatain": "عادتیں", "khatam": "ختم", "deti": "دیتی", "deta": "دیتا",
    "dete": "دیتے", "kabhi": "کبھی", "bachega": "بچے گا", "bachta": "بچتا", "bachate": "بچاتے", "bachana": "بچانا", "bache": "بچے",
    "number": "نمبر", "taqreeban": "تقریباً", "karta": "کرتا", "cheez": "چیز", "qist": "قسط", "qiston": "قسطوں", "par": "پر",
    "lena": "لینا", "bike": "بائیک", "tv": "ٹی وی", "kahenge": "کہیں گے", "dikhawe": "دکھاوے", "dikhawa": "دکھاوا", "kharcha": "خرچہ",
    "kharche": "خرچے", "kharch": "خرچ", "chahe": "چاہے", "jeb": "جیب", "khaali": "خالی", "khali": "خالی", "hi": "ہی", "to": "تو",
    "roz": "روز", "mahine": "مہینے", "mahina": "مہینہ", "aakhir": "آخر", "sach": "سچ", "salary": "سیلری", "badhi": "بڑھی",
    "badh": "بڑھ", "barhi": "بڑھی", "ya": "یا", "aisi": "ایسی", "aisa": "ایسا", "jagah": "جگہ", "jahan": "جہاں", "emergency": "ایمرجنسی",
    "fund": "فنڈ", "seedha": "سیدھا", "hafte": "ہفتے", "hafta": "ہفتہ", "badal": "بدل", "lein": "لیں", "len": "لیں", "tar": "تر",
    "aage": "آگے", "nikal": "نکل", "jayenge": "جائیں گے", "jaenge": "جائیں گے", "batayein": "بتائیں", "batain": "بتائیں",
    "kitni": "کتنی", "kitna": "کتنا", "comment": "کمنٹ", "likhein": "لکھیں", "likhen": "لکھیں", "kahan": "کہاں", "jaati": "جاتی",
    "psychology": "سائیکالوجی", "of": "آف", "money": "منی", "morgan": "مورگن", "housel": "ہاؤزل", "warren": "وارن",
    "buffett": "بفٹ", "minute": "منٹ", "minat": "منٹ", "iska": "اس کا", "uska": "اس کا", "pehla": "پہلا", "pehli": "پہلی",
    "hairaan": "حیران", "hairan": "حیران", "dega": "دے گا", "degi": "دے گی", "khel": "کھیل", "padhe": "پڑھے", "parhe": "پڑھے",
    "likhe": "لکھے", "aam": "عام", "taaqat": "طاقت", "taqat": "طاقت", "unki": "ان کی", "unka": "ان کا", "unke": "ان کے",
    "painsath": "پینسٹھ", "umar": "عمر", "umr": "عمر", "baad": "بعد", "unhone": "انہوں نے", "unhon": "انہوں", "shuru": "شروع",
    "asal": "اصل", "asli": "اصلی", "nazar": "نظر", "mehngi": "مہنگی", "mehnga": "مہنگا", "gaadi": "گاڑی", "hua": "ہوا",
    "hui": "ہوئی", "samjhein": "سمجھیں", "samjhen": "سمجھیں", "kehta": "کہتا", "kehte": "کہتے", "aksar": "اکثر", "kho": "کھو",
    "bada": "بڑا", "badi": "بڑی", "bade": "بڑے", "rehna": "رہنا", "sambhaalna": "سنبھالنا", "sambhalna": "سنبھالنا",
    "usool": "اصول", "usul": "اصول", "kamaana": "کمانا", "hunar": "ہنر", "kitaab": "کتاب", "mashhoor": "مشہور", "sabaq": "سبق",
    "daulat": "دولت", "ameer": "امیر", "kangaal": "کنگال", "jaate": "جاتے", "jaldi": "جلدی", "ruke": "رکے", "raaz": "راز",
    "us": "اس", "is": "اس", "un": "ان", "in": "ان", "ki": "کی", "jaata": "جاتا", "jata": "جاتا", "jati": "جاتی",
}
REVERSE_LEXICON = {}
for _rom, _ur in LEXICON.items():
    REVERSE_LEXICON.setdefault(_ur, _rom)

DIGRAPHS = [
    ("kh", "خ"), ("gh", "غ"), ("sh", "ش"), ("ch", "چ"), ("th", "تھ"), ("ph", "پھ"), ("bh", "بھ"), ("dh", "دھ"),
    ("jh", "جھ"), ("rh", "ڑھ"), ("aa", "ا"), ("ee", "ی"), ("oo", "و"), ("ou", "و"), ("au", "و"), ("ai", "ی"), ("ay", "ے"),
]
SINGLE = {
    "b": "ب", "p": "پ", "t": "ت", "j": "ج", "d": "د", "r": "ر", "z": "ز", "s": "س", "f": "ف", "q": "ق", "k": "ک",
    "g": "گ", "l": "ل", "m": "م", "n": "ن", "w": "و", "v": "و", "h": "ہ", "y": "ی", "c": "ک", "x": "کس", "o": "و",
}


def _roman_word_to_urdu(word: str) -> str:
    low = word.lower()
    if low in LEXICON:
        return LEXICON[low]
    if not re.fullmatch(r"[a-z']+", low):
        return word
    out = []
    i = 0
    n = len(low)
    while i < n:
        two = low[i:i + 2]
        ch = low[i]
        matched = False
        for dg, ur in DIGRAPHS:
            if two == dg:
                if dg == "ay" and i + 2 < n:
                    ur = "ی"
                out.append(ur if not (i == 0 and dg in ("aa",)) else "آ")
                i += 2
                matched = True
                break
        if matched:
            continue
        if ch == "a":
            if i == 0:
                out.append("ا")
            elif i == n - 1:
                out.append("ا")
        elif ch in "iu":
            if i == 0:
                out.append("ا")
            elif ch == "i" and i == n - 1:
                out.append("ی")
        elif ch == "e":
            if i == 0:
                out.append("ا")
            out.append("ے" if i == n - 1 else "ی")
        elif ch == "'":
            pass
        else:
            out.append(SINGLE.get(ch, ""))
        i += 1
    return "".join(out) or word


def roman_to_urdu(text: str) -> str:
    def repl(m: re.Match) -> str:
        return _roman_word_to_urdu(m.group(0))

    converted = re.sub(r"[A-Za-z']+", repl, text)
    return converted.replace(".", "۔").replace("?", "؟").replace(",", "،")


UR_TO_ROMAN = {
    "ا": "a", "آ": "aa", "ب": "b", "پ": "p", "ت": "t", "ٹ": "t", "ث": "s", "ج": "j", "چ": "ch", "ح": "h", "خ": "kh",
    "د": "d", "ڈ": "d", "ذ": "z", "ر": "r", "ڑ": "r", "ز": "z", "ژ": "zh", "س": "s", "ش": "sh", "ص": "s", "ض": "z",
    "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f", "ق": "q", "ک": "k", "گ": "g", "ل": "l", "م": "m", "ن": "n",
    "ں": "n", "و": "o", "ہ": "h", "ھ": "h", "ء": "", "ئ": "i", "ی": "i", "ے": "e", "ۓ": "e", "ۃ": "t", "ي": "i", "ك": "k", "ه": "h",
}


def _urdu_word_to_roman(word: str) -> str:
    if word in REVERSE_LEXICON:
        return REVERSE_LEXICON[word]
    out = []
    for i, ch in enumerate(word):
        r = UR_TO_ROMAN.get(ch)
        if r is None:
            out.append(ch)
            continue
        if ch == "ہ" and i == len(word) - 1 and i > 0:
            r = "a"
        if ch == "و" and i == 0:
            r = "w"
        if ch == "ی" and i == 0:
            r = "y"
        out.append(r)
    return "".join(out)


def urdu_to_roman(text: str) -> str:
    converted = re.sub(r"[؀-ۿ]+", lambda m: _urdu_word_to_roman(m.group(0)), text)
    converted = converted.replace("۔", ".").replace("؟", "?").replace("،", ",")
    # Capitalise sentence starts for readability.
    return re.sub(r"(^|[.?!]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), converted)
