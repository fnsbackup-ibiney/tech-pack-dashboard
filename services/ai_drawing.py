"""
AI Technical Drawing — generates a flat sketch from a tech pack's inputs.

Currently runs in "Demo Hack" mode: composes a realistic-looking prompt from
the form data, simulates AI latency, then returns a pre-baked drawing. Swap in
a real image-generation API (Imagen / DALL-E 3 / etc.) by replacing the body
of ``generate_drawing`` — the public signature stays the same.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from services.image_helpers import make_image_entry


# Pre-baked technical drawings used by Demo Hack mode. The cardigan one lives
# in sample_data/ already (it shipped with the demo data).
_FALLBACK_DRAWING = Path(__file__).parent.parent / "sample_data" / "images" / "cardigan_technical.png"


# How long to pretend the AI is "thinking", in seconds. Long enough for the
# spinner to feel real, short enough not to annoy the user.
DEMO_LATENCY_SECONDS = 2.2


def build_prompt(data: dict) -> str:
    """Compose a descriptive prompt from the form data — what we'd send to a
    real image API. Surfaced in the UI so the demo is transparent."""
    product_type = data.get("product_type") or "knitwear garment"
    short_type = product_type.split(" (")[0]  # "Knitwear" / "T-shirt"

    parts = [
        "Technical flat sketch, front and back view",
        f"of a {data.get('fit', '').lower()} {short_type.lower()}".strip(),
    ]

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
        parts.append(f"{data['rib_structure'].lower()} rib detail")
    if data.get("hem_style"):
        parts.append(f"{data['hem_style'].lower()}")
    if data.get("color_name"):
        parts.append(f"in {data['color_name'].lower()}")

    parts.append("black line art on white background, garment-tech-pack style, "
                 "clean industrial illustration, no shading, both sides shown side-by-side")

    # Filter empties and join
    return ", ".join(p for p in parts if p and p.strip() and p.strip() != ",")


def generate_drawing(data: dict) -> dict:
    """Produce a technical drawing image dict matching the same shape used
    elsewhere (id / caption / data / mime).

    In Demo Hack mode: sleeps to simulate latency, then loads a pre-baked PNG.
    When you swap in a real API, replace the body below with a call that
    returns raw image bytes — everything else (compression, base64, UI) stays.
    """
    # Simulate latency
    time.sleep(DEMO_LATENCY_SECONDS)

    # Demo: load a pre-baked drawing as the "AI output"
    raw_bytes = _FALLBACK_DRAWING.read_bytes()

    entry = make_image_entry(
        raw_bytes,
        filename=_FALLBACK_DRAWING.name,
        caption="AI-generated technical drawing",
    )
    # Tag it so the data model can distinguish AI-generated from user uploads
    entry["source"] = "ai_generated"
    entry["prompt"] = build_prompt(data)
    return entry


def is_demo_mode() -> bool:
    """Surfaced in the UI as a small badge — flip to False once real AI is wired up."""
    return True
