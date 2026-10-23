# Known limitations

## Not verified against real services or models in development

* **Screenshots**: verified on a GitHub runner (workflow A captured freddiemac.com and census.gov, with
  their readable card alternatives). Pages behind bot checks still fall back to source cards.
* **SEC EDGAR live**: no `SEC_USER_AGENT` was configured, so live EDGAR calls were not made. The XBRL
  logic is tested with synthetic company facts (periods, year-to-date values, restatements, Q4
  derivation, growth). Real filings have more variety (custom tags, units, fiscal calendars), so expect
  some `not_found` for company-specific metrics.
* **GDELT live**: returned HTTP 429 locally and from GitHub's runners (shared IP addresses). The run continues
  with the other providers and records the failure. Give your own `source_urls` for dependable coverage.
* **Voice cloning, Piper, MMS, faster-whisper, forced alignment**: covered by mocked tests only. The
  development machine had no eSpeak NG either (8 tests skipped there; CI installs it).
* **Meaning model (fastembed)**: not installed locally. Claim matching was tested in its rule-based
  mode; with the model, claims without figures can reach `supported`.

## Design limits

* Claim checking is conservative text matching, not understanding. A paraphrase with the same figure is
  often only `partially_supported`. `contradicted` needs very similar wording, so many wrong figures end
  up as `not_found`. Read `source_report.md`.
* Each claim is one sentence. Several facts in a sentence share one status (the finance module does
  split figures).
* Urdu claims are checked through their numbers only (and meaning, if the model is installed).
* Translation only on request, and only with a free local model (Ollama, `qwen2.5:7b` in workflow A). The result is marked
  `machine_translated`, its numbers are compared with the source script, and it must be reviewed. No model means a clear failure.
* No free, commercially usable forced aligner, so the default word timing of supplied voice-overs is
  whisper or pause based (`timing_confidence` medium or low).
* Source cards and headline cards use cairo's built-in Sans/Serif fonts, which do not shape Urdu.
  Source titles are expected to be in Latin script.
* Overlap detection (`no_overlaps`) compares bounding boxes of words, evidence regions, drawings and an estimated
  source-label box. It is a geometric check, not a pixel check, and is a warning, not a failure.
* Region filters GDELT by source country (US, GB, PK, IN, AE, CA, AU, SA); official feeds are not filtered by region.
* `duration` is a target: the narration length is estimated from the words and a warning is raised if they differ by more
  than 25 %. The video always follows the real narration.
* Commercial clearance is almost never automatic: article screenshots are "unknown" by design.
* The older `0-make-video.yml` has 18 `workflow_dispatch` inputs. GitHub accepts this (limit 25), but
  actionlint 1.7.7 reports its older limit of 10.

## Visual composition

* Node labels come from the narration words around the word a drawing was tied to (rules, not language
  understanding). Most read well ("central bank", "interest rates", "2%"), but some are thin ("first",
  "people") when the drawing was matched to a loosely related word.
* Drawings come from a fixed library (doodles + Fluent Emoji). A concept without a fitting drawing gets a
  near match or none; nothing is generated.
* Beat boundaries come from paragraph breaks in the script: a script without blank lines between ideas
  gives boards split only by length (about 14 s).

## Free is not unlimited

GitHub Actions minutes and artifact storage, GDELT and SEC rate limits, website changes, and model
download availability are outside this project's control. No paid API is used or required.
