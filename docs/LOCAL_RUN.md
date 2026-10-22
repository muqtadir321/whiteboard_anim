# Running locally

The workflows call the same CLI, so a local run gives the same outputs.

```bash
# system packages: ffmpeg, espeak-ng, cairo (Ubuntu: apt install ffmpeg espeak-ng libcairo2-dev pkg-config;
# macOS: brew install ffmpeg espeak-ng cairo pkg-config; Windows: FFmpeg on PATH, pycairo wheels include cairo)
pip install -r requirements-lock.txt
pip install playwright==1.48.0 fastembed==0.9.0 && python -m playwright install chromium   # optional: screenshots, meaning matching
pip install faster-whisper==1.2.1 "av<15"                                                    # optional: word timing for voice-overs

# A: research package (no video)
export SEC_USER_AGENT="Your Name you@example.com"      # optional: SEC EDGAR for company figures
python -m engine.cli research --topic "why aren't home prices falling" --language-mode both \
    --english-script my/english.txt --urdu-script my/urdu.txt \
    --source-urls "https://www.census.gov/construction/nrs/index.html" --output output
#   -> output/<job_id>/  (read research/source_report.md)

# B: videos from the package
python -m engine.cli render --research-dir output/<job_id> \
    --english-audio ~/voice/english.m4a --urdu-audio ~/voice/urdu.mp3 \
    --render-quality final --output output
#   -> output/<new job_id>/video/explainer_en.mp4, explainer_ur.mp4, qa/, …
```

Useful flags:

* `render --render-quality draft`: 720×1280 at 24 fps, for a quick check.
* `render --english-voice-mode system_tts` (no audio): Piper or eSpeak NG.
* `render --urdu-voice-mode clone_voice --voice-reference ~/me.wav --voice-consent true`: OpenVoice V2
  converts the TTS voice to yours. OmniVoice and MMS also need `--allow-restricted-models true`
  (non-commercial weights).
* `render --english-script new.txt`: change the words after research; claims are re-checked.
* `research --research-depth deep`, `--label`, `--ur-script-mode urdu|roman_urdu`, `--region PK`.
* `research --auto-translate true --draft-model qwen2.5:7b` (needs `ollama serve`): translate a missing language, marked machine-translated.
* `research --pronunciations words.yaml` (stored in the package) or `render --pronunciations words.yaml`: e.g. `SBP: S B P`.

Exit codes: research 0 (package written) or 2 (bad input); render 0 (all languages passed), 3 (some
failed), 1 (none), 2 (invalid package or input).

A render never repeats research and never changes the package. It copies the package into a new job
folder and checks every file's SHA-256 first, so an edited package is refused. To change a script,
use `--english-script` / `--urdu-script`, or run research again.

The classic commands are unchanged: `python -m engine.cli run …` and `python -m engine.explainer.prepare …`
(see README).

## Measured runtime (this repository's tests, not a benchmark)

Windows 11 laptop CPU, Python 3.13, no GPU. Offline fixture package, 16 s English narration
(supplied tone), final quality 1080×1920 at 30 fps: render 17.8 s in total (frames + encode 7.5 s, QA
5.0 s, audio handling 3.8 s). With the later QA additions and a memory sampler running, the same render
took 37 s, with a **peak of 748 MB** (Python process plus FFmpeg, sampled every 50 ms with psutil). The full
test suite takes about 95 s.
Live research time depends on the sites: one run with a user URL, official feeds and FRED took 64 s,
mostly because of polite per-host waits.
