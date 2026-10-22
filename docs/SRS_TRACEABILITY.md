# SRS traceability: requirement → implementation → evidence

Status: **Met**, **Partly met** (what is missing is said), **Not met**, or **Human step** (deliberately left to
the owner). "Test" names are in `tests/`. "Live" means checked against real services, not only fixtures.
Section numbers follow `WHITEBOARD_VIDEO_PIPELINE_SRS.md`.

## §0 Instructions to the agent

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 0.1 | git status / branch / remote before editing | Met | IMPLEMENTATION_AUDIT Part A |
| 0.2 | fetch, use `feature/explainer-screenshots`, never main | Met | all commits on that branch (fast-forwarded first) |
| 0.3 | preserve user changes, no destructive commands | Met | tree was clean; voice file untracked with `--cached` (local copy kept); no history rewrite |
| 0.4 | inspect the whole repository, follow code paths | Met | Audit Part A: architecture traced from `cli.py`/`pipeline.py`/`explainer/*` |
| 0.5 | run tests first; `docs/IMPLEMENTATION_AUDIT.md` | Met | baseline 129/3/10 (Windows encoding bugs), fixed → 132 |
| 0.6 | reuse, no wholesale rewrite | Met | new `engine/package/` drives the existing `LanguageJob`; Audit Part B "Decision" |
| 0.7 | no questions; safe fallbacks, documented | Met | KNOWN_LIMITATIONS |
| 0.8 | never claim untested features | Met | KNOWN_LIMITATIONS "Not verified" section |
| 0.9 | no upload to platforms; synthetic fixtures in tests | Met | `upload_metadata.md` only; `tests/package_fixtures.py` |
| 0.10 | continue until done or blocker documented | Met | this file |

## §1–2 Product and two workflows

| Requirement | Status | Evidence |
|---|---|---|
| Workflow A `0-research-prepare.yml` with topic, language_mode, scripts, duration, region, URLs, depth, label, auto_translate | Met | workflow file; `test_two_click_workflows_are_safe`, `test_research_workflow_args_match_cli`; actionlint clean; live run (see §10 validation) |
| A: validate input, unique job dir | Met | `ResearchInputs.validate`, `job.create_job_dir`; `test_job_folders_are_never_reused_and_paths_are_safe` |
| A: research, ledger, claim→source map | Met | `research.py`, `ledger.py`, `claims.py`; `test_research_package_contents` |
| A: scripts are the authority; unsupported claims flagged, not rewritten | Met | `test_research_claims_flag_unsupported_and_keep_words` (words compared), `ResearchError` if tagging changed words |
| A: no silent translation; translate only when `auto_translate_missing_language=true` | Met | `Research.translate` (local Ollama only, marked `machine_translated`, numbers compared); `test_missing_language_translated_only_on_request`, `test_cli_translation_needs_a_local_model_and_bad_packages_fail` |
| A: reviewable package (research JSON, claim report, ledger, snippets, images, charts, draft storyboard) | Met | `research/{research.json,claims.json,source_report.md,sources.*,extracted_text,screenshots,charts}`, `storyboard/draft_storyboard.*.json` |
| A: upload artifact, no large-media commit | Met | workflow uploads `research-package-<job_id>`; `contents: read` |
| A: research only, no render | Met | workflow has no render step |
| Workflow B `1-render-video.yml`: run id, narration audio/script per language, voice mode, languages, quality, music off, subtitles off | Met | workflow file (inputs combined to stay ≤ 10) |
| B: validate package and hashes | Met | `verify_package`; `test_render_refuses_tampered_package` |
| B: supplied audio wins | Met | `render.py` (`provided_audio` default when audio given; clone/TTS disabled) |
| B: no approved engine → that language fails, other renders | Met | `test_render_outputs_layout_and_one_language_failure` |
| B: word alignment if compatible free aligner, else phrase fallback, never an equal split | Met | whisper (MIT) or pause-based `distribute_words`; MMS_FA only with `allow_restricted_models`; `alignment.*.json` `phrase_fallback`/`timing_confidence`; `test_render_manifest_points_evidence_to_sources` |
| B: timeline, render, QA, upload MP4 and reports | Met | `render.export_language`, `finalize_job`; artifact `explainer-video-<job_id>` |
| B: separate English and Urdu videos | Met | `video/explainer_en.mp4`, `explainer_ur.mp4` |
| B: no YouTube upload | Met | none implemented |
| One-click: no prompts; optional inputs skip features; actionable logs; partial outputs kept; reruns don't corrupt | Met | `qa/render.log`, `research/research.log`, `qa/failed/<lang>/`, new job folder per run |
| Hosted runners can't read laptop paths: secure options documented; private audio never in public artifacts | Met | WORKFLOW_GUIDE "Getting a voice-over to a hosted runner"; secret links; no-audio check in `render_package` (`test_render_outputs_layout_and_one_language_failure`) |

