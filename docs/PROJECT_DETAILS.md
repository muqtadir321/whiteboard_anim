# Whiteboard Animation Engine: Project Details

**Repository:** https://github.com/muqtadir321/whiteboard_anim (branch `claude/admiring-dirac-ozmnhy`, the default branch)
**Language:** Python 3.12 · **Runs on:** GitHub Actions (one click) · **Cost:** free (no paid APIs)
**Output:** vertical 1080×1920 MP4 videos for YouTube Shorts, with hand-drawn whiteboard animation

---

## 1. What the project does

You give it a **script** and/or a **voice-over** in English, Roman Urdu, Urdu or Pashto. For each
language it automatically:

1. Reads the script and works out what each sentence is about (money, salary, taxes, growth, rent,
   people, shops, percentages, cause and effect, steps…).
2. Plans one or more scenes per sentence and picks illustrations that match the meaning
   (charts for growth, money moving between people for a salary, a price tag for inflation…).
3. Draws those illustrations **stroke by stroke with a moving hand**, like a real whiteboard video.
4. Writes the numbers from your script by hand (for example `$100,000`, `AED 750`, `Rs 225,000`)
   **at the moment you say them**.
5. Adds captions in the right script (Urdu and Pashto right to left, correctly joined letters).
6. Combines everything with your voice into a finished MP4 and checks the result before publishing it.

---

## 2. How to use it (one click)

1. Put your files in the repository:

   | What | Where |
   |---|---|
   | English script / voice-over | `input/scripts/english.txt` · `input/audio/english.mp3` |
   | Roman Urdu script / voice-over | `input/scripts/roman_urdu.txt` · `input/audio/roman_urdu.mp3` |
   | Urdu (Urdu script) | `input/scripts/urdu.txt` · `input/audio/urdu.mp3` |
   | Pashto | `input/scripts/pashto.txt` · `input/audio/pashto.mp3` |
   | Your voice for cloning (optional) | `input/voice/reference.mp3` |

   Every file is optional. Any audio format FFmpeg can read works (`.mp3 .wav .m4a .ogg …`).
2. Open **Actions → Generate Whiteboard Video → Run workflow** and click **Run workflow**.
3. Wait about 5–10 minutes. Closing your laptop does not stop the run.
4. Open the finished run, scroll to **Artifacts**, and download **`whiteboard-video-output`**.

### What happens with each combination of inputs

| You supply | Result |
|---|---|
| Script + voice-over | Your recording is used as is. Every word of the script is matched to the moment you say it. |
| Script only | Narration is generated. If `input/voice/reference.*` exists, it is generated **in your voice**. |
| Voice-over only | The audio is transcribed (Whisper) and the transcript is used as the script. |
| Nothing | That language is skipped. |

Voice tags such as `[curious]`, `[pause]` and `[soft]` in scripts are removed automatically. They
never appear in captions.

---

## 3. What you download

```
whiteboard-video-output/
├── english/  roman_urdu/  pashto/  (one folder per language that succeeded)
│   ├── video.mp4            1080×1920, 30 fps, H.264 + AAC 48 kHz
│   ├── thumbnail.png        a fully drawn scene
│   ├── captions.srt / .vtt  upload to YouTube for subtitles
│   ├── transcript.txt
│   ├── scene_plan.json      what was drawn in every scene, and when
│   ├── scenes/*.svg         the vector drawing of every scene
│   ├── metadata.json        voice used, timing method, licences, warnings
│   └── quality_report.json  every quality check and its result
├── reports/                 run summary, quality summary, licence list
└── README.txt
```

---

## 4. How it works inside

```
input files ─► script cleaning + language check ─► voice-over (yours / generated / cloned)
     ─► word timing (forced alignment) ─► scene planning (meaning ► pictures)
     ─► SVG illustrations ─► drawing schedule synced to words ─► hand-drawn frames
     ─► captions + audio ─► FFmpeg MP4 ─► quality checks ─► downloadable artifact
```

