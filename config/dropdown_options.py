"""
Tech Pack Dashboard - Dropdown Options Configuration
=====================================================

All dropdown options live here. To add a new option:
  1. Find the right list below
  2. Add your value to the list
  3. Restart the app

To add a new field entirely:
  1. Add a new list here
  2. Reference it in app.py with a st.selectbox / st.multiselect

Conventions:
  - Use English (this is the customer-facing UI language)
  - Put the most common option first in each list
  - Add comments where a value might be unclear to non-industry users
"""

# =============================================================================
# PRODUCT TYPE — drives which fields show up
# =============================================================================
PRODUCT_TYPES = [
    "Knitwear (Sweater / Cardigan)",
    "T-shirt / Jersey",
]

# Sub-categories derived from data-driven analysis of customer's actual catalog
# (Marie Lund, 132 active SKUs grouped into 5 buckets). Kept GENERAL on purpose —
# stakeholder feedback was that finer splits like "Short-Sleeve Cardigan" or
# "Chunky Knit" create too many buttons without adding clarity.
KNITWEAR_SUB_CATEGORIES = [
    "Cardigan",            # Strickjacke + Kurzarm-Strickjacke — ~47% of catalog
    "Pullover / Sweater",  # Strickpullover + Pullover + Grobstrick + Kurzarm — ~31%
    "Knit Shirt",          # Strickshirt + Strick-Langarmshirt — ~13%
    "Knit Wrap / Cape",    # Strickhülle — ~8%
    "Bolero",              # Strick-Bolero — ~2%
]

TSHIRT_SUB_CATEGORIES = [
    "T-shirt (Crew Neck)",
    "T-shirt (V-Neck)",
    "Polo Shirt",
    "Long-Sleeve Tee",
    "Tank Top",
    "Henley",
    "Cropped Tee",
]

# =============================================================================
# UNIVERSAL FIELDS (shown for both Knitwear and T-shirt)
# =============================================================================

SEASONS = [
    "SS26", "FW26", "SS27", "FW27", "AW26", "Pre-Fall 26", "Resort 26",
]

GENDERS = [
    "Women", "Men", "Unisex", "Kids - Girls", "Kids - Boys", "Kids - Unisex",
]

FITS = [
    "Boxy",
    "Regular",
    "Slim",
    "Oversized",
    "Loose Fit",          # broader cut than "Relaxed" — customer's term
    "Relaxed",
    "Cropped",
    "Slightly cropped",   # softer than full crop, hem just above waistband
    "Fitted",
]

SIZE_RANGES = [
    "S - XL",
    "XS - XXL",
    "XXS - XXXL",
    "Single size (Sample only)",
    "Kids 2-12Y",
    "Custom",
]

# Common composition options — user can also type free text
COMPOSITIONS = [
    "100% Cotton",
    "95% Cotton / 5% Elastane",
    "100% Polyester",
    "100% Wool",
    "100% Merino Wool",
    "100% Cashmere",
    "70% Wool / 30% Cashmere",
    "50% Cotton / 50% Cashmere",
    "65% Polyester / 35% Cotton",
    "100% Recycled Cotton",
    "100% Linen",
    "70% Linen / 30% Cotton",
    "Other (specify)",
]

# Neckline — covers both knitwear and t-shirt styles
NECKLINES = [
    "Crew neck",
    "V-neck",
    "Deep V-neck",
    "Mock neck",
    "Turtle neck",
    "Polo collar",
    "Shirt collar",
    "Hooded",
    "Boat neck",
    "Scoop neck",
    "Henley",
    "Off-shoulder",
]

SLEEVE_LENGTHS = [
    "Long sleeve",
    "Short sleeve",
    "3/4 sleeve",
    "Sleeveless",
    "Cap sleeve",
    "Tank",
]

SLEEVE_TYPES = [
    "Set-in",            # Standard fitted sleeve cap
    "Raglan",            # Diagonal seam from neck to underarm
    "Dropped Shoulder",  # Shoulder seam falls below natural shoulder (customer's notation)
    "Saddle shoulder",   # Sleeve extends across the shoulder
    "Kimono",            # No shoulder seam, sleeve is part of bodice
    "Relaxed",           # Set-in but with extra ease at armhole / sleeve
    "Balloon",           # Wide volume, tapering at the wrist
    "Bishop",            # Gathered at the cuff
]

