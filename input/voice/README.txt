Your own voice recording (reference for voice conversion) is NOT kept in this repository.

* Locally: put it here as reference.wav / .mp3 / .m4a. Git ignores it (see .gitignore).
* GitHub Actions: store a private download link to the file as the repository secret
  VOICE_REFERENCE_URL. The workflows download it to the runner for that run only; it is never
  committed, cached or uploaded as an artifact.
* 15-60 seconds of clear speech, one speaker, no music, quiet room.
* Only use a voice you own or have written permission to use (docs/VOICE_SETUP.md).
