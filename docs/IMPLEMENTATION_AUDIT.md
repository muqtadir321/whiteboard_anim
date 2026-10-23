# Implementation audit (SRS: research-driven bilingual whiteboard explainer)

This file has two parts. **Part A** records the repository as it was found, before any
behaviour change, with the SRS gaps. **Part B** (at the end) records what was implemented, what was
tested and what is still open.

---

## Part A: starting state (2026-10-22)

### Branch and working tree

* `git status`: clean, on `feature/explainer-screenshots`, 1 commit behind `origin`. Fast-forwarded to
  `23a2c81` (only topic folders were added upstream). No user changes needed preserving.
* Remote: `https://github.com/muqtadir321/whiteboard_anim` (**public** repository).
* Other remote branches: `claude/admiring-dirac-ozmnhy`. Nothing was changed there.

### Tests at the start

| Environment | Result |
|---|---|
| Windows 11, Python 3.13.2, FFmpeg (gyan build), no eSpeak NG, no Playwright, after `pip install -r requirements-lock.txt` | 129 passed, **3 failed**, 10 skipped |
| Same, with `PYTHONUTF8=1` | 132 passed, 10 skipped |

The 3 failures were real portability bugs, not test flakes. `engine/pipeline.py` wrote and read the
narration cache (`ensure_ascii=False`) with the platform default encoding (cp1252 on Windows), and
several tests did the same. The fix was a minimal correctness change made during the audit:
explicit `encoding="utf-8"` in `engine/pipeline.py`, `engine/cli.py`, `engine/explainer/prepare.py`
and the tests. After it, the suite gives **132 passed, 10 skipped** without `PYTHONUTF8`.
Skips: 8 need `espeak-ng`, 1 needs Playwright/Chromium, 1 needs `soundfile`.

### Architecture actually found (code paths followed, not docs)

```
engine/cli.py run ──► input_discovery.discover_inputs ──► per language: pipeline.LanguageJob._run
   script ─► audio_validation ─► voice_over (supplied audio | TTS chain + optional clone)
          ─► [forced alignment | whisper alignment | pause-based estimate]
          ─► duration_check ─► scene_planning (rule-based, schemas/scene_plan.schema.json)
          ─► illustration (SVG) ─► synchronisation ─► render
                 whiteboard: whiteboard_renderer (cairo strokes + hand cursor)
                 explainer:  explainer/layout.py + explainer/render.py (screenshots, charts, emoji/doodles)
          ─► outputs (captions, thumbnail, scene_plan.json, sources.txt) ─► quality_assurance
   ─► artifact_manager.finalize ─► reporting.write_reports (generation/quality/licence reports)

engine/explainer/prepare.py (workflow 2-prepare-topic.yml)
   Google News RSS (sources.py) ─► Playwright screenshots + word boxes (capture.py)
   ─► FRED CSV (fred.py) ─► Ollama or extractive draft (draft.py) / auto_tag for own scripts
   ─► Wikidata logos (logos.py) ─► Fluent Emoji [icon] tags (drawings.py, semantic.py)
   ─► input/topics/<date-slug>/{english,urdu}.txt, evidence/manifest.json, notes.md, topic.yaml
   ─► git commit + push, then renders through 0-make-video.yml

engine/research/ (workflow 1-research-viral-topics.yml): trend ranking + script briefs. Independent.
```

Workflows: `0-make-video.yml` (render, also reusable), `1-research-viral-topics.yml`,
`2-prepare-topic.yml` (prepare + commit + render), `3-make-english-urdu.yml` (wrapper), `tests.yml`.

### What works (verified by reading code and by the passing tests)

* Supplied audio wins over TTS (`voice.prefer_supplied_audio`), and is normalised without being cut.
* Per-language isolation: one failed language does not stop the others; statuses are reported per language.
* Script cleaning with Unicode NFC, voice tags and visual cue tags stripped before TTS and captions.
* Urdu/Pashto shaping with HarfBuzz, bidi runs, RTL layout, glyph-coverage QA check.
* Explainer style: real article screenshots with per-word boxes, quote highlighting by fuzzy match,
  FRED charts drawn from downloaded CSV values, headline cards, logos, progressive stroke drawing of
  emoji/doodles, camera pan/zoom, `sources.txt`.
* `{VERIFY: ...}` placeholders block rendering; the draft validator turns numbers not found in the
  evidence into `{VERIFY}`.
* QA: streams, resolution, fps, duration, silence, blank scenes, safe area, glyphs, number preservation.
* Word timing: forced alignment (torchaudio MMS_FA) when installed, otherwise whisper alignment,
  otherwise pause-based estimation (`audio_timing.estimate_timing`, not an equal split). The source of
  the timing is recorded in `metadata.json`.

### Gaps against the SRS (starting state)

