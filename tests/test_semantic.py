"""Meaning-based matching (fake embedder, no model download), emoji drawings and a never-blank board."""

import math
import re
import zlib

import numpy as np
import pytest

from engine.explainer import draft, layout as layout_mod
from engine.explainer.cues import extract_cues
from engine.explainer.drawings import choose_strict, tag_drawings
from engine.explainer.emoji import EmojiLibrary, path_polylines, render_svg
from engine.explainer.evidence import Article
from engine.explainer.semantic import Embedder, csls

from .test_explainer import build_layout, ex_cfg, make_topic  # noqa: F401  (fixture)


class HashBackend:
    """Bag of words: texts sharing words get similar vectors."""

    def embed(self, texts):
        for t in texts:
            v = np.zeros(256, np.float32)
            for w in re.findall(r"\w+", t.lower()):
                v[zlib.crc32(w.rstrip("s").encode()) % 256] += 1.0
            v[255] += 0.05  # never all zeros
            yield v


@pytest.fixture
def emb():
    return Embedder("fake", backend=HashBackend())


SQUARE = '<path fill="#ff0000" d="M2 2h10v10H2z"/>'
ROUND = '<path fill="#00a6ed" d="M16 4a12 12 0 1 1 0 24a12 12 0 0 1 0-24"/>'
GLUED = '<path fill="#123456" d="M4 16a6 6 0 101-.5z"/>'


def lib_with(*names):
    return EmojiLibrary({n: (ROUND if i % 2 else SQUARE) for i, n in enumerate(names)})


def test_path_parser_arcs_curves_and_glued_flags():
    circle = path_polylines("M16 4a12 12 0 1 1 0 24a12 12 0 0 1 0-24")[0][0]
    assert all(abs(math.dist(p, (16, 16)) - 12) < 0.05 for p in circle)
    (poly, closed), = path_polylines("M4 16a6 6 0 101-.5z")  # flags "1","0" glued to the next number
    assert closed and len(poly) > 5
    smooth = path_polylines("M0 0c1 2 3 4 5 6s1 1 2 2q1 1 2 0t2 0")[0][0]
    assert smooth[-1] == pytest.approx((11, 8))


def test_emoji_render_and_draw_strokes():
    surf, strokes = render_svg(SQUARE + ROUND, 32, 256)
    assert surf.get_width() == 256 + 24
    assert strokes and all(len(s) == 4 for s in strokes)
    data = surf.get_data()
    # the red square is painted where it should be (premultiplied BGRA)
    x, y = int(12 + 7 * 8), int(12 + 7 * 8)
    i = y * surf.get_stride() + x * 4
    assert data[i + 2] > 200 and data[i + 3] == 255


def test_library_drops_skin_tones_and_unsupported():
    data = {"width": 32, "icons": {"thumbs-up": {"body": SQUARE}, "thumbs-up-dark": {"body": SQUARE},
                                    "rainbow": {"body": '<path fill="url(#a)" d="M0 0h1v1z"/><linearGradient id="a"/>'},
                                    "keycap-1": {"body": SQUARE}}}
    lib = EmojiLibrary.from_json(data)
    assert lib.names == ["thumbs-up"]


def test_csls_penalises_hubs():
    sim = np.array([[0.9, 0.6], [0.2, 0.6], [0.1, 0.6]])  # item 1 is equally close to everything
    s = csls(sim, k=2)
    assert s[0, 0] > s[0, 1]


def test_drawings_tagged_on_the_word_without_changing_words(emb):
    lib = lib_with("house", "oil drum", "banknote", "rocket", "pill", "tree", "dog", "car", "bank", "phone", "clock", "shoe")
    script = "[curious] [news] Prices of every house rose again this year.\n\nThen oil got expensive.\n"
    tagged, notes = tag_drawings(script, "en", lib, emb, model=None)
    assert "[icon house] house" in tagged
    assert extract_cues(tagged)[0].split() == extract_cues(script)[0].split()  # same narration
    again, notes2 = tag_drawings(tagged, "en", lib, emb, model=None)
    assert again == tagged and "left as they are" in notes2[0]


