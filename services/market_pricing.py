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


# =============================================================================
# BRAND-DEFAULTS AUTO-FILL  (use the matched item's known specs to populate
# the form — stakeholder ask: "populate with MC to keep it realistic")
# =============================================================================

# Map the German measurement names PNC uses to the English measurement-point
# names we have in KNITWEAR_MEASUREMENT_POINTS. We only map the ones PNC
# actually exposes on product detail pages — the rest of the points are
# left for the user to fill in.
# Updated to match the team's vocabulary (RWS spec sheets) after we renamed
# the measurement points in KNITWEAR_MEASUREMENT_POINTS. Where the German
# term doesn't have a clean team-spec equivalent (e.g. Taillenweite = waist
# width — team doesn't track separately), we drop it rather than guess.
_DE_TO_FORM_MEASUREMENT = {
    "Rückenlänge":     "Front body Length (fm HPS)",  # back length ≈ body length on flat-laid garments
    "Vorderlänge":     "Front body Length (fm HPS)",
    "Brustweite":      "1/2 Chest Width (below armhole)",
    "Hüftweite":       "1/2 Bottom Width (at edge)",
    "Schulterbreite":  "Shoulder (seam to seam)",
    "Ärmellänge":      "Sleeve length (from CB)",
    "Armlänge":        "Sleeve length (from CB)",
    "Armausschnitt":   "Armhole (straight)",
    "Ärmelöffnung":    "Cuff width (at edge)",
    "Halsausschnitt":  "Front neck drop (fm HPS)",
}


# PNC ships composition labels in German ("100% Baumwolle"). The form's
# COMPOSITIONS dropdown is English, so anything we paste verbatim from the
# scraped data fails the selectbox match and renders blank. We translate the
# fabric noun tokens here. Order matters where one term is a substring of
# another (e.g. "Merinowolle" before "Wolle").
_FABRIC_DE_TO_EN: list[tuple[str, str]] = [
    ("Merinowolle",   "Merino Wool"),
    ("Lambswool",     "Lambswool"),
    ("Schurwolle",    "Virgin Wool"),
    ("Kaschmir",      "Cashmere"),
    ("Baumwolle",     "Cotton"),
    ("Bio-Baumwolle", "Organic Cotton"),
    ("Wolle",         "Wool"),
    ("Viskose",       "Viscose"),
    ("Polyester",     "Polyester"),
    ("Polyamid",      "Polyamide"),
    ("Acryl",         "Acrylic"),
    ("Polyacryl",     "Acrylic"),
    ("Leinen",        "Linen"),
    ("Seide",         "Silk"),
    ("Elasthan",      "Elastane"),
    ("Mohair",        "Mohair"),
    ("Alpaka",        "Alpaca"),
    ("Hanf",          "Hemp"),
    ("Modal",         "Modal"),
    ("Lyocell",       "Lyocell"),
    ("Tencel",        "Tencel"),
    ("Metallisiert",  "Metallic"),
    ("Metall",        "Metallic"),
    ("Nylon",         "Nylon"),
]


def _translate_fabric_text(text: str) -> str:
    """Translate German fabric nouns to English in a composition string.
    Leaves numbers, percent signs, and separators alone."""
    if not text:
        return text
    out = text
    for de, en in _FABRIC_DE_TO_EN:
        out = out.replace(de, en)
    return out


def _composition_to_text(composition_list) -> str:
    """Flatten the [{group:..., text:'70% Viskose|30% Polyamid'}, ...] form into
    a single readable English string. Keeps secondary groups (Futter /
    Verzierung) when they exist."""
    if not composition_list:
        return ""
    parts = []
    for entry in composition_list:
        text = _translate_fabric_text((entry.get("text") or "").replace("|", " / ").strip())
        group = (entry.get("group") or "").strip()
        if not text:
            continue
        # If there's only one group ("Obermaterial"), don't bother labeling it.
        if len(composition_list) > 1 and group and group != "Obermaterial":
            parts.append(f"{text} ({group})")
        else:
            parts.append(text)
    return ", ".join(parts)


