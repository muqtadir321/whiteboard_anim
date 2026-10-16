import pytest

from engine.scene_planner import Segment, analyze, plan_scenes
from engine.scene_schema import PlanValidationError, plan_errors, validate_plan


def plan(texts, lang, cfg):
    return plan_scenes([Segment(t, i * 4.0, i * 4.0 + 4.0, i) for i, t in enumerate(texts)], lang, cfg)


def objects(scene):
    return [a.get("object") for a in scene["visual_actions"] if a["action"] == "draw"]


def texts(scene):
    out = []
    for a in scene["visual_actions"]:
        if a.get("text"):
            out.append(a["text"])
        for v in (a.get("params") or {}).values():
            if isinstance(v, list):
                out += [x for x in v if isinstance(x, str)]
            elif isinstance(v, str):
                out.append(v)
    return out


def test_graph_with_correct_percentage(cfg):
    p = plan(["Over time, their money grows by one hundred percent."], "en", cfg)
    s = p["scenes"][0]
    assert s["scene_type"] == "chart_growth"
    assert "bar_chart" in objects(s) or "line_chart" in objects(s)
    assert any("100%" in t for t in texts(s))
    assert not any(t in ("10%", "1000%") for t in texts(s))


def test_person_with_object(cfg):
    s = plan(["A person starts investing with fifty dollars."], "en", cfg)["scenes"][0]
    assert s["scene_type"] == "person_object"
    assert objects(s)[0] == "person"
    assert any(a["position"].startswith("in_hand:") for a in s["visual_actions"])
    assert "$50" in texts(s)


def test_cause_and_effect(cfg):
    s = plan(["Because prices rise, families save less money."], "en", cfg)["scenes"][0]
    assert s["scene_type"] == "cause_effect"
    assert any(a["action"] == "connect" for a in s["visual_actions"])
    assert "price_tag" in objects(s)


def test_salary_relationship(cfg):
    s = plan(["A worker receives a salary from the company."], "en", cfg)["scenes"][0]
    assert s["scene_type"] == "transaction"
    assert {"boss", "worker"} <= set(objects(s))
    assert sum(1 for a in s["visual_actions"] if a["action"] == "connect") == 2


def test_no_invented_numbers(cfg):
    s = plan(["The economy keeps growing every year."], "en", cfg)["scenes"][0]
    for t in texts(s):
        assert not any(ch.isdigit() for ch in t), t


def test_unsupported_concept_is_honest_text_scene(cfg):
    s = plan(["The universe is vast and mysterious."], "en", cfg)["scenes"][0]
    assert s["scene_type"] == "text_emphasis" and s["fallback"] is True
    assert s["confidence"] < 0.5


def test_multilingual_concepts_map_to_same_visuals(cfg):
    ur = plan(["مہنگائی کی وجہ سے لوگ کم بچت کرتے ہیں۔"], "ur", cfg)["scenes"][0]
    ps = plan(["یو سړی دوکان ته ځي او ټیلیفون اخلي."], "ps", cfg)["scenes"][0]
    ru = plan(["Aik shakhs ne pachas dollar se sarmayakari shuru ki."], "roman_urdu", cfg)["scenes"][0]
    assert ur["scene_type"] == "cause_effect" and "price_tag" in objects(ur)
    assert "shop" in objects(ps) or "phone" in objects(ps)
    assert "$50" in texts(ru)


def test_adjacent_scenes_vary(cfg):
    p = plan(["Money grows 20%.", "Money grows 20%."], "en", cfg)
    a, b = p["scenes"]
    sig = lambda s: [(x.get("object"), x["position"]) for x in s["visual_actions"]]
    assert sig(a) != sig(b)


def test_schema_rejects_invalid_plans(cfg):
    p = plan(["Money grows 20%."], "en", cfg)
    validate_plan(p)
    bad = {**p, "scenes": [{**p["scenes"][0], "visual_actions": []}]}
    with pytest.raises(PlanValidationError):
        validate_plan(bad)
    broken = {**p, "scenes": [{**p["scenes"][0], "visual_actions": [{"id": "a1", "action": "connect", "source": "a7", "target": "a9", "position": "between", "draw_order": 1}]}]}
    assert any("does not exist" in e for e in plan_errors(broken))


def test_confidence_is_evidence_based(cfg):
    good = plan(["A worker receives a salary from the company."], "en", cfg)["scenes"][0]
    weak = plan(["Something happened somewhere."], "en", cfg)["scenes"][0]
    assert good["confidence"] > weak["confidence"]
    assert "concept_coverage" in good["confidence_basis"]


def test_analysis_trend_detection():
    assert analyze("Prices fell by 10%.", "en").trend == "down"
    assert analyze("پیسہ بڑھ گیا", "ur").trend == "up"
