"""
Pre-filled sample data for demo purposes.
Based on the customer's original TECH PACK.docx (Cotton Knit Cardigan, SS26).
"""

from datetime import date

CARDIGAN_SAMPLE = {
    # Style Overview
    "product_type": "Knitwear (Sweater / Cardigan)",
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
    "sleeve_type": "Drop shoulder",
    "hem_style": "Ribbed hem",
    "hem_height_cm": 2.0,
    "cuff_style": "Ribbed cuff",
    "cuff_height_cm": 2.0,
    "placket": "Half button placket",
    "button_size_l": "32L",  # ~20mm, standard for cardigan
    "button_count": 2,
    "button_material": "Mother of Pearl (MOP)",
    "button_color": "Natural",
    "print_embroidery": "None",
    "wash_finishing": "Anti-pilling treatment",
    "shoulder_reinforcement": True,

    # Measurements (Size M baseline)
    "base_size": "M",
    "measurements": {
        "Body length (CB)": {"value": 57.0, "tolerance": 1.0},
        "Chest width": {"value": 56.0, "tolerance": 1.0},
        "Hem width": {"value": 54.0, "tolerance": 1.0},
        "Sleeve length (CB)": {"value": 77.0, "tolerance": 1.0},
        "Shoulder drop": {"value": 3.0, "tolerance": 0.5},
        "Sleeve opening": {"value": 12.0, "tolerance": 0.5},
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
}
