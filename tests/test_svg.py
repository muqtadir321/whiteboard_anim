import xml.etree.ElementTree as ET

from engine.audio_sync import build_timeline, order_by_mention
from engine.chart_generator import bar_chart, pie_chart
from engine.illustration_composer import DRAW_REGION, Composer, to_svg
from engine.scene_planner import Segment, plan_scenes
from engine.svg_draw_order import load_program, parse_path, stroke_cost

import random


def compose(text, lang, cfg):
    p = plan_scenes([Segment(text, 0, 6, 0)], lang, cfg)
    c = Composer(cfg, lang)
    scene = p["scenes"][0]
    cs = c.compose(scene)
    return scene, cs, to_svg(cs, c.style)


def test_svg_is_valid_and_has_ordered_paths(cfg):
    scene, cs, svg = compose("A person starts investing with fifty dollars.", "en", cfg)
    root = ET.fromstring(svg)
    groups = [g for g in root.iter("{http://www.w3.org/2000/svg}g") if "item" in (g.get("class") or "")]
    assert len(groups) >= 3
    orders = [int(g.get("data-order")) for g in groups]
    assert orders == sorted(orders)
    paths = list(root.iter("{http://www.w3.org/2000/svg}path"))
    assert len(paths) > 20


def test_program_uses_real_strokes_not_images(cfg):
    _, _, svg = compose("A person starts investing with fifty dollars.", "en", cfg)
    items = load_program(svg)
    ops = [op for it in items for op in it.ops]
    assert sum(1 for op in ops if op.kind == "stroke") > 20
    assert any(op.kind == "glyph" for op in ops)  # "$50" written by hand
    assert "<image" not in svg


def test_parse_path_supports_curves():
    subs = parse_path("M0 0 L10 0 C20 0 20 10 30 10 Q40 10 40 20 Z m5 5 l5 0")
    assert len(subs) == 2
    assert subs[0][-1] == (0.0, 0.0)
    assert subs[1][0] == (5.0, 5.0)  # relative moveto after Z starts from the subpath start


def test_stroke_duration_depends_on_geometry():
    straight = [(0, 0), (300, 0)]
    curvy = [(i * 10, (i % 2) * 20) for i in range(31)]
    assert stroke_cost(curvy, "line") > stroke_cost(straight, "line")
    assert stroke_cost([(0, 0), (20, 0)], "line") < stroke_cost(straight, "line")


def test_chart_values_and_percent_labels(cfg):
    el = bar_chart(random.Random(1), values=[50, 100], value_labels=["$50", "$100"])
    assert [t.text for t in el.texts] == ["$50", "$100"]
    # taller bar for larger value
    assert el.anchors["bar1"][1] < el.anchors["bar0"][1]
    pie = pie_chart(random.Random(1), percent=25)
    assert len(pie.strokes) > 3


def test_layout_stays_inside_drawing_region(cfg):
    for text in ["A worker receives a salary from the company.", "First you learn a skill, then you find a job, finally you earn money.",
                 "Rent costs 40% of income.", "Over time, their money grows by one hundred percent."]:
        _, cs, _ = compose(text, "en", cfg)
        for it in cs.items:
            x0, y0, x1, y1 = it.bbox
            assert x0 >= DRAW_REGION[0] - 40 and x1 <= DRAW_REGION[2] + 40, (text, it.id, it.bbox)
            assert y0 >= DRAW_REGION[1] - 40 and y1 <= DRAW_REGION[3] + 40, (text, it.id, it.bbox)


def test_rtl_flow_is_mirrored(cfg):
    _, cs_ur, _ = compose("مہنگائی کی وجہ سے لوگ کم بچت کرتے ہیں۔", "ur", cfg)
    cause = cs_ur.items[0]
    effect = cs_ur.items[1]
    assert cause.bbox[0] > effect.bbox[0]  # cause on the right for right-to-left readers


def test_timeline_follows_narration(cfg):
    scene, cs, svg = compose("A person starts investing with fifty dollars.", "en", cfg)
    items = order_by_mention(scene, load_program(svg))
    tl = build_timeline(scene, items, cfg, (900, 1500))
    assert tl.ops and tl.ops[0].t0 >= 0
    assert all(a.t1 <= b.t0 + 1e-6 for a, b in zip(tl.ops, tl.ops[1:]))  # one pen, sequential strokes
    assert tl.drawing_end <= scene["end_seconds"] + 1e-6
    # "$50" is spoken last, so it is written after the person is drawn
    ids = [items[e.item_index].obj for e in tl.ops]
    assert ids.index("person") < ids.index("text")