## §3 Free and commercial-use policy

| Requirement | Status | Evidence |
|---|---|---|
| No paid APIs, subscriptions or hosted LLMs | Met | providers: user URLs, official feeds, GDELT, FRED, SEC; optional local Ollama |
| No bypass of paywalls, CAPTCHAs, logins or robots | Met | `http.Fetcher.allowed`; `test_fetcher_respects_robots_and_identifies_itself`; blocked pages → `paywall_or_blocked`. Google News RSS (robots-disallowed) is off in the new path |
| Versions and licences in THIRD_PARTY_LICENSES; code and weights separate; `commercial_status` | Met | `docs/THIRD_PARTY_LICENSES.md` (checked against PyPI/HF; OmniVoice, MMS_FA and qwen2.5:3b corrected) |
| Non-commercial/unknown models off in the monetised path, with warnings | Met | engine default `allow_noncommercial_models: false`; OmniVoice gated; MMS_FA off in render; `build_info.commercial_clearance` |
| No legal certainty claimed; never "cleared" with unknown/restricted items | Met | `finalize_job` blocking list; RIGHTS doc |
| Voice cloning only with consent; samples not committed, logged or uploaded | Met (+ human step) | consent check (`test_clone_voice_needs_consent_and_reference`); `input/voice/*` ignored, file untracked; `private_dir`; **old commits still hold the file (human step: history rewrite)** |

## §4 Research

