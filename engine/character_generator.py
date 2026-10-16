"""Procedural whiteboard characters with poses, expressions and accessories.

Characters are built from a simple skeleton (joint angles), so the same person
can stand, point, hold an object, walk, run, sit at a desk, think or celebrate.
The local coordinate box is 60 x 120 units; anchors expose hand positions so
the composer can place held objects exactly in a character's hand.
"""

from __future__ import annotations

import math
import random

from .geometry import Element, Pen, Point

W, H = 60.0, 120.0

# (right_upper, right_fore, left_upper, left_fore, right_leg_upper, right_leg_lower, left_leg_upper, left_leg_lower)
# Angles in degrees from straight down, rotating outward from the body.
POSES: dict[str, tuple[float, ...]] = {
    "stand": (18, 10, 18, 10, 7, 4, 7, 4),
    "point": (105, 112, 20, 12, 7, 4, 7, 4),
    "hold": (40, 95, 18, 10, 7, 4, 7, 4),
    "hold_both": (40, 95, 40, 95, 7, 4, 7, 4),
    "give": (70, 82, 18, 10, 7, 4, 7, 4),
    "wave": (125, 165, 18, 10, 7, 4, 7, 4),
    "celebrate": (145, 165, 145, 165, 10, 6, 10, 6),
    "think": (35, 160, 22, 70, 7, 4, 7, 4),
    "walk": (30, 20, -20, -10, 22, 8, -18, -6),
    "run": (60, 120, -45, 40, 45, -20, -30, -50),
    "sad": (8, 4, 8, 4, 5, 2, 5, 2),
    "shrug": (35, 125, 35, 125, 7, 4, 7, 4),
    "talk": (55, 120, 18, 10, 7, 4, 7, 4),
}
EXPRESSIONS = ("happy", "neutral", "sad", "surprised", "talking", "worried")
HAIR = ("none", "short", "spiky", "long", "cap", "bun")


def _limb(origin: Point, angle: float, length: float, side: int) -> Point:
    a = math.radians(angle)
    return (origin[0] + math.sin(a) * length * side, origin[1] + math.cos(a) * length)


