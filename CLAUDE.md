# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python 3.12 engine that turns scripts and/or voice-overs into hand-drawn whiteboard animation videos (vertical 1080×1920 MP4 for YouTube Shorts) in English, Urdu, Roman Urdu and Pashto. It is designed to run in GitHub Actions (`.github/workflows/0-make-video.yml`) using only free/open-source components. A second, independent tool (`engine/research/`, workflow `1-research-viral-topics.yml`) ranks trending finance topics and writes script briefs.

## Commands

System deps: `ffmpeg espeak-ng libcairo2-dev pkg-config` (macOS: `brew install ffmpeg espeak-ng cairo pkg-config`).

```bash
pip install -r requirements-lock.txt          # core deps (pinned); heavy engines are in requirements-optional.txt
python -m pytest                              # all tests (pytest.ini sets testpaths=tests)
python -m pytest tests/test_scene_planner.py::test_name   # single test
python -m engine.cli run [--english-script PATH --quality fast --target-languages english ...]   # same as the workflow
python -m engine.cli check-config             # validate input/config.yaml + overrides
python -m engine.cli deps                     # which optional engines (whisper, piper, mms, clone, aligner) the inputs need
python -m engine.cli research --topic "..." --english-script en.txt --urdu-script ur.txt   # research package (no video)
python -m engine.cli render --research-dir output/<job_id> --english-audio en.mp3 --render-quality draft
python -m engine.research.cli --config research/config.yaml --out research_output
python -m engine.explainer.prepare --topic "..." --out tmp/topics   # needs playwright + chromium; Ollama optional
python -m engine.cli run --topic-dir tmp/topics/<folder> --quality fast  # explainer style
```

Tests needing `espeak-ng` are skipped when it is missing; the screenshot test in `tests/test_explainer.py` is skipped without Playwright/Chromium (`EXPLAINER_BROWSER_CHANNEL=chrome` runs it with an installed Chrome). Piper, MMS, faster-whisper, OmniVoice/OpenVoice and edge-tts are covered only by mocked tests; they are detected at run time and fall through to the next engine.

## Architecture

Entry point `engine/cli.py` → `input_discovery.discover_inputs` → one `pipeline.LanguageJob` per language → `artifact_manager.finalize` + `reporting.write_reports`. Languages are processed independently: a failure in one is recorded as a `results.Status` and the rest continue (`automation.continue_on_language_failure`).

`LanguageJob._run` (`engine/pipeline.py`) is a sequence of named stages (`self.stage(name, fn)`), each returning a status + warnings:
1. **Script** cleaning (voice tags like `[curious]`, `[pause]` are stripped) and language detection.
2. **Audio**: supplied voice-over is used untouched; otherwise `voice_manager` runs the TTS fallback chain (`engine/tts/*`: online providers opt-in → local `piper`/`mms`/`espeak` in configured order), optionally with voice cloning (`voice_clone.py`: OmniVoice, else OpenVoice timbre conversion) when `input/voice/reference.*` exists. Roman Urdu is never translated; it is transliterated to Urdu script **only** as TTS input (`transliteration.py`).
3. **Transcription / alignment**: audio without script → faster-whisper (`transcription_manager.py`); script + audio → forced alignment (`forced_alignment.py`) for word timestamps. Timing that is not exact is labelled `estimated` (`audio_timing.py`, `audio_sync.py`).
4. **Scene planning** (`scene_planner.py` + `concept_mapper.py` + `numbers.py`): rule-based. `CONCEPTS` in `concept_mapper.py` is a multilingual keyword dictionary; quantities and relation markers select a scene template. Plans are validated against `schemas/scene_plan.schema.json` (`scene_schema.py`) and against the narration — every number must be preserved and none invented. Unrecognised sentences become an honest handwritten key-phrase scene (`fallback: true`), never an unrelated picture.
5. **Composition** (`illustration_composer.py` using `character_generator`, `object_generator`, `chart_generator`, `diagram_generator`, `geometry`): plan → laid-out SVG whose `<path>` elements carry drawing-order metadata. Elements must stay inside the safe area.
6. **Rendering** (`whiteboard_renderer.py`, `svg_draw_order.py`, `hand_cursor.py`, `text_shaper.py`, `caption_renderer.py`): cairo draws the SVG paths progressively, timed to trigger words; text is handwritten glyph by glyph, Urdu/Pashto shaped with HarfBuzz and drawn RTL.
7. **Encode + QA** (`video_assembler.py`, `quality_checker.py`): FFmpeg encode, then checks (streams, resolution, fps, duration, silence, blank scenes, safe area, caption overflow, glyph coverage, number preservation). A video is published only if QA passes.

### Explainer style (`animation.style: explainer`, `engine/explainer/`)

