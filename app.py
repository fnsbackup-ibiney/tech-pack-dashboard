"""
Tech Pack Dashboard
====================
A web-based form for generating standardized tech packs for knitwear and T-shirts.
Designed so a brand can fill it out without sending a physical reference sample —
the output is detailed enough for a factory to produce directly from.

Run locally:
    streamlit run app.py

Deploy:
    Push to GitHub, then connect via Streamlit Community Cloud.
"""

import json
from datetime import date, datetime
from io import BytesIO

import pandas as pd
import streamlit as st

from config.dropdown_options import (
    BUTTON_COLORS,
    BUTTON_MATERIALS,
    BUTTON_SIZES_MM,
    COMPOSITIONS,
    CUFF_STYLES,
    FABRIC_STRUCTURES,
    FABRIC_WEIGHTS_GSM,
    FITS,
    GAUGES,
    GENDERS,
    HEM_STYLES,
    KNIT_STRUCTURES,
    KNITWEAR_DYE_METHODS,
    KNITWEAR_MEASUREMENT_POINTS,
    LABEL_TYPES,
    NECKLINES,
    PACKING,
    PLACKETS,
    PRINT_EMBROIDERY,
    PRODUCT_TYPES,
    RIB_STRUCTURES,
    SEASONS,
    SIZE_RANGES,
    SLEEVE_LENGTHS,
    SLEEVE_TYPES,
    SUPPLIER_ACTIONS,
    TSHIRT_DYE_METHODS,
    TSHIRT_MEASUREMENT_POINTS,
    WASH_FINISHING,
    YARN_COUNTS,
    YARN_TYPES,
)
from sample_data.cardigan_sample import CARDIGAN_SAMPLE


# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="Tech Pack Dashboard",
    page_icon="🧶",
    layout="wide",
)


# =============================================================================
# HELPERS
# =============================================================================
def safe_index(options: list, value, default: int = 0) -> int:
    """Return the index of value in options, or default if not found."""
    try:
        return options.index(value)
    except (ValueError, TypeError):
        return default


def init_state():
    """Initialize session state on first run."""
    if "initialized" not in st.session_state:
        # Pre-fill with cardigan sample on first load
        for key, value in CARDIGAN_SAMPLE.items():
            st.session_state[key] = value
        st.session_state["initialized"] = True


def reset_to_blank():
    """Clear all fields to start a fresh tech pack."""
    keys_to_clear = [k for k in st.session_state.keys() if k != "initialized"]
    for k in keys_to_clear:
        del st.session_state[k]
    st.session_state["product_type"] = PRODUCT_TYPES[0]


def load_sample():
    """Load the cardigan sample data."""
    for key, value in CARDIGAN_SAMPLE.items():
        st.session_state[key] = value


def collect_data() -> dict:
    """Pull all form values from session_state into a single dict for export."""
    fields = [
        "product_type", "style_name", "style_number", "season", "gender",
        "fit", "size_range", "color_name", "pantone_code", "composition",
        "yarn_type", "yarn_count", "gauge", "knit_structure", "rib_structure",
        "fabric_structure", "fabric_weight_gsm", "dye_method",
        "neckline", "neckline_rib_cm", "sleeve_length", "sleeve_type",
        "hem_style", "hem_height_cm", "cuff_style", "cuff_height_cm",
        "placket", "button_size_mm", "button_count", "button_material",
        "button_color", "print_embroidery", "wash_finishing",
        "shoulder_reinforcement", "base_size", "measurements",
        "labels", "packing", "supplier_actions",
        "target_quantity", "target_price_usd", "delivery_date", "notes",
    ]
    data = {f: st.session_state.get(f) for f in fields}

    # Convert non-serializable types
    if isinstance(data.get("delivery_date"), (date, datetime)):
        data["delivery_date"] = data["delivery_date"].isoformat()

    data["_exported_at"] = datetime.utcnow().isoformat() + "Z"
    return data


# =============================================================================
# INIT
# =============================================================================
init_state()


# =============================================================================
# SIDEBAR
# =============================================================================
with st.sidebar:
    st.title("🧶 Tech Pack")
    st.caption("Build a factory-ready tech pack without a physical sample.")

    st.divider()

    st.subheader("Product Type")
    st.selectbox(
        "Select category",
        PRODUCT_TYPES,
        key="product_type",
        label_visibility="collapsed",
        help="This determines which construction fields show up below.",
    )

    st.divider()

    st.subheader("Quick Actions")
    col_a, col_b = st.columns(2)
    if col_a.button("📋 Load Sample", use_container_width=True):
        load_sample()
        st.rerun()
    if col_b.button("🧹 Reset", use_container_width=True):
        reset_to_blank()
        st.rerun()

    st.divider()
    st.caption("v0.1 · Draft")