def make_character(
    rng: random.Random,
    pose: str = "stand",
    expression: str = "happy",
    hair: str | None = None,
    accessory: str | None = None,
    role: str = "person",
    seated: bool = False,
) -> Element:
    pen = Pen(rng, wobble=0.35, step=2.0)
    el = Element(f"person:{pose}", W, H)
    hair = hair or rng.choice(HAIR)
    head_c = (30.0, 14.0)
    hr = 10.5
    if pose == "sad":
        head_c = (31.0, 16.0)

    # Head first (natural order: head, face, hair, body, arms, legs, details)
    el.add(pen.circle(*head_c, hr, color="ink", width=1.0, fill="skin", fill_opacity=0.9))
    ex, ey = head_c[0], head_c[1]
    el.add(pen.dot(ex - 3.6, ey - 1.5, 1.2), pen.dot(ex + 3.6, ey - 1.5, 1.2))
    if expression in ("happy", "talking"):
        el.add(pen.arc(ex, ey + 1.5, 4.5, 20, 160, width=0.7))
    elif expression in ("sad", "worried"):
        el.add(pen.arc(ex, ey + 8.0, 4.0, 200, 340, width=0.7))
        if expression == "worried":
            el.add(pen.line((ex - 6, ey - 5.5), (ex - 2, ey - 4.5), width=0.5), pen.line((ex + 6, ey - 5.5), (ex + 2, ey - 4.5), width=0.5))
    elif expression == "surprised":
        el.add(pen.circle(ex, ey + 4.5, 2.0, width=0.6))
    else:
        el.add(pen.line((ex - 3, ey + 4.5), (ex + 3, ey + 4.5), width=0.6))

    if hair == "short":
        el.add(pen.arc(head_c[0], head_c[1], hr + 0.5, 195, 345, width=1.5))
    elif hair == "spiky":
        pts = []
        for i in range(7):
            a = math.radians(200 + i * 23.0)
            r = hr + (4 if i % 2 else 0.5)
            pts.append((head_c[0] + r * math.cos(a), head_c[1] + r * math.sin(a)))
        el.add(pen.polyline(pts, width=0.9))
    elif hair == "long":
        el.add(pen.arc(head_c[0], head_c[1], hr + 0.8, 170, 370, width=1.4))
        el.add(pen.bezier((head_c[0] - hr, head_c[1]), (head_c[0] - hr - 3, head_c[1] + 10), (head_c[0] - hr + 1, head_c[1] + 18), width=1.0))
        el.add(pen.bezier((head_c[0] + hr, head_c[1]), (head_c[0] + hr + 3, head_c[1] + 10), (head_c[0] + hr - 1, head_c[1] + 18), width=1.0))
    elif hair == "cap":
        el.add(pen.arc(head_c[0], head_c[1] - 1, hr + 0.6, 180, 360, width=1.2, color="accent"))
        el.add(pen.line((head_c[0] - 2, head_c[1] - 3), (head_c[0] + hr + 8, head_c[1] - 2), width=1.2, color="accent"))
    elif hair == "bun":
        el.add(pen.arc(head_c[0], head_c[1], hr + 0.5, 190, 350, width=1.3))
        el.add(pen.circle(head_c[0], head_c[1] - hr - 3.5, 3.5, width=0.9))

    neck = (30.0, head_c[1] + hr)
    shoulder_y = neck[1] + 4
    hip_y = 72.0 if not seated else 70.0
    # Torso as a shirt shape
    sl, sr = (19.0, shoulder_y), (41.0, shoulder_y)
    hl, hrp = (21.5, hip_y), (38.5, hip_y)
    shirt_color = rng.choice(["accent", "ink", "positive"]) if role != "plain" else "ink"
    el.add(pen.polyline([sl, sr, hrp, hl], closed=True, width=1.0, fill=shirt_color, fill_opacity=0.18))
    if accessory == "tie":
        el.add(pen.polyline([(30, shoulder_y), (32, shoulder_y + 6), (30, shoulder_y + 20), (28, shoulder_y + 6)], closed=True, width=0.7, color="negative", fill="negative", fill_opacity=0.6))
    elif accessory == "badge":
        el.add(pen.rect(33, shoulder_y + 6, 38, shoulder_y + 12, width=0.6, color="accent"))
    elif accessory == "bag":
        el.add(pen.line((sl[0] + 2, shoulder_y), (hrp[0], hip_y - 6), width=0.6))
        el.add(pen.rect(hrp[0] - 4, hip_y - 9, hrp[0] + 5, hip_y - 1, r=1.5, width=0.7))

    angles = POSES.get(pose, POSES["stand"])
    ru, rf, lu, lf, rlu, rll, llu, lll = angles
    upper, fore = 17.0, 15.0
    r_sh = (sr[0], sr[1] + 1)
    l_sh = (sl[0], sl[1] + 1)
    r_el = _limb(r_sh, ru, upper, +1)
    r_hand = _limb(r_el, rf, fore, +1)
    l_el = _limb(l_sh, lu, upper, -1)
    l_hand = _limb(l_el, lf, fore, -1)
    if pose == "think":
        l_el = _limb(l_sh, 25, upper, -1)
        l_hand = (r_hand[0] - 4, r_hand[1])
        r_el = _limb(r_sh, 30, upper, +1)
        r_hand = (head_c[0] + 4, head_c[1] + hr + 2)
    el.add(pen.polyline([r_sh, r_el, r_hand], width=1.0))
    el.add(pen.polyline([l_sh, l_el, l_hand], width=1.0))
    el.add(pen.circle(*r_hand, 2.4, width=0.7, fill="skin"), pen.circle(*l_hand, 2.4, width=0.7, fill="skin"))

    thigh, shin = 22.0, 22.0
    r_hip = (hrp[0] - 3, hip_y)
    l_hip = (hl[0] + 3, hip_y)
    if seated:
        r_knee = (r_hip[0] + 18, r_hip[1] + 2)
        l_knee = (l_hip[0] + 18, l_hip[1] + 4)
        r_foot = (r_knee[0] + 1, r_knee[1] + 24)
        l_foot = (l_knee[0] - 1, l_knee[1] + 22)
    else:
        r_knee = _limb(r_hip, rlu, thigh, +1)
        r_foot = _limb(r_knee, rll, shin, +1)
        l_knee = _limb(l_hip, llu, thigh, -1)
        l_foot = _limb(l_knee, lll, shin, -1)
    el.add(pen.polyline([r_hip, r_knee, r_foot], width=1.1))
    el.add(pen.polyline([l_hip, l_knee, l_foot], width=1.1))
    el.add(pen.line(r_foot, (r_foot[0] + 6, r_foot[1] + 0.5), width=1.1))
    el.add(pen.line(l_foot, (l_foot[0] - 6 if not seated else l_foot[0] + 6, l_foot[1] + 0.5), width=1.1))

    if expression == "talking":
        mx = head_c[0] + hr + 4
        for i, (a, b) in enumerate(((-25, -10), (0, 0), (25, 10))):
            ang = math.radians(a)
            p0 = (mx + 1, head_c[1] + 2 + b * 0.25)
            el.add(pen.line(p0, (p0[0] + 7 * math.cos(ang), p0[1] + 7 * math.sin(ang)), width=0.6, color="muted"))

    el.anchors.update(
        {
            "hand_right": r_hand,
            "hand_left": l_hand,
            "head_top": (head_c[0], head_c[1] - hr - 2),
            "head_right": (head_c[0] + hr + 3, head_c[1] - 4),
            "center": (30.0, (shoulder_y + hip_y) / 2),
            "feet": (30.0, max(r_foot[1], l_foot[1])),
        }
    )
    return el


def make_group(rng: random.Random, count: int = 3, expression: str = "happy") -> Element:
    """A small crowd of people (customers, workers, population)."""
    count = max(2, min(5, count))
    el = Element("people_group", W * (0.6 * count + 0.4), H)
    for i in range(count):
        p = make_character(rng, pose=rng.choice(["stand", "wave", "stand", "talk"]), expression=expression, role="person")
        s = 0.85 if i % 2 else 1.0
        el.extend(p, dx=i * W * 0.6, dy=H * (1 - s), s=s)
    el.anchors["center"] = (el.width / 2, H * 0.45)
    el.anchors["hand_right"] = (el.width - 8, H * 0.45)
    el.anchors["head_top"] = (el.width / 2, 0)
    return el
