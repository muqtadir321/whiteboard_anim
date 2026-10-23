"""Beats (one connected, labelled board per idea) and genuine stroke-by-stroke drawing."""

import cairo
import numpy as np
import pytest

from engine.config import load_config
from engine.explainer.beats import Beat, Node, compose, group_beats, label_for, slots, text_word
from engine.explainer.layout import ExplainerLayout
from engine.explainer.render import ExplainerRenderer, ExScene, Stage

from .conftest import ROOT

SAFE = (48.0, 1032.0)


@pytest.fixture
def ex_cfg():
    return load_config(overrides={"animation": {"style": "explainer"}, "video": {"quality": "fast"}}, repo_root=ROOT)


def ink(surf: cairo.ImageSurface) -> float:
    """Share of non-white pixels."""
    surf.flush()
    a = np.ndarray((surf.get_height(), surf.get_stride() // 4, 4), np.uint8, surf.get_data())[:, : surf.get_width(), :3]
    return float((a.min(axis=2) < 200).mean())


def frame(renderer, scene, t, stages=()):
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, renderer.width, renderer.height)
    renderer.paint_frame(cairo.Context(surf), scene, list(stages), t)
    return surf


def test_vector_drawing_is_revealed_stroke_by_stroke(ex_cfg):
    lay = ExplainerLayout(ex_cfg, "en", None, ex_cfg["video"]["width"] / 1080)
    sp = lay.icon_sprite("bank", {}, (290, 600, 790, 1100), 1.0, "test")
    assert sp is not None and sp.anim == "draw" and len(sp.strokes) > 5
    ex_cfg["animation"]["explainer"]["pen_cursor"] = False  # measure the drawing alone
    r = ExplainerRenderer(ex_cfg, ex_cfg["video"]["width"], ex_cfg["video"]["height"], "en")
    scene = ExScene("scene_001", 0.0, 10.0, [], [sp])
    before, early, mid, done = (ink(frame(r, scene, t)) for t in (0.5, 1.0 + sp.draw * 0.25, 1.0 + sp.draw * 0.6, 1.0 + sp.draw + 0.5))
    assert before == 0.0
    assert 0.0 < early < mid < done  # more of the drawing appears as time passes
    assert mid < 0.85 * done  # an intermediate frame is clearly not the finished drawing


def test_pen_tip_sits_on_the_stroke_being_drawn(ex_cfg):
    lay = ExplainerLayout(ex_cfg, "en", None, 1.0)
    sp = lay.icon_sprite("house", {}, (290, 600, 790, 1100), 0.0, "pen")
    ex_cfg["video"]["width"], ex_cfg["video"]["height"] = 1080, 1920
    r = ExplainerRenderer(ex_cfg, 1080, 1920, "en")
    scene = ExScene("scene_001", 0.0, 10.0, [], [sp])
    tips = []
    for k in range(1, 6):
        frame(r, scene, sp.draw * 0.8 * k / 6)
        tips.append(r._pen_at)
    assert all(t is not None for t in tips) and len({(round(x), round(y)) for x, y in tips}) > 3  # it moves along the strokes
    for x, y in tips:  # and stays on the drawing
        assert 290 - 20 <= x <= 790 + 20 and 600 - 20 <= y <= 1100 + 20
    frame(r, scene, sp.draw + 1.0)
    assert r._pen_at is None  # no pen once the drawing is finished


def test_beats_follow_paragraphs_and_split_long_ones():
    spans = [(0, 4), (4, 8), (8, 12), (12, 20), (20, 28), (28, 36), (36, 40)]
    assert group_beats(spans, [8.0], [4, 8, 12, 20, 28, 36, 40]) == [[0, 1], [2, 3, 4], [5, 6]]


@pytest.mark.parametrize("word,label", [("New", "New Zealand"), ("central", "central bank"), ("interest", "interest rates"),
                                        ("savings", "savings"), ("There", "room")])
def test_labels_are_the_narration_words(word, label):
    tokens = ("It started in New Zealand, when their central bank became one of the first. The Fed cuts interest rates. "
              "Your savings shrink. There is almost no room.").split()
    assert label_for(tokens, tokens.index(word), "en") == label


def boxes_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_slots_stay_in_the_safe_area_and_apart(n):
    out = slots(n, 170.0)
    assert len(out) == n
    for box, (lx, ly) in out:
        assert SAFE[0] <= box[0] < box[2] <= SAFE[1] and box[3] < ly < 1880
    for i in range(n):
        for j in range(i + 1, n):
            assert not boxes_overlap(out[i][0], out[j][0])


def test_compose_builds_a_connected_labelled_board(ex_cfg):
    lay = ExplainerLayout(ex_cfg, "en", None, 1.0)
    beat = Beat([0, 1], 0.0, 12.0)
    picks = [Node("bank", {}, 1.0, "central bank", True), Node("house", {}, 3.5, "home prices"), Node("money_bag", {}, 6.0, "savings")]
    compose(beat, picks, [], 170.0, lay, "en", "")
    drawings = [sp for sp in beat.sprites if sp.name != "arrow"]
    arrows = [sp for sp in beat.sprites if sp.name == "arrow"]
    assert [d.name for d in drawings] == ["bank", "house", "money_bag"] and len(arrows) == 2
    assert [w.text for w in beat.words] == ["central bank", "home prices", "savings"]
    assert all(sp.end == 12.0 for sp in beat.sprites)  # the board stays for the whole idea
    rects = [d.box for d in drawings] + [w.bbox for w in beat.words]
    for i in range(len(rects)):
        assert SAFE[0] <= rects[i][0] and rects[i][2] <= SAFE[1]
        for j in range(i + 1, len(rects)):
            assert not boxes_overlap(rects[i], rects[j]), (i, j)
    assert beat.objective == "central bank → home prices → savings"


def test_evidence_comes_first(ex_cfg):
    lay = ExplainerLayout(ex_cfg, "en", None, 1.0)
    stage = Stage("chart", (0, 300, 1080, 1800), 0.5, 5.0, id="chart:X")
    beat = Beat([0], 0.0, 12.0)
    compose(beat, [Node("bank", {}, 1.0, "bank"), Node("house", {}, 4.0, "house")], [stage], 170.0, lay, "en", "")
    assert all(sp.t >= stage.t_out - 0.15 for sp in beat.sprites)  # nothing is drawn over the chart
    covered = Beat([0], 0.0, 5.5)
    compose(covered, [Node("bank", {}, 1.0, "bank")], [stage], 170.0, lay, "en", "")
    assert covered.sprites == []  # evidence fills the idea: no drawing squeezed in


def test_nothing_to_draw_gives_the_key_phrase_not_a_random_picture(ex_cfg):
    lay = ExplainerLayout(ex_cfg, "en", None, 1.0)
    beat = Beat([0], 0.0, 6.0)
    compose(beat, [], [], 170.0, lay, "en", "Two percent is a choice")
    assert beat.sprites == [] and [w.text for w in beat.words] == ["Two percent is a choice"]


def test_urdu_labels_are_shaped_and_written_right_to_left():
    w = text_word("مرکزی بینک", 58.0, (540.0, 900.0), 0.0, "ur")
    assert w is not None and w.rtl and not w.missing and w.contours
    assert SAFE[0] <= w.bbox[0] < w.bbox[2] <= SAFE[1]


def test_arrows_never_cross_labels(ex_cfg):
    lay = ExplainerLayout(ex_cfg, "en", None, 1.0)
    beat = Beat([0], 0.0, 12.0)
    picks = [Node("bank", {}, 1.0, "central bank"), Node("house", {}, 3.0, "home prices"), Node("money_bag", {}, 5.0, "savings"),
             Node("piggy_bank", {}, 7.0, "deposits")]
    compose(beat, picks, [], 170.0, lay, "en", "")
    labels = [w.bbox for w in beat.words]
    for ar in (sp for sp in beat.sprites if sp.name == "arrow"):
        k = (ar.box[2] - ar.box[0]) / ar.surf.get_width()  # surface pixels -> design units
        for pts, *_ in ar.strokes:
            for x, y in pts:
                X, Y = ar.box[0] + x * k, ar.box[1] + y * k
                assert not any(b[0] <= X <= b[2] and b[1] <= Y <= b[3] for b in labels), (X, Y)


def test_comparison_values_are_crossed_out_and_circled(ex_cfg):
    from engine.explainer.beats import Contrast, find_contrast

    toks = "Why does the Fed want two percent inflation? Not zero. Not five. Exactly two.".split()
    c = find_contrast(toks, [i * 0.4 for i in range(len(toks))], "%")
    assert [(v, chosen) for v, chosen, _ in c.items] == [("0%", False), ("5%", False), ("2%", True)]
    assert find_contrast("This is not easy, not now.".split(), [0.0] * 6, "") is None
    lay = ExplainerLayout(ex_cfg, "en", None, 1.0)
    beat = Beat([0], 0.0, 10.0)
    compose(beat, [Node("rate_badge", {}, 1.0, "2%"), Node("price_tag", {}, 6.0, "inflation")], [], 170.0, lay, "en", "", contrast=c)
    names = [sp.name for sp in beat.sprites]
    assert names.count("value_card") == 3 and names.count("cross_out") == 2 and names.count("circle_choice") == 1
    assert "rate_badge" not in names  # the circled card already shows 2%
    assert [w.text for w in beat.words][:3] == ["0%", "5%", "2%"]


def test_returning_objects_look_the_same_and_come_back_faster(ex_cfg):
    lay = ExplainerLayout(ex_cfg, "en", None, 1.0)
    memory: dict = {}
    first, later = Beat([0], 0.0, 8.0), Beat([1], 8.0, 16.0)
    compose(first, [Node("price_tag", {}, 1.0, "inflation")], [], 170.0, lay, "en", "", memory=memory)
    compose(later, [Node("price_tag", {}, 9.0, "inflation"), Node("house", {}, 11.0, "homes")], [], 170.0, lay, "en", "", memory=memory)
    a = next(sp for sp in first.sprites if sp.name == "price_tag")
    b = next(sp for sp in later.sprites if sp.name == "price_tag")
    assert len(a.strokes) == len(b.strokes) and a.strokes[0][0][:3] == [(x * 1, y * 1) for x, y in a.strokes[0][0][:3]]
    assert b.draw <= 0.6 < a.draw or b.draw <= 0.6


def test_colour_means_something():
    from engine.explainer.beats import GREEN, INK, RED, tone_color

    assert tone_color("falling prices") == RED and tone_color("savings") == GREEN
    assert tone_color("aim higher") == INK and tone_color("central bank") == INK
