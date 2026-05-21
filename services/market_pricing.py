"""
Market Pricing Reference — find the most similar selling item to the tech
pack being built, and show its current price.

Data source: ``data/marie_lund_pricing.json`` (120 SKUs scraped from
peek-und-cloppenburg.de on 2026-05-21). For each row we have category,
color name, price (EUR), image_url, and product url.

Matching strategy (``find_similar_item``):
  1. Hard filter — must be the same garment_sub_category. No cross-category
     matches (a Cardigan and a Knit Shirt are not similar regardless of
     color, so we never let them collide).
  2. Score remaining candidates by color overlap with the user's color_name.
  3. Return the highest-scored candidate. If the user hasn't filled a color
     yet, fall back to the median-priced item in the category as a
     "representative" — better than showing nothing.

Aggregate stats (``get_price_stats``) still exposed for callers that want
them, but the primary widget is the per-match view.
"""

from __future__ import annotations

import base64
import json
import re
import statistics
import urllib.request
from pathlib import Path

import streamlit as st

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


_DATA_FILE = Path(__file__).parent.parent / "data" / "marie_lund_pricing.json"


# Source data is in EUR (PNC.de). Tech pack ships prices in USD, so we
# convert at read time. Update this rate periodically — last refreshed
# 2026-05-21. ECB / live API would be more accurate but is overkill for a
# reference widget. Off by a couple of cents either way is fine.
EUR_TO_USD = 1.08


@st.cache_data(show_spinner=False)
def _load() -> dict:
    """Lazily load the bundled pricing JSON. Cached so the file is read once."""
    if not _DATA_FILE.exists():
        return {"items": [], "pulled_on": None, "source": None, "total": 0}
    return json.loads(_DATA_FILE.read_text(encoding="utf-8"))


def is_available() -> bool:
    """True iff we have pricing data on disk."""
    return _DATA_FILE.exists() and bool(_load().get("items"))


def get_metadata() -> dict:
    """Source URL + pull date, surfaced under the widget so the user knows
    how fresh the data is."""
    d = _load()
    return {
        "source": d.get("source"),
        "pulled_on": d.get("pulled_on"),
        "total": d.get("total", 0),
    }


def get_price_stats(category: str | None = None) -> dict | None:
    """Return basic price stats for a sub-category, or for ALL items if
    category is None / empty. Returns None when nothing matches.

    Stats:
      n       — sample size
      median  — middle price (less skewed by outliers than mean)
      mean    — average price
      low     — cheapest in the bucket
      high    — priciest in the bucket
      p25     — 25th percentile (a "low end" reference)
      p75     — 75th percentile (a "high end" reference)
      currency — always "EUR" for now
    """
    items = _load().get("items", [])

    if category:
        bucket = [it for it in items if it.get("category", "").lower() == category.lower()]
    else:
        bucket = items

    if not bucket:
        return None

    prices = sorted(float(it["price_eur"]) for it in bucket if "price_eur" in it)
    if not prices:
        return None

    n = len(prices)
    # statistics.quantiles needs >= 2 values; small categories (e.g. Bolero n=2)
    # would otherwise crash. Fall back to extremes for tiny buckets.
    if n >= 4:
        q = statistics.quantiles(prices, n=4, method="inclusive")
        p25, p75 = q[0], q[2]
    else:
        p25, p75 = prices[0], prices[-1]

    return {
        "n": n,
        "median": statistics.median(prices) * EUR_TO_USD,
        "mean": statistics.mean(prices) * EUR_TO_USD,
        "low": prices[0] * EUR_TO_USD,
        "high": prices[-1] * EUR_TO_USD,
        "p25": p25 * EUR_TO_USD,
        "p75": p75 * EUR_TO_USD,
        "currency": "USD",
        "fx_rate": EUR_TO_USD,
        "fx_base": "EUR",
    }


# =============================================================================
# SIMILAR-ITEM MATCHING
# =============================================================================

