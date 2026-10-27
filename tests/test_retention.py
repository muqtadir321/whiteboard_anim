"""Retention pack: hook card, word-by-word captions, no dead air, no repeated lead drawing, sound effects, music."""

import shutil
import subprocess
import wave

import cairo
import pytest

from engine.audio_fx import events_from_layout, render_track
from engine.audio_processor import resolve_music
from engine.config import load_config
from engine.explainer import captions
from engine.explainer.layout import ExplainerLayout, Layout
from engine.explainer.render import ExScene, Sprite, Word
from engine.quality_checker import QualityReport, check_explainer

from .conftest import ROOT
from .test_explainer import build_layout, make_topic


@pytest.fixture
def ex_cfg():
    return load_config(overrides={"animation": {"style": "explainer"}, "video": {"quality": "fast"}}, repo_root=ROOT)


# -- captions ---------------------------------------------------------------------------
def test_caption_chunks_break_at_punctuation_pauses_and_length():
    toks = "Prices rise. So each dollar buys a little less".split()
    times = [(i * 0.3, i * 0.3 + 0.25) for i in range(len(toks))]
    times[5] = (times[5][0] + 1.0, times[5][1] + 1.0)  # a pause before "buys"
    times[6:] = [(a + 1.0, b + 1.0) for a, b in times[6:]]
    groups = captions.group(toks, times)
    assert groups[0] == [0, 1]  # "Prices rise." ends a chunk
    assert all(len(g) <= captions.MAX_WORDS for g in groups)
    assert [toks[i] for i in groups[2]][0] == "buys"  # the pause starts a new chunk


def test_caption_chunks_skip_the_hook_and_fit_the_band():
    toks = "One crash. Eighty thousand in bills. Your policy pays twenty five thousand.".split()
    times = [(i * 0.35, i * 0.35 + 0.3) for i in range(len(toks))]
    chunks = captions.build(toks, times, "en", start_after=0.7)
    assert chunks and chunks[0].t0 >= 0.65  # "One crash." is on the hook card already
    for ch in chunks:
        assert captions.BAND[0] - 30 <= ch.box[0] and ch.box[2] <= captions.BAND[2] + 30
        assert ch.t1 > ch.t0


def test_urdu_captions_run_right_to_left():
    toks = "مہنگائی ہر سال بڑھتی ہے".split()
    times = [(i * 0.4, i * 0.4 + 0.35) for i in range(len(toks))]
    ch = captions.build(toks, times, "ur")[0]
    xs = [w.bbox[0] for w in ch.words]
    assert xs == sorted(xs, reverse=True)  # the first word said is on the right


# -- hook card ----------------------------------------------------------------------------
def _layout(cfg):
    return ExplainerLayout(cfg, "en", None, 1.0)


def test_hook_text_prefers_tag_then_short_first_sentence(ex_cfg):
    from engine.explainer.cues import Cue
    from engine.explainer.layout import TimedCue

    lay = _layout(ex_cfg)
    toks = "Your $100 buys less every year. Here is why.".split()
    assert lay.hook_text([], toks, {"scenes": [{}]}) == "Your $100 buys less every year."
    tagged = [TimedCue(Cue("hook", 0, ref="$100 → $74"), 0.0, 1.0)]
    assert lay.hook_text(tagged, toks, {"scenes": [{}]}) == "$100 → $74"
    long = "How much liability insurance do you actually need for your car today really?".split()
    assert lay.hook_text([], long, {"scenes": [{}]}).endswith("…")


def test_hook_card_words_pop_and_clear_mo(ex_cfg):
    lay = _layout(ex_cfg)
    toks = "One crash can cost you $80,000. Here is how.".split()
    times = [(i * 0.3, i * 0.3 + 0.25) for i in range(len(toks))]
    L = Layout([], [])
    words, sprites = lay.hook_card({"scenes": [{}]}, [], toks, times, [1.8], L)
    assert L.hook_text == "One crash can cost you $80,000." and 1.2 <= L.hook_end <= 2.2
    assert all(w.pop and w.end == L.hook_end and w.t < 0 for w in words)  # all on screen from the first frame
    assert any(w.underline for w in words if "$" in w.text)  # the amount is underlined in red
    mo = sprites[0]
    assert mo.name == "mascot" and mo.box[1] > max(w.bbox[3] for w in words)  # Mo stands below the words


