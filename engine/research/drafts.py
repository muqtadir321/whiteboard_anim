"""Optional script drafts from a local, free LLM served by Ollama (http://127.0.0.1:11434).

Opt-in only. The model is told to use only the evidence headlines and to keep numbers as
{VERIFY} placeholders unless a headline states them, and every draft is labelled DRAFT.
"""

from __future__ import annotations

import json
import urllib.request

from .analysis import Idea
from .briefs import outline, title_case

LANG_NAMES = {"english": "English", "roman_urdu": "Roman Urdu (Urdu written in Latin letters, simple everyday words)"}


def prompt_for(idea: Idea, language: str) -> str:
    headlines = "\n".join(f"- {e['title']}" for e in idea.evidence[:6]) or "- (none)"
    return (
        f"Write a 2 to 3 minute YouTube Shorts narration in {LANG_NAMES.get(language, language)} about: {title_case(idea.topic)}.\n"
        "Audience: ordinary people who want to understand money. Style: short spoken sentences, a curious hook question first, "
        "then real-number breakdown, a common mistake, three questions to ask, and a one-line call to follow.\n"
        "Start every paragraph with a voice tag in square brackets such as [curious], [calm], [measured], [serious], [pause], [confident], [warm].\n"
        "Write numbers out in words (for example 'three thousand dirham').\n"
        "Use ONLY facts from these recent headlines. If a number is not in them, write {VERIFY: what number is needed} instead of guessing:\n"
        f"{headlines}\n\n"
        f"Follow this outline:\n{outline(idea.topic, language)}\n\n"
        "Return only the narration text."
    )


def draft_script(idea: Idea, language: str, model: str, host: str = "http://127.0.0.1:11434", timeout: float = 600) -> str:
    body = json.dumps({"model": model, "prompt": prompt_for(idea, language), "stream": False,
                       "options": {"temperature": 0.6, "num_predict": 900}}).encode()
    req = urllib.request.Request(f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read()).get("response", "").strip()