# Bilingual color groups — any token below normalizes to its group.
# This is what lets "navy" (user's English) match "marine" (PNC's German label).
_COLOR_GROUPS: list[set[str]] = [
    {"ecru", "cream", "natural", "off-white", "offwhite", "creme"},
    {"weiß", "weiss", "white"},
    {"nude"},
    {"beige", "sand", "stone"},
    {"kitt", "putty"},
    {"taupe"},
    {"nougat"},
    {"braun", "brown"},
    {"schoko", "chocolate"},
    {"kaffee", "coffee"},
    {"schlamm", "mud"},
    {"schwarz", "black"},
    {"anthrazit", "anthracite", "charcoal"},
    {"grau", "grey", "gray"},
    {"hellgrau", "lightgrey", "lightgray"},
    {"silber", "silver"},
    {"melange", "heather", "mélange"},
    {"blau", "blue"},
    {"hellblau", "lightblue", "sky"},
    {"marine", "navy", "darkblue"},
    {"indigo"},
    {"denim", "jeans"},
    {"royal", "royalblue"},
    {"petrol", "teal"},
    {"grün", "green"},
    {"khaki"},
    {"schilf", "reed"},
    {"lind", "lime"},
    {"olive", "oliv"},
    {"mint"},
    {"gelb", "yellow"},
    {"gold"},
    {"zitrone", "lemon"},
    {"senf", "mustard"},
    {"ocker", "ochre"},
    {"mais", "corn"},
    {"rosa", "rose", "pink"},
    {"altrosa", "oldrose", "dustypink"},
    {"coral"},
    {"rot", "red"},
    {"bordeaux", "burgundy", "wine"},
    {"fuchsia", "magenta"},
    {"lila", "purple", "violet"},
    {"aubergine", "eggplant"},
    {"orange"},
    {"rost", "rust"},
    {"terracotta"},
]

# Tokens that aren't color but appear in labels — strip before matching
# (e.g. "beige strukturiert" → just "beige").
_COLOR_MODIFIERS = {
    "uni", "strukturiert", "meliert", "gestreift", "gemustert", "gepunktet",
    "structured", "melange", "heather", "patterned", "striped", "dotted",
}

# Reverse-lookup: token → its group (so we can expand a color into its synonyms)
_TOKEN_TO_GROUP: dict[str, set[str]] = {}
for _group in _COLOR_GROUPS:
    for _tok in _group:
        _TOKEN_TO_GROUP[_tok] = _group


def _tokenize_color(label: str) -> set[str]:
    """Split a color label into normalized tokens, dropping modifiers.

    First tries the whole label as a single phrase with separators stripped
    (so "light blue" → "lightblue", which IS in the dictionary).
    Falls back to per-token splitting for multi-color labels like
    "beige|braun" or fuzzy inputs.

    'Hellblau strukturiert' → {'hellblau'}
    'light blue'            → {'lightblue'}        (single recognized phrase)
    'navy blue'             → {'navy', 'blue'}     (each token has a group)
    'beige|braun'           → {'beige', 'braun'}
    """
    if not label:
        return set()
    raw = label.lower().strip()
    # Phrase-level match: "light blue" → "lightblue" → in {hellblau, lightblue, sky}
    phrase = re.sub(r"[\s\-]+", "", raw)
    if phrase in _TOKEN_TO_GROUP:
        return {phrase}
    # Otherwise per-token, drop modifiers
    tokens = re.split(r"[\s|/\-]+", raw)
    return {t for t in tokens if t and t not in _COLOR_MODIFIERS}


def _expand_to_groups(tokens: set[str]) -> set[frozenset[str]]:
    """Map each token to its synonym group; tokens with no group form a
    singleton group of just themselves."""
    groups = set()
    for t in tokens:
        grp = _TOKEN_TO_GROUP.get(t)
        groups.add(frozenset(grp) if grp else frozenset({t}))
    return groups


