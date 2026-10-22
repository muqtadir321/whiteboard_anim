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

* Evidence stages: headline cards, article screenshot or source card (the quoted words get a
  highlighter sweep), FRED chart drawn from the data with a dated marker, logos. Each is timed to the
  word that names it, stays on screen for at least `min_evidence_seconds` (2.2 s), and carries a
  readable source label.
* Between evidence: hand-drawn doodles and Fluent Emoji for the things being said, drawn stroke by
  stroke with a marker tip that sits exactly on the end of the current stroke, joined by arrows.
* Key amounts pop up in their display form. There are no subtitles unless `--subtitles true`.

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
(`visual_variety`), layout notes. A screenshot whose text would be smaller than `min_text_px` (18) on screen is
replaced by its readable source card, made from the same captured text. Every language
also gets `qa/contact_sheet_<lang>.jpg`; `qa/contact_sheet.jpg` combines them.
