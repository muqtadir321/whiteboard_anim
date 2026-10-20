"""Organisation logos from Wikidata / Wikimedia Commons.

The logo image (Wikidata P154, else the seal P158) of the best-matching entity is used;
the Wikipedia lead image is a fallback only when its file name looks like a logo, seal or
emblem, so a photo of a building is never shown as a "logo". Logos are trademarks: they
are used for identification in commentary and listed in sources.txt.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.parse
from pathlib import Path

from ..research.sources import fetch

LOGO_FILE = re.compile(r"logo|seal|emblem|wordmark|insignia|\.svg", re.I)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def commons_urls(fname: str, width: int = 640) -> list[str]:
    """Direct upload.wikimedia.org URLs (files live under an MD5-based path): PNG rendering, then the original."""
    name = fname.replace(" ", "_")
    h = hashlib.md5(name.encode("utf-8")).hexdigest()
    q = urllib.parse.quote(name)
    base = "https://upload.wikimedia.org/wikipedia/commons"
    thumb = f"{base}/thumb/{h[0]}/{h[:2]}/{q}/{width}px-{q}" + (".png" if name.lower().endswith(".svg") else "")
    return [thumb] if name.lower().endswith(".svg") else [thumb, f"{base}/{h[0]}/{h[:2]}/{q}"]


def wikidata_logo(name: str, out_dir: Path, timeout: float = 20) -> dict | None:
    q = urllib.parse.quote(name)
    try:
        found = json.loads(fetch(f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={q}&language=en&limit=3&format=json", timeout))
    except Exception:
        return None
    for ent in found.get("search", [])[:3]:
        for prop in ("P154", "P158"):
            try:
                claims = json.loads(fetch(f"https://www.wikidata.org/w/api.php?action=wbgetclaims&entity={ent['id']}&property={prop}&format=json", timeout))
                fname = claims["claims"][prop][0]["mainsnak"]["datavalue"]["value"]
            except Exception:
                continue
            fq = urllib.parse.quote(fname.replace(" ", "_"))
            data = None
            for url in commons_urls(fname):
                try:
                    data = fetch(url, timeout, retries=1)
                    break
                except Exception:
                    continue
            path = out_dir / f"logo_{_slug(name)}.png"
            if data and to_png(data, path):
                return {"name": name, "image": path.name, "url": f"https://commons.wikimedia.org/wiki/File:{fq}", "wikidata": ent["id"],
                        "license": "trademark of its owner; image from Wikimedia Commons"}
    return None


def find_logo(name: str, out_dir: Path, timeout: float = 20) -> dict | None:
    hit = wikidata_logo(name, out_dir, timeout)
    if hit:
        return hit
    q = urllib.parse.quote(name)
    try:
        hits = json.loads(fetch(f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={q}&srlimit=3&format=json", timeout))
        titles = [h["title"] for h in hits.get("query", {}).get("search", [])]
    except Exception:
        return None
    for title in titles[:2]:
        try:
            summ = json.loads(fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_")), timeout))
        except Exception:
            continue
        thumb = (summ.get("thumbnail") or {}).get("source", "")
        original = (summ.get("originalimage") or {}).get("source", "")
        if not thumb or not LOGO_FILE.search(urllib.parse.unquote(original or thumb)):
            continue
        url = re.sub(r"/\d+px-", "/640px-", thumb)
        try:
            data = fetch(url, timeout)
        except Exception:
            try:
                data = fetch(thumb, timeout)
            except Exception:
                continue
        path = out_dir / f"logo_{_slug(name)}.png"
        if not to_png(data, path):
            continue
        page = (summ.get("content_urls") or {}).get("desktop", {}).get("page", "")
        return {"name": name, "image": path.name, "url": page or url, "wikipedia_title": title,
                "license": "trademark of its owner; image from Wikimedia Commons"}
    return None


def to_png(data: bytes, path: Path, max_side: int = 900) -> bool:
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return False
    im.thumbnail((max_side, max_side))
    bbox = im.getchannel("A").getbbox()
    if bbox:
        im = im.crop(bbox)
    im.save(path, "PNG")
    return True
