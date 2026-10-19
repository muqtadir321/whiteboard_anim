"""Run viral-topic research and write the report.

    python -m engine.research.cli --config research/config.yaml --out research_output

Outputs: report.md (ranked ideas), ideas.json, signals.json, sources.json and one brief per
idea in briefs/ (SEO titles, hooks, description, tags, script outlines, optional drafts).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

from . import sources as S
from .analysis import rank_topics
from .briefs import brief_markdown, slug, title_case

NOT_COVERED = ("X (Twitter), TikTok and Instagram are not crawled: they offer no free official access and their terms "
               "forbid scraping. Their trends usually show up on YouTube, Google Trends and news within hours.")


def _split(v: str) -> list[str]:
    return [x.strip() for x in v.replace("\n", ",").split(",") if x.strip()]


def load_config(path: str, args: argparse.Namespace) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) if path and Path(path).exists() else {}
    cfg.setdefault("sources", {})
    if args.seeds:
        cfg["seed_keywords"] = _split(args.seeds)
    if args.extra_seeds:
        cfg["seed_keywords"] = list(dict.fromkeys(cfg.get("seed_keywords", []) + _split(args.extra_seeds)))
    if args.regions:
        cfg["regions"] = [r.upper() for r in _split(args.regions)]
    if args.ideas:
        cfg["ideas"] = args.ideas
    if not cfg.get("seed_keywords"):
        raise SystemExit("no seed keywords: set seed_keywords in research/config.yaml or the niche_keywords input")
    cfg.setdefault("regions", ["US"])
    return cfg


def collect(cfg: dict, api_key: str) -> tuple[list[S.Signal], dict[str, list[str]], list[S.SourceStatus]]:
    src = cfg["sources"]
    seeds, regions = cfg["seed_keywords"], cfg["regions"]
    timeout = float(cfg.get("request_timeout_seconds", 20))
    statuses, signals, auto = [], [], {}

    def run(name, enabled, fn):
        st = S.SourceStatus(name)
        statuses.append(st)
        if not enabled:
            st.errors.append("disabled in research/config.yaml")
            return None
        print(f"[research] {name} ...", flush=True)
        res = fn(st)
        print(f"[research] {name}: {st.signals} signals, {st.ok} ok, {st.failed} failed", flush=True)
        return res

    signals += run("google_trends", src.get("google_trends", True), lambda st: S.collect_google_trends(regions, timeout, st)) or []
    signals += run("google_news", src.get("google_news", True), lambda st: S.collect_google_news(seeds, regions, timeout, st)) or []
    signals += run("youtube_channels", src.get("youtube_channels", True),
                   lambda st: S.collect_youtube_channels(cfg.get("youtube_channels", []), timeout, st, api_key)) or []
    signals += run("youtube_api", src.get("youtube_api", True),
                   lambda st: S.collect_youtube_api(seeds, regions, api_key, int(cfg.get("lookback_days", 10)), timeout, st)) or []
    signals += run("reddit", src.get("reddit", True), lambda st: S.collect_reddit(cfg.get("reddit_subreddits", []), timeout, st)) or []
    auto = run("youtube_autocomplete", src.get("youtube_autocomplete", True), lambda st: S.collect_autocomplete(seeds, timeout, st)) or {}
    return signals, auto, statuses


def report_markdown(cfg: dict, ideas, statuses, files: dict[str, str], now: dt.datetime) -> str:
    lines = [f"# Viral topic research: {cfg.get('niche', 'finance')}", "",
             f"Generated {now.strftime('%Y-%m-%d %H:%M UTC')} · regions {', '.join(cfg['regions'])} · "
             f"{len(cfg['seed_keywords'])} seed keywords", ""]
    if ideas:
        lines += ["## Top ideas", "", "| # | Topic | Potential | Score | Signals from | Best evidence |", "|---|---|---|---|---|---|"]
        for i, idea in enumerate(ideas, 1):
            ev = idea.evidence[0] if idea.evidence else {}
            best = ev.get("title", "")[:80].replace("|", "/")
            if ev.get("views_per_hour"):
                best += f" ({ev['views_per_hour']:,.0f} views/h)"
            elif ev.get("traffic"):
                best += f" ({ev['traffic']:,}+ searches)"
            lines.append(f"| {i} | [{title_case(idea.topic)}]({files.get(idea.topic, '')}) | {idea.potential} | {idea.score} | "
                         f"{', '.join(idea.sources)} | {best} |")
    else:
        lines += ["## No ideas found", "", "No finance topic had enough signals. Check the source table below."]
    lines += ["", "## Sources", "", "| Source | Requests ok | Failed | Signals | Note |", "|---|---|---|---|---|"]
    for st in statuses:
        note = (st.errors[0] if st.errors else "").replace("|", "/")[:120]
        lines.append(f"| {st.name} | {st.ok} | {st.failed} | {st.signals} | {note} |")
    lines += ["", "## How to read this", "",
              "- **Score** mixes YouTube momentum (views per hour of recent uploads), Google Trends traffic, fresh news, "
              "Reddit discussion, YouTube search suggestions and how many sources agree. It is relative to this run, not a promise of views.",
              "- **High** = close to the strongest topic in this run; **Emerging** = early signal, less competition.",
              "- Each brief has SEO titles, hooks, description, tags and a script outline. Fill in the `{VERIFY}` numbers from the "
              "evidence links, save the script to `input/scripts/`, add your voice-over to `input/audio/` (optional) and run "
              "**Generate Whiteboard Video**.",
              f"- {NOT_COVERED}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="research/config.yaml")
    ap.add_argument("--out", default="research_output")
    ap.add_argument("--seeds", default="", help="comma-separated keywords replacing seed_keywords")
    ap.add_argument("--extra-seeds", default="", help="comma-separated keywords added to seed_keywords")
    ap.add_argument("--regions", default="")
    ap.add_argument("--ideas", type=int, default=0)
    ap.add_argument("--draft-model", default="", help="Ollama model for DRAFT scripts (empty = no drafts)")
    ap.add_argument("--draft-languages", default="english")
    ap.add_argument("--draft-top", type=int, default=3)
    args = ap.parse_args(argv)

    cfg = load_config(args.config, args)
    out = Path(args.out)
    (out / "briefs").mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()

    signals, auto, statuses = collect(cfg, api_key)
    ideas = rank_topics(signals, auto, cfg["seed_keywords"], int(cfg.get("ideas", 10)), now)
    print(f"[research] {len(signals)} signals -> {len(ideas)} ideas", flush=True)

    files: dict[str, str] = {}
    for i, idea in enumerate(ideas, 1):
        draft = {}
        if args.draft_model and i <= args.draft_top:
            from .drafts import draft_script
            for lang in _split(args.draft_languages):
                try:
                    print(f"[research] drafting {lang} script for '{idea.topic}' ...", flush=True)
                    draft[lang] = draft_script(idea, lang, args.draft_model)
                except Exception as exc:  # drafts are a bonus; never fail the research
                    print(f"[research] draft failed: {exc}", flush=True)
        name = f"briefs/{i:02d}-{slug(idea.topic)}.md"
        (out / name).write_text(brief_markdown(i, idea, now.year, draft), encoding="utf-8")
        files[idea.topic] = name

    report = report_markdown(cfg, ideas, statuses, files, now)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "ideas.json").write_text(json.dumps([dict(idea.to_dict(), brief=files[idea.topic]) for idea in ideas],
                                               indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "signals.json").write_text(json.dumps([s.to_dict() for s in signals], indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "autocomplete.json").write_text(json.dumps(auto, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "sources.json").write_text(json.dumps([s.to_dict() for s in statuses], indent=1), encoding="utf-8")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(re.sub(r"\[([^\]]+)\]\(briefs/[^)]*\)", r"\1", report) + "\n\nDownload the **viral-topic-research** artifact for the full briefs.\n")
    if not signals:
        print("[research] ERROR: every source failed; see sources.json", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
