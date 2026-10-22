# Workflow guide (two clicks)

## A: Research & Prepare (`.github/workflows/0-research-prepare.yml`)

Actions → **A: Research & Prepare** → Run workflow.

| Input | Required | Notes |
|---|---|---|
| topic | yes | Also the search words. |
| language_mode | no (both) | english, urdu or both. |
| english_script / urdu_script | no | A repository path to a `.txt` file, or the text itself with `||` between paragraphs (the input box is one line). Urdu may be Urdu script or Roman Urdu; the form you use is kept. |
| duration | no | Recorded only; length always comes from the narration. |
| region | no (global) | Recorded in the package. |
| source_urls | no | Read first, before anything found by search. |
| research_depth | no (standard) | `deep` reads twice as many documents. |
| label | no | Used in the job id. |
| auto_translate_missing_language | no (false) | `true`: a missing language is translated by a free local model (Ollama `qwen2.5:7b`, started only then), saved as `machine_translated` with a number check. Review it. With `false` a missing language is never written. |

Optional settings: the repository **secret** `SEC_USER_AGENT` ("Your Name you@example.com") turns on SEC
EDGAR, and the repository **variable** `RESEARCH_USER_AGENT` overrides the default User-Agent.

The run uploads the artifact **`research-package-<job_id>`** (kept 14 days). Nothing is committed.
Read `research/source_report.md` first; `research/qa.json` holds the research gates (claims mapped to
sources, canonical URLs, exact quotes, chart values traceable to the CSV, dates vs data periods) and
`research/research.log` the run log.

## B: Render Video (`.github/workflows/1-render-video.yml`)

Actions → **B: Render Video** → Run workflow.

| Input | Notes |
|---|---|
| research_run_id | The number at the end of run A's URL (`…/actions/runs/<id>`). Required. |
| output_languages | both, english or urdu. Urdu renders whichever form the package holds. |
| english_audio / urdu_audio | Supplied narration always wins. Use a repository path, an `https://` link, or `secret` to read the link from the secret `ENGLISH_AUDIO_URL` / `URDU_AUDIO_URL`. |
| english_script / urdu_script | Optional narration that replaces the package script (path or text with `||`). Claims are re-checked against the package's sources. |
| voice_modes | Used for languages without audio: `english=system_tts`, `urdu=clone_voice`, … |
| render_quality | final = 1080×1920 at 30 fps; draft = 720×1280 at 24 fps. |
| options | Comma list: `subtitles`, `voice_consent`, `allow_restricted_models`, `music=<repository path>`, `pronunciations=<repository path .yaml>` (word → how the voice says it; voice only). |

`clone_voice` also needs the secret **`VOICE_REFERENCE_URL`** (a private link to your own recording)
and `voice_consent` in options.

The run uploads **`explainer-video-<job_id>`**:

```
output/<job_id>/
  research/…                      the package, copied and hash-checked
  storyboard/scene_manifest.{en,ur}.json, consistency_report.json, draft_storyboard.*.json
  audio/{en,ur}_audio_info.json, alignment.{en,ur}.json      (no audio files)
  video/explainer_{en,ur}.mp4, thumbnail_{en,ur}.png
  qa/qa_report.json, qa_report.md, contact_sheet.jpg, warnings.json, render.log, failed/<lang>/… (diagnostics)
  build_info.json  sources.txt  upload_metadata.md  package.json
```

The job is green when every requested language passed QA. It is red when any failed, but the
languages that did pass are still in the artifact.

## Getting a voice-over to a hosted runner

A GitHub-hosted runner cannot read a file on your computer. The options, from most private:

1. **Run locally** with the same CLI (`docs/LOCAL_RUN.md`). Nothing leaves your machine.
2. **Secret link**: upload the file somewhere private that can make a download link (expiring if
   possible), store the link as `ENGLISH_AUDIO_URL` / `URDU_AUDIO_URL`, and pass `secret`. Secrets
   are masked in logs.
3. **Direct `https://` link** in the input: visible to anyone who can see the run.
4. **Repository path**: in this **public** repository the file is public.

Artifacts of a public repository can be downloaded by anyone with read access to its Actions. The
render artifact contains the final videos but never the narration files or voice samples.

## Older workflows (kept)

`0-make-video.yml` (classic render, also used by Prepare Topic), `2-prepare-topic.yml` (commits a topic
folder and renders it; uses Google News RSS, see RESEARCH_POLICY.md), `1-research-viral-topics.yml`,
`3-make-english-urdu.yml` and `tests.yml` work as before.
