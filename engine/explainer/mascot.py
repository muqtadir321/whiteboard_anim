"""Mo, the channel's recurring character.

The same person in every video (fixed seed, cap, glasses and tie), so viewers recognise the
channel at a glance: Mo opens each video next to the hook, looks worried when the narration
names a problem, and appears on thumbnails and chapter cards. Built on character_generator,
so it is drawn in the same hand as every other doodle.
"""

from __future__ import annotations

import random

from ..character_generator import make_character
from ..geometry import Element, Pen

NAME = "Mo"
SEED = 7
# mood -> (pose, expression)
MOODS = {
    "hook": ("point", "surprised"),
    "worried": ("shrug", "worried"),
    "happy": ("celebrate", "happy"),
    "talk": ("talk", "talking"),
}


def mascot(mood: str = "hook") -> Element:
    pose, expression = MOODS.get(mood, MOODS["hook"])
    # the same seed every time: same shirt colour and the same wobble, so Mo always looks like Mo
    el = make_character(random.Random(SEED), pose=pose, expression=expression, hair="cap", accessory="tie")
    el.name = f"mascot:{mood}"
    for st in el.strokes:  # the shirt is always the channel blue (the generator picks a colour per pose)
        if st.closed and st.fill in ("accent", "ink", "positive") and st.fill_opacity < 0.5:
            st.fill, st.fill_opacity = "accent", 0.35
    pen = Pen(random.Random(SEED + 1), wobble=0.15, step=1.5)
    hx, hy = 30.0, 14.0
    el.add(pen.circle(hx - 3.6, hy - 1.5, 2.4, width=0.35), pen.circle(hx + 3.6, hy - 1.5, 2.4, width=0.35),
           pen.line((hx - 1.2, hy - 1.7), (hx + 1.2, hy - 1.7), width=0.3))
    return el