def _derive_yarn_type(composition_list) -> str:
    """Pick a yarn-type label from the matched item's primary composition.
    The form's YARN_TYPES list is English and percentage-prefixed (e.g.
    "100% Cotton"). We use the translated primary fibre — if it matches a
    known yarn-type label we return that, otherwise we return the verbatim
    primary composition string so the dropdown can show it dynamically.
    """
    if not composition_list:
        return ""
    primary = composition_list[0]
    text = _translate_fabric_text((primary.get("text") or "").replace("|", " / ").strip())
    return text


def _sizes_to_range(available_sizes) -> str | None:
    """Pick the SIZE_RANGES dropdown option that best fits the given list of
    sizes. Returns None if nothing fits cleanly — caller leaves the field empty."""
    if not available_sizes:
        return None
    letters = {s.upper() for s in available_sizes if s and not s[0].isdigit()}
    if not letters:
        # Numeric sizes (34, 36, etc.) — falls outside the dropdown shapes
        return "Custom"
    # Pick the smallest dropdown option that covers what we see
    covers = {
        "S - XL":     {"S", "M", "L", "XL"},
        "XS - XXL":   {"XS", "S", "M", "L", "XL", "XXL"},
        "XXS - XXXL": {"XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"},
    }
    for label, supported in covers.items():
        if letters.issubset(supported):
            return label
    return "Custom"


# Hardcoded brand standards derived from the team's own internal spec sheets
# (RWS-M21xx / NC2xxx series, March 2025). These are the values they actually
# use every time — no variation across the 9 styles inspected. Used as a
# fallback default when no per-SKU detail overrides them.
#
# Source: ~/Desktop/RWS-...-190325-R1.xls
_TEAM_KNITWEAR_DEFAULTS = {
    # composition: kept in the team's preferred "2/26Nm 100% RWS Wool" format.
    # The form's selectbox dynamically accepts values not in COMPOSITIONS, so
    # this displays even though it's not a standard dropdown option.
    "composition":   "2/26Nm 100% RWS Wool",
    "gauge":         "7GG",                # in GAUGES dropdown exactly
    "yarn_count":    "Nm 2/26",            # YARN_COUNTS dropdown format (Nm-prefixed)
    "ends":          "2 ends",             # not yet a form field but kept for export
    "maker":         "New world",          # factory name (not yet a form field)
    "unit":          "CM",                 # measurements unit
}


def team_default(field: str, product_type: str = "Knitwear (Sweater / Cardigan)") -> str:
    """Return the team's standard default for a given field, or empty string.
    Currently only knitwear has documented defaults — T-shirts fall through to ''.
    """
    if product_type and "Knitwear" in product_type:
        return _TEAM_KNITWEAR_DEFAULTS.get(field, "")
    return ""


