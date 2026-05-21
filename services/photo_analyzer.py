"""
Photo analyzer — looks at an uploaded garment image with Gemini Vision and
returns a dict of field suggestions to pre-fill the tech pack form.

The point isn't to replace human judgement — it's to do the boring work of
"I can clearly see this is a boxy cropped crew neck with long sleeves and a
ribbed hem" so the user only has to fix what AI got wrong.
"""

from __future__ import annotations

import base64
import json
import re

import streamlit as st

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

from config.dropdown_options import (
    CUFF_STYLES,
    FABRIC_STRUCTURES,
    FITS,
    HEM_STYLES,
    KNIT_STRUCTURES,
    KNITWEAR_SUB_CATEGORIES,
    NECKLINES,
    PLACKETS,
    PRINT_EMBROIDERY,
    PRODUCT_TYPES,
    RIB_STRUCTURES,
    SLEEVE_LENGTHS,
    SLEEVE_TYPES,
    TSHIRT_SUB_CATEGORIES,
)


# Which model to use. gemini-2.5-flash is fast, cheap (or free at small volume)
# and good enough for clothing classification.
MODEL = "gemini-2.5-flash"


def is_configured() -> bool:
    """True iff we have a Gemini key in secrets and the SDK installed."""
    if not GENAI_AVAILABLE:
        return False
    try:
        return "gemini_api_key" in st.secrets
    except Exception:
        return False


def _get_client():
    if not GENAI_AVAILABLE:
        raise RuntimeError(
            "google-genai not installed. Run: pip install google-genai"
        )
    if "gemini_api_key" not in st.secrets:
        raise RuntimeError(
            "Gemini API key missing. Add `gemini_api_key = \"AIzaSy...\"` to "
            ".streamlit/secrets.toml (local) or Streamlit Cloud secrets."
        )
    return genai.Client(api_key=st.secrets["gemini_api_key"])


def _build_prompt() -> str:
    """Compose a structured prompt that pins the model to our dropdown values."""
    return f"""You are analyzing a garment photo to pre-fill a fashion tech-pack form.

Focus on the MAIN garment in the image. Ignore the background and any other garments. If something is occluded, ambiguous, or not clearly visible, return null rather than guessing.

Return ONLY a JSON object. No markdown, no code fences, no commentary.

For each field, you MUST either pick an option from the list EXACTLY as written, or return null.

{{
  "product_type": one of {PRODUCT_TYPES} (use "Knitwear (Sweater / Cardigan)" for any sweater/cardigan/bolero/wrap; use "T-shirt / Jersey" for cut-and-sew jersey tees/polos),
  "garment_sub_category_knit": one of {KNITWEAR_SUB_CATEGORIES} (only if product_type is Knitwear, otherwise null),
  "garment_sub_category_tee": one of {TSHIRT_SUB_CATEGORIES} (only if product_type is T-shirt, otherwise null),
  "fit": one of {FITS},
  "neckline": one of {NECKLINES},
  "sleeve_length": one of {SLEEVE_LENGTHS},
  "sleeve_type": one of {SLEEVE_TYPES},
  "hem_style": one of {HEM_STYLES},
  "cuff_style": one of {CUFF_STYLES},
  "placket": one of {PLACKETS},
  "knit_structure": one of {KNIT_STRUCTURES} (null unless it's clearly a knit garment with visible structure),
  "rib_structure": one of {RIB_STRUCTURES} (null unless rib detail is clearly visible),
  "fabric_structure": one of {FABRIC_STRUCTURES} (only for T-shirts),
  "print_embroidery": one of {PRINT_EMBROIDERY},
  "color_description": short phrase describing the dominant colour(s), e.g. "cream with navy stripes",
  "pattern_notes": short phrase describing any pattern/stripe/texture you see, or null,
  "confidence_notes": one short sentence on what was clearly visible vs uncertain
}}

Important rules:
- Only return one of the exact options listed, or null. Do not invent new values.
- For colour, just describe what you see in plain language — don't try to match a Pantone code.
- If the garment is hanging on a rack (not flat-laid or on a model), be more conservative with fit estimates.
"""


def analyze_garment_photo(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    max_retries: int = 2,
) -> dict:
    """Send the image to Gemini Vision and return a dict of suggestions.

    Retries up to ``max_retries`` times on transient connection errors
    (Gemini occasionally drops the connection mid-request).

    Raises RuntimeError if the API isn't configured, or ValueError if the
    response can't be parsed as JSON.
    """
    import time

    client = _get_client()
    prompt = _build_prompt()
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=[
                    genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt,
                ],
            )
            text = (response.text or "").strip()
            # Some models wrap output in ```json ... ``` — strip those.
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise ValueError(f"AI didn't return valid JSON. Raw output: {text[:400]}") from e
        except (ValueError, RuntimeError):
            # JSON parse error or missing key — don't retry, fail immediately.
            raise
        except Exception as e:
            # Network / transient errors — retry with backoff.
            last_error = e
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))  # 1.5s, 3s
                continue
            raise RuntimeError(
                f"Couldn't reach Gemini after {max_retries + 1} attempts. "
                f"Last error: {type(e).__name__}: {e}"
            ) from e

    # Unreachable, but appeases type checkers.
    raise RuntimeError(f"Unexpected exit. Last error: {last_error}")


# Map analyzer keys → session_state keys, so the caller can apply suggestions
# without knowing the internals of the prompt schema.
SUGGESTION_TO_STATE = {
    "product_type": "product_type",
    "fit": "fit",
    "neckline": "neckline",
    "sleeve_length": "sleeve_length",
    "sleeve_type": "sleeve_type",
    "hem_style": "hem_style",
    "cuff_style": "cuff_style",
    "placket": "placket",
    "knit_structure": "knit_structure",
    "rib_structure": "rib_structure",
    "fabric_structure": "fabric_structure",
    "print_embroidery": "print_embroidery",
}


def apply_suggestions(
    suggestions: dict,
    session_state,
    overwrite: bool = False,
) -> dict:
    """Apply analyzer suggestions to session_state.

    Returns a dict of {field_label: applied_value} so the UI can show what
    was filled in. By default, doesn't overwrite fields the user has already
    set — pass overwrite=True to force-replace everything.
    """
    applied = {}
    blank_marker = "— Not specified —"

    for src_key, state_key in SUGGESTION_TO_STATE.items():
        value = suggestions.get(src_key)
        if not value or value == "null":
            continue
        current = session_state.get(state_key)
        is_empty = current in (None, "", blank_marker)
        if overwrite or is_empty:
            session_state[state_key] = value
            applied[src_key] = value

    # Sub-category — depends on product_type
    sub_knit = suggestions.get("garment_sub_category_knit")
    sub_tee = suggestions.get("garment_sub_category_tee")
    sub = sub_knit or sub_tee
    if sub:
        current = session_state.get("garment_sub_category")
        if overwrite or current in (None, "", blank_marker):
            session_state["garment_sub_category"] = sub
            applied["garment_sub_category"] = sub

    # Color name — text field
    color = suggestions.get("color_description")
    if color:
        current = session_state.get("color_name")
        if overwrite or not current:
            session_state["color_name"] = color
            applied["color_description"] = color

    return applied
