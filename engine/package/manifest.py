"""scene_manifest.json: what is on screen, when, why (claims) and from where (sources), per language.

Built from the explainer layout after rendering, so it describes the video that was actually
made. validate() checks the JSON Schema and the rules the schema cannot express: times,
events inside their scene, source ids on factual evidence, existing assets, ordering.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema

from ..config import SCHEMA_DIR
from . import SCHEMA_VERSION

EVIDENCE_ACTIONS = {"show_evidence", "hold_evidence", "highlight", "chart_draw", "chart_marker"}
FACTUAL_TYPES = {"article_evidence_card", "official_report_card", "data_chart", "quote_highlight"}
STAGE_TYPE = {"article": "article_evidence_card", "news": "article_evidence_card", "chart": "data_chart", "logos": "logo_or_icon"}


def timing_confidence(source: str, confidence: float) -> str:
    if source == "forced_alignment":
        return "high" if confidence >= 0.85 else "medium"
    if source in ("words", "exact"):
        return "medium" if confidence >= 0.6 else "low"
    return "low"  # pause/phrase-based estimate


def _rel(path, base: Path | None) -> str:
    if base is None:
        return str(path)
    try:
        return Path(os.path.relpath(Path(path).resolve(), base.resolve())).as_posix()
    except ValueError:
        return str(path)


def _sprite_asset(name: str) -> tuple[str, str, str]:
    from ..explainer.layout import ICON_EXTRA
    from ..object_generator import OBJECTS

    if name == "arrow":
        return "doodle:arrow", "drawing", "original (this repository)"
    if name in OBJECTS or name in ICON_EXTRA:
        return f"doodle:{name}", "drawing", "original (this repository)"
    return f"emoji:{name}", "drawing", "MIT (Microsoft Fluent Emoji, via Iconify)"


def build(job_id: str, lang: str, topic: str, script_sha: str, audio_sha: str, cfg: dict, plan: dict, layout, timing, offset: float,
          total: float, claims: list[dict] | None = None, asset_base: Path | None = None, ledger=None) -> dict:
    from ..explainer.layout import TEXT_ZONE, word_times

    v = cfg["video"]
    conf = timing_confidence(timing.source, timing.confidence)
    sent_spans = [(ts.start + offset, ts.end + offset) for ts in timing.sentences]
    claims = claims or []
    used_sources: set[str] = set()
    scenes = []
    for sc, ps in zip(layout.scenes, plan["scenes"]):
        s0, s1 = round(sc.start, 3), round(sc.end, 3)
        times = [(a + offset, b + offset) for a, b in word_times(ps, ps["start_seconds"], ps["end_seconds"])]
        tokens = ps["narration_text"].split()
        anchors = [{"anchor_id": f"{sc.scene_id}.w{i + 1:03d}", "text": tok, "start_sec": round(max(s0, a), 3), "end_sec": round(min(s1, max(a, b)), 3)}
                   for i, (tok, (a, b)) in enumerate(zip(tokens, times))]

        def anchor_at(t: float) -> dict:
            best = anchors[0] if anchors else {"anchor_id": "", "text": ""}
            for an in anchors:
                if an["start_sec"] <= t + 1e-3:
                    best = an
            return best

        events: list[dict] = []
        assets: dict[str, dict] = {}
        src_ids: set[str] = set()

        def add(action, a, b, target, params=None, source=None):
            a, b = max(s0, a), min(s1, b)
            if b - a < 1e-3:
                return
            an = anchor_at(a)
            ev = {"event_id": f"{sc.scene_id}.e{len(events) + 1:03d}", "start_sec": round(a, 3), "end_sec": round(b, 3), "action": action,
                  "target_asset": target, "parameters": params or {}, "source_id": source}
            if an.get("anchor_id"):
                ev["anchor_id"], ev["trigger_text"] = an["anchor_id"], an["text"]
            else:
                ev["trigger_text"] = ""
            events.append(ev)

        stage_kinds = []
        for st in layout.stages:
            if not (st.t_in < s1 and s0 < st.t_out):
                continue
            sid = st.source_ids[0] if st.source_ids else None
            src_ids.update(st.source_ids)
            kind = st.kind
            path = None
            if st.kind == "article":
                art = getattr(layout, "shown", {}).get(st.id) or next((x for x in getattr(layout, "articles", {}).values()
                                                                       if f"article:{x.id}" == st.id), None)
                path = art.image if art else None
                kind = "source_card" if art is not None and art.capture == "source_card" else "screenshot"
            elif st.kind == "news":
                kind, path = "headline_cards", getattr(layout, "news_image", None)
            assets[st.id] = {"asset_id": st.id, "kind": kind, **({"path": _rel(path, asset_base)} if path else {}), **({"source_id": sid} if sid else {})}
            first = s0 <= st.t_in < s1
            act = ("chart_draw" if st.kind == "chart" else "show_evidence") if first else "hold_evidence"
            add(act, st.t_in if first else s0, st.t_out, st.id, {"region": [round(x, 1) for x in st.region], "label": st.label}, sid)
            for h in st.highlights:
                if s0 <= h.t0 < s1:
                    add("highlight", h.t0, h.t1, st.id, {"lines": len(h.rects), "color": h.color}, sid)
            for k in st.keys[1:]:
                if s0 <= k.t < s1:
                    add("camera_move", k.t - k.move, k.t, st.id, {"zoom": round(k.zoom, 3)}, sid)
            if st.painter is not None:
                for tm, d, val in getattr(st.painter, "markers", []):
                    if s0 <= tm < s1:
                        add("chart_marker", tm, min(s1, tm + 1.0), st.id, {"date": str(d), "value": val}, sid)
            stage_kinds.append((min(st.t_out, s1) - max(st.t_in, s0), st))
        for w in sc.words:
            add("pop" if w.pop else "write", w.t, w.t + max(w.dur, 0.3), f"text:{w.text}", {"color": w.color})
        for sp in sc.sprites:
            aid, kind, lic = _sprite_asset(sp.name)
            assets.setdefault(aid, {"asset_id": aid, "kind": kind, "license": lic})
            carried = sp.t < s0 - 1e-3
            action = "connect" if sp.name == "arrow" else ("draw" if sp.anim == "draw" else "pop")
            add(action, sp.t, sp.end if sp.end is not None else s1, aid, {"carried_over": True} if carried else {"draw_seconds": round(sp.draw, 2)})
        events.sort(key=lambda e: (e["start_sec"], e["event_id"]))
        if stage_kinds:
            st = max(stage_kinds, key=lambda x: x[0])[1]
            vt = STAGE_TYPE[st.kind]
            if st.kind == "article" and any(s0 <= h.t0 < s1 for h in st.highlights):
                vt = "quote_highlight"
            elif st.kind == "article" and ledger is not None and st.source_ids:
                src = ledger.get(st.source_ids[0])
                if src is not None and src.source_type in ("official", "primary_filing", "issuer_release"):
                    vt = "official_report_card"
        elif sc.sprites:
            vt = "hand_drawn_illustration"
        elif sc.words:
            vt = "number_callout"
        else:
            vt = "transition_or_pause"
        mid = [(a, b) for a, b in sent_spans if a < s1 and s0 < b]
        idx = {i for i, (a, b) in enumerate(sent_spans) if a < s1 and s0 < b}
        cids = sorted(c["claim_id"] for c in claims if c.get("sentence_index") in idx and c.get("language") == plan.get("language", lang)) if mid else []
        used_sources |= src_ids
        starts_stage = any(e["action"] in ("show_evidence", "chart_draw") for e in events)
        notes = []
        if any(e["parameters"].get("carried_over") for e in events):
            notes.append("drawings from the previous scene stay until this scene's first drawing")
        if ps.get("fallback"):
            notes.append("no specific concept recognised: key phrase or amounts only")
        scenes.append({"scene_id": sc.scene_id, "start_sec": s0, "end_sec": s1, "narration_text": ps["narration_text"], "word_or_phrase_anchors": anchors,
                       "claim_ids": cids, "visual_type": vt, "assets": sorted(assets.values(), key=lambda a: a["asset_id"]),
                       "source_ids": sorted(src_ids), "animation_events": events,
                       "transition_in": "card_reveal" if starts_stage else ("stroke_reveal" if sc.sprites else "cut"),
                       "transition_out": "cross_fade" if sc is not layout.scenes[-1] else "end",
                       "layout": {"text_zone": list(TEXT_ZONE), "stage_regions": sorted({tuple(round(x, 1) for x in st.region) for _, st in stage_kinds})},
                       "timing_confidence": conf, "render_notes": notes})
    for s in scenes:
        s["layout"]["stage_regions"] = [list(r) for r in s["layout"]["stage_regions"]]
    sources = []
    for sid in sorted(used_sources):
        src = ledger.get(sid) if ledger is not None else None
        sources.append({"source_id": sid, "label": f"{src.publisher or src.domain}: {src.title}" if src else sid, **({"url": src.canonical_url} if src else {})})
    return {"schema_version": SCHEMA_VERSION, "job_id": job_id, "language": lang, "topic": topic, "script_sha256": script_sha, "audio_sha256": audio_sha,
            "video_settings": {"width": v["width"], "height": v["height"], "fps": v["fps"], "duration_seconds": round(total, 3)},
            "sources": sources,
            "claims": [{"claim_id": c["claim_id"], "status": c["status"], "source_ids": c.get("source_ids", [])} for c in claims],
            "scenes": scenes,
            "qa_expectations": {"min_evidence_seconds": cfg["animation"]["explainer"]["min_evidence_seconds"],
                                "safe_margin_px": 48, "duration_tolerance_seconds": cfg["quality"]["maximum_duration_mismatch_seconds"],
                                "timing": {"source": timing.source, "confidence": timing.confidence}}}


def repeated_types(manifest: dict, limit: int = 2) -> list[str]:
    """Runs of more than `limit` scenes of the same non-evidence visual type (SRS: vary the visuals unless
    the evidence requires it)."""
    out, run = [], []
    for sc in manifest["scenes"] + [{"visual_type": None, "scene_id": ""}]:
        if run and sc["visual_type"] == run[-1]["visual_type"]:
            run.append(sc)
            continue
        if len(run) > limit and run[0]["visual_type"] not in FACTUAL_TYPES:
            out.append(f"{run[0]['visual_type']} x{len(run)} ({run[0]['scene_id']}-{run[-1]['scene_id']})")
        run = [sc]
    return out


def validate(manifest: dict, base: Path | None = None) -> list[str]:
    schema = json.loads((SCHEMA_DIR / "scene_manifest.schema.json").read_text(encoding="utf-8"))
    errors = [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
              for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(manifest), key=lambda e: list(e.path))]
    if errors:
        return errors
    out = []
    prev_end, seen = -1.0, set()
    dur = manifest["video_settings"]["duration_seconds"]
    known_sources = {s["source_id"] for s in manifest["sources"]}
    for sc in manifest["scenes"]:
        sid = sc["scene_id"]
        if sid in seen:
            out.append(f"{sid}: duplicate scene id")
        seen.add(sid)
        if sc["end_sec"] <= sc["start_sec"]:
            out.append(f"{sid}: end_sec must be after start_sec")
        if sc["start_sec"] < prev_end - 1e-3:
            out.append(f"{sid}: starts before the previous scene ends (scenes must be in order)")
        if sc["end_sec"] > dur + 1e-3:
            out.append(f"{sid}: ends after the video")
        prev_end = sc["end_sec"]
        if sc["visual_type"] in FACTUAL_TYPES and not sc["source_ids"]:
            out.append(f"{sid}: factual evidence ({sc['visual_type']}) without a source id")
        for s in sc["source_ids"]:
            if s not in known_sources:
                out.append(f"{sid}: source id {s} is not listed in sources")
        for an in sc["word_or_phrase_anchors"]:
            if an["start_sec"] < sc["start_sec"] - 1e-3 or an["end_sec"] > sc["end_sec"] + 1e-3:
                out.append(f"{sid}: anchor {an['anchor_id']} outside the scene")
        for ev in sc["animation_events"]:
            if ev["end_sec"] <= ev["start_sec"]:
                out.append(f"{ev['event_id']}: end_sec must be after start_sec")
            if ev["start_sec"] < sc["start_sec"] - 1e-3 or ev["end_sec"] > sc["end_sec"] + 1e-3:
                out.append(f"{ev['event_id']}: outside its scene ({sc['start_sec']}-{sc['end_sec']})")
            if ev["action"] in EVIDENCE_ACTIONS and not ev["source_id"] and not ev["target_asset"].startswith("logos"):
                out.append(f"{ev['event_id']}: evidence event without a source id")
        for a in sc["assets"]:
            if base is not None and a.get("path") and not (base / a["path"]).exists():
                out.append(f"{sid}: missing asset {a['path']}")
    return out
