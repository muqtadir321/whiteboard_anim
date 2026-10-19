# Autonomous Multilingual Whiteboard Animation Engine

Turns scripts and/or voice-overs into original, hand-drawn **whiteboard animation videos**
(vertical 1080×1920 MP4 for YouTube Shorts) in **English, Urdu, Roman Urdu and Pashto**,
entirely inside GitHub Actions with free, open-source components.

For every sentence the engine works out what is being said (people, money, growth,
percentages, causes and effects, processes, comparisons and so on) and plans a scene for it.
It then composes original vector illustrations and draws them stroke by stroke with a moving
hand, in sync with the narration and with captions.

---

## One-click usage

1. Put a script in `input/scripts/` (for example `english.txt`) and/or a voice-over in
   `input/audio/` (for example `english.mp3`). Every file is optional.
2. Open **Actions → Generate Whiteboard Video → Run workflow** and click **Run workflow**.
   You don't need to fill in any field.
3. When the run finishes, open the run page, scroll to **Artifacts** and download
   **`whiteboard-video-output`**. Each language folder contains `video.mp4`.

The repository ships with sample `english.txt`, `urdu.txt` and `pashto.txt` scripts, so the first click
already produces demo videos. Replace or delete them with your own content.

| You supply | What happens |
|---|---|
| Script + voice-over | Your audio is used as is. Scenes are timed to your audio, and the script is used for captions. |
| Script only | Narration is generated with the free voice chain (see below). |
| Voice-over only | The audio is transcribed locally (faster-whisper), then scenes and captions are built from the transcript. |
| Neither | The language is skipped (or, if `automatic_topic_discovery` is on, a topic is taken from RSS). |

Every language is processed independently. If one language fails (for example, no Pashto
voice is available), the others still produce videos. The run summary lists every language
as SUCCESS, PARTIAL_SUCCESS, SKIPPED or FAILED, with the reason.

### Workflow inputs (all optional)

`english_script`, `english_audio`, `urdu_script`, `urdu_audio`, `roman_urdu_script`,
`roman_urdu_audio`, `pashto_script`, `pashto_audio` (paths inside the repository),
`output_format` (`shorts_9_16`), `target_languages` (`auto` or e.g. `english,urdu`),
`generate_missing_narration`, `transcribe_missing_scripts`, `automatic_topic_discovery`,
`animation_quality` (`fast` | `standard` | `high`), `max_duration` (default 180 s),
`captions`, `publish_artifacts`.

An explicit path always wins over the repository default. If an explicit path is invalid,
that language is reported as failed; it is never replaced with other content.
Supported audio formats are anything FFmpeg decodes (`.mp3 .wav .m4a .ogg .flac .opus .webm …`).

The older **Make English + Urdu Video** workflow (`3-make-english-urdu.yml`) is kept for
compatibility and runs the same engine.

---

## Output

```
whiteboard-video-output/
├── english/
│   ├── video.mp4            1080×1920, 30 fps, H.264 + AAC 48 kHz, yuv420p
│   ├── thumbnail.png        a fully drawn scene
│   ├── captions.srt / .vtt
│   ├── transcript.txt
│   ├── scene_plan.json      concepts, visual actions, timing, confidence basis
│   ├── scenes/scene_*.svg   the vector illustration of every scene
│   ├── metadata.json        voice used, timing method, licences, warnings
│   └── quality_report.json  every QA check with its measured value
├── urdu/ …  roman_urdu/ …  pashto/ …   (only languages that succeeded)
├── reports/ generation_report.json, quality_report.json, asset_licenses.json, summary.md
└── README.txt
```

A video is published only after it passes the quality checks: the file must be readable,
have the right streams, resolution, frame rate and duration, contain no silent audio, decode
cleanly, and have no blank scenes. Drawings must stay inside the safe area, captions must not
overflow, glyph coverage must be complete, and every number in the narration must be
preserved in the visuals.

---

## Find viral finance topics (research workflow)

**Actions → Research Viral Finance Topics → Run workflow.** In a few minutes it collects what is
gaining attention right now and ranks finance topics for your next video:

| Source | What it measures | Access |
|---|---|---|
| YouTube channel feeds (`research/config.yaml`) | views per hour of recent uploads | public RSS |
| YouTube Data API (optional `YOUTUBE_API_KEY` secret) | most-viewed uploads of the last days per keyword | free official API |
| Google Trends | searches trending today per region | public RSS |
| Google News | fresh stories per keyword and region | public RSS |
| Reddit | top posts of the week in finance communities | public RSS |
| YouTube search suggestions | what people type (SEO keywords) | suggestion endpoint |

X (Twitter), TikTok and Instagram are not crawled: they have no free official access and their terms
forbid scraping.

Download the **`viral-topic-research`** artifact:

- `report.md`: ranked ideas (High / Medium / Emerging potential) with the evidence behind each.
- `briefs/NN-topic.md`: SEO titles (English + Roman Urdu), opening hooks, description, tags,
  hashtags and a script outline in the same `[tag]` style as `input/scripts`. Numbers are
  `{VERIFY: ...}` placeholders; nothing is invented.
- `ideas.json`, `signals.json`, `sources.json`: raw data and which sources worked.

Turn on **draft_scripts** to have a free local AI model (Qwen 2.5 3B via Ollama) also write a DRAFT
script for the top 3 ideas. Then fill in the checked numbers, save the script to `input/scripts/`,
add your voice-over (optional) and run **Generate Whiteboard Video**.

Edit keywords, regions, channels and subreddits in `research/config.yaml`, or add keywords for one
run with the `niche_keywords` input. To add the YouTube API key: Google Cloud console → enable
"YouTube Data API v3" → create an API key → repository **Settings → Secrets and variables →
Actions → New repository secret** named `YOUTUBE_API_KEY`.

## Languages and voices

| Language | Script handling | Captions / labels | Generated narration (script only) | Transcription (audio only) |
|---|---|---|---|---|
| English | as written | Kalam handwriting font | Piper `en_US-ljspeech-medium` → eSpeak NG `en-us` | faster-whisper `en` |
| Urdu | as written (Arabic script) | Noto Nastaliq Urdu, HarfBuzz shaping, RTL | MMS `urd-script_arabic` → eSpeak NG `ur` | faster-whisper `ur` |
| Roman Urdu | kept in Latin script, **never translated** | Kalam | transliterated to Urdu script **only for the TTS input** (saved as `tts_input_text.txt`) → Urdu voice | Whisper `ur` → rule-based Roman transliteration (approximate, flagged) |
| Pashto | separate language (not Urdu) | Noto Naskh Arabic, HarfBuzz shaping, RTL | OmniVoice in your voice (needs `input/voice/reference.*`); MMS has no Pashto model | faster-whisper `ps` |

What was actually tested while building this project:

* **Tested end to end:** eSpeak NG voices for English and Urdu (and Roman Urdu through Urdu),
  FFmpeg processing, rendering, shaping and RTL captions for all four languages, and the
  pipeline with a supplied voice-over in every language.
* **Not exercised in the development container** (Hugging Face was unreachable there):
  Piper voices, MMS voices and faster-whisper models. Their adapters follow the published
  APIs (`piper-tts` 1.8, `transformers` VITS, `faster-whisper` 1.2) and are covered by mocked
  tests. They are detected at run time, and any failure falls through to the next engine or is
  reported clearly.
* **Pashto TTS:** eSpeak NG has no Pashto voice, so Pashto narration depends on the MMS
  model being downloadable. If it is not, the Pashto job fails with a clear message and the
  other languages continue. **Supplying your own Pashto voice-over always works.**
* The voice chain never uses a voice in the wrong language unless you set
  `voice.allow_wrong_language_fallback: true`, and that is then reported.

### Licences that matter for monetised channels

* **Meta MMS voices are CC-BY-NC-4.0 (non-commercial).** They are used only when
  `voice.allow_noncommercial_models: true` (the default, so that Urdu and Pashto have a neural
  voice), and every video that uses one says so in `metadata.json` and
  `reports/asset_licenses.json`. Set the flag to `false` if you monetise, or supply your own
  audio.
* `edge-tts` (high-quality Urdu, Pashto and English voices) talks to an **unofficial**
  Microsoft service. It is off unless you add it: `voice.online_providers: [edge_tts]`.
* eSpeak NG is GPL-3.0. That covers the program only; the generated audio is yours.
* Fonts are SIL OFL 1.1. Illustrations and the hand cursor are original programmatic artwork.

`reports/asset_licenses.json` lists the fonts, packages, tools and voices used in each run.
Check it yourself before commercial use.

---

## How it works

```
input discovery → script/audio validation → language detection
→ transcription (audio only) / narration generation (script only) / alignment (both)
→ sentence timing → semantic scene planner → SVG illustration composer
→ drawing order + hand cursor timeline → captions + audio sync → FFmpeg encode → QA → artifacts
```

* **Semantic planner** (`engine/scene_planner.py`, `engine/concept_mapper.py`): a
  multilingual concept dictionary (around 70 concepts with English, Roman Urdu, Urdu and
  Pashto keywords), quantity extraction (`fifty dollars` → `$50`, `سل سلنه` → `100%`,
  `doubles` → `2x`) and relation markers (because, so, first/then, than, ki wajah se, ځکه …).
  These pick a scene template: growth or decline chart, percentage pie, transaction (money
  flowing between people, shops or employers), cause → effect, process steps, comparison,
  timeline, problem → solution, person with object, concept cluster, and so on. Each plan is
  validated against JSON Schema (`schemas/scene_plan.schema.json`) and checked against the
  narration: numbers must be preserved and no invented numbers are allowed.