| Requirement | Status | Evidence |
|---|---|---|
| Source priority (user URLs → official → RSS → APIs → search HTML → Wikipedia orientation) | Met (Wikipedia not used) | `research.discover`, `capture` order; RESEARCH_POLICY |
| Independent adapters, timeouts, retries/backoff, cache, rate limits, user agent | Met | `providers.py`, `http.py`; `test_fetcher_backs_off_on_429_and_never_retries_403`, `test_one_blocked_official_feed_does_not_hide_the_others`, `test_rate_limited_host_waits_at_least_its_published_interval`, `test_fetcher_cache_has_provenance_and_ttl` |
| Atomic claims; primary + second source; rank by authority/directness/date/independence | Met | one claim per sentence (finance splits per figure); `independent_sources`, `primary_source`; `claims.rank_evidence` (authority → figures matched → original before syndicated → wording → recency); `test_evidence_ranked_by_authority_then_directness` |
| Publication date vs data date | Met | `published_at` vs `data_period`; gate `publication_date_vs_data_period` |
| Six claim statuses | Met | `claims.STATUSES`; `test_claim_statuses_are_conservative`, `test_outdated_when_claim_says_now_but_source_is_old` |
| Keyword overlap is not verification; conservative without semantics | Met | `match_claims`; `test_small_numbers_alone_do_not_support_a_claim` |
| Never fabricate | Met | cards only from verified metadata/text; charts only from CSV; gate `chart_values_traceable` |
| Predictions, opinions, rhetoric classified | Met | `classify`; `test_claim_kinds` |
| Ledger files and 18 fields | Met | `ledger.CSV_FIELDS`; `test_ledger_merges_duplicates_and_writes_all_fields` |
| Every sourced claim → source IDs; labels on evidence; full URLs in sources.txt; no endorsement | Met | gate `claims_map_to_sources`; stage labels; `sources.txt` |
| **Finance**: EDGAR facts and filings, issuer releases, Yahoo (no scraping), independent news, ≥ 3 sources | Met in code; live run = Human step | `sec.py`, `finance.py` (XBRL, 8-K EX-99, news cross-check, Yahoo links only, `min_sources_per_claim`). A live EDGAR call needs your name and e-mail in `SEC_USER_AGENT` (SEC rule); without it EDGAR is skipped and reported |
| Finance: entity, metric, value, currency, scale, period, baseline, basis; normalised, originals kept | Met | `parse_claims`; `test_financial_amounts_are_normalised`, `test_financial_periods`, `test_one_sentence_two_atomic_financial_claims` |
| Finance: excerpt, URL, form, accession, period, value, unit, formula, timestamp | Met | match records in `verify_with_facts` / `verify_in_text` |
| Finance: recompute derived values | Met | `growth`, derived metrics, Q4; `test_xbrl_periods_restatement_and_q4_derivation`, `test_financial_matches_and_conflicts` |
| Finance: 5 match statuses; restatements, preliminary values, basis, stale values, period flags | Met | restatement, basis, calendar-vs-fiscal, percent-vs-points, preliminary/unaudited, stale period; `test_preliminary_and_stale_financial_figures_are_flagged` and the tests above |
| Finance: report, no rewrite | Met | `financial_verification_report.md` (proposed corrections only) |
| SEC fair access: UA, cache, rate limit, 403/429 | Met | `sec_user_agent`, `HOST_INTERVALS`; fetcher behaviour tested |
| Structured data first; HTML table fallback | Met | companyfacts → `document_text` table rows; `test_issuer_release_table_in_millions` |
| Financial visuals: filing crop or evidence card; charts only from verified values | Met | a confirmed release row becomes a labelled card quoting the row (`Research.finance_cards`), the SRS's "clean evidence card" option; `test_matched_release_row_becomes_a_labelled_evidence_card` |
| Finance outputs and tests (normalisation, periods, GAAP, syndication, restatement, missing, calculations) | Met | `financial_claims.json/csv`, report; tests listed above plus `test_syndicated_copy_is_not_an_independent_source`, `test_non_gaap_and_missing_values_are_flagged` |
| Screenshots: accessible pages, settled, cropped; else source card; short exact quotes; record URL/time/size/status | Met | existing `capture.py`; `source_card.py`; `test_screenshot_failure_becomes_labelled_source_card`, `test_source_card_words_allow_highlighting`; live source cards |
| Charts from official data, units/period/series/retrieval; no data → no chart | Met | FRED CSV stored, json metadata; `test_missing_dataset_omits_chart_and_research_continues` |

## §5 English and Urdu

| Requirement | Status | Evidence |
|---|---|---|
| Preserve exact wording; no paraphrase at render | Met | tag-only changes, words compared |
| Pronunciation/normalisation maps | Met | `voice_manager.apply_pronunciations`; `test_pronunciations_only_change_what_the_voice_reads`, `test_package_pronunciations_reach_the_voice_only` |
| Urdu script and Roman Urdu; `ur_script_mode`; no silent transliteration | Met | `urdu_form`; `test_urdu_form_detection_keeps_supplied_form`; transliteration only for TTS input |
| NFC, Urdu fonts, shaping, RTL, punctuation | Met | `script_analyzer`, `text_shaper`; `test_urdu_script_is_kept_as_supplied_and_nfc_normalised` |
| Mixed Urdu/English, numerals, dates, currency, parentheses, URLs, acronyms; URLs LTR | Met | `test_mixed_urdu_english_urls_numbers_and_acronyms_stay_readable` |
| Urdu aligner if available, else phrase timing, low confidence | Met | MMS_FA optional (restricted); else whisper/pauses with `timing_confidence: low` |
| Bilingual: semantic groups, shared evidence, independent timing, `consistency_report.json` | Met | `consistency.py` (paragraph groups); `test_consistency_report_finds_number_and_evidence_differences` |

## §6 Narration and voice