def apply_brand_defaults(match: dict, session_state, overwrite: bool = False) -> dict:
    """Populate the tech-pack form from the matched MC (Marie Lund) item.

    Stakeholder's framing: a "new" garment for an existing brand is mostly a
    remix of brand conventions — composition, gauge, size chart, etc. So when
    we find the closest catalog item via ``find_similar_item``, we copy its
    known brand-convention values into the form as DEFAULTS. The user only
    has to edit what's actually different about their new design.

    Only fills:
      - composition (free-text field)
      - measurements (dict — uses the matched item's sample_measurements as
        baseline; German measurement names mapped to the English form labels)
      - size_range (dropdown — best-fit from the matched item's available sizes)
      - base_size (mirrors the sample-size PNC uses for measurements, usually S)

    By default ``overwrite=False`` — we never replace something the user has
    already filled in. Pass overwrite=True to force-replace.

    Returns a dict {field_label: applied_value} for the UI to show what got
    filled, mirroring photo_analyzer.apply_suggestions's contract.
    """
    applied: dict = {}
    blank = "— Not specified —"

    def _empty(v):
        return v in (None, "", blank, {}, [])

    # 1. Composition — single free-text field
    comp_text = _composition_to_text(match.get("composition"))
    if comp_text:
        current = session_state.get("composition")
        if overwrite or _empty(current):
            session_state["composition"] = comp_text
            applied["composition"] = comp_text

    # 2. Size range
    size_label = _sizes_to_range(match.get("available_sizes"))
    if size_label:
        current = session_state.get("size_range")
        if overwrite or _empty(current):
            session_state["size_range"] = size_label
            applied["size_range"] = size_label

    # 3. Base size — use whichever size PNC reported measurements against
    sample_meas = match.get("sample_measurements") or []
    if sample_meas:
        # All entries share the same sample_size in our data, but be defensive
        base_size = sample_meas[0].get("sample_size")
        if base_size:
            current = session_state.get("base_size")
            if overwrite or _empty(current):
                session_state["base_size"] = base_size
                applied["base_size"] = base_size

    # 4. Measurements — German labels → English form labels
    if sample_meas:
        current_meas = session_state.get("measurements") or {}
        new_meas = dict(current_meas) if not overwrite else {}
        applied_meas: dict = {}
        for entry in sample_meas:
            en_label = _DE_TO_FORM_MEASUREMENT.get(entry.get("name"))
            if not en_label:
                continue
            if not overwrite and en_label in new_meas:
                continue
            new_meas[en_label] = {
                "value": float(entry.get("value") or 0),
                "tolerance": 1.0,
            }
            applied_meas[en_label] = entry.get("value")
        if applied_meas:
            session_state["measurements"] = new_meas
            applied["measurements"] = applied_meas

    # 5. Team hardcoded defaults — composition / gauge / yarn_count baseline
    # (from the RWS-M21xx spec sheets). Filled ONLY when still empty AFTER
    # the per-SKU pass above, so a more specific match always wins.
    pt = session_state.get("product_type") or match.get("product_type") or ""
    if "Knitwear" in pt:
        for field in ("composition", "gauge", "yarn_count"):
            if _empty(session_state.get(field)):
                fallback = team_default(field, pt)
                if fallback:
                    session_state[field] = fallback
                    applied.setdefault(field, fallback)

    # 6. Gender — Marie Lund is exclusively women's wear, so set safely
    if _empty(session_state.get("gender")):
        session_state["gender"] = "Women"
        applied["gender"] = "Women"

    # 7. Season — derived purely from the current date. Fashion convention
    # is SS (spring/summer) for Jan-Jun, AW (autumn/winter) for Jul-Dec.
    # User can override to next-season if they're working ahead.
    if _empty(session_state.get("season")):
        from datetime import date as _date
        today = _date.today()
        yr2 = str(today.year)[-2:]
        season = f"SS{yr2}" if 1 <= today.month <= 6 else f"AW{yr2}"
        session_state["season"] = season
        applied["season"] = season

    # 8. Style Name — placeholder so the form doesn't look unfinished.
    # Built from "Color + Sub-category" (e.g. "Yellow Cardigan").
    # User typically overrides with the customer's internal style name.
    if _empty(session_state.get("style_name")):
        sub_cat = session_state.get("garment_sub_category") or ""
        color = session_state.get("color_name") or ""
        if sub_cat:
            # Take first word of color (e.g. "Bright yellow" → "Yellow"),
            # title-case the sub-category, glue them.
            first_color = color.split()[-1].title() if color else ""
            cat_clean = sub_cat.split("/")[0].strip().title()  # "Pullover / Sweater" → "Pullover"
            sn = f"{first_color} {cat_clean}".strip()
            if sn:
                session_state["style_name"] = sn
                applied["style_name"] = sn

    return applied


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


# German names for each English fibre filter option. PNC ships composition
# labels in German, so the filter has to look for the German tokens. Some
# English names map to multiple German variants (Acrylic → "Acryl" or
# "Polyacryl") — we accept any of them. We're case-insensitive on match.
_MATERIAL_DE_TOKENS: dict[str, list[str]] = {
    "Cotton":     ["Baumwolle"],
    "Wool":       ["Wolle"],          # also covers Schurwolle, Merinowolle
    "Cashmere":   ["Kaschmir"],
    "Viscose":    ["Viskose"],
    "Polyester":  ["Polyester"],
    "Acrylic":    ["Acryl", "Polyacryl"],
    "Linen":      ["Leinen"],
    "Silk":       ["Seide"],
    "Polyamide":  ["Polyamid"],
    "Elastane":   ["Elasthan"],
    "Mohair":     ["Mohair"],
    "Alpaca":     ["Alpaka"],
}

# English fibre labels surfaced in the filter UI, in display order.
FILTER_MATERIALS = list(_MATERIAL_DE_TOKENS.keys())


