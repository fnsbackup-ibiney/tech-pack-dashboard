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
import json
import re
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


# Gemini image-generation model. Bench-tested gemini-3.1-flash-image-preview
# against gemini-3-pro-image-preview on the cardigan reference — Flash 3.1
# captured the chevron pointelle, cropped length, balloon sleeves, and rib
# bands just as well as Pro, slightly faster and cheaper. Swap back to Pro
# if quality regresses on a different garment type.
MODEL = "gemini-3.1-flash-image-preview"

# Vision-only model — used both for the upfront photo DESCRIBER and the
# post-generation CRITIC. Flash is enough; we don't need image gen here,
# just structured text output.
DESCRIBER_MODEL = "gemini-2.5-flash"

# Maximum number of self-critique rounds. After the first sketch, we ask
# Vision to compare it against the original photo and identify deviations.
# If any are flagged as significant, we regenerate once with the correction
# notes appended to the prompt. Two passes total is the cost ceiling.
MAX_CRITIQUE_ROUNDS = 1


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
                    "Describe this garment for someone drawing a fashion CAD flat sketch. "
                    "Image-gen models tend to substitute generic 'typical' details when "
                    "they're uncertain (4 buttons instead of 2, regular length instead of "
                    "cropped, ribbed hem band even when there isn't one). Your job is to "
                    "PIN DOWN exact specifics so they can't drift.\n\n"
                    "Cover every item below. Use ALL CAPS for the critical numeric/length "
                    "values so they stand out to the downstream model:\n\n"
                    "1. Garment type + front opening (cardigan / pullover / wrap). "
                    "If there are visible buttons or zips, STATE THE EXACT COUNT in caps "
                    "(e.g. 'EXACTLY 2 buttons visible').\n"
                    "2. Neckline shape AND depth (deep V / shallow V / crew / scoop / boat). "
                    "If V-neck, note roughly how far down the V dips relative to the "
                    "bust line.\n"
                    "3. Sleeve LENGTH (short / three-quarter / long / extra-long), VOLUME "
                    "(slim / regular / balloon / bishop / dolman). Use ALL CAPS for the length.\n"
                    "4. Body LENGTH RATIO — estimate the body length from neckline to hem "
                    "as a multiple of shoulder width. E.g. 'BODY LENGTH ≈ 1.0× shoulder "
                    "width (CROPPED, ends at natural waist)'. Use ALL CAPS for the "
                    "qualitative label (CROPPED / REGULAR / LONG / TUNIC).\n"
                    "5. HEM FINISHING — this is the CRITICAL distinction to call out:\n"
                    "   - 'CLEAN FINISH' = bottom edge ends with the same fabric as the "
                    "body. NO separate horizontal rib band even if the body itself is "
                    "ribbed.\n"
                    "   - 'RIBBED HEM BAND' = a visibly separate horizontal strip of "
                    "ribbing at the bottom, distinct from the body fabric. Note the "
                    "approximate band height.\n"
                    "   Look hard before deciding — body-rib-stitch ≠ ribbed hem band.\n"
                    "6. CUFF FINISHING — same critical distinction:\n"
                    "   - 'CLEAN' = sleeve ends with the same fabric, no separate band.\n"
                    "   - 'RIBBED CUFF BAND' = a visibly separate ribbed strip at the wrist.\n"
                    "7. BODY KNIT PATTERN — be specific. Examples: 'vertical 2x2 rib "
                    "throughout the body', 'diagonal pointelle openwork in chevron stripes', "
                    "'vertical cable knit', 'plain stockinette', 'fair isle', 'jacquard "
                    "intarsia'. Include pattern direction (vertical / diagonal / horizontal) "
                    "and density (sparse / medium / dense). This is INDEPENDENT of items "
                    "5 and 6 — the body can be ribbed all over but still have a clean "
                    "(non-band) hem.\n"
                    "8. Knit gauge (fine / medium / chunky).\n"
                    "9. Distinctive details — pockets (count them), contrast trim, "
                    "panels, seam placement, any embellishment.\n\n"
                    "STRICT RULES:\n"
                    "- If part of the garment is cut off in the photo (hem not visible, "
                    "back not visible), say so explicitly. Do not invent hidden parts.\n"
                    "- Count carefully. If you can only count 2 buttons, write 2 — "
                    "never round to a 'typical' number.\n"
                    "- Describe ONLY what you can clearly see. No filling-in.\n\n"
                    "Output: 5-9 sentences of plain prose. No preamble, no bullet points, "
                    "no markdown."
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