def test_layout_starts_after_hook_with_captions(tmp_path, ex_cfg):
    _, lay = build_layout(make_topic(tmp_path), ex_cfg)
    assert lay.hook_end > 1.0 and lay.captions
    assert all(st.t_in >= lay.hook_end - 1e-6 for st in lay.stages)  # no screenshot over the hook
    need = ex_cfg["animation"]["explainer"]["min_evidence_seconds"] - 0.05
    assert all(st.t_out - st.t_in >= need for st in lay.stages[:-1])  # pushed-back evidence keeps its time
    later = [it for sc in lay.scenes for it in list(sc.words) + list(sc.sprites) if getattr(it, "end", None) != lay.hook_end]
    assert all(it.t >= lay.hook_end - 1e-6 or it.t < 0 for it in later)


def test_captions_off_and_hook_off(tmp_path, ex_cfg):
    ex_cfg["animation"]["explainer"].update(captions="off", hook="off", mascot="off")
    _, lay = build_layout(make_topic(tmp_path), ex_cfg)
    assert not lay.captions and lay.hook_end == 0.0
    assert not [sp for sc in lay.scenes for sp in sc.sprites if sp.name == "mascot"]


def test_caption_and_hook_are_painted(tmp_path, ex_cfg):
    r, lay = build_layout(make_topic(tmp_path), ex_cfg)
    r.karaoke, r.hook_end = lay.captions, lay.hook_end
    frame = cairo.ImageSurface(cairo.FORMAT_RGB24, r.width, r.height)
    ch = lay.captions[0]
    sc = next(s for s in reversed(lay.scenes) if s.start <= ch.t0)
    r.paint_frame(cairo.Context(frame), sc, lay.stages, (ch.words[0].t0 + ch.words[0].t1) / 2)
    buf, stride = frame.get_data(), frame.get_stride()
    y = int((ch.box[1] + ch.box[3]) / 2 * r.scale)
    row = [buf[y * stride + 4 * x: y * stride + 4 * x + 3] for x in range(0, r.width, 2)]
    assert any(px[2] > 200 and px[1] > 180 and px[0] < 120 for px in row)  # yellow marker behind the spoken word


# -- no dead air ----------------------------------------------------------------------------
def test_static_gap_gets_a_circle_and_qa_reports_leftovers(ex_cfg):
    lay = _layout(ex_cfg)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
    hero = Sprite(surf, (200, 400, 800, 1000), 0.5, "draw", name="house", draw=0.6)
    layout = Layout([ExScene("scene_001", 0.0, 10.0, [], [hero])], [])
    lay.fill_static_gaps(layout, 10.0)
    rings = [sp for sp in layout.scenes[0].sprites if sp.name == "emphasis"]
    assert len(rings) == 1 and 1.0 < rings[0].t < 2.0  # soon after the board went still, one circle per drawing
    assert layout.static_gaps  # the rest of a 10 s still board is still reported
    report = QualityReport()
    check_explainer(report, layout, ex_cfg)
    check = next(c for c in report.checks if c.name == "max_static_seconds")
    assert not check.passed and not check.critical


def test_lead_drawing_not_repeated_on_next_board(tmp_path, ex_cfg):
    topic = make_topic(tmp_path)
    (topic / "english.txt").write_text(
        "Insurance protects your house.\n\nInsurance also protects your car and your bank account.\n\n"
        "Insurance pays when your house burns and your car is stolen.\n", encoding="utf-8")
    _, lay = build_layout(topic, ex_cfg)
    leads = [b.nodes[0].name for b in lay.beats if b.nodes]
    assert all(a != b for a, b in zip(leads, leads[1:]))