| Requirement | Status | Evidence |
|---|---|---|
| Priority: supplied → authorised clone → commercial TTS → fail | Met | `render.py` voice modes; tests for consent, missing audio, missing TTS |
| WAV/MP3/M4A/FLAC → PCM WAV; no aggressive processing; conservative normalisation | Met | `audio_processor.normalize` (loudnorm −16 LUFS, −1.5 dBTP; edge trim only) |
| Music off; FFmpeg build documented | Met | default off; `build_info.ffmpeg`; THIRD_PARTY_LICENSES |
| Missing model doesn't block supplied audio | Met | supplied path needs no TTS |
| Don't claim Urdu cloning works | Met | VOICE_SETUP and KNOWN_LIMITATIONS say mocked only |
| Voice sample path and consent explicit | Met | `--voice-reference` + `--voice-consent` |

## §7 Visual style

| Requirement | Status | Evidence |
|---|---|---|
| 9:16, 1080×1920, 30 fps | Met | `final` → standard profile; QA resolution/fps checks |
| Paper background, marker lines, evidence + drawings, no default subtitles | Met | explainer style; `captions_enabled` false unless `--subtitles` |
| No fake screenshots, invented brands or AI charts | Met | cards labelled; charts from data |
| Visual types vocabulary | Met | manifest `visual_type` enum (13 types) |
| Same type no more than twice in a row unless evidence | Met (warning) | `manifest.repeated_types`, QA `visual_variety`; `test_visual_variety_flags_long_runs_of_one_type` |
| Progressive SVG strokes; pen follows the stroke; raster uses reveal, not fake strokes | Met | `render._draw_strokes` returns the tip; `HandCursor` marker; screenshots pop/pan |
| Missing asset → simple shape, no block | Met | fallback "person" doodle, unknown icon warning |
| Evidence cards show publisher/title/date/domain; highlight only the passage; readable crops | Met | `article_label`; highlights; readable card alternative (`test_unreadable_screenshot_is_replaced_by_its_card`) |
| Quotation vs paraphrase | Met | quotes in quotation marks on cards; highlights only exact words |
| Transitions, safe margins, no overlaps, consistent styling | Met (overlap = warning) | QA `drawings_inside_safe_area`, `no_overlaps` |
| Word-by-word = events anchored to phrases | Met | manifest anchors and events per scene |

## §8 Scene manifest

| Requirement | Status | Evidence |
|---|---|---|
| Top-level, scene and event fields | Met | `schemas/scene_manifest.schema.json`, `manifest.build` |
| Reject negative times, missing source IDs, missing assets, invalid languages, events outside scenes; stable IDs, ordering | Met | `manifest.validate`; `test_manifest_validation_rules` |

## §9 Architecture

| Requirement | Status | Evidence |
|---|---|---|
| Testable boundaries; reuse | Met | `engine/package/*` modules |
| Deterministic; `build_info.json` with versions, hashes, settings | Met | fixed seeds; `job.build_info` |

## §10 Actions and CLI

| Requirement | Status | Evidence |
|---|---|---|
| A: dispatch inputs, validate, pinned deps, sanity tests, cache, artifacts, concurrency, no paid key | Met | workflow A (runs `tests/test_package.py` first) |
| B: artifact by run id, languages, audio, voice mode, quality, hashes, cache, QA, no secrets exposed, no private audio upload | Met | workflow B |
| Critical QA failures fail the job but keep reports | Met | exit codes 0/3/1/2; upload `if: always()` |
| CLI equivalents | Met | `engine.cli research` / `render`; LOCAL_RUN |
| Workflows validated on GitHub | see "Hosted validation" below | |

## §11 Output layout

| Requirement | Status | Evidence |
|---|---|---|
| research/, storyboard/, audio/, video/, qa/, build_info.json, sources.txt, upload_metadata.md | Met | `test_render_outputs_layout_and_one_language_failure` |
| Only relevant languages; no voice samples | Met | same test (no audio files) |

## §12 QA gates

| Requirement | Status | Evidence |
|---|---|---|
| Research gates: claims → sources, canonical URLs, exact quotes, chart values traced, unsupported flagged, dates distinct | Met | `research/qa.json`; `test_research_gates_and_log` |
| Audio/timing: decodes, duration and sample rate known, within tolerance, no truncation, alignment confidence, independent timing | Met | `check_file`, `alignment.*.json` |
| Visual: resolution, blank/frozen, safe margins, readable text, label duration, overlaps, contact sheet, black frames, clipping, no silent blank scenes | Met | `check_motion`, `check_explainer`, `contact_sheet`; `test_render_log_and_overlap_checks` |
| Tests: unit, integration with tone and fixtures, regression fixtures (English, Urdu, Roman Urdu, numerals, screenshot failure, missing data, missing TTS), workflows and CLI | Met | `tests/test_package.py` (66), `test_languages.py`, `test_workflow.py` |

