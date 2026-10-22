# Rights and attribution

This is how the pipeline handles other people's work. It is a best effort, not legal advice; you
decide what to publish.

## What appears in a video

| Element | Where it comes from | How it is handled |
|---|---|---|
| Article screenshot | the publisher's page, captured by headless Chromium when the page is reachable and robots.txt allows it | Cropped from the headline down; only the quoted words are highlighted. Labelled `Source: <publisher> · <date> · <domain>`. Rights: **unknown**. |
| Source card | drawn by this code when a page cannot be captured | Shows only verified metadata (publisher, headline, date, domain) and, if the page text was read, a short exact excerpt in quotation marks (≤ 60 words). Says "Source card" on the card and in the label, so it can never be taken for a screenshot. |
| Headline cards | drawn from discovered results (our layout, not the publisher's) | Labelled "Headlines found during research". |
| Data chart | drawn from downloaded values (FRED CSV) | Shows the source and series ID. Values are never invented: no data, no chart. |
| Logos | Wikidata/Wikimedia files (older Prepare Topic workflow only) | "Logos shown for identification only". Prefer a plain publisher label when unsure. |
| Drawings | original doodles (this repository) and Fluent Emoji (MIT, Microsoft) | Recorded per scene in `scene_manifest.*.json` (`assets[].license`). |
| Narration | your voice-over, or local TTS / voice conversion | See `docs/VOICE_SETUP.md`. Non-commercial models are off by default. |
| Music | none by default | Only a file you supply (`--music`), and only if you have the rights. |

## Attribution in the outputs

* `sources.txt` (job root): every source shown on screen, with its ID and full URL.
* `research/sources.txt`, `.csv`, `.json`: everything consulted, including documents that were blocked
  or only seen as metadata.
* `upload_metadata.md`: a description with the source list and the line "no publisher endorses this
  video". Nothing is uploaded automatically.
* The research step never implies endorsement and never presents a source card as a screenshot.

## Commercial clearance

`build_info.json` → `commercial_clearance` is `cleared: true` only when:

* every narration voice is your supplied audio or a model whose licence allows commercial use,
* no restricted word-timing model (MMS_FA) was used, and
* every source shown on screen has `commercial_status: approved` (U.S. federal works).

Article screenshots and quotes are "unknown" by design, so most explainer videos come out **not
cleared**. The blocking items are listed so you can judge them (commentary, fair use or fair dealing,
licences you hold). The pipeline never marks them cleared for you.

## Voice

Clone only your own voice, or a voice you have **written** permission to use. The render path refuses
`clone_voice` without `--voice-consent true` (or `voice.clone.consent_confirmed`). Reference recordings
and voice-overs are read from where they are and never copied into job folders or artifacts.

**This repository:** `input/voice/reference.mp3` was committed to a public repository. It is now untracked
(`git rm --cached`; your local copy stays) and `input/voice/*` is git-ignored. The workflows read the
reference from the `VOICE_REFERENCE_URL` secret instead. Copies remain in **earlier commits**: removing
them needs a history rewrite (for example `git filter-repo --path input/voice/reference.mp3 --invert-paths`
followed by a force-push). That is destructive for everyone who has cloned the repository, so it is left
to you.