# -- sound ------------------------------------------------------------------------------------
def test_effects_track_length_and_merged_pops(tmp_path):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4)
    words = [Word([], "#000000", 0.1 + 0.05 * k, 0.2, (0, 0, 1, 1), f"w{k}", pop=True) for k in range(5)]
    sprites = [Sprite(surf, (0, 0, 1, 1), 2.0, "draw", name="house", draw=0.8)]
    layout = Layout([ExScene("s1", 0.0, 4.0, words, sprites)], [])
    ev = events_from_layout(layout)
    assert [e[0] for e in ev] == ["pop"]  # five hook words: one pop; drawing makes no sound
    path = render_track(ev, 4.0, tmp_path / "fx.wav")
    with wave.open(str(path)) as wf:
        assert wf.getnchannels() == 2 and abs(wf.getnframes() / wf.getframerate() - 4.0) < 0.01


def test_resolve_music(tmp_path):
    assert resolve_music("auto", tmp_path)[0] is None
    (tmp_path / "README.txt").write_text("x")
    (tmp_path / "calm.mp3").write_bytes(b"0")
    assert resolve_music("auto", tmp_path)[0].endswith("calm.mp3")
    assert resolve_music("generated", tmp_path) == (None, "generated pad")
    assert resolve_music("my/track.wav", tmp_path)[0] == "my/track.wav"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_tighten_cuts_pauses_and_speeds_up(tmp_path):
    from engine.audio_processor import probe, tighten

    src = tmp_path / "talk.wav"  # 1 s of "speech", 1 s of silence, five times
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=9",
                    "-af", "volume='if(lt(mod(t,2),1),1,0)':eval=frame", "-ar", "48000", "-ac", "1", str(src)], check=True)
    info, summary = tighten(src, tmp_path / "tight.wav", speed=1.0, max_pause=0.25)
    assert summary["pauses_shortened"] == 4 and abs(info.duration - (9 - 4 * 0.75)) < 0.25  # four 1 s pauses -> 0.25 s
    fast, s2 = tighten(src, tmp_path / "fast.wav", speed=1.25, max_pause=0.0)
    assert abs(fast.duration - 9 / 1.25) < 0.1 and s2["pauses_shortened"] == 0
    same, s3 = tighten(src, tmp_path / "same.wav", speed=1.0, max_pause=0.0)
    assert abs(same.duration - probe(src).duration) < 0.01 and s3["seconds_cut"] == 0.0


def test_matching_headline_card_is_focused(ex_cfg):
    lay = _layout(ex_cfg)
    cards = [{"title": "Everything About Teen Driving", "box": [0, 0, 10, 10]},
             {"title": "How Much Car Insurance Do I Need?", "box": [0, 20, 10, 30]},
             {"title": "Stocks Fall", "box": [0, 40, 10, 50]}]
    said = set("how much liability insurance do you actually need for your car".split())
    assert lay.matching_card(cards, said)["box"] == [0, 20, 10, 30]
    assert lay.matching_card(cards[:1], said) is None  # a single card needs no focus
    lay.narration_words = ["how", "much", "everything", "teen"]
    lay.narration_times = [(0.0, 0.3), (0.3, 0.6), (9.0, 9.3), (9.3, 9.6)]
    assert lay.words_between(0.0, 2.0) == {"how", "much"}  # only what is said while the cards are up


def test_opening_moves_from_the_first_frame(ex_cfg):
    lay = _layout(ex_cfg)
    toks = "One crash can cost you $80,000. Here is how.".split()
    words, sprites = lay.hook_card({"scenes": [{}]}, [], toks, [(i * 0.3, i * 0.3 + 0.25) for i in range(len(toks))], [1.8], Layout([], []))
    assert all(w.wobble > 0 for w in words) and len({w.phase for w in words}) == len(words)  # a wave, not one pulse
    assert sprites[0].rock > 0 and sprites[0].float_amp > 0