HEM_STYLES = [
    "Ribbed hem",
    "Clean finish",
    "Split hem (side vents)",
    "Curved hem",
    "Drawstring hem",
    "Raw edge",
]

CUFF_STYLES = [
    "Ribbed cuff",
    "Clean finish",
    "Elastic cuff",
    "Folded cuff",
    "None (sleeveless)",
]

PLACKETS = [
    "None",
    "Half button placket",
    "Full button placket",
    "Zipper",
    "Half zip",
    "Snap",
    "Henley placket",
]

# How the placket is constructed — separate from the placket "type" above.
# Customer's example: "Self-fabric, light interlining"
PLACKET_INTERLINING = [
    "Self-fabric, no interlining",
    "Self-fabric, light interlining",
    "Self-fabric, heavy interlining",
    "Contrast fabric, no interlining",
    "Contrast fabric, light interlining",
    "Contrast fabric, heavy interlining",
    "Bound (with binding tape)",
]

# Garment assembly / seam construction. Knitwear has its own conventions
# distinct from cut-and-sew wovens.
STITCHING_TYPES = [
    "Standard knitwear construction",   # customer's term — generic linked + serged
    "Linked (hand-linked seams)",       # smoother, higher quality
    "Linked + overlocked",
    "Fully fashioned",                  # shaped on the knit machine, no cut-sew
    "Cut and sew",                      # cut from knit panels and sewn
    "Overlocked",
    "Flat-locked",                      # flat seams (athletic / sportswear)
]

# Button options
# Button sizes use the Ligne (L) unit, the industry standard for buttons.
# 1L = 1/40 inch ≈ 0.635 mm. Common conversions:
#   14L ≈ 9mm   16L ≈ 10mm   18L ≈ 11.5mm   20L ≈ 12.5mm
#   24L ≈ 15mm  28L ≈ 18mm   32L ≈ 20mm     36L ≈ 23mm    40L ≈ 25mm
BUTTON_SIZES_L = ["14L", "16L", "18L", "20L", "24L", "28L", "32L", "36L", "40L", "44L"]
BUTTON_MATERIALS = [
    "Plastic",
    "Horn",
    "Mother of Pearl (MOP)",
    "Metal",
    "Wooden",
    "Coconut",
    "Resin",
]
BUTTON_COLORS = [
    "Tonal (matches body)",
    "Natural",
    "White",
    "Black",
    "Brown",
    "Contrast (specify)",
]

PRINT_EMBROIDERY = [
    "None",
    "Screen print",
    "Digital print",
    "Embroidery",
    "Patch (woven)",
    "Patch (embroidered)",
    "Heat transfer",
    "Sublimation",
]

WASH_FINISHING = [
    "None",
    "Garment wash",
    "Enzyme wash",
    "Stone wash",
    "Anti-pilling treatment",
    "Softening",
    "Mercerizing",
    "Bio-polishing",
]

LABEL_TYPES = [
    "Main label (brand)",
    "Care label",
    "Size label",
    "Composition label",
    "Country of origin",
    "Hangtag",
]

PACKING = [
    "Polybag (folded)",
    "Polybag (hanger)",
    "Hanger only",
    "Box",
    "Tissue paper + polybag",
    "Garment bag",
]

# =============================================================================
# KNITWEAR SPECIFIC FIELDS
# =============================================================================

YARN_TYPES = [
    "100% Cotton",
    "100% Wool",
    "100% Merino Wool",
    "100% Lambswool",
    "100% Cashmere",
    "70% Wool / 30% Cashmere",
    "50% Cotton / 50% Cashmere",
    "100% Acrylic",
    "Wool / Acrylic blend",
    "Alpaca blend",
    "Mohair blend",
    "Recycled Wool",
    "Recycled Cotton",
]

# Ne = English count (cotton system), Nm = Metric count (wool system)
YARN_COUNTS = [
    "Ne 12/1",
    "Ne 16/1",
    "Ne 20/1",
    "Ne 20/2",
    "Ne 2/30",
    "Ne 2/48",
    "Nm 2/26",
    "Nm 2/28",
    "Nm 2/30",
    "Nm 1/14",
    "Custom",
]

