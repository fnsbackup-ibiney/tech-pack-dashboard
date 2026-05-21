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

import base64
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


# Gemini image-generation model. Verified via list_models on 2026-05-21.
# Also valid: "gemini-3-pro-image-preview" (higher quality, slower/pricier)
# or "gemini-3.1-flash-image-preview" (newest). Swap here if you want to
# trade speed vs quality — the SDK call interface is identical.
MODEL = "gemini-2.5-flash-image"


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

# Visual fields — these are things you can SEE in a photo. When a reference
# photo is provided, the photo is the ground truth for these and we don't
# echo them from the form (to avoid form↔photo conflict).
_VISUAL_FIELDS = {
    "fit", "neckline", "sleeve_length", "sleeve_type",
    "hem_style", "cuff_style", "placket", "rib_structure",
}


def build_prompt(data: dict, has_reference_image: bool = False) -> str:
    """Compose a descriptive prompt from the form data — what we send to the
    image API. Also surfaced in the UI so the user can audit what AI saw.

    Rule of thumb:
      - With reference photo → photo is GROUND TRUTH for visual features.
        Spec is only used for things you can't see (composition, knit
        structure, print/embroidery, garment category).
      - Without photo → spec is all we have, used directly.
    """
    product_type = data.get("product_type") or "knitwear garment"
    short_type = product_type.split(" (")[0]  # "Knitwear" / "T-shirt"

    if has_reference_image:
        # Photo-anchored prompt — strict "no invented details" rule
        parts = [
            "TASK: Convert the garment in the reference photo into an industry fashion CAD flat sketch — "
            "two views side by side: FRONT (left) and BACK (right).",
            "PRIMARY RULE: The photo is the source of truth. Match the photo exactly for: "
            "silhouette / body length / sleeve length / sleeve width and style / neckline shape / "
            "hem style / cuff style / placket / side seam details / any pattern (stripes, prints, textures, colorblocks).",
            "DO NOT INVENT DETAILS: If the photo shows a plain hem, draw a plain hem (no rib, no decorative band). "
            "If the photo shows plain cuffs, draw plain cuffs. If the photo has no side splits or vents, do not add them. "
            "If a feature is not clearly visible in the photo AND not in the spec below, omit it rather than guessing. "
            "It's better to draw a feature too simple than to invent one.",
            "If the photo shows stripes or a repeating pattern, draw it as line patterns at roughly the same scale and direction.",
        ]
    else:
        parts = [
            "Industry fashion CAD flat sketch — front and back view side by side,",
            f"of a {data.get('fit', '').lower()} {short_type.lower()}".strip(),
        ]

    # Build spec list — what to include depends on whether we have a photo.
    spec_parts = []
    if data.get("garment_sub_category"):
        spec_parts.append(f"category: {data['garment_sub_category'].lower()}")

    if has_reference_image:
        # Photo handles visuals — only add NON-visual / construction info
        if data.get("print_embroidery") and data["print_embroidery"].lower() not in ("none", ""):
            spec_parts.append(f"print/embroidery: {data['print_embroidery'].lower()}")
        if data.get("knit_structure"):
            spec_parts.append(f"knit structure: {data['knit_structure'].lower()}")
    else:
        # No photo — spec is all we have. Echo every visual field user filled.
        if data.get("neckline"):
            spec_parts.append(f"{data['neckline'].lower()} neckline")
        if data.get("sleeve_length") or data.get("sleeve_type"):
            sleeve_desc = " ".join(filter(None, [
                (data.get("sleeve_length") or "").lower(),
                (data.get("sleeve_type") or "").lower(),
            ]))
            if sleeve_desc.strip():
                spec_parts.append(f"{sleeve_desc} sleeves")
        if data.get("placket"):
            spec_parts.append(f"{data['placket'].lower()} closure")
        if data.get("rib_structure"):
            spec_parts.append(f"{data['rib_structure'].lower()} detail")
        if data.get("hem_style"):
            spec_parts.append(data["hem_style"].lower())
        if data.get("cuff_style"):
            spec_parts.append(data["cuff_style"].lower())

    if spec_parts:
        if has_reference_image:
            parts.append("Additional spec (for things not visible in the photo): " + "; ".join(spec_parts) + ".")
        else:
            parts.extend(spec_parts)

    # Hard styling rules — explicit so the image model doesn't add color/model/background
    parts.append(
        "STYLE: Black line art only, white background, no model, no human body, "
        "no color filling, no shading, no gradient, no perspective. "
        "Clean vector-style technical illustration suitable for a manufacturing tech pack. "
        "Label 'FRONT' under the front view and 'BACK' under the back view."
    )

    return " ".join(p for p in parts if p and p.strip())