# Common spec values that image-gen models routinely misinterpret. Each entry
# is the literal sketch instruction the model should follow for that value.
# Only the values listed here get a glossary line — others go through with
# just their plain spec name and that's fine.
_HEM_GLOSSARY = {
    "Clean finish": "Hem 'Clean finish' = bottom edge ends with the same body fabric. "
                    "Draw NO separate horizontal rib band at the bottom, even if the "
                    "body fabric is itself ribbed.",
    "Ribbed hem":   "Hem 'Ribbed hem' = a visibly separate horizontal rib band at the "
                    "bottom, distinct from the body fabric.",
    "Curved hem":   "Hem 'Curved hem' = bottom edge is curved (longer at sides, shorter "
                    "at front/back center), not straight.",
    "Split hem (side vents)": "Hem 'Split hem' = straight bottom edge with side vents — "
                              "short vertical splits at the side seams.",
    "Raw edge":     "Hem 'Raw edge' = no finishing at the bottom — unfinished cut edge.",
}
_CUFF_GLOSSARY = {
    "Ribbed cuff":  "Cuff 'Ribbed cuff' = a visibly separate ribbed band at the wrist, "
                    "distinct from the sleeve fabric.",
    "Plain cuff":   "Cuff 'Plain cuff' = sleeve ends with the same fabric, no separate band.",
    "Elasticated cuff": "Cuff 'Elasticated cuff' = a gathered cuff at the wrist (elastic "
                        "inside), tighter than the sleeve body.",
    "Folded cuff":  "Cuff 'Folded cuff' = a folded-back cuff, double layer at the wrist.",
}
_PLACKET_GLOSSARY = {
    "Half button placket": "Placket 'Half button placket' = button placket runs from "
                           "neckline only PART-WAY down the body (not full length).",
    "Full button placket": "Placket 'Full button placket' = button placket runs the FULL "
                           "length of the body, from neckline to hem.",
    "Zip closure":  "Placket 'Zip closure' = visible zipper down the front, not buttons.",
}


def _spec_glossary(data: dict) -> list[str]:
    """Pick glossary lines only for fields whose value is in our 'commonly
    misread' dictionary. Keeps the prompt concise — we don't dump definitions
    of every possible value.
    """
    out = []
    for value, table in [
        (data.get("hem_style"), _HEM_GLOSSARY),
        (data.get("cuff_style"), _CUFF_GLOSSARY),
        (data.get("placket"), _PLACKET_GLOSSARY),
    ]:
        if value and value in table:
            out.append(f"- {table[value]}")
    return out


# Common color words we look for in the user-edited description. If any of
# these appear AS A WHOLE WORD, we treat it as an explicit user request to
# render the sketch in that color (default is B&W tech-pack convention).
# Includes EN + a few German colors that might leak in from the describer.
_COLOR_REQUEST_WORDS = {
    # Universal / generic
    "color", "colour", "colored", "coloured", "tint", "tinted",
    # Yellows
    "yellow", "gold", "mustard", "lemon", "gelb",
    # Reds / pinks
    "red", "pink", "rose", "fuchsia", "magenta", "bordeaux", "burgundy",
    "coral", "salmon", "rot", "rosa", "altrosa",
    # Blues
    "blue", "navy", "indigo", "teal", "turquoise", "petrol", "blau",
    "marine", "hellblau",
    # Greens
    "green", "olive", "khaki", "mint", "grün", "schilf",
    # Neutrals
    "white", "black", "grey", "gray", "beige", "cream", "ecru", "ivory",
    "tan", "taupe", "stone", "kitt", "schwarz", "weiß", "weiss", "grau",
    # Browns / earth
    "brown", "chocolate", "tan", "camel", "rust", "terracotta", "braun",
    # Purples
    "purple", "violet", "lavender", "lilac", "aubergine", "lila",
    # Orange
    "orange", "apricot", "peach",
}