| SRS area | Starting state |
|---|---|
| Two workflows: research → package artifact, render consumes package | **Missing.** Prepare commits the topic folder (incl. PNG screenshots) to git and renders in the same run. No research-only artifact, no package hashes, no `research_run_id` input. |
| Job directory `output/<job_id>/…` layout | **Missing.** Output is `output/<language>/video.mp4`; reruns overwrite. |
| Source ledger (`sources.json/csv/txt` with the 18 fields, stable `source_id`) | **Missing.** Only `topic.yaml` source list and `sources.txt` of shown evidence. No IDs. |
| Claim extraction, claim→source map, claim statuses | **Missing.** Only "numbers in draft must appear in evidence" (`draft.validate`). User scripts are tagged but never checked. |
| Financial module (EDGAR/XBRL, issuer releases, normalisation, `financial_claims.*`) | **Missing entirely.** |
| User-supplied source URLs | **Missing.** Only Google News RSS. |
| Screenshot failure → source card | **Missing.** Failed captures are skipped silently. |
| Research HTTP: rate limits, robots, TTL cache with provenance | Partial: `research.sources.fetch` has retries and a UA; no robots check, no cache, no per-host rate limit. |
| Scene manifest with schema (`scene_manifest.json`) | **Missing.** `scene_plan.json` exists (whiteboard plan schema) but has no evidence/source IDs, events or anchors for the explainer timeline. |
| `consistency_report.json` (English ↔ Urdu) | **Missing.** |
| `ur_script_mode`, preserve supplied Urdu/Roman Urdu form | Partial: separate `urdu` and `roman_urdu` language keys; no auto-detection of which form a supplied "Urdu" script is in. |
| `auto_translate_missing_language=false` | **Not honoured**: prepare translates the English draft into Urdu with Ollama by default. |
| Voice mode per language; consent for cloning | Partial: cloning is automatic when `input/voice/reference.*` exists; no consent setting. |
| Restricted models off by default | **Not met**: `voice.allow_noncommercial_models` defaults to `true` (MMS weights are CC-BY-NC-4.0). |
| Pen/hand cursor following strokes | Whiteboard style: yes. **Explainer style: no cursor.** |
| Source labels | Articles: "Source: publisher". Charts: source text inside the chart. News cards / logos: none. |
| Contact sheet, `qa_report.md`, `warnings.json`, `build_info.json`, `upload_metadata.md` | **Missing.** |
| Draft/final render quality names | Partial: `fast/standard/high` quality profiles exist. |
| Docs: THIRD_PARTY_LICENSES, RIGHTS_AND_ATTRIBUTION, RESEARCH_POLICY, … | **Missing** (licences partly in README and `reports/asset_licenses.json`). |

### Privacy and licence blockers found

1. **`input/voice/reference.mp3` (a voice sample) is committed to a public repository.** The SRS
   forbids committing voice samples. Removing it from the repository *and from git history* is
   destructive and was **not** done. It is recorded as a required human step.
2. `allow_noncommercial_models: true` in both the built-in defaults and `input/config.yaml`: Urdu
   narration can use MMS weights (CC-BY-NC-4.0) in a monetised path.
3. Screenshots of third-party articles are committed to a public repository by `2-prepare-topic.yml`.
4. edge-tts (unofficial service) is opt-in only. This is correct.

### Dead or duplicate code noticed

* `engine/research/` (viral topics) and `engine/explainer/sources.py` both talk to Google News. The
  explainer reuses `research.sources.fetch`/`_clean`/`_date`, so there is little real duplication.
* `planner.local_llm` (off by default) is only consulted for whiteboard fallback scenes
  (`scene_planner.py`); the explainer path never calls it.
* `docs/AUDIT.md` describes a much earlier state (80 tests) and is superseded by this file.

---

## Part B: after implementation (2026-10-23)

### Decision: extend, don't rewrite

The renderer, pipeline, explainer layout, capture, FRED, cue and auto-tag code were sound, so they
were reused. New code is in `engine/package/` (contract, research, claims, finance, manifest,
render orchestration). It drives the existing `LanguageJob` through a new optional `package=`
argument, so the classic `run` path and existing topic folders behave as before. `prepare.py` and
`2-prepare-topic.yml` were left in place (they still commit topic folders and use Google News RSS);
the new workflows are the compliant path.

### Changes to existing behaviour (deliberate)

| Change | Why |
|---|---|
| `voice.allow_noncommercial_models` engine default `true` → `false` | SRS: restricted models off by default. `input/config.yaml` still sets `true` (user choice, comment corrected). |
| OmniVoice metadata `Apache-2.0 / commercial yes` → `CC-BY-NC weights / commercial no`, and gated like MMS | Its model card says the pre-trained model is CC-BY-NC. Earlier videos made with it used non-commercial weights. |
| Render path: forced alignment (MMS_FA, CC-BY-NC) off unless `--allow-restricted-models` | SRS licence policy; timing falls back to whisper or labelled phrase timing. |
| Text files read and written as UTF-8 everywhere | Urdu/Pashto failed on Windows. |
| Evidence image paths normalised | Windows 260-character path limit dropped evidence silently. |
| Source cards are not zoomed into | The zoom cropped the card text. |
| `evidence_cues_resolved` is critical in package renders | Missing evidence must not pass quietly where the package promised it. |