# GG = needles per inch on the knitting machine. Lower = chunkier, higher = finer.
# Ranges (e.g. "5-7GG") are common when the customer is OK with either gauge.
GAUGES = [
    "3GG (chunky)",
    "5GG",
    "5-7GG (range)",     # customer's notation in the brief
    "7GG",
    "7-9GG (range)",
    "9GG",
    "12GG (fine)",
    "14GG",
    "16GG (very fine)",
]

KNIT_STRUCTURES = [
    "Jersey / Plain knit",
    "Rib",
    "Cable",
    "Jacquard",
    "Intarsia",
    "Pointelle (eyelet)",
    "Pique",
    "Links-Links (purl)",
    "Tuck stitch",
    "Mesh",
    "Fully fashioned",
]

RIB_STRUCTURES = [
    "1x1 Rib",
    "2x2 Rib",
    "2x1 Rib",
    "3x3 Rib",
    "Half cardigan",
    "Full cardigan",
    "Fisherman rib",
]

KNITWEAR_DYE_METHODS = [
    "Yarn-dyed",        # Color is dyed into the yarn BEFORE knitting
    "Piece-dyed",       # Knitted in natural, then dyed
    "Garment-dyed",     # Whole finished garment is dyed
]

# =============================================================================
# T-SHIRT / JERSEY SPECIFIC FIELDS
# =============================================================================

FABRIC_STRUCTURES = [
    "Single jersey",
    "Interlock",
    "Pique",
    "French terry",
    "Fleece",
    "Rib jersey",
    "Slub jersey",
    "Waffle",
    "Mesh",
]

# gsm = grams per square meter. Higher = heavier fabric
FABRIC_WEIGHTS_GSM = [
    "120 (very light)",
    "140",
    "160",
    "180",
    "200",
    "220",
    "240",
    "260",
    "280",
    "300 (heavy)",
    "Custom",
]

TSHIRT_DYE_METHODS = [
    "Piece-dyed (reactive)",
    "Piece-dyed (pigment)",
    "Yarn-dyed (stripes / heather)",
    "Garment-dyed",
    "Sublimation print",
]

# =============================================================================
# MEASUREMENT POINTS (shown as editable table, with tolerance)
# =============================================================================

# Each entry: (point_name, default_unit, default_tolerance_cm)
KNITWEAR_MEASUREMENT_POINTS = [
    ("Body length (CB)", "cm", 1.0),
    ("Body length (HPS)", "cm", 1.0),
    ("Chest width", "cm", 1.0),
    ("Waist width", "cm", 1.0),
    ("Hem width", "cm", 1.0),
    ("Shoulder width", "cm", 0.5),
    ("Shoulder drop", "cm", 0.5),
    ("Sleeve length (CB)", "cm", 1.0),
    ("Sleeve length (from armhole)", "cm", 1.0),
    ("Armhole", "cm", 0.5),
    ("Sleeve opening", "cm", 0.5),
    ("Neckline drop", "cm", 0.5),
    ("Neck width", "cm", 0.5),
    ("Placket length", "cm", 0.5),
    ("Hem rib height", "cm", 0.3),
    ("Cuff rib height", "cm", 0.3),
    ("Neck rib height", "cm", 0.3),
]

TSHIRT_MEASUREMENT_POINTS = [
    ("Body length (CB)", "cm", 1.0),
    ("Body length (HPS)", "cm", 1.0),
    ("Chest width", "cm", 1.0),
    ("Waist width", "cm", 1.0),
    ("Hem width", "cm", 1.0),
    ("Shoulder width", "cm", 0.5),
    ("Sleeve length (CB)", "cm", 1.0),
    ("Sleeve length (from armhole)", "cm", 1.0),
    ("Armhole", "cm", 0.5),
    ("Sleeve opening", "cm", 0.5),
    ("Neckline drop", "cm", 0.5),
    ("Neck width", "cm", 0.5),
    ("Neck rib height", "cm", 0.3),
]

# =============================================================================
# SUPPLIER ACTIONS (checklist at the bottom of the tech pack)
# =============================================================================
SUPPLIER_ACTIONS = [
    "Confirm yarn availability",
    "Confirm fabric availability",
    "Advise on price",
    "Confirm MOQ",
    "Confirm lead time",
    "Send knit swatch (after order placement)",
    "Send fabric swatch (after order placement)",
    "Send button options",
    "Send trim options",
    "Send proto sample",
    "Send size set sample",
    "Send PP (pre-production) sample",
]
