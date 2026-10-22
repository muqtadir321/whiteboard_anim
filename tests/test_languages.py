from engine.language_detector import detect_language
from engine.numbers import extract_quantities
from engine.script_analyzer import analyze_script, split_sentences
from engine.transliteration import roman_to_urdu, urdu_to_roman


def test_english_detected():
    assert detect_language("The company grows when people buy more products.").code == "en"


def test_roman_urdu_not_treated_as_english():
    d = detect_language("Aaj hum baat karenge ke paisa kaise barhta hai aur log bachat kyun nahi karte.")
    assert d.code == "roman_urdu"


def test_urdu_vs_pashto():
    assert detect_language("مہنگائی کی وجہ سے لوگ کم بچت کرتے ہیں۔").code == "ur"
    assert detect_language("د وخت په تېرېدو یې پیسې زیاتې شوې او ګټه یې ډېره ده.").code == "ps"


def test_ambiguous_prefers_input_field():
    assert detect_language("Investment", expected="roman_urdu").code == "roman_urdu"


def test_sentence_split_keeps_decimals_and_abbreviations():
    s = [t for t, _, _ in split_sentences("Dr. Khan earned 3.5 million. Then he saved it!")]
    assert s == ["Dr. Khan earned 3.5 million.", "Then he saved it!"]
    u = [t for t, _, _ in split_sentences("پہلا جملہ۔ دوسرا جملہ؟")]
    assert len(u) == 2


def test_quantities_multilingual():
    en = extract_quantities("A person starts investing with fifty dollars and gains one hundred percent.", "en")
    assert [q.display for q in en] == ["$50", "100%"]
    ru = extract_quantities("Paisa 100 fisad barh gaya aur pachas rupay bache.", "roman_urdu")
    assert "100%" in [q.display for q in ru] and "Rs 50" in [q.display for q in ru]
    ur = extract_quantities("منافع 25 فیصد ہوا اور اس نے پچاس ڈالر لگائے۔", "ur")
    assert {"25%", "$50"} <= {q.display for q in ur}
    ps = extract_quantities("ګټه یې سل سلنه شوه او ۵۰ ډالر یې واچول.", "ps")
    assert {"100%", "$50"} <= {q.display for q in ps}
    mult = extract_quantities("Her savings doubled.", "en")
    assert mult[0].display == "2x"


def test_analyze_script_attaches_quantities():
    s = analyze_script("Sales rose 35% in 2020.", "en")
    kinds = {q.kind for q in s[0].quantities}
    assert {"percent", "year"} <= kinds


def test_transliteration_round_trip_common_words():
    assert roman_to_urdu("Aik shakhs ne pachas dollar lagaye.") == "ایک شخص نے پچاس ڈالر لگائے۔"
    assert urdu_to_roman("یہ ہے").lower() == "yeh hai"


def test_mixed_urdu_english_urls_numbers_and_acronyms_stay_readable():
    """URLs, acronyms, amounts and dates inside Urdu are left-to-right islands in an RTL line."""
    from engine.text_shaper import bidi_runs, shape_line

    text = "اسٹیٹ بینک (SBP) کے مطابق شرح سود 11% ہے، دیکھیں www.sbp.org.pk/press 2026 میں Rs 1,250 ارب"
    runs = bidi_runs(text, True)
    ltr = [r.text.strip() for r in runs if not r.rtl]
    assert "SBP" in ltr and "11%" in ltr and "Rs 1,250" in ltr
    assert any(r.startswith("www.sbp.org.pk/press") for r in ltr)  # the URL is one run, in reading order
    assert runs[-1].rtl and runs[-1].text.startswith("اسٹیٹ")  # visual order: the first word is on the right
    assert not shape_line(text, 60, "ur").missing_chars
    eastern = bidi_runs("شرح ۱۱٪ ہے", True)
    assert any(not r.rtl and "۱۱" in r.text for r in eastern)


def test_urdu_script_is_kept_as_supplied_and_nfc_normalised():
    import unicodedata

    from engine.package.research import urdu_form
    from engine.script_analyzer import normalize_script

    decomposed = "آ"  # alef + madda above (NFD)
    assert unicodedata.normalize("NFC", decomposed) in normalize_script(decomposed + "ج کی قیمت 51 لاکھ روپے ہے۔")
    assert urdu_form("Aaj dollar 277 rupay ka hai aur log pareshan hain.", "auto")[0] == "roman_urdu"