# Color families — broader buckets that group similar colors so users can
# filter by "show me all blues" instead of having to know whether a specific
# item is labelled "marine" or "navy" or "hellblau".
_COLOR_FAMILY_PATTERNS = [
    ("Cream / White",  r"ecru|cream|natur|wei|off-?white|white|nude"),
    ("Beige / Sand",   r"beige|sand|stone|taupe|kitt|nougat"),
    ("Brown",          r"braun|brown|cognac|mocha|kaffee|schoko|schlamm"),
    ("Black",          r"schwarz|black|anthrazit|charcoal"),
    ("Grey",           r"grau|grey|gray|silber|melange"),
    ("Blue",           r"blau|blue|navy|denim|jeans|petrol|marine|indigo|royal"),
    ("Green",          r"grün|green|oliv|moss|mint|khaki|jade|schilf|lind"),
    ("Yellow / Gold",  r"gelb|yellow|gold|senf|mustard|ocker|zitrone|mais"),
    ("Pink / Rose",    r"rosa|pink|rose|peach|coral|altrosa"),
    ("Red / Burgundy", r"rot|red|bordeaux|wein|cherry|kirsch|burgundy|fuchsia"),
    ("Purple",         r"lila|violet|purple|aubergine|flieder|lavendel"),
    ("Orange",         r"orange|apricot|terracotta|rost|rust"),
    ("Turquoise",      r"türkis|turquoise|teal|aqua"),
]


def color_family_for(color_label: str) -> str:
    """Map a raw color label (German or English) to a broader family bucket
    for the catalog browse view. Returns "Other" if no pattern matches."""
    if not color_label:
        return "Other"
    lower = color_label.lower()
    for family, pattern in _COLOR_FAMILY_PATTERNS:
        if re.search(pattern, lower):
            return family
    return "Other"


# Catalog-browse helpers (used by the Browse Catalog tab).
# These return the FULL dataset for the catalog view — distinct from
# find_similar_item which only returns one match.

def load_all_items() -> list[dict]:
    """Return every SKU in the dataset (with USD prices computed)."""
    items = _load().get("items", [])
    out = []
    for it in items:
        out.append({**it, "price_usd": (it.get("price_eur") or 0) * EUR_TO_USD})
    return out


def get_all_categories() -> list[str]:
    """Distinct sub-category labels, sorted by frequency descending."""
    items = _load().get("items", [])
    counts: dict[str, int] = {}
    for it in items:
        c = it.get("category") or ""
        if c:
            counts[c] = counts.get(c, 0) + 1
    return [c for c, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]


def get_all_color_families() -> list[str]:
    """Distinct color families present in the catalog, sorted by frequency."""
    items = _load().get("items", [])
    counts: dict[str, int] = {}
    for it in items:
        fam = color_family_for(it.get("color") or "")
        if fam:
            counts[fam] = counts.get(fam, 0) + 1
    return [c for c, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]


def filter_catalog(
    items: list[dict],
    categories: list[str] | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    materials: list[str] | None = None,
    color_families: list[str] | None = None,
    pattern_keyword: str | None = None,
) -> list[dict]:
    """Apply all catalog filters to an item list. Empty/None filters are skipped.

    pattern_keyword is matched against the item's name AND description
    (case-insensitive substring), so a user typing "stripe" finds anything
    that mentions stripes regardless of where.
    """
    out = items
    if categories:
        cset = {c.lower() for c in categories}
        out = [it for it in out if (it.get("category") or "").lower() in cset]
    if price_min is not None:
        out = [it for it in out if (it.get("price_usd") or 0) >= price_min]
    if price_max is not None:
        out = [it for it in out if (it.get("price_usd") or 0) <= price_max]
    if materials:
        out = [it for it in out if all(_item_has_material(it, m) for m in materials)]
    if color_families:
        fset = set(color_families)
        out = [it for it in out if color_family_for(it.get("color") or "") in fset]
    if pattern_keyword:
        kw = pattern_keyword.lower().strip()
        if kw:
            out = [
                it for it in out
                if kw in (it.get("name") or "").lower()
                or kw in (it.get("description") or "").lower()
            ]
    return out


def _item_has_material(item: dict, english_material: str) -> bool:
    """True iff the item's composition mentions the given fibre.

    Looks up the German equivalents of ``english_material`` (see
    ``_MATERIAL_DE_TOKENS``) and returns True if any of them appears in any
    of the composition entries.
    """
    tokens = _MATERIAL_DE_TOKENS.get(english_material, [english_material])
    comp = item.get("composition") or []
    for entry in comp:
        text = (entry.get("text") or "").lower()
        for tok in tokens:
            if tok.lower() in text:
                return True
    return False