### SRS gap table: status now

| SRS area | Status |
|---|---|
| Two workflows (research artifact → render by run id) | **Done**: `0-research-prepare.yml`, `1-render-video.yml`; actionlint clean. Not yet run on GitHub. |
| Job folders, output layout, `build_info.json`, `package.json` hashes | Done; reruns create new folders, and tampered packages are refused (tested). |
| Source ledger with the 18 fields, stable ids | Done (`sources.json/csv/txt`). |
| Claims: atomic sentence claims, 6 statuses, conservative matching, syndication | Done; rule-based. With fastembed, claims without figures can be fully supported. |
| Financial module | Done (XBRL, 8-K EX-99, news cross-checks, Yahoo links only, normalisation, restatements, derived values, reports). **Live EDGAR untested** (needs `SEC_USER_AGENT`). |
| User URLs, providers, robots, rate limits, TTL cache | Done; tested offline. Live: official feeds and FRED worked; GDELT gave 429 after repeated manual use. |
| Screenshot failure → source card | Done and tested; the live run produced real source cards. |
| Charts from real data | Existing FRED path, now with CSV, retrieval time, series id and transformation stored. |
| Scene manifest with schema and validation | Done for the explainer style (the whiteboard style still writes `scene_plan.json` only). |
| `consistency_report.json` | Done (paragraph alignment). |
| Urdu: form detection, kept as supplied, NFC, mixed-script tests | Done. |
| Voice modes, consent, supplied audio first, no audio in outputs | Done; cloning only mocked. |
| Pen cursor following strokes (explainer) | Done (marker tip at the stroke's current point). |
| QA: contact sheet, black/frozen frames, clipping, label duration, source ids | Done. Explainer overlap detection: **not done** (see KNOWN_LIMITATIONS). |
| Docs (11 files) | Done. |

### Tests

`python -m pytest`: **192 passed, 10 skipped** (Windows, Python 3.13; skipped: 8 need espeak-ng, 1
Playwright, 1 soundfile). New: `tests/test_package.py` (56 tests: unit tests plus offline research →
render end to end, covering screenshot failure, unsupported claims, missing TTS, one language failing,
tampered package, cloning without consent, Roman Urdu). Also new: 2 mixed-script Urdu tests and 2
workflow tests. Live checks (not in CI): one research run against real sites, and a draft render
from it.

### Still open (human steps)

1. Remove `input/voice/reference.mp3` from the public repository and its history (see RIGHTS_AND_ATTRIBUTION.md).
2. Set the `SEC_USER_AGENT` secret to enable EDGAR, and optionally `VOICE_REFERENCE_URL` and the audio URL secrets.
4. Decide whether `input/config.yaml` should keep `allow_noncommercial_models: true`.

### Hosted validation (GitHub Actions, branch `feature/explainer-screenshots`)

| Run | Result |
|---|---|
| Tests (push), runs 39843904086 and 39845331153 | success: full suite on Ubuntu, including the eSpeak NG and Chromium tests that are skipped on Windows |
| A: Research & Prepare, 39843937068 and 39845334725 | success: real screenshots (freddiemac.com, census.gov) with readable card alternatives, FRED charts, all six research gates passed; GDELT returned HTTP 429 (recorded, run continued) |
| B: Render Video, 39846066803 | success: `explainer_en.mp4` (14.7 s) and `explainer_ur.mp4` (23.5 s), 1080×1920, 30 fps, H.264/AAC; QA passed for both with no warnings; every evidence scene has a source ID; the chart marks 7.32 % (22 Oct 2026) and 2.98 % (1 Jul 2021) on the narrated figures; `commercial_clearance` not cleared (FRED content rights unknown) |

Found in the hosted output and fixed afterwards: the chart's source line touched the year labels (bottom margin
increased, regression test added); the axis now shows units; Piper's English voice licence is recorded as verified
(MIT voice, public-domain training data).

**Voice quality (not a measured check):** the Urdu narration in run 39846066803 was eSpeak NG, the only local Urdu
voice whose licence allows commercial use. It is robotic; the owner listened and rejected it. Supplied voice-overs
remain the intended path for Urdu.

### Remaining open items (2026-10-23)

* Urdu voice: supply your own voice-over (best, always used first). Non-commercial voices stay off. A commercially
  licensed neural Urdu voice was looked for: AI4Bharat Indic Parler-TTS is Apache-2.0 and lists Urdu, but it is
  gated on Hugging Face (needs an accepted licence and a token) and has not been integrated or tested.
* Old commits still contain `input/voice/reference.mp3` (history rewrite is the owner's decision).
* Live SEC EDGAR needs `SEC_USER_AGENT`.
