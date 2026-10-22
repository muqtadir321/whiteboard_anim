# Urdu support

## Script forms

* A supplied "Urdu" script may be **Urdu (Arabic script)** or **Roman Urdu**. `research.ur_script_mode`
  (`auto` by default, or `--ur-script-mode`) decides which, and `auto` detects it from the letters. The
  script is saved byte for byte (`research/scripts/<form>.original.txt`) and processed as Unicode NFC.
* Nothing is transliterated for the screen. Roman Urdu is converted to Urdu script **only** as the
  input of a text-to-speech voice (`transliteration.roman_to_urdu`); the board and the manifests keep
  your spelling. Roman Urdu spelling is not normalised.
* Pronunciation hints (`--pronunciations` or `pronunciations.yaml` in the package) adjust what the voice reads,
  for example a Roman Urdu spelling to an Urdu-script word, without touching the screen text.
* Urdu output files use the suffix `ur` for either form; `build_info.json` records which one was used.

## Shaping and layout

* HarfBuzz shapes Noto Nastaliq Urdu (Noto Naskh Arabic as a fallback). Lines are laid out right to
  left by a small bidi implementation (`text_shaper.bidi_runs`). Latin words, acronyms, numbers with
  `%`, `Rs`, `$` and separators, Urdu-Indic digits (۰–۹, ٪) and URLs are left-to-right islands, kept in
  reading order. Tests cover mixed Urdu/English, numerals, currencies, parentheses, URLs and acronyms.
* Glyph coverage is a critical QA check. A missing glyph fails the Urdu video with the characters listed.
* Evidence labels stay short. Amounts pop up in their display form ("51 لاکھ" → "Rs 51 lakh").

## Timing

* There is no free, commercially usable Urdu forced aligner here. MMS_FA works for Urdu but is
  CC-BY-NC, so it is off unless allowed. Without it, Urdu timing is phrase level, from pauses or from
  whisper (whose Urdu recognition is weaker). That timing is marked `timing_confidence: low`.
* English and Urdu are timed independently, from their own audio.

## Checks across languages

`storyboard/consistency_report.json` aligns the English and Urdu scripts by paragraph (the scene
groups) and lists differing numbers, years, Latin-script names (for Roman Urdu) and evidence cues, plus
paragraphs where one language has claims and the other does not. If the paragraph counts differ,
numbers are compared over the whole script and the report says so.

Urdu claims are checked against (mostly English) sources by their numbers. Without the multilingual
meaning model they can reach at most `partially_supported`, with the note "meaning not verified across
languages".
