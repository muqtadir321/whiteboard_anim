"""Research package: the contract between "Research & Prepare" and "Render Video".

    output/<job_id>/
      package.json                    files + sha256, inputs, settings (validated on render)
      research/research.json          what was searched, provider coverage, warnings
      research/sources.{json,csv,txt} the source ledger (stable source_id per canonical URL)
      research/claims.json            narration claims -> source ids, status per claim
      research/source_report.md       readable claim report
      research/financial_claims.*     only when finance claims are found
      research/topic/                 a topic folder the explainer renderer reads
                                      (<lang>.txt with cue tags, evidence/manifest.json + images)
      research/{extracted_text,screenshots,charts,data}/

Rendering never repeats research: it copies the package into a new job folder, checks the
hashes and renders from the copy (engine.package.render).
"""

SCHEMA_VERSION = "1.0"
