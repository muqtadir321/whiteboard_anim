# Voice setup

Per language, the render path uses the first option that applies:

1. **Your voice-over** (`--english-audio` / `--urdu-audio`, or the workflow inputs). It is used as it
   is: normalised to 48 kHz PCM with leading/trailing silence trimmed, never time-stretched, cut or
   denoised. WAV, MP3, M4A, FLAC, OGG, Opus and AAC are accepted.
2. **`clone_voice`**: local TTS converted to *your* voice by OpenVoice V2 (MIT code and weights). It
   needs `--voice-reference` (a clean 10–60 s recording of your own voice) and `--voice-consent true`.
3. **`system_tts`**: local TTS that allows commercial use: Piper for English (MIT voice), eSpeak NG for
   English and Urdu (robotic, but licence-safe).
4. Otherwise that language **fails with a clear message** and the other language still renders.

Non-commercial models are **off** unless you pass `--allow-restricted-models true` (render) or set
`voice.allow_noncommercial_models: true` (classic `run`):

| Model | Licence of the weights | Used for |
|---|---|---|
| Meta MMS-TTS (`facebook/mms-tts-urd-script_arabic`, Pashto) | CC-BY-NC-4.0 | natural-sounding Urdu/Pashto TTS |
| k2-fsa OmniVoice | CC-BY-NC (model card); code Apache-2.0 | true zero-shot cloning in en/ur/ps |
| torchaudio MMS_FA | CC-BY-NC-4.0 | exact word timing of a voice-over (forced alignment) |

Without MMS_FA, the word timing of a supplied voice-over comes from faster-whisper (MIT), or from
pauses in the audio. That timing is labelled `phrase_fallback: true`, `timing_confidence: low` in
`audio/alignment.*.json`. Words are never spread evenly over the duration.

**Note on the classic path:** `input/config.yaml` in this repository sets `allow_noncommercial_models: true`
and lists OmniVoice for en/ur/ps. Earlier videos made that way used CC-BY-NC weights (OmniVoice used to be
labelled Apache-2.0 here, which was wrong). Set it to `false` for a monetised channel.

## Privacy

* Use only your own voice, or one you have written permission to use.
* Reference recordings and voice-overs are read in place (locally) or downloaded to the runner's
  temporary folder (Actions), and never copied into a job folder or artifact. The render deletes any
  audio file it finds in a job folder and stops with an error.
* Actions: put a private link in the `VOICE_REFERENCE_URL` secret (all video workflows read it). `input/voice/*` is
  git-ignored. Older commits still hold the earlier recording: see `docs/RIGHTS_AND_ATTRIBUTION.md`.
* Files derived from your voice (OmniVoice's reference clip, OpenVoice's speaker embedding) go to the system temp
  folder, never to `$WHITEBOARD_CACHE`, which the workflows save to the shared Actions cache.
* Pronunciation maps (`--pronunciations`, or `research/topic/pronunciations.yaml` in a package) change only what the
  voice reads: acronyms, names, Roman Urdu spellings.
* Urdu voice cloning has only mocked tests (no real OmniVoice/OpenVoice run in development). Listen to
  the result before publishing.