# =============================================================================
# MAIN TABS
# =============================================================================
tab_editor, tab_preview, tab_export = st.tabs(
    ["📝 Editor", "👀 Preview", "📥 Export"]
)


# -----------------------------------------------------------------------------
# TAB 1: EDITOR
# -----------------------------------------------------------------------------
with tab_editor:
    st.header("Fill out the tech pack")
    is_knitwear = st.session_state["product_type"].startswith("Knitwear")

    # --- Section 1: Style Overview ---
    with st.expander("1. Style Overview", expanded=True):
        c1, c2, c3 = st.columns(3)
        c1.text_input("Style name", key="style_name", placeholder="e.g. Cotton Knit Cardigan")
        c2.text_input("Style number", key="style_number", placeholder="e.g. KW-SS26-001")
        c3.selectbox(
            "Season",
            SEASONS,
            index=safe_index(SEASONS, st.session_state.get("season")),
            key="season",
        )

        c1, c2, c3 = st.columns(3)
        c1.selectbox(
            "Gender",
            GENDERS,
            index=safe_index(GENDERS, st.session_state.get("gender")),
            key="gender",
        )
        c2.selectbox(
            "Fit",
            FITS,
            index=safe_index(FITS, st.session_state.get("fit")),
            key="fit",
        )
        c3.selectbox(
            "Size range",
            SIZE_RANGES,
            index=safe_index(SIZE_RANGES, st.session_state.get("size_range")),
            key="size_range",
        )

        c1, c2, c3 = st.columns(3)
        c1.text_input("Color name", key="color_name", placeholder="e.g. Sunshine Yellow")
        c2.text_input("Pantone code", key="pantone_code", placeholder="e.g. 13-0859 TCX")
        c3.selectbox(
            "Composition",
            COMPOSITIONS,
            index=safe_index(COMPOSITIONS, st.session_state.get("composition")),
            key="composition",
        )

    # --- Section 2: Construction (Conditional on Product Type) ---
    with st.expander("2. Construction — Material & Knit/Fabric", expanded=True):
        if is_knitwear:
            st.markdown("**Knitwear-specific fields**")
            c1, c2, c3 = st.columns(3)
            c1.selectbox(
                "Yarn type",
                YARN_TYPES,
                index=safe_index(YARN_TYPES, st.session_state.get("yarn_type")),
                key="yarn_type",
            )
            c2.selectbox(
                "Yarn count",
                YARN_COUNTS,
                index=safe_index(YARN_COUNTS, st.session_state.get("yarn_count")),
                key="yarn_count",
                help="Ne = English count (cotton system). Nm = Metric count (wool system).",
            )
            c3.selectbox(
                "Gauge (GG)",
                GAUGES,
                index=safe_index(GAUGES, st.session_state.get("gauge")),
                key="gauge",
                help="Needles per inch. Lower GG = chunkier, higher GG = finer.",
            )

            c1, c2, c3 = st.columns(3)
            c1.selectbox(
                "Knit structure",
                KNIT_STRUCTURES,
                index=safe_index(KNIT_STRUCTURES, st.session_state.get("knit_structure")),
                key="knit_structure",
            )
            c2.selectbox(
                "Rib structure",
                RIB_STRUCTURES,
                index=safe_index(RIB_STRUCTURES, st.session_state.get("rib_structure")),
                key="rib_structure",
            )
            c3.selectbox(
                "Dyeing method",
                KNITWEAR_DYE_METHODS,
                index=safe_index(KNITWEAR_DYE_METHODS, st.session_state.get("dye_method")),
                key="dye_method",
            )
        else:
            st.markdown("**T-shirt / Jersey-specific fields**")
            c1, c2, c3 = st.columns(3)
            c1.selectbox(
                "Fabric structure",
                FABRIC_STRUCTURES,
                index=safe_index(FABRIC_STRUCTURES, st.session_state.get("fabric_structure")),
                key="fabric_structure",
            )
            c2.selectbox(
                "Fabric weight (gsm)",
                FABRIC_WEIGHTS_GSM,
                index=safe_index(FABRIC_WEIGHTS_GSM, st.session_state.get("fabric_weight_gsm")),
                key="fabric_weight_gsm",
                help="gsm = grams per square meter. Higher = heavier fabric.",
            )
            c3.selectbox(
                "Dyeing method",
                TSHIRT_DYE_METHODS,
                index=safe_index(TSHIRT_DYE_METHODS, st.session_state.get("dye_method")),
                key="dye_method",
            )

    # --- Section 3: Construction Details ---
    with st.expander("3. Construction — Style Details", expanded=True):
        c1, c2 = st.columns(2)
        c1.selectbox(
            "Neckline",
            NECKLINES,
            index=safe_index(NECKLINES, st.session_state.get("neckline")),
            key="neckline",
        )
        c2.number_input(
            "Neckline rib height (cm)",
            min_value=0.0, max_value=20.0, step=0.5,
            key="neckline_rib_cm",
        )

        c1, c2 = st.columns(2)
        c1.selectbox(
            "Sleeve length",
            SLEEVE_LENGTHS,
            index=safe_index(SLEEVE_LENGTHS, st.session_state.get("sleeve_length")),
            key="sleeve_length",
        )
        c2.selectbox(
            "Sleeve type",
            SLEEVE_TYPES,
            index=safe_index(SLEEVE_TYPES, st.session_state.get("sleeve_type")),
            key="sleeve_type",
            help="Set-in = standard. Raglan = diagonal seam. Drop shoulder = relaxed.",
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.selectbox(
            "Hem style",
            HEM_STYLES,
            index=safe_index(HEM_STYLES, st.session_state.get("hem_style")),
            key="hem_style",
        )
        c2.number_input(
            "Hem rib height (cm)",
            min_value=0.0, max_value=20.0, step=0.5,
            key="hem_height_cm",
        )
        c3.selectbox(
            "Cuff style",
            CUFF_STYLES,
            index=safe_index(CUFF_STYLES, st.session_state.get("cuff_style")),
            key="cuff_style",
        )
        c4.number_input(
            "Cuff rib height (cm)",
            min_value=0.0, max_value=20.0, step=0.5,
            key="cuff_height_cm",
        )

        c1, c2 = st.columns(2)
        c1.selectbox(
            "Placket / closure",
            PLACKETS,
            index=safe_index(PLACKETS, st.session_state.get("placket")),
            key="placket",
        )

        # Button details (only show if placket uses buttons)
        placket_has_buttons = "button" in (st.session_state.get("placket") or "").lower()
        if placket_has_buttons:
            st.markdown("**Button details**")
            c1, c2, c3, c4 = st.columns(4)
            c1.number_input(
                "Number of buttons",
                min_value=1, max_value=20, step=1,
                key="button_count",
            )
            c2.selectbox(
                "Button size (mm)",
                BUTTON_SIZES_MM,
                index=safe_index(BUTTON_SIZES_MM, st.session_state.get("button_size_mm")),
                key="button_size_mm",
            )
            c3.selectbox(
                "Button material",
                BUTTON_MATERIALS,
                index=safe_index(BUTTON_MATERIALS, st.session_state.get("button_material")),
                key="button_material",
            )
            c4.selectbox(
                "Button color",
                BUTTON_COLORS,
                index=safe_index(BUTTON_COLORS, st.session_state.get("button_color")),
                key="button_color",
            )

        c1, c2, c3 = st.columns(3)
        c1.selectbox(
            "Print / embroidery",
            PRINT_EMBROIDERY,
            index=safe_index(PRINT_EMBROIDERY, st.session_state.get("print_embroidery")),
            key="print_embroidery",
        )
        c2.selectbox(
            "Wash / finishing",
            WASH_FINISHING,
            index=safe_index(WASH_FINISHING, st.session_state.get("wash_finishing")),
            key="wash_finishing",
        )
        c3.checkbox(
            "Shoulder reinforcement required",
            key="shoulder_reinforcement",
        )

    # --- Section 4: Measurements ---
    with st.expander("4. Measurements (Base size)", expanded=True):
        c1, _ = st.columns([1, 3])
        c1.selectbox(
            "Base size for these measurements",
            ["S", "M", "L", "XL"],
            index=safe_index(["S", "M", "L", "XL"], st.session_state.get("base_size", "M")),
            key="base_size",
        )

        st.caption(
            "Fill in only the measurements that apply. Leave others at 0. "
            "Tolerance is the acceptable +/- range for the factory."
        )

        measurement_points = (
            KNITWEAR_MEASUREMENT_POINTS if is_knitwear else TSHIRT_MEASUREMENT_POINTS
        )

        # Build a DataFrame for st.data_editor
        existing = st.session_state.get("measurements", {})
        rows = []
        for point, unit, default_tol in measurement_points:
            existing_entry = existing.get(point, {})
            rows.append({
                "Measurement Point": point,
                "Value": existing_entry.get("value", 0.0),
                "Unit": unit,
                "Tolerance (±)": existing_entry.get("tolerance", default_tol),
            })
        df = pd.DataFrame(rows)

        edited_df = st.data_editor(
            df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Measurement Point": st.column_config.TextColumn(disabled=True),
                "Value": st.column_config.NumberColumn(format="%.1f", min_value=0.0),
                "Unit": st.column_config.TextColumn(disabled=True),
                "Tolerance (±)": st.column_config.NumberColumn(format="%.1f", min_value=0.0),
            },
            key="measurements_editor",
        )

        # Save back to session state
        new_measurements = {}
        for _, row in edited_df.iterrows():
            if row["Value"] > 0:
                new_measurements[row["Measurement Point"]] = {
                    "value": float(row["Value"]),
                    "tolerance": float(row["Tolerance (±)"]),
                }
        st.session_state["measurements"] = new_measurements

    # --- Section 5: Labels & Packing ---
    with st.expander("5. Labels & Packing", expanded=False):
        c1, c2 = st.columns(2)
        c1.multiselect(
            "Labels to include",
            LABEL_TYPES,
            default=st.session_state.get("labels", []),
            key="labels",
        )
        c2.selectbox(
            "Packing method",
            PACKING,
            index=safe_index(PACKING, st.session_state.get("packing")),
            key="packing",
        )

    # --- Section 6: Supplier Actions ---
    with st.expander("6. Supplier Actions Required", expanded=False):
        st.multiselect(
            "What we need from the supplier",
            SUPPLIER_ACTIONS,
            default=st.session_state.get("supplier_actions", []),
            key="supplier_actions",
        )

    # --- Section 7: Commercial ---
    with st.expander("7. Commercial Information", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.number_input(
            "Target quantity (pcs)",
            min_value=0, step=50,
            key="target_quantity",
        )
        c2.number_input(
            "Target price (USD)",
            min_value=0.0, step=0.5,
            key="target_price_usd",
            value=st.session_state.get("target_price_usd") or 0.0,
        )
        c3.date_input(
            "Target delivery date",
            key="delivery_date",
            value=st.session_state.get("delivery_date") or date.today(),
        )
        st.text_area(
            "Additional notes",
            key="notes",
            height=100,
            placeholder="Anything else the factory should know...",
        )


# -----------------------------------------------------------------------------
# TAB 2: PREVIEW
# -----------------------------------------------------------------------------
with tab_preview:
    is_knitwear = st.session_state["product_type"].startswith("Knitwear")
    data = collect_data()

    st.title("TECH PACK")
    st.caption(f"Generated by Tech Pack Dashboard · {datetime.now().strftime('%Y-%m-%d')}")

    # 1. Style Overview
    st.header("1. Style Overview")
    cols = st.columns(3)
    cols[0].markdown(f"**Style Name:** {data.get('style_name') or '—'}")
    cols[1].markdown(f"**Style Number:** {data.get('style_number') or '—'}")
    cols[2].markdown(f"**Season:** {data.get('season') or '—'}")
    cols = st.columns(3)
    cols[0].markdown(f"**Gender:** {data.get('gender') or '—'}")
    cols[1].markdown(f"**Fit:** {data.get('fit') or '—'}")
    cols[2].markdown(f"**Size Range:** {data.get('size_range') or '—'}")
    cols = st.columns(3)
    cols[0].markdown(f"**Color:** {data.get('color_name') or '—'}")
    cols[1].markdown(f"**Pantone:** {data.get('pantone_code') or '—'}")
    cols[2].markdown(f"**Composition:** {data.get('composition') or '—'}")

    # 2. Material & Knit / Fabric
    st.header("2. Material & Construction")
    if is_knitwear:
        cols = st.columns(3)
        cols[0].markdown(f"**Yarn Type:** {data.get('yarn_type') or '—'}")
        cols[1].markdown(f"**Yarn Count:** {data.get('yarn_count') or '—'}")
        cols[2].markdown(f"**Gauge:** {data.get('gauge') or '—'}")
        cols = st.columns(3)
        cols[0].markdown(f"**Knit Structure:** {data.get('knit_structure') or '—'}")
        cols[1].markdown(f"**Rib Structure:** {data.get('rib_structure') or '—'}")
        cols[2].markdown(f"**Dye Method:** {data.get('dye_method') or '—'}")
    else:
        cols = st.columns(3)
        cols[0].markdown(f"**Fabric Structure:** {data.get('fabric_structure') or '—'}")
        cols[1].markdown(f"**Fabric Weight:** {data.get('fabric_weight_gsm') or '—'} gsm")
        cols[2].markdown(f"**Dye Method:** {data.get('dye_method') or '—'}")

    # 3. Style details
    st.header("3. Style Details")
    cols = st.columns(2)
    cols[0].markdown(f"**Neckline:** {data.get('neckline') or '—'}  (rib: {data.get('neckline_rib_cm') or 0} cm)")
    cols[1].markdown(f"**Sleeve:** {data.get('sleeve_length') or '—'} / {data.get('sleeve_type') or '—'}")
    cols = st.columns(2)
    cols[0].markdown(f"**Hem:** {data.get('hem_style') or '—'} ({data.get('hem_height_cm') or 0} cm)")
    cols[1].markdown(f"**Cuff:** {data.get('cuff_style') or '—'} ({data.get('cuff_height_cm') or 0} cm)")
    cols = st.columns(2)
    cols[0].markdown(f"**Placket:** {data.get('placket') or '—'}")
    if "button" in (data.get("placket") or "").lower():
        cols[1].markdown(
            f"**Buttons:** {data.get('button_count') or 0} × {data.get('button_size_mm') or '—'} mm "
            f"{data.get('button_material') or ''} ({data.get('button_color') or ''})"
        )
    cols = st.columns(3)
    cols[0].markdown(f"**Print / Embroidery:** {data.get('print_embroidery') or '—'}")
    cols[1].markdown(f"**Wash / Finishing:** {data.get('wash_finishing') or '—'}")
    cols[2].markdown(f"**Shoulder Reinforcement:** {'Yes' if data.get('shoulder_reinforcement') else 'No'}")

    # 4. Measurements
    st.header(f"4. Measurements (Size {data.get('base_size') or 'M'})")
    measurements = data.get("measurements") or {}
    if measurements:
        m_df = pd.DataFrame([
            {
                "Measurement Point": k,
                "Value": f"{v['value']} cm",
                "Tolerance": f"± {v['tolerance']} cm",
            }
            for k, v in measurements.items()
        ])
        st.dataframe(m_df, hide_index=True, use_container_width=True)
    else:
        st.info("No measurements filled in yet.")

    # 5. Labels & Packing
    st.header("5. Labels & Packing")
    cols = st.columns(2)
    cols[0].markdown(f"**Labels:** {', '.join(data.get('labels') or []) or '—'}")
    cols[1].markdown(f"**Packing:** {data.get('packing') or '—'}")

    # 6. Supplier actions
    st.header("6. Supplier Actions Required")
    actions = data.get("supplier_actions") or []
    if actions:
        for a in actions:
            st.markdown(f"- {a}")
    else:
        st.info("No supplier actions specified.")

    # 7. Commercial
    st.header("7. Commercial")
    cols = st.columns(3)
    cols[0].markdown(f"**Target Quantity:** {data.get('target_quantity') or 0} pcs")
    cols[1].markdown(
        f"**Target Price:** ${data.get('target_price_usd') or 0:.2f} USD"
    )
    cols[2].markdown(f"**Delivery:** {data.get('delivery_date') or '—'}")
    if data.get("notes"):
        st.markdown(f"**Notes:** {data['notes']}")


# -----------------------------------------------------------------------------
# TAB 3: EXPORT
# -----------------------------------------------------------------------------
with tab_export:
    st.header("Export Tech Pack")
    st.markdown(
        "Download the tech pack data as a JSON file. This can be fed into AI tools "
        "(Copilot, ChatGPT) or imported into ERP/PLM systems."
    )

    data = collect_data()

    style_id = data.get("style_number") or "tech_pack"
    filename = f"{style_id}_{datetime.now().strftime('%Y%m%d')}.json"

    json_str = json.dumps(data, indent=2, ensure_ascii=False)

    st.download_button(
        label="📥 Download JSON",
        data=json_str.encode("utf-8"),
        file_name=filename,
        mime="application/json",
        use_container_width=True,
    )

    st.divider()
    st.subheader("JSON Preview")
    st.code(json_str, language="json")