| Step | What it does | Main file |
|---|---|---|
| Input discovery | Finds scripts and audio, validates paths, accepts other formats | `engine/input_discovery.py` |
| Script analysis | Cleans text, removes `[tags]`, splits sentences, reads numbers in 4 languages | `engine/script_analyzer.py`, `engine/numbers.py` |
| Language check | Tells English, Roman Urdu, Urdu and Pashto apart | `engine/language_detector.py` |
| Voice | Uses your audio, or generates narration (optionally in your voice) | `engine/voice_manager.py`, `engine/tts/*` |
| Word timing | Finds the exact time of every word | `engine/forced_alignment.py`, `engine/audio_timing.py` |
| Scene planning | Turns meaning into visual actions (draw, write, connect…) | `engine/scene_planner.py`, `engine/concept_mapper.py` |
| Illustrations | Characters, 50+ objects, charts, diagrams, handwriting | `engine/character_generator.py`, `object_generator.py`, `chart_generator.py`, `diagram_generator.py`, `illustration_composer.py` |
| Text | Correct Urdu (Nastaliq) and Pashto (Naskh) shaping, right-to-left | `engine/text_shaper.py` |
| Sync + drawing | Schedules strokes on the spoken words, draws with the hand cursor | `engine/audio_sync.py`, `engine/whiteboard_renderer.py`, `engine/hand_cursor.py` |
| Video | Encodes the MP4 with FFmpeg | `engine/video_assembler.py` |
| Quality checks | Rejects broken or incomplete videos | `engine/quality_checker.py` |
| Reports | Run summary, licences, GitHub summary page | `engine/reporting.py`, `engine/artifact_manager.py` |

### 4.1 Scene planning (how pictures are chosen)

- A **multilingual concept dictionary** (~70 concepts) maps words in English, Roman Urdu, Urdu and
  Pashto to the same ideas: e.g. *salary / tankhwah / تنخواہ / تنخواه* → salary.
- **Numbers are extracted** from words and digits: "one hundred thousand dollars" → `$100,000`,
  "teen hazaar dirham" → `AED 3000`, "دوه لکه پنځه ويشت زره روپۍ" → `Rs 225,000`.
- Each sentence gets a scene type: growth/decline chart, percentage pie, transaction (money moving
  between people), cause → effect, steps, comparison, timeline, problem → solution, person with object…
- If no concept matches, the engine writes the key phrase by hand instead of drawing something
  unrelated.
- Every plan is checked against a JSON Schema (`schemas/scene_plan.schema.json`), and every
  number in the narration must appear in the drawing.

### 4.2 Synchronisation (voice ↔ drawing)

1. **Forced alignment** (Meta MMS_FA via torchaudio + uroman) finds the exact moment of every word.
   On the last run: English 427/427, Roman Urdu 418/418 and Pashto 410/410 words aligned.
2. If that is not available, sentence boundaries are placed on the real pauses in the voice
   (dynamic programming). Tested error: about 0.05 s per sentence.
3. **Drawing scheduler:** every item starts on its spoken word. Numbers and labels are written as
   they are said. If an earlier drawing is still going, it is sped up. Pictures may start up to
   1.2 s early when the pen is free.

### 4.3 Voices

| Situation | Voice used |
|---|---|
| You uploaded a voice-over | **Your recording** (never changed) |
| Script only + reference voice | **Your voice:** OmniVoice for Pashto; Piper (English) / MMS (Urdu) converted to your voice with OpenVoice for the others |
| Script only, no reference | Piper (English), MMS (Urdu), eSpeak NG (fallback) |

---

## 5. Configuration (`input/config.yaml`)

| Setting | Default | Meaning |
|---|---|---|
| `video.quality` | `standard` | `fast` (720×1280), `standard`/`high` (1080×1920) |
| `video.maximum_duration_seconds` | `180` | The run fails rather than cutting your audio if the narration is longer |
| `video.captions_enabled` | `true` | Burn captions into the video |
| `voice.clone.omnivoice_languages` | `[ps]` | Languages cloned with OmniVoice (slow on GitHub; add `en`, `ur` for closer cloning) |
| `voice.clone.reference_text` | `""` | Words spoken in the first ~12 s of your reference (improves cloning) |
| `voice.allow_noncommercial_models` | `false` (engine default; `input/config.yaml` sets `true`) | Allows MMS voices and the OmniVoice model (non-commercial licences) |
| `voice.online_providers` | `[]` | Add `edge_tts` for Microsoft online voices (unofficial service) |
| `timing.forced_alignment` | `true` | Exact word timing of voice-overs |
| `animation.hand_cursor_style` | `hand` | `hand` or `marker` |
| `animation.accent_color` | `#246BCE` | Colour of highlighted numbers and drawings |
| `animation.drawing_density` | `2` | `0` = main drawing only, `1` = + side icons and number highlights, `2` = + headline (board never blank) |

