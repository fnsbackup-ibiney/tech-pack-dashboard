"""
AI Technical Drawing — generates a flat sketch from a tech pack's inputs.

How it works:
  1. ``build_prompt`` composes a descriptive prompt from the form data.
  2. If a Gemini key is configured, ``generate_drawing`` calls Gemini's image
     model and returns the generated PNG.
  3. If the key is missing OR the API call fails, we fall back to the
     pre-baked cardigan PNG (so the UI never silently breaks).

The demo PNG path keeps the artificial latency from the original demo so that
spinner-driven UX still feels real. The real API path doesn't sleep — Gemini
itself takes 5-15 s and that's the natural wait.

To swap to a different image API (Imagen / DALL-E / Stable Diffusion / etc.),
replace ``_gemini_generate`` — everything else stays.
"""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

from services.image_helpers import make_image_entry


# Pre-baked technical drawing used when AI is unavailable. Shipped with the
# demo data so we always have a valid fallback.
_FALLBACK_DRAWING = Path(__file__).parent.parent / "sample_data" / "images" / "cardigan_technical.png"


# How long to pretend the AI is "thinking" in DEMO mode only. In real mode the
# API call itself takes 5-15 s, no need for artificial wait.
DEMO_LATENCY_SECONDS = 2.2


# Gemini image-generation model. "gemini-2.5-flash-image-preview" is the
# current image-capable variant (aka "Nano Banana"). If it gets renamed,
# swap this constant — nothing else needs to change.
MODEL = "gemini-2.5-flash-image-preview"


# =============================================================================
# CONFIG CHECKS
# =============================================================================

def is_configured() -> bool:
    """True iff we can call the real image API (Gemini key in secrets + SDK)."""
    if not GENAI_AVAILABLE:
        return False
    try:
        return "gemini_api_key" in st.secrets
    except Exception:
        return False


def is_demo_mode() -> bool:
    """Surfaced in the UI as a small badge. Returns True when we're falling
    back to the pre-baked PNG (no key / SDK missing)."""
    return not is_configured()


# =============================================================================
# PROMPT
# =============================================================================

def build_prompt(data: dict) -> str:
    """Compose a descriptive prompt from the form data — what we send to the
    image API. Also surfaced in the UI so the user can audit what AI saw."""
    product_type = data.get("product_type") or "knitwear garment"
    short_type = product_type.split(" (")[0]  # "Knitwear" / "T-shirt"

    parts = [
        "Industry fashion CAD flat sketch — front and back view side by side",
        f"of a {data.get('fit', '').lower()} {short_type.lower()}".strip(),
    ]

    if data.get("garment_sub_category"):
        parts.append(f"({data['garment_sub_category'].lower()})")
    if data.get("neckline"):
        parts.append(f"with {data['neckline'].lower()} neckline")
    if data.get("sleeve_length") or data.get("sleeve_type"):
        sleeve_desc = " ".join(filter(None, [
            (data.get("sleeve_length") or "").lower(),
            (data.get("sleeve_type") or "").lower(),
        ]))
        if sleeve_desc.strip():
            parts.append(f"{sleeve_desc} sleeves")
    if data.get("placket"):
        parts.append(f"{data['placket'].lower()} closure")
    if data.get("rib_structure"):
        # Value already says "rib" (e.g. "1x1 Rib") — just describe as detail
        parts.append(f"{data['rib_structure'].lower()} detail")
    if data.get("hem_style"):
        # Value already says "hem" (e.g. "Ribbed hem") — don't double it
        parts.append(data["hem_style"].lower())
    if data.get("cuff_style"):
        # Value already says "cuff" (e.g. "Ribbed cuff") — don't double it
        parts.append(data["cuff_style"].lower())

    # Hard styling rules — explicit so the image model doesn't add color/model/background
    parts.append(
        "Strict requirements: black line art only, white background, no model, "
        "no human body, no color, no shading, no gradient, no perspective, "
        "clean vector-style technical illustration suitable for a manufacturing tech pack. "
        "Label FRONT and BACK below each respective view."
    )

    return ", ".join(p for p in parts if p and p.strip() and p.strip() != ",")


# =============================================================================
# REAL AI GENERATION (Gemini Image)
# =============================================================================

def _gemini_generate(prompt: str) -> bytes:
    """Call Gemini Image API and return raw PNG bytes.

    Raises RuntimeError if no image is returned in the response. Other
    exceptions (network / quota / etc.) bubble up to ``generate_drawing``,
    which converts them into a graceful demo-fallback.
    """
    client = genai.Client(api_key=st.secrets["gemini_api_key"])
    response = client.models.generate_content(
        model=MODEL,
        contents=[prompt],
        config=genai_types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
        ),
    )

    # Walk through response parts — image is in inline_data
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data

    raise RuntimeError("Gemini returned no image in the response — check quota / model availability.")


# =============================================================================
# DEMO FALLBACK
# =============================================================================

def _demo_drawing(prompt: str, error: str | None = None) -> dict:
    """Return the pre-baked cardigan PNG as the 'AI output'.

    Used when:
      - No Gemini key is configured (true demo mode)
      - The API call fails (error path — caption shows why)
    """
    time.sleep(DEMO_LATENCY_SECONDS)
    raw_bytes = _FALLBACK_DRAWING.read_bytes()
    caption = "AI-generated technical drawing (demo placeholder)"
    if error:
        caption = f"AI generation failed — showing demo placeholder ({error[:60]})"
    entry = make_image_entry(
        raw_bytes,
        filename=_FALLBACK_DRAWING.name,
        caption=caption,
    )
    entry["source"] = "ai_generated_demo"
    entry["prompt"] = prompt
    if error:
        entry["error"] = error
    return entry


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

def generate_drawing(data: dict) -> dict:
    """Produce a technical drawing image dict matching the same shape used
    elsewhere (id / caption / data / mime / source / prompt).

    Real AI path:
      - Builds a prompt from form data
      - Calls Gemini Image
      - Wraps the returned PNG in a standard image dict
    Fallback path:
      - Returns the pre-baked cardigan PNG with a clear caption
    """
    prompt = build_prompt(data)

    if not is_configured():
        return _demo_drawing(prompt)

    try:
        image_bytes = _gemini_generate(prompt)
        entry = make_image_entry(
            image_bytes,
            filename="ai_sketch.png",
            caption="AI-generated technical drawing",
        )
        entry["source"] = "ai_generated"
        entry["prompt"] = prompt
        return entry
    except Exception as e:
        # Any failure (network, quota, JSON shape, etc.) → demo fallback so the
        # UI never silently breaks. The caption tells the user what happened.
        return _demo_drawing(prompt, error=f"{type(e).__name__}: {e}")
