# How it works

```
 A: research (engine/package/research.py)                        B: render (engine/package/render.py)
 ─────────────────────────────────────────                       ─────────────────────────────────────
 topic, scripts, URLs ─► validate (urls.py, job.py)               research package ─► verify hashes (job.py)
   │                                                                 │  copy into a new job folder
   ├─ discover: user URLs, official feeds, GDELT  (providers.py)     ├─ per language (independent):
   │    every request: robots, rate limit, retry, cache (http.py)    │    voice mode: supplied audio > clone_voice > system_tts
   ├─ capture: Chromium screenshot + word boxes (explainer/capture)  │    LanguageJob (engine/pipeline.py): script → audio →
   │    else source card from verified metadata (source_card.py)     │      timing → scene plan → explainer layout + render
   ├─ text: extract.py → research/extracted_text/<source_id>.txt     │      (explainer/layout.py, render.py) → encode → QA
   ├─ charts: FRED CSV → research/charts/                            │    scene_manifest (manifest.py) + validate
   ├─ ledger: sources.json/csv/txt, stable source_id (ledger.py)     │    alignment.json, audio_info.json, contact sheet
   ├─ claims: classify + match (claims.py); syndication dedupe       ├─ export: video/, storyboard/, audio/, qa/
   ├─ finance: SEC XBRL, 8-K releases, news (finance.py, sec.py)     └─ build_info.json, sources.txt, upload_metadata.md,
   ├─ cue tags in your scripts, words unchanged (explainer/draft)         commercial clearance (never automatic)
   ├─ draft storyboard + consistency report (consistency.py)
   └─ package.json (sha256 of every file)
```

## Package contract

`package.json` lists every file with its SHA-256. A render refuses a package with a missing or changed
file, never writes into an existing job folder, and renders from its own copy. Voice samples are refused
in a package, and audio files are refused in a render job.

`research/topic/` is the same "topic folder" the explainer renderer already understood
(`<lang>.txt` with cue tags, plus `evidence/manifest.json`). Evidence entries now carry `source_id` and
`capture` (`screenshot` or `source_card`). Older topic folders still work: their source IDs are derived
from the URLs, with the same function the ledger uses.

## What the viewer sees (explainer style)

* Evidence stages: headline cards (the card that best matches the narration stays bright, the others fade back), article screenshot or source card (the quoted words get a
  highlighter sweep), FRED chart drawn from the data with a dated marker, logos. Each is timed to the
  word that names it, stays on screen for at least `min_evidence_seconds` (2.2 s), and carries a
  readable source label.
* Between evidence: **one board per idea (beat)**, built in `engine/explainer/beats.py`. A beat is a paragraph
  of the script (split after about 14 s at a sentence end). Up to 4 nodes (a drawing plus a handwritten label
  taken from the narration words that named it, or the amount as shown, e.g. "2%") appear when their words
  are spoken. Each is drawn stroke by stroke, with a marker tip on the end of the current stroke, and
  connected to the previous node by an arrow routed clear of the labels. Layout is a fixed grid by node
  count (1 large; 2–3 zigzag; 4 as a clockwise 2×2) inside the safe area. The board stays until the idea
  ends. Evidence has priority: nodes wait while a screenshot or chart is up. A beat with nothing drawable gets
  its key phrase handwritten large. The manifest records `beat_id` and `visual_objective`
  (e.g. "interest rates → inflation → room left → economy").
* Key amounts pop up in their display form. Narration is not written on the board.

### Retention (why viewers stay)