Workflow inputs can override these for a single run: target languages, quality, maximum duration,
captions, and file paths.

---

## 6. Quality checks (every video)

A video is published only if all critical checks pass:

- **File:** readable MP4, H.264 video + AAC audio, 1080×1920, 30 fps, yuv420p, 48 kHz.
- **Duration:** matches the narration and stays within the limit. Audio is not silent and decodes
  without errors.
- **Picture:** no blank scenes, drawings inside the safe area, captions fit, all glyphs available.
- **Meaning:** scene plans are valid, numbers from the script are shown, at least one scene is
  topic-specific, and scenes cover the whole narration.

---

## 7. GitHub workflows

| Workflow | Purpose |
|---|---|
| `0-make-video.yml`: **Generate Whiteboard Video** | Main one-click workflow (17 optional inputs). Installs only the engines your inputs need and uploads the artifact. |
| `1-research-viral-topics.yml`: **Research Viral Finance Topics** | Finds trending finance topics (YouTube, Google Trends, Google News, Reddit, YouTube suggestions) and writes SEO briefs and script outlines. See the README. |
| `3-make-english-urdu.yml`: **Make English + Urdu Video** | Compatibility workflow; runs the same engine. |
| `tests.yml`: **Tests** | Runs the automated tests on every push. |

---

## 7.1 Research workflow

`research/config.yaml` holds the niche, seed keywords, regions, YouTube channels and subreddits.
The engine (`engine/research/`) collects signals, keeps finance-related ones, extracts 2–3 word topic
phrases that recur across items, and scores them: YouTube views per hour 35%, Google Trends 20%,
news 15%, YouTube suggestions 15%, Reddit 10%, number of agreeing sources 5%. Each idea gets a brief
with SEO titles, hooks, description, tags and a `[tag]`-style script outline (numbers left as
`{VERIFY}`), plus an optional local-AI DRAFT script.

## 8. Tests

- **97 automated tests** (`tests/`): input handling, languages, numbers, transliteration, scene
  planning, SVG drawing, captions and right-to-left text, voices, cloning, timing, full video
  renders, workflow files.
- Run locally with `python -m pytest`.

---

## 9. Free components and licences

| Component | Licence | Note |
|---|---|---|
| Engine code | MIT | this repository |
| Fonts: Kalam, Noto Nastaliq Urdu, Noto Naskh Arabic | SIL OFL 1.1 | in `assets/fonts/` |
| Illustrations, hand cursor | original, programmatic | no third-party images |
| FFmpeg, eSpeak NG | LGPL/GPL | output is not restricted |
| Piper (English voice) | GPL-3.0 code; LJ Speech voice (public-domain data) | |
| OmniVoice (voice cloning) | code Apache-2.0; **model weights CC-BY-NC** | Gated like MMS (see docs/THIRD_PARTY_LICENSES.md). |
| OpenVoice V2 (voice conversion) | MIT | |
| faster-whisper / Whisper | MIT | transcription |
| **MMS voices and MMS_FA aligner (Meta)** | **CC-BY-NC-4.0 (non-commercial)** | Recorded in `metadata.json`. Can be disabled. |

`reports/asset_licenses.json` in each download lists exactly what was used in that run.

---

## 10. Known limitations

- Scene understanding is rule-based. Abstract sentences become handwritten key-phrase scenes.
- Fast narration makes the pen draw quickly (often 4–7× its normal pace) so drawings stay on the
  spoken word.
- OmniVoice is slow on GitHub's CPU machines (about 5 minutes per language), so it is used only for
  Pashto by default.
- Roman Urdu ↔ Urdu transliteration is approximate.
- Some models (MMS voices, the MMS_FA aligner) are licensed for non-commercial use. Check before
  monetising.

---

## 11. Current repository content

- **Scripts and voice-overs:** "$100K salary" (English), "Dubai salary" (Roman Urdu), and
  "Dubai salary" (Pashto), each about 2:55.
- **Reference voice:** `input/voice/reference.mp3`.
- **Latest successful run:** https://github.com/muqtadir321/whiteboard_anim/actions/runs/39118876456
  (all three videos generated, with every script word aligned to the recording).