def _color_score(user_color: str, item_color: str) -> int:
    """How well does the user's color match this item's color?

    Uses bilingual EN↔DE synonym groups so "navy" matches "marine", etc.

    100 — same canonical color group (e.g. user "navy" / item "marine")
     70 — overlap on some but not all tokens (multi-color labels)
      0 — no group overlap at all
    """
    u_groups = _expand_to_groups(_tokenize_color(user_color))
    i_groups = _expand_to_groups(_tokenize_color(item_color))
    if not u_groups or not i_groups:
        return 0
    shared = u_groups & i_groups
    if not shared:
        return 0
    # Coverage: fraction of the smaller side's groups that overlap
    coverage = len(shared) / min(len(u_groups), len(i_groups))
    return int(round(100 * coverage))


def _score_candidate(form_data: dict, item: dict) -> tuple[int, list[str]]:
    """Total similarity score for one item, plus the reasons that contributed.

    Score components (max 130; we report rounded):
      Color match (0-100)  — bilingual via _color_score
      Category was a hard filter, so we don't double-count it here.
      Future-proof: fit / sleeve / pattern only matchable when we enrich
      catalog data, which we don't have today (item names don't carry those
      attributes). Hook is here so adding them later is one-liner.
    """
    score = 0
    reasons: list[str] = []

    color_pts = _color_score(form_data.get("color_name", ""), item.get("color", ""))
    if color_pts > 0:
        score += color_pts
        if color_pts >= 100:
            reasons.append("exact color match (incl. EN↔DE synonyms)")
        elif color_pts >= 70:
            reasons.append("close color match")
        else:
            reasons.append("partial color overlap")

    return score, reasons


