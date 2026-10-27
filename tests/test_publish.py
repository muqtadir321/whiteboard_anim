"""Publishing kit, script checker, story-format briefs and source ranking."""

import json
import shutil
import subprocess

import pytest

from engine.explainer.sources import NewsItem
from engine.publish import build_kit, description, short_topic, takeaways, titles
from engine.research.briefs import shorts_outline
from engine.script_lint import lint

INSURANCE = ("How much liability insurance do you actually need for your car? Every state sets a minimum amount of liability "
             "coverage. But here’s the catch: the minimum is rarely enough. Medical bills, lost wages, legal fees. "
             "So don’t just buy the minimum. Buy enough to protect everything you’ve worked for.")


def test_titles_fit_and_keep_the_question():
    opts = titles(INSURANCE, "how much car liability insurance do you need")
    assert 3 <= len(opts) <= 5 and all(len(t) <= 60 for t in opts)
    assert "How Much Car Liability Insurance Do You Need?" in opts
    assert any("Car Liability Insurance" in t for t in opts)


def test_short_topic_without_a_topic_uses_the_repeated_phrase():
    text = "Inflation eats purchasing power. Your purchasing power shrinks every year. Prices rise."
    assert short_topic("", text) == "purchasing power"


def test_description_lists_takeaways_sources_and_disclaimer():
    d = description(INSURANCE.replace("’", "'"), "car liability insurance", ["Forbes: https://forbes.com/a"])
    assert "• So don't just buy the minimum." in d
    assert "Sources:\n• Forbes: https://forbes.com/a" in d
    assert "not financial advice" in d and "#CarLiabilityInsurance" in d and "#Shorts" in d
    assert len(takeaways(INSURANCE)) == 3


def test_build_kit_writes_everything(tmp_path):
    job = tmp_path / "english"
    job.mkdir()
    (job / "video.mp4").write_bytes(b"0")
    (job / "thumbnail.png").write_bytes(b"png")
    (job / "sources.txt").write_text("Sources:\n- Forbes: Cheapest Insurance https://forbes.com/a\n- AARP: no link\n", encoding="utf-8")
    out = build_kit(job, '[hook "One crash. $80,000."] ' + INSURANCE, "car liability insurance", synthetic_voice=True)
    names = {p.name for p in out.iterdir()}
    assert names == {"titles.txt", "description.txt", "tags.txt", "pinned_comment.txt", "thumbnail.png", "upload.json"}
    assert (out / "titles.txt").read_text(encoding="utf-8").splitlines()[0] == "One crash. $80,000."  # the hook tag leads
    up = json.loads((out / "upload.json").read_text(encoding="utf-8"))
    assert up["snippet"]["categoryId"] == "27" and up["status"]["privacyStatus"] == "private"
    assert up["status"]["containsSyntheticMedia"] is True  # a generated/cloned voice must be disclosed
    assert "https://forbes.com/a" in up["snippet"]["description"] and "no link" not in up["snippet"]["description"]


def test_build_kit_keeps_the_hook_thumbnail(tmp_path):
    job = tmp_path / "english"
    (job / "publish").mkdir(parents=True)
    (job / "video.mp4").write_bytes(b"0")
    (job / "thumbnail.png").write_bytes(b"scene")
    (job / "publish" / "thumbnail.png").write_bytes(b"hook")
    build_kit(job, INSURANCE)
    assert (job / "publish" / "thumbnail.png").read_bytes() == b"hook"


# -- script checker ---------------------------------------------------------------------
def test_lint_flags_a_weak_hook_and_passes_a_strong_script():
    weak = lint("In this video we talk about insurance and why it matters for families in many different situations today. " * 1)
    msgs = " ".join(n.message for n in weak)
    assert "generic phrase" in msgs and "no number and no stakes" in msgs and "example amount" in msgs
    strong = lint("One crash can cost you $80,000. Your policy pays $25,000. You pay the rest. "
                  "Minimum cover is cheap for a reason. Buy more than the minimum. "
                  "Because one crash can cost you $80,000, and the minimum will not cover it." * 1)
    assert not [n for n in strong if n.level == "fix"], strong


def test_lint_never_changes_text_and_flags_promises():
    text = "[hook \"Guaranteed returns?\"] This fund is guaranteed to double your money. You get 10% a year."
    notes = lint(text)
    assert any("financial promise" in n.message for n in notes)


def test_shorts_outline_has_hook_tag_and_callback():
    out = shorts_outline("car insurance")
    assert out.startswith('[hook "One car insurance mistake.')
    assert out.splitlines()[-1].startswith("Because one car insurance mistake")


def test_affiliate_rankings_rank_below_neutral_sources():
    def rank(title, url):
        return NewsItem(title, "", url.split("/")[2], url, None).rank()

    assert rank("Auto insurance basics", "https://content.naic.org/x") == 0
    assert rank("Cheapest Liability-Only Car Insurance Of 2026", "https://www.forbes.com/x") == 2
    assert rank("Best Car Insurance Companies", "https://www.nerdwallet.com/x") == 2
    assert rank("What liability coverage pays for", "https://www.investopedia.com/x") == 1


# -- long-form compiler -------------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_compile_two_shorts_into_chaptered_long_video(tmp_path):
    from engine.compile_long import CARD_SECONDS, compile_long, probe_duration

    clips = []
    for i, (sec, freq) in enumerate(((3.0, 330), (4.0, 440))):
        p = tmp_path / f"short_{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=216x384:rate=30:duration={sec}",
                        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={sec}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(p)], check=True)
        clips.append(p)
    out = tmp_path / "long" / "series.mp4"
    summary = compile_long(clips, out, "Money Basics", ["First lesson", "Second lesson"], log=lambda m: None)
    assert abs(probe_duration(out) - (3.0 + 4.0 + 2 * CARD_SECONDS)) < 0.3
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert probe == "1920,1080"
    assert summary["chapters"] == ["0:00 First lesson", "0:04 Second lesson"]
    assert not summary["youtube_chapters_ok"]  # YouTube needs 3+ chapters of 10 s
    desc = (out.parent / "series" / "publish" / "description.txt").read_text(encoding="utf-8")
    assert "Chapters:\n0:00 First lesson\n0:04 Second lesson" in desc
