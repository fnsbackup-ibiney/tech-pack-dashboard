"""
Market Pricing Reference — show what similar garments are selling for now.

Data source: ``data/marie_lund_pricing.json`` (120 SKUs scraped from
peek-und-cloppenburg.de on 2026-05-21). When the user picks a garment
sub-category in the Editor, we show count / median / range / average from
that bucket so they have a sanity check while filling target_price_usd.

If you want to refresh the data, re-scrape page 1+2 of the Marie Lund
knitwear listing, dedupe color variants from each style's detail page, and
overwrite the JSON. The keys we depend on are ``items[].category`` (matched
against KNITWEAR_SUB_CATEGORIES) and ``items[].price_eur``.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import streamlit as st


_DATA_FILE = Path(__file__).parent.parent / "data" / "marie_lund_pricing.json"


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
        "median": statistics.median(prices),
        "mean": statistics.mean(prices),
        "low": prices[0],
        "high": prices[-1],
        "p25": p25,
        "p75": p75,
        "currency": "EUR",
    }