def find_similar_item(form_data: dict) -> dict | None:
    """Return the catalog SKU most similar to the user's tech pack inputs,
    or ``None`` if no category is selected / category has no entries.

    Text-only matching. Fast (sub-millisecond). For the AI-vision variant
    that visually compares the uploaded photo to each candidate, see
    ``find_similar_item_vision``.
    """
    cat = form_data.get("garment_sub_category")
    if not cat:
        return None

    items = _load().get("items", [])
    candidates = [it for it in items if it.get("category", "").lower() == cat.lower()]
    if not candidates:
        return None

    scored = [(item, *_score_candidate(form_data, item)) for item in candidates]
    scored.sort(key=lambda triple: triple[1], reverse=True)
    top_item, top_score, top_reasons = scored[0]

    if top_score == 0:
        # No useful signal — fall back to median-priced item as a "representative".
        sorted_by_price = sorted(candidates, key=lambda c: c["price_eur"])
        top_item = sorted_by_price[len(sorted_by_price) // 2]
        match_reason = f"no specific signal — showing median-priced {cat.lower()}"
    else:
        match_reason = ", ".join(top_reasons) if top_reasons else "category match"

    return {
        **top_item,
        "price_usd": top_item["price_eur"] * EUR_TO_USD,
        "match_score": top_score,
        "match_reason": match_reason,
        "match_method": "text",
        "fx_rate": EUR_TO_USD,
    }


# =============================================================================
# AI VISION MATCHING (slower, more accurate)
# =============================================================================

def _vision_available() -> bool:
    if not GENAI_AVAILABLE:
        return False
    try:
        return "gemini_api_key" in st.secrets
    except Exception:
        return False


def _shortlist_candidates(form_data: dict, top_n: int = 8) -> list[dict]:
    """Return the top_n text-scored candidates within the selected category.

    Used to narrow before sending images to Vision — we don't want to upload
    all 120 photos every time.
    """
    cat = form_data.get("garment_sub_category")
    if not cat:
        return []
    items = _load().get("items", [])
    cands = [it for it in items if it.get("category", "").lower() == cat.lower()]
    if not cands:
        return []
    scored = sorted(cands, key=lambda c: _score_candidate(form_data, c)[0], reverse=True)
    return scored[:top_n]


@st.cache_data(show_spinner=False, ttl=3600)
def _fetch_image(url: str) -> bytes | None:
    """Download a candidate's product image. Cached per URL for an hour."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tech-pack-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read()
    except Exception:
        return None


def find_similar_item_vision(
    form_data: dict,
    user_photo_data: str,
    user_photo_mime: str,
    top_n: int = 6,
) -> dict | None:
    """Vision-narrowed match: text-shortlist top_n, then ask Gemini which is
    the most visually similar to the user's uploaded photo.

    ``user_photo_data`` is the base64 string from session_state (same shape
    as images stored elsewhere in the app).

    Returns the same dict shape as ``find_similar_item`` but with
    ``match_method="vision"`` and a reasoning sentence from the model.
    Falls back to text-only ``find_similar_item`` on any vision error.
    """
    if not _vision_available():
        return find_similar_item(form_data)

    shortlist = _shortlist_candidates(form_data, top_n=top_n)
    if not shortlist:
        return None
    if len(shortlist) == 1:
        # Only one option — no need to call Vision
        return find_similar_item(form_data)

    # Download candidate images
    cand_with_bytes: list[tuple[dict, bytes]] = []
    for c in shortlist:
        img = _fetch_image(c.get("image_url", "")) if c.get("image_url") else None
        if img:
            cand_with_bytes.append((c, img))
    if len(cand_with_bytes) < 2:
        # Nothing or only one fetch succeeded — degrade to text
        return find_similar_item(form_data)

    # Build the Vision call
    try:
        client = genai.Client(api_key=st.secrets["gemini_api_key"])
        contents: list = [
            "REFERENCE GARMENT (user's photo):",
            genai_types.Part.from_bytes(
                data=base64.b64decode(user_photo_data),
                mime_type=user_photo_mime,
            ),
        ]
        for idx, (_, img_bytes) in enumerate(cand_with_bytes, start=1):
            contents.append(f"CANDIDATE #{idx}:")
            contents.append(genai_types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))

        spec_notes = []
        if form_data.get("color_name"):
            spec_notes.append(f"color: {form_data['color_name']}")
        if form_data.get("fit"):
            spec_notes.append(f"fit: {form_data['fit']}")
        if form_data.get("sleeve_length"):
            spec_notes.append(f"sleeves: {form_data['sleeve_length']}")
        if form_data.get("neckline"):
            spec_notes.append(f"neckline: {form_data['neckline']}")
        spec_str = ("Tech-pack spec the user has confirmed: " + "; ".join(spec_notes) + ".") if spec_notes else ""

        contents.append(
            f"\nWhich of the {len(cand_with_bytes)} candidates is the MOST VISUALLY SIMILAR to "
            f"the reference garment? Consider silhouette, body length, sleeve length, neckline, "
            f"hem detail, pattern (stripes, prints, solid), and overall proportions. "
            f"{spec_str}\n\n"
            f"Reply with ONLY a JSON object — no markdown — of the form: "
            f'{{"choice": <integer 1..{len(cand_with_bytes)}>, "reason": "<one short sentence>"}}.'
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",  # text+vision; cheap & fast
            contents=contents,
        )
        text = (response.text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        choice_idx = int(parsed.get("choice", 1)) - 1
        if not (0 <= choice_idx < len(cand_with_bytes)):
            raise ValueError(f"Vision returned out-of-range choice: {parsed}")
        chosen, _ = cand_with_bytes[choice_idx]
        reason = parsed.get("reason", "AI Vision picked this as the most similar")

        return {
            **chosen,
            "price_usd": chosen["price_eur"] * EUR_TO_USD,
            "match_score": 100,
            "match_reason": f"AI Vision: {reason}",
            "match_method": "vision",
            "fx_rate": EUR_TO_USD,
            "vision_pool_size": len(cand_with_bytes),
        }

    except Exception:
        # Any vision failure → graceful text fallback
        result = find_similar_item(form_data)
        if result is not None:
            result["match_reason"] = result.get("match_reason", "") + " (AI Vision failed, fell back to text match)"
        return result

