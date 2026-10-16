# Repository audit and verification notes

## Phase 1 audit (before implementation)

* `muqtadir321/whiteboard_anim` had no commits, files or workflows. There were no existing
  inputs, rendering, audio or download code, tests or workflows to preserve.
* `3-make-english-urdu.yml` is named in the specification as an existing workflow. It did not
  exist, so it was created as a compatibility workflow that calls the primary workflow.
  Legacy-style input locations (`input/english.txt`, `scripts/english.txt`,
  `input/english_voiceover.mp3`, …) are accepted as aliases by `engine/input_discovery.py`.
* Compatibility risk: none, since there was no prior behaviour.

## Verification performed in the development container

* `python -m pytest`: 80 tests (unit, integration and end-to-end) pass.
* `actionlint` passes on all workflows.
* Real renders from the repository's sample inputs: English and Urdu videos at 1080×1920,
  30 fps, H.264/AAC 48 kHz. All quality checks pass.
* Roman Urdu (narrated through derived Urdu-script text) and Pashto (with supplied or test
  audio) were rendered and checked.

## Not verified here

The container could not reach Hugging Face, PyTorch's package index or Microsoft's speech
service, so these adapters were not run against real models:

* Piper (`engine/tts/piper.py`): the voice download failed as expected and the chain fell
  back to eSpeak NG, which was verified.
* MMS-TTS (`engine/tts/mms.py`), including whether `facebook/mms-tts-pbt` is the right
  Pashto model id.
* faster-whisper (`engine/transcription_manager.py`): covered by mocked tests only.
* edge-tts (`engine/tts/edge.py`): opt-in only.

All of them are detected at run time and fail over cleanly.
