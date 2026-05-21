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

import json
import statistics
from pathlib import Path

import streamlit as st


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

def _color_score(user_color: str, item_color: str) -> int:
    """How well does the user's color match this item's color?

    100 — exact match (case-insensitive after normalize)
     70 — one is substring of the other (e.g. "navy" vs "navy blue")
     30+ — at least one shared word
      0 — no overlap
    """
    u = (user_color or "").lower().strip()
    i = (item_color or "").lower().strip()
    if not u or not i:
        return 0
    if u == i:
        return 100
    if u in i or i in u:
        return 70
    u_words = set(u.split())
    i_words = set(i.split())
    overlap = u_words & i_words
    if overlap:
        return 30 + 10 * len(overlap)
    return 0


def find_similar_item(form_data: dict) -> dict | None:
    """Return the catalog SKU most similar to the user's tech pack inputs,
    or ``None`` if no category is selected / category has no entries.

    The returned dict has price_eur AND price_usd — the latter is computed
    here so the caller doesn't have to know the FX rate.
    """
    cat = form_data.get("garment_sub_category")
    if not cat:
        return None

    items = _load().get("items", [])
    candidates = [it for it in items if it.get("category", "").lower() == cat.lower()]
    if not candidates:
        return None

    user_color = (form_data.get("color_name") or "").strip()

    if user_color:
        # Score and pick the best
        scored = [(_color_score(user_color, c.get("color", "")), c) for c in candidates]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top_score, top_item = scored[0]
        match_reason = (
            "exact color match" if top_score >= 100 else
            "close color match"  if top_score >= 70  else
            "partial color match" if top_score >= 30 else
            f"no color match — showing median-priced {cat.lower()}"
        )
        # If even the best score is 0, fall back to median-priced
        if top_score == 0:
            sorted_by_price = sorted(candidates, key=lambda c: c["price_eur"])
            top_item = sorted_by_price[len(sorted_by_price) // 2]
    else:
        # No color filter — pick the median-priced item in the category
        sorted_by_price = sorted(candidates, key=lambda c: c["price_eur"])
        top_item = sorted_by_price[len(sorted_by_price) // 2]
        match_reason = f"category match — showing median-priced {cat.lower()}"
        top_score = 0

    return {
        **top_item,
        "price_usd": top_item["price_eur"] * EUR_TO_USD,
        "match_score": top_score,
        "match_reason": match_reason,
        "fx_rate": EUR_TO_USD,
    }

