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


# Gemini image-generation model. Pro variant chosen for stronger fidelity
# to the reference photo (the Flash variant tends to draw the same generic
# pullover regardless of input). Alternatives: "gemini-2.5-flash-image"
# (faster/cheaper) or "gemini-3.1-flash-image-preview" (newest). Swap here
# if quality/speed/cost trade-off changes — SDK call interface is identical.
MODEL = "gemini-3-pro-image-preview"

# Vision-only model used to describe the photo in detail BEFORE we ask the
# image-gen model to draw. The description bridges visual ambiguity — pure
# image-gen models tend to substitute generic textures for specific patterns
# (e.g. "small dots" instead of "diagonal pointelle openwork"). Flash is
# enough here — we only need a textual reading of the photo, not generation.
DESCRIBER_MODEL = "gemini-2.5-flash"


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


@st.cache_data(show_spinner=False, ttl=3600)
def _describe_for_sketch(image_data: str, mime: str) -> str:
    """Use Gemini Vision to read the photo and write a detailed, sketch-focused
    description. Cached per image hash so we only pay once per photo.

    The text returned is plain prose (4-6 sentences) covering silhouette,
    sleeve, hem, knit pattern, gauge, and any other distinctive details. We
    feed this back into the image-gen prompt so the Pro model has explicit
    textual cues for things it would otherwise substitute (e.g. "diagonal
    pointelle openwork" instead of generic "knit texture").

    Returns empty string on any failure — caller treats no-description as
    photo-only mode and still works.
    """
    if not GENAI_AVAILABLE or not image_data:
        return ""
    try:
        if "gemini_api_key" not in st.secrets:
            return ""
        client = genai.Client(api_key=st.secrets["gemini_api_key"])
        image_bytes = base64.b64decode(image_data)
        response = client.models.generate_content(
            model=DESCRIBER_MODEL,
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime),
                (
                    "Describe this garment for someone drawing a fashion CAD flat sketch.\n\n"
                    "Be SPECIFIC about visible details:\n"
                    "1. Garment type + front opening (cardigan with buttons / pullover / wrap)\n"
                    "2. Neckline shape and depth (deep V / shallow V / crew / scoop / etc.)\n"
                    "3. Sleeve length, width, cuff style (e.g. 'long balloon sleeves with elasticated cuffs')\n"
                    "4. Body length and fit (cropped at waist / regular / boxy / fitted / oversized)\n"
                    "5. Hem style (plain straight / ribbed / curved / asymmetric / split)\n"
                    "6. KNIT PATTERN — describe what you actually see. Examples: 'diagonal "
                    "pointelle openwork (small holes in chevron stripes)', 'cable knit running "
                    "vertically', 'plain stockinette', 'ribbed', 'fair isle'. Include pattern "
                    "direction and density.\n"
                    "7. Knit gauge (fine / medium / chunky)\n"
                    "8. Any distinctive details (pockets, contrast trim, panels)\n\n"
                    "CRITICAL:\n"
                    "- If part of the garment is cut off in the photo (e.g. hem not visible "
                    "because of cropping), say so explicitly. Do not guess hidden parts.\n"
                    "- Describe ONLY what you can clearly see. No invention.\n\n"
                    "Output: 4-6 sentences of plain prose. No preamble, no bullet points, no markdown."
                ),
            ],
        )
        return (response.text or "").strip()
    except Exception:
        return ""


def is_demo_mode() -> bool:
    """Surfaced in the UI as a small badge. Returns True when we're falling
    back to the pre-baked PNG (no key / SDK missing)."""
    return not is_configured()


# =============================================================================
# PROMPT
# =============================================================================

def _collect_spec_lines(data: dict) -> list[str]:
    """Pull out the spec values the user has filled in (visual + construction).

    These become the SPEC block in the prompt — taken as user-confirmed
    overrides for the specific features they cover. Empty fields are skipped
    so we don't claim "neckline: —" to the model.
    """
    lines = []
    if data.get("garment_sub_category"):
        lines.append(f"- Garment type: {data['garment_sub_category']}")
    if data.get("fit"):
        lines.append(f"- Fit / silhouette: {data['fit']}")
    if data.get("neckline"):
        lines.append(f"- Neckline: {data['neckline']}")
    sleeve_desc = " ".join(filter(None, [data.get("sleeve_length"), data.get("sleeve_type")])).strip()
    if sleeve_desc:
        lines.append(f"- Sleeves: {sleeve_desc}")
    if data.get("placket"):
        lines.append(f"- Placket / closure: {data['placket']}")
    if data.get("hem_style"):
        lines.append(f"- Hem: {data['hem_style']}")
    if data.get("cuff_style"):
        lines.append(f"- Cuffs: {data['cuff_style']}")
    if data.get("rib_structure"):
        lines.append(f"- Rib detail: {data['rib_structure']}")
    if data.get("knit_structure"):
        lines.append(f"- Knit structure: {data['knit_structure']}")
    if data.get("print_embroidery") and data["print_embroidery"].lower() not in ("none", ""):
        lines.append(f"- Print / embroidery: {data['print_embroidery']}")
    return lines