def _apply_filters(candidates: list[dict], filters: dict | None) -> list[dict]:
    """Trim a candidate list by user filters. ``filters`` shape:

      {
        "price_min": float | None,  # USD
        "price_max": float | None,  # USD
        "materials": list[str],     # English fibre names — ALL must be present
      }
    """
    if not filters:
        return candidates
    out = candidates
    pmin = filters.get("price_min")
    pmax = filters.get("price_max")
    if pmin is not None:
        out = [c for c in out if (c.get("price_eur") or 0) * EUR_TO_USD >= pmin]
    if pmax is not None:
        out = [c for c in out if (c.get("price_eur") or 0) * EUR_TO_USD <= pmax]
    mats = filters.get("materials") or []
    if mats:
        out = [c for c in out if all(_item_has_material(c, m) for m in mats)]
    return out


def find_similar_item(form_data: dict, filters: dict | None = None) -> dict | None:
    """Return the catalog SKU most similar to the user's tech pack inputs,
    or ``None`` if no category is selected / category has no entries.

    Optional ``filters`` constrain the candidate pool BEFORE scoring:
      - price_min / price_max (USD): keep only items inside the range
      - materials: list of English fibre names; item must mention all of them

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

    # Apply user filters BEFORE scoring — no point scoring items the user
    # has explicitly excluded.
    filtered = _apply_filters(candidates, filters)
    if not filtered:
        # User filters too restrictive — surface this via a sentinel return so
        # the caller can show a helpful "no items match" message instead of
        # silently falling back to an unrelated item.
        return {
            "_no_filter_match": True,
            "category_size": len(candidates),
            "match_reason": "no items in this category match the active filters",
        }

    scored = [(item, *_score_candidate(form_data, item)) for item in filtered]
    scored.sort(key=lambda triple: triple[1], reverse=True)
    top_item, top_score, top_reasons = scored[0]

    if top_score == 0:
        # No useful signal — fall back to median-priced item as a "representative".
        sorted_by_price = sorted(filtered, key=lambda c: c["price_eur"])
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
        "filter_pool_size": len(filtered),
        "category_pool_size": len(candidates),
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


def _shortlist_candidates(form_data: dict, top_n: int = 8, filters: dict | None = None) -> list[dict]:
    """Return the top_n text-scored candidates within the selected category.

    Honors the same filters as ``find_similar_item`` so that AI Vision
    refine also respects user-applied price/material constraints.
    """
    cat = form_data.get("garment_sub_category")
    if not cat:
        return []
    items = _load().get("items", [])
    cands = [it for it in items if it.get("category", "").lower() == cat.lower()]
    cands = _apply_filters(cands, filters)
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
    filters: dict | None = None,
) -> dict | None:
    """Vision-narrowed match: text-shortlist top_n, then ask Gemini which is
    the most visually similar to the user's uploaded photo.

    Respects the same ``filters`` as ``find_similar_item`` so AI Vision
    refine doesn't suggest items the user has explicitly filtered out.
    """
    if not _vision_available():
        return find_similar_item(form_data, filters=filters)

    shortlist = _shortlist_candidates(form_data, top_n=top_n, filters=filters)
    if not shortlist:
        return None
    if len(shortlist) == 1:
        # Only one option — no need to call Vision
        return find_similar_item(form_data, filters=filters)

    # Download candidate images
    cand_with_bytes: list[tuple[dict, bytes]] = []
    for c in shortlist:
        img = _fetch_image(c.get("image_url", "")) if c.get("image_url") else None
        if img:
            cand_with_bytes.append((c, img))
    if len(cand_with_bytes) < 2:
        # Nothing or only one fetch succeeded — degrade to text
        return find_similar_item(form_data, filters=filters)

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
        # Any vision failure → graceful text fallback.
        # Pass filters through so we don't silently suggest items the user
        # has explicitly filtered out (matches the docstring contract above).
        result = find_similar_item(form_data, filters=filters)
        if result is not None:
            result["match_reason"] = result.get("match_reason", "") + " (AI Vision failed, fell back to text match)"
        return result