# Intent phrases that signal the user is REQUESTING a specific output color
# (vs. just describing what the photo shows). Default Vision descriptions
# often say "this is a cream cardigan" — that's a statement of fact, not a
# request, so we don't switch to color mode for those. But if the user adds
# "make it yellow" or "i want it in red" to the description, that's an
# explicit instruction we should honor.
_COLOR_INTENT_RE = re.compile(
    r"\b("
    r"i\s+want|i'd\s+like|id\s+like|"        # "I want", "I'd like"
    r"make\s+(it|this|the)|"                  # "make it", "make this"
    r"render\s+(it|in)|"                      # "render it", "render in"
    r"should\s+be|must\s+be|"                 # "should be", "must be"
    r"in\s+(yellow|blue|red|green|black|white|pink|orange|brown|grey|gray|"
    r"navy|cream|beige|tan|purple|gold|silver|color|colour)|"
    r"colored|coloured|color:|colour:"
    r")\b",
    re.IGNORECASE,
)


def _description_requests_color(desc: str) -> bool:
    """Did the USER explicitly request a colored output?

    Returns True only when both:
      - an intent phrase is present (e.g. "i want", "make it", "in red")
      - AND a color word is present somewhere
    so Vision's neutral "this is a yellow cardigan" doesn't accidentally
    flip the sketch to color, while the user's "make it yellow" does.
    """
    if not desc:
        return False
    if not _COLOR_INTENT_RE.search(desc):
        return False
    tokens = set(re.findall(r"[A-Za-zäöüÄÖÜß]+", desc.lower()))
    return bool(tokens & _COLOR_REQUEST_WORDS)


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
            # Add glossary lines only for spec values that are commonly misinterpreted.
            glossary = _spec_glossary(data)
            if glossary:
                parts.append(
                    "INTERPRETATION GLOSSARY (some spec terms get misread — here's exactly "
                    "what they mean for the sketch):\n" + "\n".join(glossary)
                )
        # Build a sleeve-specific instruction based on the spec value.
        # If the user picked a NON-volumetric sleeve type (Set-in, Raglan,
        # Drop/Dropped Shoulder, Saddle, Kimono), the AI MUST draw straight
        # slim sleeves regardless of fluffy yarn in the photo. This is the
        # #1 source of "AI added puff sleeves" complaints — mohair / brushed
        # yarn LOOKS voluminous, the model interprets that as bishop cut.
        _slim_sleeve_types = {
            "Set-in", "Raglan", "Drop shoulder", "Dropped Shoulder",
            "Saddle shoulder", "Kimono",
        }
        _user_sleeve = (data.get("sleeve_type") or "").strip()
        sleeve_rule = ""
        if _user_sleeve in _slim_sleeve_types:
            sleeve_rule = (
                f"- SLEEVES MUST BE STRAIGHT AND SLIM. The spec says sleeve type is "
                f"'{_user_sleeve}', which is a regular non-volumetric cut. Draw the "
                f"sleeves with normal slim width and NO added volume — NO bishop, NO "
                f"balloon, NO puff at the shoulder, NO gathering at the cuff. "
                f"VERY IMPORTANT: if the reference photo's yarn texture is FLUFFY (mohair, "
                f"brushed, alpaca, fuzzy), that's the YARN looking voluminous — the SLEEVE "
                f"CUT is still slim/straight. Fluffy texture is NOT balloon cut. Do NOT "
                f"add sleeve volume just because the yarn looks fluffy.\n"
            )
        parts.append(
            "DRAWING REQUIREMENTS:\n"
            "- Reproduce the SPECIFIC knit pattern described — not a generic texture. "
            "Pointelle openwork ≠ small dots. Cable knit ≠ random lines.\n"
            "- Match the body length precisely (cropped vs regular vs long).\n"
            + sleeve_rule
            + "- BODY STITCH ≠ HEM/CUFF BAND. If the body is ribbed all over but the spec "
            "says 'Hem: Clean finish', the bottom edge ends WITHOUT a separate rib band. "
            "Same for cuffs: 'Plain cuff' means no separate band even if the body is ribbed.\n"
            "- If a feature is neither in the photo description nor the spec, draw it plain "
            "(no rib, no decorative band, no side splits, no extra seams, no balloon)."
        )
    else:
        # Text-only prompt
        parts = [
            f"Industry fashion CAD flat sketch — front and back view side by side, "
            f"of a {data.get('fit', '').lower()} {short_type.lower()}."
        ]
        if spec_lines:
            parts.append("Spec:\n" + "\n".join(spec_lines))

    # Hard styling rules — monochrome by default (industry convention for
    # tech pack flats). But if the user has explicitly asked for color in
    # the description (e.g. "make it yellow", "in cream", "i want this red"),
    # we soften the rule to honor that request. Default stays B&W so factories
    # get the standard format unless someone deliberately opts into color.
    if _description_requests_color(photo_description):
        parts.append(
            "STYLE — COLORED FLAT (user requested color in the description):\n"
            "- Output is a flat technical illustration with the color the user "
            "specified in the description. Use a light, flat color wash on the "
            "garment with crisp black linework on top.\n"
            "- No shading, no gradient, no 3D rendering — just flat color fill + line art.\n"
            "- No model, no human body, no background details, no props.\n"
            "- Label 'FRONT' below the front view and 'BACK' below the back view."
        )
    else:
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
# SELF-CRITIQUE — compare the generated sketch against the reference photo
# =============================================================================