def build_prompt(
    data: dict,
    has_reference_image: bool = False,
    photo_description: str = "",
) -> str:
    """Compose a descriptive prompt from the form data — what we send to the
    image API. Also surfaced in the UI so the user can audit what AI saw.

    With a reference photo:
      We pass the photo itself AND (when available) a Vision-generated
      written description of what's in the photo. The description gives the
      image-gen model explicit textual cues for specific patterns/details
      that pure-visual generation tends to substitute with generic textures.
      Plus the user-confirmed SPEC block overrides any specific feature.

    Without a photo:
      The spec is everything — assembled into a descriptive sketch prompt.
    """
    product_type = data.get("product_type") or "knitwear garment"
    short_type = product_type.split(" (")[0]
    spec_lines = _collect_spec_lines(data)

    if has_reference_image:
        parts = [
            "TASK: Produce an industry fashion CAD flat sketch from the reference photo — "
            "two views side by side: FRONT (left) and BACK (right).",
        ]
        if photo_description:
            parts.append(
                "WHAT THE PHOTO SHOWS (carefully analyzed by a vision model — match this "
                "description precisely, especially the knit pattern and length):\n"
                + photo_description
            )
        else:
            parts.append(
                "VISUAL ANCHOR (from the photo): silhouette, body length, sleeve width, "
                "neckline shape, pattern (stripes / prints / textures / colorblocks), "
                "and overall proportions."
            )
        if spec_lines:
            parts.append(
                "USER-CONFIRMED SPEC (these override the photo on any specific feature listed — "
                "if a value here disagrees with what you 'see' in the photo, USE THE SPEC):\n"
                + "\n".join(spec_lines)
            )
        parts.append(
            "DRAWING REQUIREMENTS:\n"
            "- Reproduce the SPECIFIC knit pattern described — not a generic texture. "
            "Pointelle openwork ≠ small dots. Cable knit ≠ random lines.\n"
            "- Match the body length precisely (cropped vs regular vs long).\n"
            "- Match the sleeve volume (slim vs balloon vs dropped vs balloon-cuffed).\n"
            "- If a feature is neither in the photo description nor the spec, draw it plain "
            "(no rib, no decorative band, no side splits, no extra seams)."
        )
    else:
        # Text-only prompt
        parts = [
            f"Industry fashion CAD flat sketch — front and back view side by side, "
            f"of a {data.get('fit', '').lower()} {short_type.lower()}."
        ]
        if spec_lines:
            parts.append("Spec:\n" + "\n".join(spec_lines))

    # Hard styling rules — explicit so the image model doesn't add color/model/background.
    # Note: Pro tends to leak the photo's color into the output ("if the input is yellow,
    # the drawing comes out yellow"). Hammer this point until it's unambiguous.
    parts.append(
        "STYLE — STRICT MONOCHROME REQUIREMENT:\n"
        "- Output MUST be pure BLACK linework on a WHITE background. No color whatsoever.\n"
        "- The reference photo's color is for SHAPE and PATTERN reference only — DO NOT "
        "reproduce its color in the sketch, even faintly. Even if the input is bright yellow / "
        "red / blue, the output is monochrome black-and-white.\n"
        "- No color fills, no shading, no gradients, no tinting, no perspective.\n"
        "- No model, no human body, no background details, no props.\n"
        "- Clean vector-style technical illustration suitable for a manufacturing tech pack.\n"
        "- Label 'FRONT' below the front view and 'BACK' below the back view."
    )

    return "\n\n".join(p for p in parts if p and p.strip())


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

    # Step 1: ask Vision to describe the photo in detail (cached per image).
    # This bridges the gap between what the image-gen model "sees" and what
    # it actually draws — without this, Pro tends to substitute generic
    # textures (small dots) for specific knit patterns (chevron pointelle).
    description = ""
    if reference is not None and is_configured():
        description = _describe_for_sketch(
            reference["data"],
            reference.get("mime", "image/jpeg"),
        )

    prompt = build_prompt(
        data,
        has_reference_image=(reference is not None),
        photo_description=description,
    )

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
        entry["photo_description"] = description
        return entry
    except Exception as e:
        # Any failure (network, quota, JSON shape, etc.) → demo fallback so the
        # UI never silently breaks. The caption tells the user what happened.
        return _demo_drawing(prompt, error=f"{type(e).__name__}: {e}")