This style is turned on by `--topic-dir input/topics/<name>` (CLI) or `topic_dir` (workflow). A topic folder overrides every language's script/audio path, and `discover_inputs(..., aliases=False)` stops default-location files from being picked up. The stages up to synchronisation are shared with the whiteboard style. `LanguageJob._explainer_scenes` then replaces the whiteboard renderer:
- `cues.py`: visual tags in scripts (`[news]`, `[article N "quote"]`, `[chart ID date]`, `[logo "Name"]`, `[icon name]`). `script_analyzer.normalize_script_with_cues` strips them, with voice tags, so they never reach TTS or captions. Cue `word` indexes into the final `text.split()`. `{VERIFY: ...}` placeholders make the script stage fail.
- `evidence.py`: reads `evidence/manifest.json` (screenshots with per-word boxes, FRED points, logos). `Article.find_quote` fuzzy-matches a quote to word boxes, which become highlight rectangles.
- `layout.py`: cues are timed by word timestamps, not by scene index, because the planner merges short segments. An evidence `Stage` lasts for the sentence that names it (`evidence_hold`), then doodles take over: the planner's draw objects filtered by `is_doodle`, topic-specific ones first, drawn line by line and joined by arrows; doodles wait while a stage is up. No subtitles by default (`write_narration: false`): only key amounts pop up, in their display form ("seven percent" → "7%", "51 lakh" → "Rs 51 lakh"). `keep_board_busy` carries a scene's drawings over until the next scene's first drawing starts, so the board is never blank.
- Meaning-based matching (no per-topic word lists): `semantic.py` (fastembed + multilingual MiniLM, CSLS hub correction; `set_embedder` for tests) and `emoji.py` (Fluent Emoji Flat from the Iconify npm package, own SVG path parser incl. arcs → cairo, draw-on strokes). `drawings.tag_drawings` shortlists emoji per spoken word, lets Ollama pick the literal ones (or a strict threshold without a model) and writes `[icon name]` tags; `draft.semantic_gaps` gives paragraphs without evidence the closest article sentence/chart. Prepare does both; at render time a script without `[icon]` tags gets strict picks (`animation.explainer.drawings: emoji`, `semantic`). `cli deps` reports `semantic=true` so the workflow installs fastembed; the model and emoji JSON are cached under `$WHITEBOARD_CACHE/semantic`.
- `render.py` / `charts.py`: per-frame cairo compositing (stage with camera pan/zoom and highlighter sweeps → words → sprites). Its output streams through the same `video_assembler.assemble`. QA uses `quality_checker.check_explainer` instead of `check_visuals`.
- Preparation (network; `prepare.py`, run by `2-prepare-topic.yml`): `sources.py` (Google News RSS, ranked so official sites come first and paywalls last) → `capture.py` (Playwright; `EXPLAINER_BROWSER_CHANNEL=chrome` uses an installed Chrome) → `fred.py` → `draft.py` (Ollama, or an extractive fallback; `validate` drops unknown evidence and turns unsupported numbers into `{VERIFY}`) → `logos.py` (Wikidata P154/P158 via direct `upload.wikimedia.org` URLs). With `--script-dir` (the user's own scripts) there is no drafting; `draft.auto_tag` inserts cue tags where an article sentence shares numbers/concepts with a script sentence (then by meaning for paragraphs still without evidence), without changing any words. The workflow commits the folder and, unless `render: false`, renders it in the same run (reusable `0-make-video.yml` with `ref` = the pushed commit). Urdu narration should be Urdu script: Roman Urdu goes through rule-based `transliteration.roman_to_urdu`.

### Research package (two-click path, `engine/package/`)

`python -m engine.cli research` (workflow `0-research-prepare.yml`) writes `output/<job_id>/` with `package.json` (sha256 of every file), `research/` (ledger `sources.*`, `claims.json`, financial reports, `extracted_text/`, `screenshots/`, `charts/`, and `topic/` = a normal topic folder whose evidence entries carry `source_id` and `capture`) and `storyboard/`. `python -m engine.cli render --research-dir …` (workflow `1-render-video.yml`) verifies the hashes, copies the package into a new job folder and runs `LanguageJob(..., package={...})` per language; the `package` argument makes source ids, resolved evidence cues and a valid `scene_manifest.json` (`package/manifest.py`, `schemas/scene_manifest.schema.json`) critical QA checks. All research HTTP goes through `package/http.Fetcher` (robots.txt, per-host intervals, retries, TTL cache); never fetch with plain urllib in that path. Claims (`package/claims.py`) and finance (`package/finance.py`, `package/sec.py`) are conservative; scripts are never rewritten. Tests: `tests/test_package.py` with the offline fake web in `tests/package_fixtures.py`. Docs: `docs/HOW_IT_WORKS.md`, `docs/RESEARCH_POLICY.md`, `docs/THIRD_PARTY_LICENSES.md`.

Configuration: defaults live in `engine/config.py`; `input/config.yaml` overrides them and is validated against `schemas/config.schema.json` (unknown keys are rejected — add new keys to both the defaults and the schema). CLI/workflow inputs override the YAML. Explicit input paths always win; an invalid explicit path fails that language rather than falling back.

The video workflow passes inputs via env vars (never interpolated into shell), runs `cli deps` to install only the optional engines the inputs need, then `cli run`. When adding an optional engine, update `cmd_deps` and the corresponding install step in `0-make-video.yml`. `3-make-english-urdu.yml` is a compatibility wrapper around the same engine.

## Content conventions

- Current video inputs live in `input/scripts/<lang>.txt`, `input/audio/<lang>.*`, `input/voice/reference.*`. When starting a new video, move the previous inputs into `input/archive/<YYYY-MM-topic>/` with a short README.txt; the workflow does not read the archive.
- Licensing matters for monetised output: MMS, OmniVoice and MMS_FA weights are CC-BY-NC (voices gated by `voice.allow_noncommercial_models`, default false; the render path also turns forced alignment off unless `--allow-restricted-models`), edge-tts is unofficial and opt-in. `docs/THIRD_PARTY_LICENSES.md` is the register. Any new voice/model must be recorded in `metadata.json` / `reports/asset_licenses.json` via the existing reporting.
- Pashto is a separate language (not Urdu); there is no MMS Pashto model, so Pashto narration relies on OmniVoice cloning or a supplied voice-over.
- `docs/PROJECT_DETAILS.md` and `docs/AUDIT.md` describe what has and has not been verified against real models.