# =============================================================================
# REAL AI GENERATION (Gemini Image)
# =============================================================================

def _gemini_generate(prompt: str, reference_image: dict | None = None) -> bytes:
    """Call Gemini Image API and return raw PNG bytes.

    When ``reference_image`` is provided (an image dict from session_state with
    base64 ``data`` and ``mime``), it is passed alongside the prompt so the
    image model uses it as a visual reference (multimodal input). This is what
    makes the generated sketch actually look like the garment the user uploaded.

    Raises RuntimeError if no image is returned in the response. Other
    exceptions (network / quota / etc.) bubble up to ``generate_drawing``,
    which converts them into a graceful demo-fallback.
    """
    client = genai.Client(api_key=st.secrets["gemini_api_key"])

    # Build multimodal contents — image first (so model sees it before instructions)
    contents: list = []
    if reference_image is not None:
        img_bytes = base64.b64decode(reference_image["data"])
        mime = reference_image.get("mime", "image/jpeg")
        contents.append(genai_types.Part.from_bytes(data=img_bytes, mime_type=mime))
    contents.append(prompt)

    response = client.models.generate_content(
        model=MODEL,
        contents=contents,
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

def _pick_reference_image(data: dict) -> dict | None:
    """Find the best uploaded photo to use as a visual reference for the AI.

    Strategy: take the FIRST user-uploaded image that isn't itself an AI
    output (so we don't feed the model its own previous drawing). Returns
    None if no usable reference exists — caller falls back to text-only.
    """
    for img in (data.get("images") or []):
        source = (img.get("source") or "").lower()
        # Skip AI-generated images — we don't want to echo our own output
        if "ai_generated" in source:
            continue
        if img.get("data"):
            return img
    return None


def generate_drawing(data: dict) -> dict:
    """Produce a technical drawing image dict matching the same shape used
    elsewhere (id / caption / data / mime / source / prompt).

    Real AI path:
      - Picks the first uploaded reference photo (if any)
      - Builds a prompt that asks the AI to match the photo's silhouette /
        pattern / proportions while using the form spec as supplementary info
      - Calls Gemini Image with image + text (multimodal)
      - Wraps the returned PNG in a standard image dict
    Fallback path:
      - Returns the pre-baked cardigan PNG with a clear caption
    """
    reference = _pick_reference_image(data)
    prompt = build_prompt(data, has_reference_image=(reference is not None))

    if not is_configured():
        return _demo_drawing(prompt)

    try:
        image_bytes = _gemini_generate(prompt, reference_image=reference)
        caption = (
            "AI-generated technical drawing (matched to uploaded photo)"
            if reference is not None
            else "AI-generated technical drawing (text-only — no reference photo)"
        )
        entry = make_image_entry(
            image_bytes,
            filename="ai_sketch.png",
            caption=caption,
        )
        entry["source"] = "ai_generated"
        entry["prompt"] = prompt
        entry["used_reference_photo"] = reference is not None
        return entry
    except Exception as e:
        # Any failure (network, quota, JSON shape, etc.) → demo fallback so the
        # UI never silently breaks. The caption tells the user what happened.
        return _demo_drawing(prompt, error=f"{type(e).__name__}: {e}")