def test_strict_choice_keeps_one_drawing_per_phrase():
    rows = [[(0.9, 3, "phone", "telephone", 0.92, 0.6), (0.95, 2, "mobile phone", "mobile-phone", 0.95, 0.7),
             (0.7, 6, "moon", "crescent-moon", 0.70, 0.5)]]
    assert [r[3] for r in choose_strict(rows)[0]] == ["mobile-phone"]


def test_model_choice_must_come_from_the_shortlist(monkeypatch, emb):
    from engine.explainer import drawings

    lib = lib_with("house", "lungs", "window", "rocket", "pill", "tree", "dog", "car")
    replies = iter(['[{"sentence": 1, "word": "breath", "drawing": "lungs"}, {"sentence": 2, "word": "window", "drawing": "window"},'
                    ' {"sentence": 2, "word": "x", "drawing": "unicorn"}]'])
    monkeypatch.setattr(draft, "ollama", lambda *a, **k: next(replies))
    script = "Do not hold your breath lungs.\n\nA shop puts a sign in the window.\n"
    tagged, _ = drawings.tag_drawings(script, "en", lib, emb, model="qwen")
    assert "[icon window] window" in tagged and "unicorn" not in tagged


def test_semantic_gap_gives_untagged_paragraphs_evidence(emb):
    sentences = ["Investors move money to America when interest rates there are high and rising fast.",
                 "The football season starts with a match between two local clubs this weekend.",
                 "A new bakery opened downtown and sells bread, cakes and fresh coffee daily.",
                 "Heavy rain is expected across the northern hills during the coming week."]
    text = " ".join(sentences)
    words = [(w, i * 10.0, 0.0, i * 10.0 + 8, 10.0) for i, w in enumerate(text.split())]
    arts = {"1": Article("1", None, words=words)}
    paras = {"1": sentences}
    script = "Why is the dollar so strong?\n\nInterest rates in America are high, so investors move money there.\n"
    # as for an Urdu script: no shared English words, only the meaning can link it to the article
    tagged, notes = draft.auto_tag(script, "ur", arts, paras, [], True, emb=emb)
    assert '[article 1 "Investors move money' in tagged and any("meaning match" in n for n in notes)
    plain, _ = draft.auto_tag(script, "ur", arts, paras, [], True)  # without the embedder: the rules alone
    assert "[article" not in plain


def test_board_is_never_blank_between_sentences(tmp_path, ex_cfg):
    topic = make_topic(tmp_path)
    with open(topic / "english.txt", "a") as f:
        f.write("\nOwners stay in their homes. Banks lend less money. Families wait for lower prices next year.\n")
    _, lay = build_layout(topic, ex_cfg)
    end = lay.scenes[-1].end
    t = 0.6
    while t < end - 0.1:
        sc = next(s for s in reversed(lay.scenes) if s.start <= t)
        stage = any(st.t_in <= t < st.t_out for st in lay.stages)
        sprite = any(sp.t <= t and (sp.end is None or t < sp.end) for sp in sc.sprites)
        assert stage or sprite, f"blank board at {t:.1f}s"
        t += 0.25


def test_emoji_names_render_as_sprites(ex_cfg, monkeypatch):
    monkeypatch.setattr(layout_mod, "_EMOJI", [lib_with("oil-drum", "fuel-pump")])
    lay = layout_mod.ExplainerLayout(ex_cfg, "en", None, 0.5)
    sp = lay.icon_sprite("oil-drum", {}, (100, 800, 500, 1200), 1.0, "s1")
    assert sp is not None and sp.anim == "draw" and sp.strokes
    assert lay.icon_sprite("house", {}, (100, 800, 500, 1200), 1.0, "s1").name == "house"  # hand-made doodle still wins