* **Hook card** (0 to about 2 s, on screen from the first frame; the voice starts at 0 s): the first sentence, or a
  `[hook "One crash. $80,000."]` tag, large, with amounts in red and underlined, a slow wave pulsing through the words, and Mo
  (rocking and bobbing) (the channel's recurring character, `engine/explainer/mascot.py`)
  pointing at it. Nothing else starts until it has had its moment; evidence that would start during it waits, and
  later evidence is pushed back so each still gets `min_evidence_seconds`. Setting: `animation.explainer.hook`
  (`auto`, `off` or your own text), `hook_seconds`.
* **Word-by-word captions** (`captions: karaoke`): 2–3 large words at a time above the Shorts title
  area, white with a thick black outline, the spoken word in yellow (`engine/explainer/captions.py`; Urdu and Pashto run right to
  left). Drawings stay above the caption band.
* **No dead air**: the whole board breathes slowly (`board_motion`, about 2 %). When nothing is drawn, written or
  shown for 2 s, the main drawing gets a red hand-drawn circle; stretches still longer than `max_static_seconds`
  are reported by QA. A board's main drawing never leads the next board too (three identical shields in a row
  read as a template).
* **Mo** also peeks in, worried, when the narration names a problem (at most twice, never over drawings).
* **Sound**: a pop for amounts and the hook, a whoosh for evidence (drawing is silent), all
  synthesised in `engine/audio_fx.py` (no licences) and mixed about 20 dB under the voice. Music: the first
  track in `input/music/` (ducked under the voice), or `background_music_path: generated` for a quiet pad.
* **Loop ending**: the last 0.6 s cross-fades into the hook frame, so the replay starts seamlessly.

## Timing ("word by word")

Visual events are tied to word timestamps. These come from forced alignment (if allowed), else from
whisper aligned to your script, else from pauses in the audio. Every manifest scene records
`timing_confidence` (high, medium or low), and `alignment.*.json` says which method was used. The
phrase fallback weights words by their length within pause-delimited phrases; it never splits the
duration evenly.

## Scene manifest

`storyboard/scene_manifest.<lang>.json` (schema: `schemas/scene_manifest.schema.json`) describes the
video as rendered:

* per scene: narration, word anchors, claim IDs, visual type, assets (with licence or source ID),
  events (`show_evidence`, `hold_evidence`, `highlight`, `camera_move`, `chart_draw`, `chart_marker`,
  `draw`, `pop`, `write`, `connect`), transitions, layout, timing confidence and notes
* validation, beyond the schema: no negative or reversed times, events inside their scene, scenes in
  order, factual evidence always with a source ID, every source ID listed, assets present

## QA (per language; critical checks fail the language)

Streams, codec, resolution, fps, duration against the narration, silence, decode errors, black frames,
blank scenes, glyph coverage, safe area, numbers kept, evidence with a URL, and in package renders:
evidence with a source ID, evidence cues resolved, scene manifest valid, and source labels on screen
long enough. Warnings: long frozen runs, audio clipping, highlights not found, overlapping boxes (`no_overlaps`),
evidence text too small (`evidence_text_readable`), more than two scenes of the same non-evidence type in a row
(`visual_variety`), the board standing still too long (`max_static_seconds`), layout notes. A screenshot whose text would be smaller than `min_text_px` (18) on screen is
replaced by its readable source card, made from the same captured text. Every language
also gets `qa/contact_sheet_<lang>.jpg`; `qa/contact_sheet.jpg` combines them.

## After the render: publishing kit and long-form

Every run writes `<lang>/publish/`: five title options (60 characters or less, the hook first), a description with
three takeaways from the script, the evidence sources with links, an education disclaimer and hashtags, tags,
a pinned-comment question, the hook card as the Shorts thumbnail, and `upload.json` (YouTube Data API fields,
category Education, private, with the synthetic-voice disclosure set when the voice was generated or cloned).
`python -m engine.cli publish-kit --job output/english --topic "..."` rebuilds it.

`python -m engine.cli compile --videos <job or mp4> ... --title "Series" --out long.mp4` joins finished Shorts
into one 1920x1080 video: each Short centred on a paper board with the chapter list and Mo at the sides,
a 1.5 s chapter card before each, loudness normalised to -14 LUFS, chapter timestamps in its own
publishing kit (YouTube shows chapters from three chapters of 10 s or more).

`python -m engine.cli lint-script <file>` gives retention advice on a script without changing it: hook length,
number or stakes in the first sentence, speaking to "you", one example amount, 20–60 s, an ending that calls
back to the hook, and wording that reads as a financial promise. `prepare` adds the same notes to `notes.md`.