* If no concept is recognised, the scene is an honest **handwritten key-phrase** scene with
  a presenter (marked `fallback: true`, low confidence). No unrelated picture is ever used.
* An optional local LLM (Ollama-compatible, `planner.local_llm`) can suggest concepts for
  sentences the rules miss. It is off by default and never required.
* **Illustrations** are generated from vector primitives with slight hand-drawn wobble:
  characters with poses (point, hold, give, think, celebrate, walk, sit…), more than 50
  objects, bar, line and pie charts, trend arrows, timelines, cycles, decision trees and
  supply/demand curves. Every scene is written as SVG.
* **Renderer** (`engine/whiteboard_renderer.py`) reads those SVG paths and draws them
  progressively with cairo. Drawing time depends on path length, curvature and the time the
  narration allows. Text is handwritten glyph by glyph (right to left for Urdu and Pashto).
  The hand follows the pen tip and glides away when idle. Items are drawn when their trigger
  word is spoken.
* **Timing:** generated narration has exact sentence timing. Supplied audio with a recogniser
  uses word timestamps. Otherwise timing is estimated from sentence length and detected pauses,
  and is labelled `estimated` (it is never presented as word-perfect).

---

## Your own voice (voice cloning)

Put a recording of your own voice at `input/voice/reference.wav` (`.mp3`/`.m4a` also work)
and run the workflow. Every narration the engine generates from a script is then spoken in
your voice:

* **OmniVoice** (k2-fsa, Apache-2.0, 600+ languages) speaks English, Urdu (Roman Urdu is
  read via derived Urdu text) and Pashto directly in the reference voice. It needs only
  3–10 seconds of clean speech; the engine takes a 12-second excerpt from the start of your
  file. This is also the only free voice that covers **Pashto**, because Meta MMS has no
  Pashto model.
* If OmniVoice can't be installed or loaded, the narration falls back to Piper/MMS/eSpeak NG
  converted to your timbre with **OpenVoice V2** (MIT). If that fails too, the normal voice
  is used.
* Voice-overs you upload to `input/audio/` are never changed.
* `metadata.json` records which engine produced the voice and whether cloning was applied.
* The reference file lives in your repository. If the repository is public, so is your
  voice sample.
* Optional: put the exact words spoken in the first 12 seconds of the reference in
  `voice.clone.reference_text` (in `input/config.yaml`). This skips automatic transcription
  and improves cloning.
* Both cloning engines are first exercised on GitHub (the development container could not
  download their models).

## Configuration

Edit `input/config.yaml` (validated against `schemas/config.schema.json`; unknown keys are
rejected). Common settings:

```yaml
voice:
  online_providers: []              # add edge_tts to opt in
  local_engines: [piper, mms, espeak]
  allow_noncommercial_models: true
video:
  quality: standard                 # fast = 720×1280@24, standard/high = 1080×1920@30
  maximum_duration_seconds: 180
  allow_longer_than_maximum: false  # narration is never cut; the run fails instead
animation:
  hand_cursor_style: hand           # or marker
  accent_color: "#246BCE"
automation:
  automatic_topic_discovery: false
```

All defaults are listed in `engine/config.py`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No suitable free voice available for 'ps'" | Upload a Pashto voice-over, or make sure `allow_noncommercial_models` is true and Hugging Face is reachable from the runner. |
| "no speech-recognition engine is installed" | The workflow installs faster-whisper automatically when an audio file has no script. Check the "Install speech recognition" step log. |
| "above the 180s limit" | Shorten the script, raise `max_duration`, or set `video.allow_longer_than_maximum: true`. |
| Some scenes are text-only | The sentence used no concept the dictionary knows. Add keywords to `CONCEPTS` in `engine/concept_mapper.py`. |
| Captions or labels in the wrong script | Check that the file is in the right language field. Detection warnings are in `metadata.json`. |
| Run is slow | Use `animation_quality: fast`. Rendering a 60 s video at 1080×1920 takes roughly 1–2 minutes on a GitHub runner. |

## Local development

```bash
sudo apt-get install ffmpeg espeak-ng libcairo2-dev pkg-config
pip install -r requirements-lock.txt
python -m pytest            # unit, integration and end-to-end tests
python -m engine.cli run    # same as the workflow
```

## Known limitations

* Scene understanding is rule-based (concept dictionary and templates). It covers everyday
  money, business, work, education, health and process topics well. Abstract sentences fall
  back to handwritten key phrases.
* eSpeak NG voices sound robotic. For natural narration, supply your own recording or enable
  Piper (English), MMS or edge-tts.
* Roman Urdu ↔ Urdu transliteration is rule-based with a lexicon of common words, so it is
  approximate.
* Free third-party services and model hosts can change. The engine always falls back to local
  engines and reports what happened.
