"""Semantic scene planner: narration -> structured, validated visual actions.

Layer 1  deterministic rules (quantities, trends, relations, transactions)
Layer 2  multilingual concept classification (concept_mapper)
Layer 3  optional local LLM (Ollama-compatible) for sentences the rules miss
Layer 4  validation against the narration (numbers preserved, no invented data)

Confidence values are computed from the evidence that produced each plan
(see `confidence_basis` in every scene); they are never hard-coded.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import urllib.request
from dataclasses import dataclass, field

from .concept_mapper import CONCEPTS, ConceptMatch, content_tokens, match_concepts
from .numbers import Quantity, extract_quantities
from .scene_schema import SCHEMA_VERSION, validate_plan

HOLDABLE = {"coin", "wallet", "phone", "book", "money_bag", "banknote", "key", "document", "lightbulb", "coin_stack", "briefcase", "envelope", "pill", "magnifier"}
BIG = {"house", "building", "shop", "factory", "bank", "school", "hospital", "car", "truck", "tree", "globe"}
ACTOR_VISUAL = {"person": "person", "people": "people_group", "customer": "people_group", "worker": "worker", "employer": "boss", "student": "student", "teacher": "teacher"}
# How an entity concept is drawn when it is the "thing" in a scene.
ENTITY_VISUAL_OVERRIDE = {"investment": "money_growth", "salary": "banknote", "payment": "coin", "buy": "cart", "sell": "shop", "work": "desk_work"}

GENERIC_CONCEPTS = {"time", "year", "question", "research", "work", "people", "world", "city"}

PRONOUN_ACTORS = {"he", "she", "i", "you", "we", "they", "main", "mai", "aap", "tum", "woh", "wo", "hum", "میں", "آپ", "تم", "وہ", "ہم", "زه", "ته", "تاسو", "هغه", "موږ"}


@dataclass
class Segment:
    text: str
    start: float
    end: float
    sentence_index: int
    words: list[dict] = field(default_factory=list)  # optional [{word,start,end}]


@dataclass
class Analysis:
    text: str
    lang: str
    matches: list[ConceptMatch]
    quantities: list[Quantity]
    actors: list[ConceptMatch]
    entities: list[ConceptMatch]
    trend: str | None
    trend_match: ConceptMatch | None
    relations: dict[str, ConceptMatch]
    emotion: str | None
    actions: list[ConceptMatch]
    coverage: float
    llm_assisted: bool = False


def _rng_for(text: str, seed: int, salt: str = "") -> random.Random:
    h = hashlib.sha256(f"{seed}|{salt}|{text}".encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def analyze(text: str, lang: str, extra_concepts: list[str] | None = None) -> Analysis:
    matches = match_concepts(text, lang)
    if extra_concepts:
        for cid in extra_concepts:
            if cid in CONCEPTS and all(m.concept != cid for m in matches):
                spec = CONCEPTS[cid]
                matches.append(ConceptMatch(cid, spec["kind"], spec["visual"], cid, 0, 0, "", False))
    quantities = extract_quantities(text, lang)
    strong_actors = [m for m in matches if m.kind == "actor" and not m.weak]
    weak_actors = [m for m in matches if m.kind == "actor" and m.weak and m.keyword in PRONOUN_ACTORS]
    actors = strong_actors or weak_actors[:1]
    entities = []
    seen_visual = set()
    for m in matches:
        if m.kind in ("entity", "action") and not m.weak:
            vis = ENTITY_VISUAL_OVERRIDE.get(m.concept, m.visual)
            if vis and vis not in seen_visual:
                seen_visual.add(vis)
                entities.append(m)
    # Specific things outrank generic ones ("money" over "time") as the scene subject.
    entities.sort(key=lambda m: (m.concept in GENERIC_CONCEPTS, m.start))
    actions = [m for m in matches if m.kind == "action"]
    trend = None
    trend_match = None
    for m in matches:
        if m.kind == "trend":
            # weak trend words ("more", "less") need a number or an entity to mean a trend
            if m.weak and not (quantities or entities):
                continue
            trend = "up" if m.concept == "increase" else "down"
            trend_match = m
            break
    if trend is None and any(q.kind == "multiplier" and q.value >= 2 for q in quantities):
        trend = "up"
    relations: dict[str, ConceptMatch] = {}
    for m in matches:
        if m.kind == "relation" and m.concept not in relations:
            if m.concept == "cause" and m.keyword in ("so", "نو", "makes") and len(entities) + len(actors) < 2:
                continue
            relations[m.concept] = m
    emotion = None
    for m in matches:
        if m.kind == "emotion":
            emotion = m.concept
            break
    tokens = content_tokens(text, lang)
    covered = 0
    for tok, s, e in tokens:
        if any(m.start <= s < m.end or s <= m.start < e for m in matches) or any(q.span[0] <= s < q.span[1] for q in quantities):
            covered += 1
    coverage = covered / len(tokens) if tokens else 0.0
    return Analysis(text, lang, matches, quantities, actors, entities, trend, trend_match, relations, emotion, actions, coverage)


def _local_llm_concepts(text: str, cfg: dict) -> list[str]:
    """Ask an optional local open-weight model to pick concepts from our vocabulary."""
    llm = cfg["planner"]["local_llm"]
    if not llm.get("enabled"):
        return []
    vocab = sorted(c for c, s in CONCEPTS.items() if s["kind"] in ("entity", "actor", "action", "trend"))
    prompt = (
        "Pick up to 3 concept ids from this list that best illustrate the sentence. "
        "Answer with a JSON array of ids only.\nIDs: " + ", ".join(vocab) + f"\nSentence: {text}\n"
    )
    body = json.dumps({"model": llm["model"], "prompt": prompt, "stream": False}).encode()
    try:
        req = urllib.request.Request(llm["endpoint"], data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            answer = json.loads(resp.read().decode()).get("response", "")
        ids = json.loads(re.search(r"\[.*?\]", answer, re.S).group(0))
        return [i for i in ids if i in vocab][:3]
    except Exception:
        return []


class PlanBuilder:
    """Accumulates visual actions for one scene."""

    def __init__(self, text: str):
        self.text = text
        self.actions: list[dict] = []

    def _add(self, action: dict) -> str:
        aid = f"a{len(self.actions) + 1}"
        action = {"id": aid, **action, "draw_order": len(self.actions) + 1}
        self.actions.append(action)
        return aid

    def draw(self, obj: str, position: str, anchor: int = 0, params: dict | None = None, emphasis: bool = False, scale: float = 1.0) -> str:
        a = {"action": "draw", "object": obj, "position": position, "anchor_char": max(0, anchor)}
        if params:
            a["params"] = params
        if emphasis:
            a["emphasis"] = True
        if scale != 1.0:
            a["scale"] = round(scale, 3)
        return self._add(a)

    def write(self, text: str, position: str, anchor: int = 0, style: str = "label", color: str = "ink") -> str:
        return self._add({"action": "write", "text": text, "position": position, "anchor_char": max(0, anchor), "style": style, "color": color})

    def connect(self, source: str, target: str, anchor: int = 0, color: str = "accent") -> str:
        return self._add({"action": "connect", "object": "arrow", "source": source, "target": target, "position": "between", "anchor_char": max(0, anchor), "color": color})

    def duplicate(self, source: str, position: str, anchor: int = 0) -> str:
        return self._add({"action": "duplicate", "source": source, "position": position, "anchor_char": max(0, anchor)})

    def highlight(self, target: str, anchor: int = 0, color: str = "accent") -> str:
        return self._add({"action": "highlight", "target": target, "position": "around", "anchor_char": max(0, anchor), "color": color})

    def cross_out(self, target: str, anchor: int = 0) -> str:
        return self._add({"action": "cross_out", "target": target, "position": "around", "anchor_char": max(0, anchor), "color": "negative"})


def _entity_visual(m: ConceptMatch) -> str:
    return ENTITY_VISUAL_OVERRIDE.get(m.concept, m.visual) or "label_box"


def _short_label(text: str, lang: str, max_words: int = 3, max_chars: int = 22) -> str:
    """A short label taken verbatim from the narration (never translated or invented)."""
    toks = content_tokens(text, lang)
    if not toks:
        words = text.split()
        return " ".join(words[:max_words])[:max_chars].strip(" ,.;:!?،۔")
    # Prefer the longest content words, keep narration order.
    ranked = sorted(toks, key=lambda t: -len(t[0]))[:max_words]
    ranked.sort(key=lambda t: t[1])
    label = " ".join(text[s:e] for _, s, e in ranked)
    while len(label) > max_chars and " " in label:
        label = label.rsplit(" ", 1)[0]
    return label


def _label_at(text: str, start: int, lang: str, max_words: int = 2) -> str:
    """Up to `max_words` content words of the narration, starting at a concept match."""
    toks = [t for t in content_tokens(text, lang) if t[1] >= start]
    if not toks:
        return _short_label(text, lang, max_words)
    toks = toks[:max_words]
    return text[toks[0][1]:toks[-1][2]]


def _phrase_label(text: str, lang: str) -> str:
    stripped = text.strip(" ,.;:!?،۔؟\"'")
    if len(stripped) <= 26:
        return stripped
    return _short_label(text, lang, max_words=4, max_chars=30)


def _actor_pose(an: Analysis, holding: bool) -> tuple[str, str]:
    expression = "happy"
    if an.emotion == "sad" or (an.trend == "down") or any(e.concept in ("problem", "loss", "debt") for e in an.entities):
        expression = "worried"
    pose = "hold" if holding else "point"
    if any(e.concept == "idea" for e in an.entities):
        pose = "think"
    elif any(e.concept == "success" for e in an.entities) or (an.emotion == "happy" and an.trend == "up"):
        pose, expression = "celebrate", "happy"
    elif expression == "worried" and not holding:
        pose = "shrug"
    return pose, expression


def _quantity_by_kind(an: Analysis, *kinds: str) -> list[Quantity]:
    return [q for q in an.quantities if q.kind in kinds]


def _write_quantities(b: PlanBuilder, qs: list[Quantity], positions: list[str], style: str = "number", color: str = "accent") -> None:
    for q, pos in zip(qs, positions):
        b.write(q.display, pos, anchor=q.span[0], style=style, color=color)


# ---------------------------------------------------------------------------
# Scene templates
# ---------------------------------------------------------------------------

def plan_chart(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    direction = an.trend or "up"
    anchor = an.trend_match.start if an.trend_match else 0
    percents = _quantity_by_kind(an, "percent")
    amounts = _quantity_by_kind(an, "currency", "count")
    mults = [q for q in an.quantities if q.kind == "multiplier" and q.value >= 1]
    subject = an.actors[0] if an.actors else None
    entity = an.entities[0] if an.entities else None
    mirror = variant % 2 == 1
    side, other = ("bottom_left", "bottom_right") if not mirror else ("bottom_right", "bottom_left")
    if subject:
        b.draw(ACTOR_VISUAL.get(subject.concept, "person"), side, anchor=subject.start, params={"pose": "point", "expression": "happy" if direction == "up" else "worried"}, scale=0.9)
    elif entity:
        b.draw(_entity_visual(entity), side, anchor=entity.start, scale=0.8)
    use_bars = bool(len(amounts) >= 2 or mults or percents) if variant % 3 != 2 else bool(len(amounts) >= 2)
    if len(amounts) >= 2:
        vals = [q.value for q in amounts[:4]]
        chart = b.draw("bar_chart", "upper", anchor=amounts[0].span[0], params={"values": vals, "value_labels": [q.display for q in amounts[:4]], "direction": direction})
    elif mults:
        m = mults[0].value
        base_label = amounts[0].display if amounts else ""
        chart = b.draw("bar_chart", "upper", anchor=mults[0].span[0], params={"values": [1, m] if direction == "up" else [m, 1], "value_labels": [base_label, ""], "direction": direction})
        b.write(mults[0].display, "top", anchor=mults[0].span[0], style="title", color="positive")
    elif percents and use_bars:
        p = percents[0].value
        base = amounts[0].value if amounts else 100.0
        after = base * (1 + p / 100.0) if direction == "up" else base * max(0.05, 1 - p / 100.0)
        chart = b.draw("bar_chart", "upper", anchor=percents[0].span[0], params={"values": [base, after], "value_labels": [amounts[0].display if amounts else "", ""], "direction": direction})
        sign = "+" if direction == "up" else "-"
        b.write(sign + percents[0].display, "top", anchor=percents[0].span[0], style="title", color="positive" if direction == "up" else "negative")
    else:
        chart = b.draw("line_chart", "upper", anchor=anchor, params={"direction": direction, "start_label": amounts[0].display if amounts else "", "end_label": percents[0].display if percents else ""})
        if not amounts and not percents and entity and subject:
            b.draw(_entity_visual(entity), other, anchor=entity.start, scale=0.7)
    b.draw("trend_arrow", other if not (entity and subject and not amounts and not percents) else "center_right", anchor=anchor, params={"direction": direction}, scale=0.7)
    for extra in percents[1:2] + amounts[4:5]:
        b.write(extra.display, "bottom", anchor=extra.span[0], style="number")
    _ = chart
    return "chart_growth" if direction == "up" else "chart_decline"


def plan_percentage(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    q = _quantity_by_kind(an, "percent")[0]
    mirror = variant % 2 == 1
    b.draw("pie_chart", "center_left" if not mirror else "center_right", anchor=q.span[0], params={"percent": q.value})
    if an.actors:
        b.draw(ACTOR_VISUAL.get(an.actors[0].concept, "person"), "center_right" if not mirror else "center_left", anchor=an.actors[0].start, params={"pose": "point"}, scale=0.9)
    elif an.entities:
        b.draw(_entity_visual(an.entities[0]), "center_right" if not mirror else "center_left", anchor=an.entities[0].start, scale=0.8)
    b.write(q.display, "top", anchor=q.span[0], style="title", color="accent")
    if an.entities:
        b.write(an.entities[0].surface, "bottom", anchor=an.entities[0].start, style="label")
    for extra in _quantity_by_kind(an, "percent")[1:2] + _quantity_by_kind(an, "currency", "count")[:1]:
        b.write(extra.display, "bottom_right" if not mirror else "bottom_left", anchor=extra.span[0], style="number")
    return "percentage"


def plan_transaction(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    concepts = {m.concept for m in an.matches}
    money_q = _quantity_by_kind(an, "currency")
    mirror = variant % 2 == 1
    left, right = ("lower_left", "lower_right") if not mirror else ("lower_right", "lower_left")
    if "salary" in concepts or ("employer" in concepts and "worker" in concepts):
        payer = b.draw("boss", left, anchor=0, params={"pose": "give"})
        payee = b.draw("worker", right, anchor=0, params={"pose": "hold", "expression": "happy"})
        money_obj = "banknote"
    else:
        actor = an.actors[0] if an.actors else None
        payer = b.draw(ACTOR_VISUAL.get(actor.concept, "person") if actor else "person", left, anchor=actor.start if actor else 0, params={"pose": "give"})
        dest = next((e for e in an.entities if _entity_visual(e) in ("shop", "bank", "building", "house", "school", "hospital", "factory")), None)
        if "sell" in concepts and not dest:
            payee = b.draw("people_group", right, anchor=0)
        else:
            payee = b.draw(_entity_visual(dest) if dest else "shop", right, anchor=dest.start if dest else 0)
        money_obj = "coin_stack" if "payment" in concepts or money_q else "banknote"
    trigger = next((m for m in an.matches if m.concept in ("salary", "payment", "buy", "sell")), None)
    t_anchor = trigger.start if trigger else 0
    money = b.draw(money_obj, "upper_center", anchor=t_anchor, scale=0.8)
    b.connect(payer, money, anchor=t_anchor)
    b.connect(money, payee, anchor=t_anchor)
    if money_q:
        b.write(money_q[0].display, "top", anchor=money_q[0].span[0], style="number", color="positive")
    elif "buy" in concepts:
        b.draw("cart", "lower", anchor=t_anchor, scale=0.5)
    if trigger and trigger.surface:
        b.write(trigger.surface, "bottom", anchor=trigger.start, style="label")
    return "transaction"


def plan_cause_effect(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    marker = an.relations["cause"]
    before, after = an.text[: marker.start], an.text[marker.end:]
    # "X because Y" puts the cause after the marker; postpositional forms
    # ("X ki wajah se Y", "د X له امله Y") put it before.
    reverse = marker.keyword in ("because", "due to", "kyunke", "kyunki", "کیونکہ", "ځکه")
    cause_txt, effect_txt = (after, before) if reverse else (before, after)
    cause_off = marker.end if reverse else 0
    effect_off = 0 if reverse else marker.end
    if reverse and len(content_tokens(before, an.lang)) == 0:
        # "Because X, Y": cause is the clause before the first comma, effect the rest.
        comma = re.search(r"[,،]", after)
        if comma:
            cause_txt, effect_txt = after[: comma.start()], after[comma.end():]
            cause_off, effect_off = marker.end, marker.end + comma.end()

    def side_visual(txt: str, offset: int):
        ms = [m for m in an.matches if offset <= m.start < offset + len(txt) and m.kind in ("entity", "action", "actor", "trend") and not m.weak]
        ents = [m for m in ms if m.kind in ("entity", "action")]
        if ents:
            return _entity_visual(ents[0]), ents[0]
        trends = [m for m in ms if m.kind == "trend"]
        if trends:
            return ("trend_arrow", trends[0])
        acts = [m for m in ms if m.kind == "actor"]
        if acts:
            return ACTOR_VISUAL.get(acts[0].concept, "person"), acts[0]
        return None, None

    vertical = variant % 2 == 0
    pos_a, pos_b = ("upper", "lower") if vertical else ("left", "right")
    lab_a, lab_b = ("top", "bottom") if vertical else ("bottom_left", "bottom_right")
    va, ma = side_visual(cause_txt, cause_off)
    vb, mb = side_visual(effect_txt, effect_off)
    if va:
        params = {"direction": "up" if ma and ma.concept == "increase" else "down"} if va == "trend_arrow" else None
        a = b.draw(va, pos_a, anchor=ma.start if ma else 0, params=params, scale=0.75)
    else:
        a = b.draw("label_box", pos_a, anchor=cause_off, params={"text": _phrase_label(cause_txt, an.lang)})
    if vb:
        params = {"direction": "up" if mb and mb.concept == "increase" else "down"} if vb == "trend_arrow" else None
        c = b.draw(vb, pos_b, anchor=mb.start if mb else marker.start, params=params, scale=0.75)
    else:
        c = b.draw("label_box", pos_b, anchor=effect_off, params={"text": _phrase_label(effect_txt, an.lang)})
    b.connect(a, c, anchor=marker.start)
    if va:
        b.write(_label_at(an.text, ma.start, an.lang) if ma else _short_label(cause_txt, an.lang, 2, 18), lab_a, anchor=cause_off, style="label")
    if vb:
        b.write(_label_at(an.text, mb.start, an.lang) if mb else _short_label(effect_txt, an.lang, 2, 18), lab_b, anchor=effect_off, style="label")
    for q in an.quantities[:2]:
        b.write(q.display, "center" if vertical else "upper_center", anchor=q.span[0], style="number", color="accent")
        break
    return "cause_effect"


def plan_comparison(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    marker = an.relations.get("comparison")
    amounts = [q for q in an.quantities if q.kind in ("currency", "count", "percent")]
    items = an.entities[:2] if len(an.entities) >= 2 else (an.entities + an.actors)[:2]
    mirror = variant % 2 == 1
    if len(amounts) >= 2 and variant % 2 == 0:
        b.draw("bar_chart", "upper", anchor=amounts[0].span[0], params={"values": [q.value for q in amounts[:2]], "value_labels": [q.display for q in amounts[:2]],
                                                                         "labels": [m.surface for m in items[:2]] if len(items) >= 2 else None, "direction": "up" if amounts[1].value >= amounts[0].value else "down"})
        if items:
            b.draw(_entity_visual(items[0]) if items[0].kind != "actor" else ACTOR_VISUAL.get(items[0].concept, "person"), "bottom_left", anchor=items[0].start, scale=0.6)
        if len(items) > 1:
            b.draw(_entity_visual(items[1]) if items[1].kind != "actor" else ACTOR_VISUAL.get(items[1].concept, "person"), "bottom_right", anchor=items[1].start, scale=0.6)
        return "comparison"
    left, right = ("left", "right") if not mirror else ("right", "left")
    vis = []
    for m in items:
        vis.append(ACTOR_VISUAL.get(m.concept, "person") if m.kind == "actor" else _entity_visual(m))
    if len(vis) < 2:
        # Same subject before vs after (e.g. "more than before"): before/after panels.
        labels = [q.display for q in amounts[:2]] + ["", ""]
        b.draw("before_after", "upper", anchor=marker.start if marker else 0, params={"left": labels[0], "right": labels[1], "direction": an.trend or "up"})
        if vis:
            b.draw(vis[0], "lower", anchor=items[0].start, scale=0.7)
        return "comparison"
    a = b.draw(vis[0], left, anchor=items[0].start, scale=0.8)
    c = b.draw(vis[1], right, anchor=items[1].start, scale=0.8)
    b.write("vs", "center", anchor=marker.start if marker else items[1].start, style="title", color="accent")
    b.write(items[0].surface, "bottom_left" if not mirror else "bottom_right", anchor=items[0].start)
    b.write(items[1].surface, "bottom_right" if not mirror else "bottom_left", anchor=items[1].start)
    for q, pos in zip(amounts[:2], ("top_left" if not mirror else "top_right", "top_right" if not mirror else "top_left")):
        b.write(q.display, pos, anchor=q.span[0], style="number", color="accent")
    if an.trend:
        b.highlight(c if an.trend == "up" else a, anchor=an.trend_match.start if an.trend_match else 0, color="positive")
    return "comparison"


def _sequence_pieces(an: Analysis) -> list[tuple[str, int]]:
    """Narration fragments between sequence markers that carry real content."""
    marks = [m for m in an.matches if m.concept == "sequence"]
    cuts = [m.start for m in marks] + [len(an.text)]
    pieces = []
    start = 0
    for c in cuts:
        frag = an.text[start:c].strip(" ,;:.،۔")
        if frag:
            pieces.append((frag, start))
        start = c
    seq_words = {m.surface.lower() for m in marks}
    return [(f, s) for f, s in pieces if [t for t in content_tokens(f, an.lang) if t[0] not in seq_words]]


def _step_label(frag: str, an: Analysis, max_words: int = 2, max_chars: int = 16) -> str:
    """Verbatim words of a step, without the sequence marker and pronouns."""
    seq = {m.surface.lower() for m in an.matches if m.concept == "sequence"}
    words = [w for w in frag.split() if w.lower().strip(",.;:،۔") not in seq | PRONOUN_ACTORS]
    return _short_label(" ".join(words) or frag, an.lang, max_words, max_chars)


def plan_process(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    # Split narration at sequence markers into steps; labels are verbatim fragments.
    marks = [m for m in an.matches if m.concept == "sequence"]
    pieces = _sequence_pieces(an)
    step_entities = []
    for frag, s in pieces:
        e = next((m for m in an.entities if s <= m.start < s + len(frag)), None)
        step_entities.append(e)
    if 2 <= len(pieces) <= 3 and sum(1 for e in step_entities if e) >= 2 and variant % 2 == 0:
        # Vertical flow for the 9:16 frame: icons in one column, verbatim step labels beside them.
        icon_pos = ["top_left", "center_left", "bottom_left"]
        label_pos = ["top_right", "center_right", "bottom_right"]
        ids = []
        for (frag, s), e, ipos, lpos in zip(pieces, step_entities, icon_pos, label_pos):
            label = _step_label(frag, an)
            if e:
                ids.append(b.draw(_entity_visual(e), ipos, anchor=s, scale=0.6))
                b.write(label, lpos, anchor=s)
            else:
                ids.append(b.draw("label_box", ipos, anchor=s, params={"text": label}, scale=0.6))
        for x, y in zip(ids, ids[1:]):
            b.connect(x, y, anchor=0)
        return "process"
    labels = [_step_label(f, an, 3, 20) for f, _ in pieces[:4]] or [_short_label(an.text, an.lang)]
    b.draw("steps", "full", anchor=marks[0].start if marks else 0, params={"labels": labels, "count": max(2, len(labels))})
    return "process"


def plan_timeline(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    years = _quantity_by_kind(an, "year")
    b.draw("timeline", "center", anchor=years[0].span[0], params={"labels": [q.display for q in years[:5]]})
    if an.actors:
        b.draw(ACTOR_VISUAL.get(an.actors[0].concept, "person"), "bottom_left", anchor=an.actors[0].start, params={"pose": "point"}, scale=0.8)
    if an.entities:
        b.draw(_entity_visual(an.entities[0]), "top", anchor=an.entities[0].start, scale=0.6)
    for q in [q for q in an.quantities if q.kind != "year"][:1]:
        b.write(q.display, "bottom_right", anchor=q.span[0], style="number", color="accent")
    return "timeline"


def plan_problem_solution(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    prob = next(m for m in an.matches if m.concept == "problem")
    sol = next((m for m in an.matches if m.concept in ("solution", "idea")), None)
    mirror = variant % 2 == 1
    if an.actors or sol is None:
        b.draw(ACTOR_VISUAL.get(an.actors[0].concept, "person") if an.actors else "person", "lower_left" if not mirror else "lower_right",
               anchor=an.actors[0].start if an.actors else 0, params={"pose": "shrug" if sol is None else "think", "expression": "worried"}, scale=0.9)
    p = b.draw("warning", "top_left" if not mirror else "top_right", anchor=prob.start, scale=0.7)
    b.write(prob.surface, "center_left" if not mirror else "center_right", anchor=prob.start)
    if sol:
        s = b.draw("lightbulb" if sol.concept == "idea" else "check", "top_right" if not mirror else "top_left", anchor=sol.start, scale=0.7)
        b.connect(p, s, anchor=sol.start, color="positive")
        b.write(sol.surface, "center_right" if not mirror else "center_left", anchor=sol.start, color="positive")
    else:
        other = next((e for e in an.entities if e.concept != "problem"), None)
        if other:
            b.draw(_entity_visual(other), "top_right" if not mirror else "top_left", anchor=other.start, scale=0.6)
        b.draw("question", "center_right" if not mirror else "center_left", anchor=prob.end, scale=0.5)
    for q in an.quantities[:1]:
        b.write(q.display, "bottom", anchor=q.span[0], style="number", color="negative")
    return "problem_solution"


def plan_special(b: PlanBuilder, an: Analysis, kind: str, rng: random.Random, variant: int) -> str:
    m = an.relations[kind]
    labels = [e.surface for e in an.entities[:3]]
    if kind == "risk_reward":
        b.draw("scale", "center", anchor=m.start, params={"tilt": 0.0, "left_label": labels[0] if labels else "", "right_label": labels[1] if len(labels) > 1 else ""})
    elif kind == "supply_demand":
        b.draw("supply_demand", "upper", anchor=m.start)
        if an.trend:
            b.draw("trend_arrow", "bottom_right", anchor=an.trend_match.start if an.trend_match else m.start, params={"direction": an.trend}, scale=0.6)
    elif kind == "cycle":
        b.draw("cycle", "center", anchor=m.start, params={"labels": labels})
    elif kind == "decision":
        b.draw("decision_tree", "upper", anchor=m.start, params={"question": "?", "options": labels[:3] or ["", ""]})
    if an.actors:
        b.draw(ACTOR_VISUAL.get(an.actors[0].concept, "person"), "bottom_left", anchor=an.actors[0].start, params={"pose": "think" if kind == "decision" else "point"}, scale=0.75)
    for q in an.quantities[:1]:
        b.write(q.display, "bottom_right", anchor=q.span[0], style="number", color="accent")
    return {"risk_reward": "risk_reward", "supply_demand": "supply_demand", "cycle": "cycle", "decision": "decision"}[kind]


def plan_person_object(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    actor = an.actors[0]
    ents = an.entities[:3]
    mirror = variant % 2 == 1
    left, right = ("left", "right") if not mirror else ("right", "left")
    holdable = next((e for e in ents if _entity_visual(e) in HOLDABLE), None)
    concepts = {m.concept for m in an.matches}
    if holdable is None and ("money" in concepts or "investment" in concepts or _quantity_by_kind(an, "currency")):
        holdable_vis = "wallet"
    else:
        holdable_vis = _entity_visual(holdable) if holdable else None
    pose, expr = _actor_pose(an, holding=holdable_vis is not None)
    person = b.draw(ACTOR_VISUAL.get(actor.concept, "person"), left, anchor=actor.start, params={"pose": pose, "expression": expr})
    held = None
    if holdable_vis:
        held = b.draw(holdable_vis, f"in_hand:{person}", anchor=holdable.start if holdable else actor.end, scale=0.45)
    others = [e for e in ents if e is not holdable and _entity_visual(e) != holdable_vis]
    if others:
        o = others[0]
        obj = b.draw(_entity_visual(o), right, anchor=o.start, scale=0.85 if _entity_visual(o) in BIG else 0.7)
        if pose == "point":
            b.connect(person, obj, anchor=o.start, color="muted")
        b.write(o.surface, "bottom_right" if not mirror else "bottom_left", anchor=o.start)
        if len(others) > 1:
            b.draw(_entity_visual(others[1]), "top_right" if not mirror else "top_left", anchor=others[1].start, scale=0.5)
    elif held and holdable_vis in ("wallet", "coin_stack", "money_bag", "banknote", "coin"):
        # Money held by the person: show the amount growing into coins next to them.
        coins = b.draw("coin_stack", right, anchor=holdable.start if holdable else actor.end, scale=0.6)
        b.connect(held, coins, anchor=holdable.start if holdable else actor.end, color="muted")
    money = _quantity_by_kind(an, "currency")
    other_q = [q for q in an.quantities if q not in money]
    if money:
        b.write(money[0].display, "top_left" if not mirror else "top_right", anchor=money[0].span[0], style="number", color="positive")
    for q in money[1:2] + other_q[:1]:
        b.write(q.display, "top_right" if not mirror else "top_left", anchor=q.span[0], style="number", color="accent")
    if an.trend and not money:
        b.draw("trend_arrow", "top_right" if not mirror else "top_left", anchor=an.trend_match.start if an.trend_match else 0, params={"direction": an.trend}, scale=0.5)
    return "person_object"


def plan_cluster(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    main = an.entities[0]
    b.draw(_entity_visual(main), "center", anchor=main.start, emphasis=True)
    b.write(main.surface, "lower", anchor=main.start, style="label")
    corners = ["top_left", "top_right"] if variant % 2 == 0 else ["bottom_left", "bottom_right"]
    for e, pos in zip(an.entities[1:3], corners):
        b.draw(_entity_visual(e), pos, anchor=e.start, scale=0.55)
    qpos = ["top", "upper_center"]
    for q, pos in zip(an.quantities[:2], qpos):
        b.write(q.display, pos, anchor=q.span[0], style="number", color="accent")
    if an.trend:
        b.draw("trend_arrow", "bottom_right" if variant % 2 == 0 else "top_right", anchor=an.trend_match.start if an.trend_match else 0, params={"direction": an.trend}, scale=0.5)
    return "concept"


def plan_quantity(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    q = an.quantities[0]
    if an.actors:
        b.draw(ACTOR_VISUAL.get(an.actors[0].concept, "person"), "lower_left" if variant % 2 == 0 else "lower_right", anchor=an.actors[0].start, params={"pose": "point"})
    b.draw("percent_badge", "upper", anchor=q.span[0], params={"text": q.display}, emphasis=True)
    for extra in an.quantities[1:3]:
        b.write(extra.display, "center_right" if variant % 2 == 0 else "center_left", anchor=extra.span[0], style="number")
    return "quantity"


def plan_fallback(b: PlanBuilder, an: Analysis, rng: random.Random, variant: int) -> str:
    """No representable concept: write the key phrase by hand with a presenter.
    This is an honest text scene, never an unrelated picture."""
    label = _phrase_label(an.text, an.lang)
    b.draw("label_box", "upper", anchor=0, params={"text": label})
    b.draw("teacher" if not an.actors else ACTOR_VISUAL.get(an.actors[0].concept, "person"), "lower_left" if variant % 2 == 0 else "lower_right",
           anchor=0, params={"pose": "point", "expression": "talking"})
    for q in an.quantities[:1]:
        b.write(q.display, "center_right" if variant % 2 == 0 else "center_left", anchor=q.span[0], style="number", color="accent")
    return "text_emphasis"


def choose_template(an: Analysis):
    concepts = {m.concept for m in an.matches}
    if "cause" in an.relations and (len(an.entities) + len(an.actors) >= 1 or an.trend):
        return plan_cause_effect, "relation:cause"
    if "sequence" in an.relations and len(_sequence_pieces(an)) >= 2:
        return plan_process, "relation:sequence"
    for kind in ("risk_reward", "supply_demand", "cycle", "decision"):
        if kind in an.relations:
            return (lambda b, a, r, v, k=kind: plan_special(b, a, k, r, v)), f"relation:{kind}"
    if "comparison" in an.relations and (len(an.entities) + len(an.actors) >= 2 or len(an.quantities) >= 2):
        return plan_comparison, "relation:comparison"
    growth_story = an.trend == "up" and bool({"investment", "savings", "profit"} & concepts)
    if ({"salary", "payment", "buy", "sell"} & concepts) and (an.actors or {"shop", "employer", "worker", "customer", "bank"} & concepts) and not growth_story:
        return plan_transaction, "action:transaction"
    if an.trend:
        return plan_chart, f"trend:{an.trend}"
    if _quantity_by_kind(an, "percent"):
        return plan_percentage, "quantity:percent"
    if len(_quantity_by_kind(an, "year")) >= 2:
        return plan_timeline, "quantity:years"
    if "problem" in concepts:
        return plan_problem_solution, "concept:problem"
    if an.actors and (an.entities or an.quantities):
        return plan_person_object, "actor+entity"
    if an.entities:
        return plan_cluster, "entity"
    if an.quantities:
        return plan_quantity, "quantity"
    if an.actors:
        return plan_person_object if an.entities else plan_fallback, "actor-only"
    return plan_fallback, "fallback"


def compute_confidence(an: Analysis, rule: str, problems: list[str]) -> tuple[float, dict]:
    """Evidence-based confidence: how much of the sentence the visuals account for."""
    basis = {
        "concept_coverage": round(an.coverage, 3),
        "rule": rule,
        "explicit_marker": rule.startswith(("relation:", "trend:", "quantity:", "action:")),
        "quantities_preserved": not any(p.startswith("missing_quantity") for p in problems),
        "llm_assisted": an.llm_assisted,
        "validation_problems": len(problems),
    }
    if rule == "fallback":
        score = 0.15 + 0.25 * an.coverage
    else:
        score = 0.35 + 0.4 * an.coverage + (0.15 if basis["explicit_marker"] else 0.0) + (0.1 if an.entities or an.actors else 0.0)
    if an.llm_assisted:
        score = min(score, 0.6)
    score -= 0.2 * len(problems)
    # Rule-based plans are never reported as certain.
    return round(max(0.0, min(0.95, score)), 3), basis


def validate_scene_semantics(scene: dict, an: Analysis) -> list[str]:
    """Layer 4: check the plan against the narration."""
    problems = []
    texts = []
    for a in scene["visual_actions"]:
        if a.get("text"):
            texts.append(a["text"])
        params = a.get("params") or {}
        for key in ("value_labels", "labels"):
            texts.extend([t for t in params.get(key) or [] if t])
        for key in ("label", "text", "start_label", "end_label", "left_label", "right_label", "question"):
            if params.get(key):
                texts.append(str(params[key]))
        if params.get("options"):
            texts.extend(o for o in params["options"] if o)
    joined = " ".join(texts)
    for q in an.quantities:
        if q.display not in joined:
            problems.append(f"missing_quantity:{q.display}")
    allowed = {q.display for q in an.quantities} | {str(i) for i in range(1, 6)} | {"+" + q.display for q in an.quantities} | {"-" + q.display for q in an.quantities}
    narration_digits = set(re.findall(r"\d+(?:\.\d+)?", " ".join(q.display for q in an.quantities)))
    for t in texts:
        for num in re.findall(r"\d+(?:\.\d+)?", t.replace(",", "")):
            if num not in narration_digits and t not in allowed and num not in {"1", "2", "3", "4", "5"}:
                problems.append(f"invented_number:{t}")
    if not scene["visual_actions"]:
        problems.append("no_actions")
    if an.entities and scene["scene_type"] != "text_emphasis":
        drawn = {a.get("object") for a in scene["visual_actions"]}
        if not any(_entity_visual(e) in drawn or e.surface in joined for e in an.entities) and scene["scene_type"] not in ("chart_growth", "chart_decline", "process", "percentage", "transaction", "cause_effect", "comparison"):
            problems.append("main_subject_missing")
    return problems


def _repair(scene: dict, an: Analysis, problems: list[str]) -> list[str]:
    """Bounded repair: add missing quantities, drop invented numbers."""
    remaining = []
    existing_positions = {a["position"] for a in scene["visual_actions"]}
    free = [p for p in ("bottom", "top", "upper_center", "bottom_right", "bottom_left", "top_right", "top_left") if p not in existing_positions]
    for p in problems:
        if p.startswith("missing_quantity:") and free:
            disp = p.split(":", 1)[1]
            q = next(q for q in an.quantities if q.display == disp)
            n = len(scene["visual_actions"]) + 1
            scene["visual_actions"].append({"id": f"a{n}", "action": "write", "text": disp, "position": free.pop(0), "draw_order": n,
                                            "anchor_char": q.span[0], "style": "number", "color": "accent"})
        elif p.startswith("invented_number:"):
            bad = p.split(":", 1)[1]
            scene["visual_actions"] = [a for a in scene["visual_actions"] if a.get("text") != bad]
        else:
            remaining.append(p)
    for i, a in enumerate(scene["visual_actions"], 1):
        a["draw_order"] = i
    return remaining


def merge_short_segments(segments: list[Segment], min_seconds: float) -> list[Segment]:
    out: list[Segment] = []
    for seg in segments:
        if out and (out[-1].end - out[-1].start) < min_seconds:
            prev = out[-1]
            out[-1] = Segment(prev.text + " " + seg.text, prev.start, seg.end, prev.sentence_index, prev.words + seg.words)
        else:
            out.append(seg)
    if len(out) > 1 and (out[-1].end - out[-1].start) < min_seconds:
        last = out.pop()
        prev = out[-1]
        out[-1] = Segment(prev.text + " " + last.text, prev.start, last.end, prev.sentence_index, prev.words + last.words)
    return out


def plan_scenes(segments: list[Segment], lang: str, cfg: dict, language_key: str = "") -> dict:
    seed = cfg["animation"]["seed"]
    segments = merge_short_segments(segments, cfg["planner"]["min_scene_seconds"])
    scenes = []
    prev_signature = None
    type_counts: dict[str, int] = {}
    for idx, seg in enumerate(segments):
        an = analyze(seg.text, lang)
        func, rule = choose_template(an)
        if rule == "fallback" and cfg["planner"]["local_llm"].get("enabled"):
            extra = _local_llm_concepts(seg.text, cfg)
            if extra:
                an = analyze(seg.text, lang, extra)
                an.llm_assisted = True
                func, rule = choose_template(an)
        rng = _rng_for(seg.text, seed, str(idx))
        variant = type_counts.get(rule, 0) + rng.randint(0, 1)
        b = PlanBuilder(seg.text)
        scene_type = func(b, an, rng, variant)
        signature = (scene_type, tuple((a.get("object"), a["position"]) for a in b.actions))
        if signature == prev_signature:
            # Avoid identical consecutive layouts: re-plan with the alternate variant.
            b = PlanBuilder(seg.text)
            scene_type = func(b, an, rng, variant + 1)
            signature = (scene_type, tuple((a.get("object"), a["position"]) for a in b.actions))
        prev_signature = signature
        type_counts[rule] = type_counts.get(rule, 0) + 1
        scene = {
            "schema_version": SCHEMA_VERSION,
            "scene_id": f"scene_{idx + 1:03d}",
            "language": lang,
            "narration_text": seg.text,
            "start_seconds": round(seg.start, 3),
            "end_seconds": round(seg.end, 3),
            "concept": next((m.concept for m in an.matches if m.kind in ("entity", "action", "trend", "relation") and not m.weak), "key_phrase"),
            "scene_type": scene_type,
            "visual_actions": b.actions,
            "caption_text": seg.text,
            "concepts": sorted({m.concept for m in an.matches}),
            "quantities": [q.to_dict() for q in an.quantities],
            "transition": ["slide", "erase", "slide", "fade"][idx % 4] if idx else "none",
            "fallback": rule == "fallback",
            "words": seg.words,
        }
        problems = validate_scene_semantics(scene, an)
        if problems:
            problems = _repair(scene, an, problems)
            problems = validate_scene_semantics(scene, an)
        conf, basis = compute_confidence(an, rule, problems)
        scene["confidence"] = conf
        scene["confidence_basis"] = basis
        scene["validation_problems"] = problems
        scenes.append(scene)
    plan = {
        "schema_version": SCHEMA_VERSION,
        "language": lang,
        "language_key": language_key,
        "canvas": {"width": 1080, "height": 1920},
        "duration_seconds": round(segments[-1].end, 3) if segments else 0.0,
        "scenes": scenes,
    }
    validate_plan(plan)
    return plan