## §13 Error fallback matrix

| Case | Status | Evidence |
|---|---|---|
| Provider fails → others continue, coverage reported | Met | `research.json.providers`; feed isolation test |
| Screenshot fails → source card | Met | test + live |
| No source → flagged | Met | `EN-004 not_found` in tests |
| Dataset missing → no chart | Met | test |
| TTS missing/restricted → require audio for that language | Met | test |
| Aligner fails → phrase timing, lower confidence | Met | pipeline fallback; `alignment.phrase_fallback` |
| Urdu font/shaping fails → Urdu QA fails with the characters | Met | `glyph_coverage` (critical) |
| One language fails → other kept | Met | test |
| Renderer fails → manifest, sources, logs, images kept | Met | `qa/failed/<lang>/`, `render.log`, `sources.txt` |
| Rate limit/robots → back off or skip | Met | tests |

## §14–15 Security, privacy, rights

| Requirement | Status | Evidence |
|---|---|---|
| No credentials, cookies, personal data or voice samples committed | Met (+ human step) | voice file untracked and ignored; history rewrite left to owner |
| Secrets only for optional providers; redacted | Met | `SEC_USER_AGENT`, audio/voice URL secrets (masked by Actions); temp paths hidden in printed args |
| Validate paths/URLs; no traversal or injection | Met | `safe_join`, `validate_url`, env-only workflow inputs; tests |
| No scripts from web pages run; article text is data | Met | stdlib parser; RESEARCH_POLICY |
| Cache with TTL and provenance | Met | fetcher cache test |
| RIGHTS_AND_ATTRIBUTION, THIRD_PARTY_LICENSES; no NC models in monetised path; attribution | Met | docs |

## §16 Performance and configuration

| Requirement | Status | Evidence |
|---|---|---|
| CPU only; bounded parallelism; resume without repeating research; draft/final | Met | sequential per language; package reuse |
| Runtime/RAM measured, no invented benchmarks | Met | LOCAL_RUN: final 16 s video 18–37 s, peak 748 MB (psutil sampling) |
| Validated configuration with all listed defaults; no secrets in config | Met | `research` section in `engine/config.py` and schema (resolution, fps, languages, screenshots, charts, music off, subtitles off, labels on, supplied audio first, no restricted models, limits/timeouts/retries, cache TTL, `min_evidence_seconds`, safe margins) |

## §17 Documentation

All 11 files exist: README, IMPLEMENTATION_AUDIT, HOW_IT_WORKS, WORKFLOW_GUIDE, LOCAL_RUN, VOICE_SETUP,
URDU_SUPPORT, RESEARCH_POLICY, RIGHTS_AND_ATTRIBUTION, THIRD_PARTY_LICENSES, KNOWN_LIMITATIONS. **Met.**

## §19 Definition of done

| # | Item | Status |
|---|---|---|
| 1 | Feature branch, user changes preserved | Met |
| 2 | Audit of starting state | Met |
| 3 | Two workflows | Met (hosted validation below) |
| 4 | Traceable evidence package | Met |
| 5 | Renderer consumes package; supplied audio first | Met |
| 6 | English and Urdu independent, language-aware | Met |
| 7 | Factual evidence scenes have source IDs | Met (critical QA in package renders) |
| 8 | Screenshot-failure fallback | Met |
| 9 | Charts use real data | Met |
| 10 | Timeline anchors visuals to narration | Met |
| 11 | Valid vertical MP4 and QA reports | Met |
| 12 | Tests cover success and failure paths | Met |
| 13 | Licences recorded; restricted models not in the default path | Met |
| 14 | No paid service required | Met |
| 15 | Docs explain usage, limits and costs | Met |
| 16 | Honest reporting | Met |

## Hosted validation (GitHub Actions)

Recorded after the runs on the pushed branch; see the end of IMPLEMENTATION_AUDIT Part B.