def _critique_sketch(
    photo_data: str,
    photo_mime: str,
    sketch_bytes: bytes,
    intent_description: str,
) -> dict:
    """Compare the generated sketch against the original photo. Return a dict
    with deviations + a regeneration hint, or empty deviations if the sketch
    is already faithful.

    Output shape (parsed from Gemini's JSON response):
      {
        "faithful": bool,         # True if no significant deviations
        "deviations": [str, ...], # concrete issues, e.g. "shows 4 buttons, photo has 2"
        "correction_note": str,   # one-sentence summary to add to regen prompt
      }
    """
    empty = {"faithful": True, "deviations": [], "correction_note": ""}
    if not GENAI_AVAILABLE or not photo_data:
        return empty
    try:
        if "gemini_api_key" not in st.secrets:
            return empty
        client = genai.Client(api_key=st.secrets["gemini_api_key"])
        photo_bytes = base64.b64decode(photo_data)
        response = client.models.generate_content(
            model=DESCRIBER_MODEL,
            contents=[
                "REFERENCE PHOTO (the target):",
                genai_types.Part.from_bytes(data=photo_bytes, mime_type=photo_mime),
                "GENERATED SKETCH (what we drew so far — compare against the reference):",
                genai_types.Part.from_bytes(data=sketch_bytes, mime_type="image/jpeg"),
                (
                    "Your job: spot SPECIFIC deviations between the sketch and the photo. "
                    "We don't need style commentary — only objective mismatches in "
                    "things like:\n"
                    "- Button count (e.g. 'sketch has 4 buttons, photo has 2')\n"
                    "- Body length (e.g. 'sketch is regular length, photo is cropped at waist')\n"
                    "- Sleeve length (3/4 vs long vs short)\n"
                    "- Sleeve volume (slim vs balloon vs dolman)\n"
                    "- Neckline shape or depth\n"
                    "- Knit pattern type or direction\n"
                    "- HEM finishing: does the sketch show a separate horizontal rib BAND "
                    "at the bottom that the photo does NOT have? (Critical: body-fabric-rib "
                    "is different from a separate hem rib band. If the photo's body is "
                    "ribbed all over but ends cleanly without a separate band, the sketch "
                    "must not add one.)\n"
                    "- CUFF finishing: same distinction — separate rib band at the wrist "
                    "vs sleeve ending with body fabric.\n"
                    "- Pocket / panel / seam details\n\n"
                    "Don't flag color (the sketch is supposed to be black-and-white) or "
                    "framing (front+back layout vs single view in photo).\n\n"
                    "Also use this DESCRIPTION of the intent as ground truth — if the "
                    "sketch deviates from it, that's a deviation:\n\n"
                    f"{intent_description or '(no description available)'}\n\n"
                    "Output ONLY a JSON object — no markdown, no code fences — of the form:\n"
                    '{\n'
                    '  "faithful": <true if no significant deviations>,\n'
                    '  "deviations": ["specific issue 1", "specific issue 2", ...],\n'
                    '  "correction_note": "<one short sentence summarizing what to fix>"\n'
                    '}\n\n'
                    "If the sketch matches well, return faithful=true and deviations=[]. "
                    "If you see only minor stylistic differences (line weight, slight "
                    "proportion drift), still return faithful=true — we only re-render "
                    "for meaningful misses."
                ),
            ],
        )
        text = (response.text or "").strip()
        # Strip markdown fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        return {
            "faithful": bool(parsed.get("faithful", True)),
            "deviations": list(parsed.get("deviations", []) or []),
            "correction_note": str(parsed.get("correction_note", "")).strip(),
        }
    except Exception:
        # Critique failed — treat as faithful, so we don't trigger a wasted regen
        return empty


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

    # Step 1: get the photo description. Two sources, in priority order:
    #   1. ``data["_photo_description_override"]`` — if the user edited the
    #      description in the UI, use their version verbatim. This is how
    #      a customer corrects AI's reading without regenerating from photo.
    #   2. Otherwise auto-generate via ``_describe_for_sketch`` (cached).
    description = (data.get("_photo_description_override") or "").strip()
    if not description and reference is not None and is_configured():
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
        # PASS 1 — first sketch from photo + spec + description
        image_bytes = _gemini_generate(prompt, reference_image=reference)
        final_prompt = prompt
        critique_result = None

        # PASS 2 — self-critique loop. Only runs when we have a reference photo;
        # without one, there's nothing concrete to compare against.
        if reference is not None and MAX_CRITIQUE_ROUNDS > 0:
            critique_result = _critique_sketch(
                photo_data=reference["data"],
                photo_mime=reference.get("mime", "image/jpeg"),
                sketch_bytes=image_bytes,
                intent_description=description,
            )
            # Only regenerate if the critic found significant deviations
            if (not critique_result.get("faithful", True)) and critique_result.get("deviations"):
                deviations_block = "\n".join(f"- {d}" for d in critique_result["deviations"])
                correction = critique_result.get("correction_note", "").strip()
                final_prompt = (
                    prompt
                    + "\n\nCORRECTION NOTES (a previous attempt at this sketch had the "
                    "following deviations from the reference photo — please FIX these in "
                    "this generation):\n" + deviations_block
                    + (f"\n\nSummary: {correction}" if correction else "")
                )
                # Regenerate with the correction notes added
                try:
                    image_bytes = _gemini_generate(final_prompt, reference_image=reference)
                except Exception:
                    # If the regen fails, fall back to pass-1 image rather than erroring
                    pass

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
        entry["prompt"] = final_prompt
        entry["used_reference_photo"] = reference is not None
        entry["photo_description"] = description
        if critique_result is not None:
            entry["critique"] = critique_result
        return entry
    except Exception as e:
        # Any failure (network, quota, JSON shape, etc.) → demo fallback so the
        # UI never silently breaks. The caption tells the user what happened.
        return _demo_drawing(prompt, error=f"{type(e).__name__}: {e}")
