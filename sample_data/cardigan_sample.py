"""
Pre-filled sample data for demo purposes.
Based on the customer's original TECH PACK.docx (Cotton Knit Cardigan, SS26).

The two demo images live in sample_data/images/ — they're compressed at import
time so the demo always has visible reference photos, matching what was in
the customer's original Word doc.
"""

from datetime import date
from pathlib import Path

from services.image_helpers import make_image_entry

_IMAGES_DIR = Path(__file__).parent / "images"


def _load_demo_image(filename: str, caption: str) -> dict:
    path = _IMAGES_DIR / filename
    return make_image_entry(path.read_bytes(), filename=filename, caption=caption)


_DEMO_IMAGES = [
    _load_demo_image("cardigan_reference.png", "Front reference (model)"),
    _load_demo_image("cardigan_technical.png", "Technical drawing (front & back)"),
]

CARDIGAN_SAMPLE = {
    # Style Overview
    "product_type": "Knitwear (Sweater / Cardigan)",
    "garment_sub_category": "Cardigan",
    "style_name": "Cotton Knit Cardigan",
    "style_number": "KW-SS26-001",
    "season": "SS26",
    "gender": "Women",
    "fit": "Boxy",
    "size_range": "S - XL",
    "color_name": "Sunshine Yellow",
    "pantone_code": "13-0859 TCX",
    "composition": "100% Cotton",

    # Construction - Knitwear specific
    "yarn_type": "100% Cotton",
    "yarn_count": "Ne 12/1",
    "gauge": "5GG",
    "knit_structure": "Rib",
    "rib_structure": "1x1 Rib",
    "dye_method": "Yarn-dyed",

    # Construction details
    "neckline": "Deep V-neck",
    "neckline_rib_cm": 2.5,
    "sleeve_length": "Long sleeve",
    "sleeve_type": "Dropped Shoulder",
    "hem_style": "Ribbed hem",
    "hem_height_cm": 2.0,
    "cuff_style": "Ribbed cuff",
    "cuff_height_cm": 2.0,
    "placket": "Half button placket",
    "placket_interlining": "Self-fabric, light interlining",
    "button_size_l": "32L",  # ~20mm, standard for cardigan
    "button_count": 2,
    "button_material": "Mother of Pearl (MOP)",
    "button_color": "Natural",
    "print_embroidery": "None",
    "wash_finishing": "Anti-pilling treatment",
    "stitching_type": "Standard knitwear construction",
    "shoulder_reinforcement": True,

    # Measurements (Size M baseline)
    # Keys MUST match KNITWEAR_MEASUREMENT_POINTS in config/dropdown_options.py
    # — the editor renders rows from that list and looks up values by name. If
    # a key here doesn't match, the value silently doesn't show, and the editor
    # then overwrites session_state["measurements"] on the next render, losing
    # this demo data permanently. Values 1–6 are from the customer's original
    # tech pack; 7–15 are sensible size-M boxy-cardigan estimates so the demo
    # has a complete measurement table when the user clicks "Load Demo".
    "base_size": "M",
    "measurements": {
        "Front body Length (fm HPS)": {"value": 57.0, "tolerance": 1.0},
        "1/2 Chest Width (below armhole)": {"value": 56.0, "tolerance": 1.0},
        "1/2 Bottom Width (at edge)": {"value": 54.0, "tolerance": 1.0},
        "Sleeve length (from CB)": {"value": 77.0, "tolerance": 1.0},
        "Cuff width (at edge)": {"value": 12.0, "tolerance": 0.5},
        "Hem rib Height": {"value": 2.0, "tolerance": 0.3},
        "Shoulder (seam to seam)": {"value": 42.0, "tolerance": 0.5},
        "Armhole (straight)": {"value": 22.0, "tolerance": 0.5},
        "Bicep (below armhole)": {"value": 19.0, "tolerance": 0.5},
        "Cuff trim Height": {"value": 2.0, "tolerance": 0.3},
        "Neck Width (seam to seam)": {"value": 18.0, "tolerance": 0.5},
        "Front neck drop (fm HPS)": {"value": 16.0, "tolerance": 0.5},
        "Back Neck drop (fm HPS)": {"value": 2.0, "tolerance": 0.5},
        "Neck Height": {"value": 2.5, "tolerance": 0.3},
        "Placket width": {"value": 3.0, "tolerance": 0.3},
    },

    # Packing & Labels
    "labels": ["Main label (brand)", "Care label", "Size label", "Hangtag"],
    "packing": "Polybag (folded)",

    # Supplier actions
    "supplier_actions": [
        "Confirm yarn availability",
        "Advise on price",
        "Confirm MOQ",
        "Confirm lead time",
        "Send knit swatch (after order placement)",
        "Send button options",
    ],

    # Quantity / commercial
    "target_quantity": 500,
    "target_price_usd": None,
    "delivery_date": date(2026, 8, 15),

    # Notes
    "notes": "Lightweight, airy rib structure. Loose fit, slightly cropped body.",

    # Images — preloaded references from the customer's original TECH PACK.docx
    "images": _DEMO_IMAGES,
}
