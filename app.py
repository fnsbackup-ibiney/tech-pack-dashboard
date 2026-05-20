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

import pandas as pd
import streamlit as st

from config.dropdown_options import (
    BUTTON_COLORS,
    BUTTON_MATERIALS,
    BUTTON_SIZES_L,
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
from exporters.docx_exporter import generate_docx
from exporters.pdf_exporter import generate_pdf
from sample_data.cardigan_sample import CARDIGAN_SAMPLE
from services import firestore_client
from services.ai_drawing import (
    build_prompt as build_drawing_prompt,
    generate_drawing,
    is_demo_mode as ai_demo_mode,
)
from services.image_helpers import (
    approximate_size_kb,
    make_image_entry,
    to_data_url,
)


# =============================================================================
# CONFIGURATION — flip these when going from testing to production
# =============================================================================

# Pre-fill the form with the cardigan demo data on first visit?
#   True  → testing / demo mode (handy while you're still building it out)
#   False → production / customer-facing (every new visitor sees a blank form;
#           they can still click "Load Demo" in the sidebar to see the example)
LOAD_SAMPLE_ON_FIRST_VISIT = False


# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="Tech Pack Dashboard",
    page_icon="🧶",
    layout="wide",
)


# =============================================================================
# CONSTANTS & HELPERS
# =============================================================================

# Every dropdown has this as the first option, so the user can always "un-select"
BLANK = "— Not specified —"


def with_blank(options: list) -> list:
    """Prepend the BLANK placeholder to a list of dropdown options."""
    return [BLANK] + list(options)


def safe_index(options: list, value, default: int = 0) -> int:
    """Return the index of value in options, or default (0 = BLANK)."""
    try:
        return options.index(value)
    except (ValueError, TypeError):
        return default


def clean(value):
    """Convert BLANK placeholder to None when exporting data."""
    if value in (BLANK, "", None):
        return None
    return value


def init_state():
    """Initialize session state on first run.

    If LOAD_SAMPLE_ON_FIRST_VISIT is True, we pre-fill the form with the
    cardigan demo data. Otherwise the form starts blank (every dropdown
    showing '— Not specified —').
    """
    if "initialized" not in st.session_state:
        if LOAD_SAMPLE_ON_FIRST_VISIT:
            for key, value in CARDIGAN_SAMPLE.items():
                if key not in WIDGET_ONLY_KEYS:
                    st.session_state[key] = value
        st.session_state["initialized"] = True


# Keys we must NOT touch via session_state — Streamlit forbids it for
# some widgets (e.g. data_editor, button, file_uploader, form_submit_button).
# Touching them throws StreamlitValueAssignmentNotAllowedError.
WIDGET_ONLY_KEYS = {"measurements_editor", "_image_uploader"}
PROTECTED_KEYS = {"initialized", "_snapshot"} | WIDGET_ONLY_KEYS


def _snapshot():
    """Save the current state so the user can Undo later."""
    st.session_state["_snapshot"] = {
        k: v for k, v in st.session_state.items() if k not in PROTECTED_KEYS
    }


def reset_to_blank():
    """Clear all fields to start a fresh tech pack (everything becomes BLANK).

    Used as an on_click callback so it runs BEFORE widgets are re-instantiated.
    """
    _snapshot()
    for k in [k for k in st.session_state.keys() if k not in PROTECTED_KEYS]:
        del st.session_state[k]


def load_sample():
    """Reload the cardigan sample data.

    Used as an on_click callback so it runs BEFORE widgets are re-instantiated.
    """
    _snapshot()
    for key, value in CARDIGAN_SAMPLE.items():
        if key not in WIDGET_ONLY_KEYS:
            st.session_state[key] = value


def undo():
    """Restore the state from the most recent snapshot (before Load Sample / Reset)."""
    snapshot = st.session_state.get("_snapshot")
    if not snapshot:
        return
    # Clear current state (except protected keys)
    for k in [k for k in st.session_state.keys() if k not in PROTECTED_KEYS]:
        del st.session_state[k]
    # Restore from snapshot (skip widget-only keys)
    for k, v in snapshot.items():
        if k not in WIDGET_ONLY_KEYS:
            st.session_state[k] = v
    # Remove the snapshot itself (single-level undo)
    del st.session_state["_snapshot"]


# -----------------------------------------------------------------------------
# Image management — callbacks used by the Editor's Images section.
# Images live in st.session_state["images"] as a list of dicts:
#   {"id": str, "caption": str, "data": base64_str, "mime": str}
# -----------------------------------------------------------------------------

def _ensure_image_list():
    if "images" not in st.session_state:
        st.session_state["images"] = []


def add_image(file_bytes: bytes, filename: str):
    """Compress and append a new image to the list."""
    _ensure_image_list()
    st.session_state["images"].append(make_image_entry(file_bytes, filename))


def delete_image(img_id: str):
    _ensure_image_list()
    st.session_state["images"] = [
        i for i in st.session_state["images"] if i["id"] != img_id
    ]


def move_image(img_id: str, direction: int):
    """Move an image up (-1) or down (+1) in the list."""
    _ensure_image_list()
    images = st.session_state["images"]
    idx = next((i for i, img in enumerate(images) if img["id"] == img_id), -1)
    if idx < 0:
        return
    new_idx = idx + direction
    if 0 <= new_idx < len(images):
        images[idx], images[new_idx] = images[new_idx], images[idx]


def update_caption(img_id: str):
    """on_change callback — reads the latest caption from the text_input widget
    and writes it back into the image dict."""
    _ensure_image_list()
    widget_key = f"_caption_input_{img_id}"
    new_caption = st.session_state.get(widget_key, "")
    for img in st.session_state["images"]:
        if img["id"] == img_id:
            img["caption"] = new_caption
            return


def restore_from_dict(loaded: dict):
    """Replace current state with a saved tech pack record from Firestore.

    Used as an on_click callback so it runs BEFORE widgets are re-instantiated.
    """
    _snapshot()
    # Clear current state
    for k in [k for k in st.session_state.keys() if k not in PROTECTED_KEYS]:
        del st.session_state[k]
    # Apply the loaded values
    for k, v in loaded.items():
        if k in WIDGET_ONLY_KEYS or k.startswith("_"):
            continue
        # delivery_date comes back as ISO string — try to parse back to date
        if k == "delivery_date" and isinstance(v, str):
            try:
                v = date.fromisoformat(v.split("T")[0])
            except (ValueError, AttributeError):
                pass
        st.session_state[k] = v


def collect_data() -> dict:
    """Pull all form values from session_state into a single dict for export."""
    fields = [
        "product_type", "style_name", "style_number", "season", "gender",
        "fit", "size_range", "color_name", "pantone_code", "composition",
        "yarn_type", "yarn_count", "gauge", "knit_structure", "rib_structure",
        "fabric_structure", "fabric_weight_gsm", "dye_method",
        "neckline", "neckline_rib_cm", "sleeve_length", "sleeve_type",
        "hem_style", "hem_height_cm", "cuff_style", "cuff_height_cm",
        "placket", "button_size_l", "button_count", "button_material",
        "button_color", "print_embroidery", "wash_finishing",
        "shoulder_reinforcement", "base_size", "measurements",
        "labels", "packing", "supplier_actions",
        "target_quantity", "target_price_usd", "delivery_date", "notes",
    ]
    data = {f: clean(st.session_state.get(f)) for f in fields}

    # Non-text fields shouldn't go through clean()
    data["measurements"] = st.session_state.get("measurements") or {}
    data["labels"] = st.session_state.get("labels") or []
    data["supplier_actions"] = st.session_state.get("supplier_actions") or []
    data["images"] = st.session_state.get("images") or []
    data["technical_drawing"] = st.session_state.get("technical_drawing")
    data["shoulder_reinforcement"] = bool(st.session_state.get("shoulder_reinforcement"))
    data["neckline_rib_cm"] = st.session_state.get("neckline_rib_cm")
    data["hem_height_cm"] = st.session_state.get("hem_height_cm")
    data["cuff_height_cm"] = st.session_state.get("cuff_height_cm")
    data["button_count"] = st.session_state.get("button_count")
    data["target_quantity"] = st.session_state.get("target_quantity")
    data["target_price_usd"] = st.session_state.get("target_price_usd")

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
    col_a.button(
        "📋 Load Demo",
        on_click=load_sample,
        use_container_width=True,
        help="Fill the form with an example tech pack (the cotton cardigan).",
    )
    col_b.button(
        "🧹 Reset",
        on_click=reset_to_blank,
        use_container_width=True,
        help="Clear all fields back to '— Not specified —'.",
    )

    # Undo — only enabled if there's a snapshot to restore
    can_undo = bool(st.session_state.get("_snapshot"))
    st.button(
        "↩️ Undo (restore previous)" if can_undo else "↩️ Undo (nothing to undo)",
        on_click=undo,
        use_container_width=True,
        disabled=not can_undo,
        help="Restore the state from before your last Load Demo, Reset, or History load.",
    )

    st.divider()

    # --- Save to cloud ---
    st.subheader("Save / Cloud")
    firestore_ready = firestore_client.is_configured()
    current_doc_id = st.session_state.get("_current_doc_id")

    if current_doc_id:
        st.caption(f"📝 Editing record `{current_doc_id[:8]}…`")
    else:
        st.caption("💡 Not saved yet — click below to save a new record.")

    def _do_save():
        """on_click callback: save to Firestore."""
        try:
            data = collect_data()
            doc_id = firestore_client.save_tech_pack(
                data, doc_id=st.session_state.get("_current_doc_id")
            )
            st.session_state["_current_doc_id"] = doc_id
            st.session_state["_save_status"] = ("success", doc_id)
        except Exception as e:
            st.session_state["_save_status"] = ("error", str(e))

    def _save_as_new():
        """on_click callback: force a brand-new record (don't overwrite current)."""
        st.session_state["_current_doc_id"] = None
        _do_save()

    save_label = "💾 Save (update record)" if current_doc_id else "💾 Save to cloud"
    st.button(
        save_label,
        on_click=_do_save,
        use_container_width=True,
        disabled=not firestore_ready,
        type="primary" if firestore_ready else "secondary",
        help=(
            "Save the current tech pack to Firestore."
            if firestore_ready
            else "Firestore not configured. Add credentials to Streamlit secrets."
        ),
    )
    if current_doc_id:
        st.button(
            "💾 Save as new (don't overwrite)",
            on_click=_save_as_new,
            use_container_width=True,
            disabled=not firestore_ready,
            help="Create a brand-new record instead of updating the one you're editing.",
        )

    # Surface the result of the last save action
    status = st.session_state.pop("_save_status", None)
    if status:
        kind, payload = status
        if kind == "success":
            st.success(f"✅ Saved · id `{payload[:8]}…`")
        else:
            st.error(f"❌ Save failed: {payload}")

    st.divider()
    st.caption("v0.3 · Draft")


# =============================================================================
# MAIN TABS
# =============================================================================
tab_editor, tab_preview, tab_export, tab_history = st.tabs(
    ["📝 Editor", "👀 Preview", "📥 Export", "🗂️ History"]
)


# -----------------------------------------------------------------------------
# TAB 1: EDITOR
# -----------------------------------------------------------------------------
with tab_editor:
    st.header("Fill out the tech pack")
    st.caption(
        "Every dropdown has a **— Not specified —** option at the top — leave it "
        "there if you don't want to lock that detail down yet."
    )
    is_knitwear = st.session_state["product_type"].startswith("Knitwear")

    # --- Section 1: Style Overview ---
    with st.expander("1. Style Overview", expanded=True):
        c1, c2, c3 = st.columns(3)
        c1.text_input("Style name", key="style_name", placeholder="e.g. Cotton Knit Cardigan")
        c2.text_input("Style number", key="style_number", placeholder="e.g. KW-SS26-001")
        opts = with_blank(SEASONS)
        c3.selectbox("Season", opts, index=safe_index(opts, st.session_state.get("season")), key="season")

        c1, c2, c3 = st.columns(3)
        opts = with_blank(GENDERS)
        c1.selectbox("Gender", opts, index=safe_index(opts, st.session_state.get("gender")), key="gender")
        opts = with_blank(FITS)
        c2.selectbox("Fit", opts, index=safe_index(opts, st.session_state.get("fit")), key="fit")
        opts = with_blank(SIZE_RANGES)
        c3.selectbox("Size range", opts, index=safe_index(opts, st.session_state.get("size_range")), key="size_range")

        c1, c2, c3 = st.columns(3)
        c1.text_input("Color name", key="color_name", placeholder="e.g. Sunshine Yellow")
        c2.text_input("Pantone code", key="pantone_code", placeholder="e.g. 13-0859 TCX")
        opts = with_blank(COMPOSITIONS)
        c3.selectbox("Composition", opts, index=safe_index(opts, st.session_state.get("composition")), key="composition")

    # --- Images section ---
    with st.expander("📷 Images & References", expanded=True):
        st.caption(
            "Upload reference photos, technical drawings, mood boards, etc. "
            "Images are auto-resized to 800 px and compressed before saving "
            "(keeps each tech pack under Firestore's size limit)."
        )

        # Multi-file uploader — anything dropped here gets added to the list.
        uploaded_files = st.file_uploader(
            "Drop images here or click to browse",
            accept_multiple_files=True,
            type=["png", "jpg", "jpeg", "webp", "gif"],
            key="_image_uploader",
            label_visibility="collapsed",
        )
        if uploaded_files:
            # Process new uploads — but only ones we haven't already added
            # (st.file_uploader re-yields the same files on every rerun).
            already_added = st.session_state.get("_uploaded_filenames", set())
            for f in uploaded_files:
                # Use name + size as a cheap dedupe key
                fkey = f"{f.name}::{f.size}"
                if fkey not in already_added:
                    try:
                        add_image(f.getvalue(), f.name)
                        already_added.add(fkey)
                    except Exception as e:
                        st.error(f"Couldn't add {f.name}: {e}")
            st.session_state["_uploaded_filenames"] = already_added

        # Show the current list of images with controls
        images = st.session_state.get("images") or []
        if not images:
            st.info("No images yet. Drag some onto the uploader above.")
        else:
            st.markdown(f"**{len(images)}** image{'s' if len(images) != 1 else ''}")
            for idx, img in enumerate(images):
                cols = st.columns([1, 4, 1, 1, 1])
                cols[0].image(to_data_url(img), width=110)
                # Seed the widget value on first render only — after that the
                # widget owns its session_state key and on_change syncs back.
                caption_key = f"_caption_input_{img['id']}"
                if caption_key not in st.session_state:
                    st.session_state[caption_key] = img.get("caption", "")
                cols[1].text_input(
                    "Caption",
                    key=caption_key,
                    on_change=update_caption,
                    args=(img["id"],),
                    label_visibility="collapsed",
                    placeholder="Caption (e.g. Front reference, Technical drawing)",
                )
                cols[1].caption(f"~{approximate_size_kb(img):.0f} KB")
                cols[2].button(
                    "↑",
                    key=f"_up_{img['id']}",
                    on_click=move_image,
                    args=(img["id"], -1),
                    use_container_width=True,
                    disabled=(idx == 0),
                )
                cols[3].button(
                    "↓",
                    key=f"_down_{img['id']}",
                    on_click=move_image,
                    args=(img["id"], +1),
                    use_container_width=True,
                    disabled=(idx == len(images) - 1),
                )
                cols[4].button(
                    "🗑️",
                    key=f"_del_{img['id']}",
                    on_click=delete_image,
                    args=(img["id"],),
                    use_container_width=True,
                    help="Remove this image",
                )

    # --- AI Technical Drawing section ---
    with st.expander("🎨 Technical Drawing (AI-generated)", expanded=True):
        ai_cols = st.columns([3, 1])
        ai_cols[0].caption(
            "Click the button below — AI reads the fields you've filled in "
            "above and generates a flat technical sketch (front + back view). "
            "Use this in place of a hand-drawn tech illustration."
        )
        if ai_demo_mode():
            ai_cols[1].caption("🧪 **Demo mode** — placeholder output")

        # Generate / re-generate button
        existing_drawing = st.session_state.get("technical_drawing")

        def _do_generate():
            try:
                data_now = collect_data()
                drawing = generate_drawing(data_now)
                st.session_state["technical_drawing"] = drawing
                st.session_state["_drawing_status"] = ("success", None)
            except Exception as e:
                st.session_state["_drawing_status"] = ("error", str(e))

        def _do_clear_drawing():
            st.session_state["technical_drawing"] = None

        btn_label = (
            "🎨 Re-generate Technical Drawing"
            if existing_drawing
            else "🎨 Generate Technical Drawing"
        )

        gen_cols = st.columns([2, 1])
        gen_cols[0].button(
            btn_label,
            on_click=_do_generate,
            use_container_width=True,
            type="primary",
        )
        if existing_drawing:
            gen_cols[1].button(
                "🗑️ Clear",
                on_click=_do_clear_drawing,
                use_container_width=True,
            )

        # Surface result of last generation
        drawing_status = st.session_state.pop("_drawing_status", None)
        if drawing_status:
            kind, payload = drawing_status
            if kind == "success":
                st.success("✅ Drawing generated.")
            else:
                st.error(f"❌ Generation failed: {payload}")

        # Display the current drawing
        if existing_drawing:
            disp_cols = st.columns([2, 3])
            with disp_cols[0]:
                st.image(to_data_url(existing_drawing), use_container_width=True)
                st.caption(existing_drawing.get("caption") or "—")
            with disp_cols[1]:
                with st.expander("🔍 View AI prompt that was used", expanded=False):
                    st.code(existing_drawing.get("prompt") or "(no prompt recorded)", language="text")

    # --- Section 2: Construction (Conditional on Product Type) ---
    with st.expander("2. Construction — Material & Knit/Fabric", expanded=True):
        if is_knitwear:
            st.markdown("**Knitwear-specific fields**")
            c1, c2, c3 = st.columns(3)
            opts = with_blank(YARN_TYPES)
            c1.selectbox("Yarn type", opts, index=safe_index(opts, st.session_state.get("yarn_type")), key="yarn_type")
            opts = with_blank(YARN_COUNTS)
            c2.selectbox(
                "Yarn count", opts,
                index=safe_index(opts, st.session_state.get("yarn_count")),
                key="yarn_count",
                help="Ne = English count (cotton system). Nm = Metric count (wool system).",
            )
            opts = with_blank(GAUGES)
            c3.selectbox(
                "Gauge (GG)", opts,
                index=safe_index(opts, st.session_state.get("gauge")),
                key="gauge",
                help="Needles per inch. Lower GG = chunkier, higher GG = finer.",
            )

            c1, c2, c3 = st.columns(3)
            opts = with_blank(KNIT_STRUCTURES)
            c1.selectbox("Knit structure", opts, index=safe_index(opts, st.session_state.get("knit_structure")), key="knit_structure")
            opts = with_blank(RIB_STRUCTURES)
            c2.selectbox("Rib structure", opts, index=safe_index(opts, st.session_state.get("rib_structure")), key="rib_structure")
            opts = with_blank(KNITWEAR_DYE_METHODS)
            c3.selectbox("Dyeing method", opts, index=safe_index(opts, st.session_state.get("dye_method")), key="dye_method")
        else:
            st.markdown("**T-shirt / Jersey-specific fields**")
            c1, c2, c3 = st.columns(3)
            opts = with_blank(FABRIC_STRUCTURES)
            c1.selectbox("Fabric structure", opts, index=safe_index(opts, st.session_state.get("fabric_structure")), key="fabric_structure")
            opts = with_blank(FABRIC_WEIGHTS_GSM)
            c2.selectbox(
                "Fabric weight (gsm)", opts,
                index=safe_index(opts, st.session_state.get("fabric_weight_gsm")),
                key="fabric_weight_gsm",
                help="gsm = grams per square meter. Higher = heavier fabric.",
            )
            opts = with_blank(TSHIRT_DYE_METHODS)
            c3.selectbox("Dyeing method", opts, index=safe_index(opts, st.session_state.get("dye_method")), key="dye_method")

    # --- Section 3: Construction Details ---
    with st.expander("3. Construction — Style Details", expanded=True):
        c1, c2 = st.columns(2)
        opts = with_blank(NECKLINES)
        c1.selectbox("Neckline", opts, index=safe_index(opts, st.session_state.get("neckline")), key="neckline")
        c2.number_input("Neckline rib height (cm)", min_value=0.0, max_value=20.0, step=0.5, key="neckline_rib_cm")

        c1, c2 = st.columns(2)
        opts = with_blank(SLEEVE_LENGTHS)
        c1.selectbox("Sleeve length", opts, index=safe_index(opts, st.session_state.get("sleeve_length")), key="sleeve_length")
        opts = with_blank(SLEEVE_TYPES)
        c2.selectbox(
            "Sleeve type", opts,
            index=safe_index(opts, st.session_state.get("sleeve_type")),
            key="sleeve_type",
            help="Set-in = standard. Raglan = diagonal seam. Drop shoulder = relaxed.",
        )

        c1, c2, c3, c4 = st.columns(4)
        opts = with_blank(HEM_STYLES)
        c1.selectbox("Hem style", opts, index=safe_index(opts, st.session_state.get("hem_style")), key="hem_style")
        c2.number_input("Hem rib height (cm)", min_value=0.0, max_value=20.0, step=0.5, key="hem_height_cm")
        opts = with_blank(CUFF_STYLES)
        c3.selectbox("Cuff style", opts, index=safe_index(opts, st.session_state.get("cuff_style")), key="cuff_style")
        c4.number_input("Cuff rib height (cm)", min_value=0.0, max_value=20.0, step=0.5, key="cuff_height_cm")

        c1, c2 = st.columns(2)
        opts = with_blank(PLACKETS)
        c1.selectbox("Placket / closure", opts, index=safe_index(opts, st.session_state.get("placket")), key="placket")

        # Button details (only show if placket uses buttons)
        placket_has_buttons = "button" in (st.session_state.get("placket") or "").lower()
        if placket_has_buttons:
            st.markdown("**Button details** _(L = Ligne, the industry-standard unit. 32L ≈ 20 mm)_")
            c1, c2, c3, c4 = st.columns(4)
            c1.number_input("Number of buttons", min_value=1, max_value=20, step=1, key="button_count")
            opts = with_blank(BUTTON_SIZES_L)
            c2.selectbox(
                "Button size (L)", opts,
                index=safe_index(opts, st.session_state.get("button_size_l")),
                key="button_size_l",
                help="L = Ligne. 1L = 1/40 inch ≈ 0.635 mm. 32L ≈ 20 mm.",
            )
            opts = with_blank(BUTTON_MATERIALS)
            c3.selectbox("Button material", opts, index=safe_index(opts, st.session_state.get("button_material")), key="button_material")
            opts = with_blank(BUTTON_COLORS)
            c4.selectbox("Button color", opts, index=safe_index(opts, st.session_state.get("button_color")), key="button_color")

        c1, c2, c3 = st.columns(3)
        opts = with_blank(PRINT_EMBROIDERY)
        c1.selectbox("Print / embroidery", opts, index=safe_index(opts, st.session_state.get("print_embroidery")), key="print_embroidery")
        opts = with_blank(WASH_FINISHING)
        c2.selectbox("Wash / finishing", opts, index=safe_index(opts, st.session_state.get("wash_finishing")), key="wash_finishing")
        c3.checkbox("Shoulder reinforcement required", key="shoulder_reinforcement")

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
        c1.multiselect("Labels to include", LABEL_TYPES, default=st.session_state.get("labels", []), key="labels")
        opts = with_blank(PACKING)
        c2.selectbox("Packing method", opts, index=safe_index(opts, st.session_state.get("packing")), key="packing")

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
        c1.number_input("Target quantity (pcs)", min_value=0, step=50, key="target_quantity")
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

    # --- Images ---
    images = data.get("images") or []
    if images:
        st.header("📷 Images & References")
        # Render in a responsive grid: 3 columns for up to ~9 images
        per_row = 3
        for row_start in range(0, len(images), per_row):
            row_imgs = images[row_start:row_start + per_row]
            cols = st.columns(per_row)
            for i, img in enumerate(row_imgs):
                with cols[i]:
                    st.image(to_data_url(img), use_container_width=True)
                    st.caption(img.get("caption") or "—")

    # --- AI Technical Drawing ---
    tech_drawing = data.get("technical_drawing")
    if tech_drawing:
        st.header("🎨 Technical Drawing")
        cols = st.columns([2, 3])
        cols[0].image(to_data_url(tech_drawing), use_container_width=True)
        cols[0].caption(tech_drawing.get("caption") or "—")
        with cols[1]:
            st.markdown("**AI-generated from inputs above**")
            with st.expander("🔍 Prompt used", expanded=False):
                st.code(tech_drawing.get("prompt") or "—", language="text")

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
            f"**Buttons:** {data.get('button_count') or 0} × {data.get('button_size_l') or '—'} "
            f"{data.get('button_material') or ''} ({data.get('button_color') or ''})"
        )
    cols = st.columns(3)
    cols[0].markdown(f"**Print / Embroidery:** {data.get('print_embroidery') or '—'}")
    cols[1].markdown(f"**Wash / Finishing:** {data.get('wash_finishing') or '—'}")
    cols[2].markdown(f"**Shoulder Reinforcement:** {'Yes' if data.get('shoulder_reinforcement') else 'No'}")

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

    st.header("5. Labels & Packing")
    cols = st.columns(2)
    cols[0].markdown(f"**Labels:** {', '.join(data.get('labels') or []) or '—'}")
    cols[1].markdown(f"**Packing:** {data.get('packing') or '—'}")

    st.header("6. Supplier Actions Required")
    actions = data.get("supplier_actions") or []
    if actions:
        for a in actions:
            st.markdown(f"- {a}")
    else:
        st.info("No supplier actions specified.")

    st.header("7. Commercial")
    cols = st.columns(3)
    cols[0].markdown(f"**Target Quantity:** {data.get('target_quantity') or 0} pcs")
    cols[1].markdown(f"**Target Price:** ${data.get('target_price_usd') or 0:.2f} USD")
    cols[2].markdown(f"**Delivery:** {data.get('delivery_date') or '—'}")
    if data.get("notes"):
        st.markdown(f"**Notes:** {data['notes']}")


# -----------------------------------------------------------------------------
# TAB 3: EXPORT
# -----------------------------------------------------------------------------
with tab_export:
    st.header("Export Tech Pack")
    st.markdown(
        "Pick the format you need. **PDF** and **Word** are formatted documents "
        "you can send straight to a factory. **JSON** is structured data — feed it "
        "into AI tools (Copilot, ChatGPT) or import into ERP/PLM systems."
    )

    data = collect_data()

    style_id = data.get("style_number") or "tech_pack"
    timestamp = datetime.now().strftime("%Y%m%d")

    col_pdf, col_docx, col_json = st.columns(3)

    with col_pdf:
        st.subheader("📄 PDF")
        st.caption("Best for sharing externally — print-ready, fixed layout.")
        try:
            pdf_bytes = generate_pdf(data)
            st.download_button(
                label="Download PDF",
                data=pdf_bytes,
                file_name=f"{style_id}_{timestamp}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"PDF generation failed: {e}")

    with col_docx:
        st.subheader("📝 Word")
        st.caption("Editable — drop into your existing tech pack template.")
        try:
            docx_bytes = generate_docx(data)
            st.download_button(
                label="Download Word",
                data=docx_bytes,
                file_name=f"{style_id}_{timestamp}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"Word generation failed: {e}")

    with col_json:
        st.subheader("🔧 JSON")
        st.caption("For Copilot/ChatGPT/ERP — structured machine-readable data.")
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        st.download_button(
            label="Download JSON",
            data=json_str.encode("utf-8"),
            file_name=f"{style_id}_{timestamp}.json",
            mime="application/json",
            use_container_width=True,
        )

    st.divider()
    with st.expander("👀 Preview JSON data", expanded=False):
        st.code(json.dumps(data, indent=2, ensure_ascii=False), language="json")


# -----------------------------------------------------------------------------
# TAB 4: HISTORY
# -----------------------------------------------------------------------------
with tab_history:
    st.header("🗂️ Saved Tech Packs")
    st.caption(
        "Every tech pack you save shows up here. Click **Load** to restore it "
        "into the Editor; **Delete** removes it permanently."
    )

    if not firestore_client.is_configured():
        st.warning(
            "**Firestore is not connected yet.** To enable cloud history:\n\n"
            "1. Set up a Firebase project + Firestore database\n"
            "2. Create a service account, download the JSON key\n"
            "3. Add the key to `.streamlit/secrets.toml` (local) or Streamlit "
            "Cloud secrets (deployed) under `[firebase_service_account]`\n\n"
            "Until then, you can still use the dashboard normally — Save and "
            "History just won't work."
        )
    else:
        # --- Refresh button ---
        col_refresh, col_info = st.columns([1, 4])
        if col_refresh.button("🔄 Refresh", use_container_width=True):
            st.rerun()
        col_info.caption("List is sorted by most recently updated.")

        try:
            records = firestore_client.list_tech_packs()
        except Exception as e:
            st.error(f"Couldn't load history: {e}")
            records = []

        if not records:
            st.info(
                "No saved tech packs yet. Fill out the Editor, then click "
                "**💾 Save to cloud** in the sidebar."
            )
        else:
            st.write(f"**{len(records)}** record{'s' if len(records) != 1 else ''}")
            st.divider()

            for rec in records:
                with st.container():
                    cols = st.columns([3, 2, 2, 2, 1, 1])

                    # Display info
                    cols[0].markdown(f"**{rec['name'] or 'Untitled'}**")
                    cols[0].caption(f"`{rec['id'][:8]}…`")

                    cols[1].markdown(rec.get("style_number") or "—")
                    cols[1].caption("Style #")

                    product_short = (rec.get("product_type") or "—").split(" (")[0]
                    cols[2].markdown(product_short)
                    cols[2].caption("Type")

                    cols[3].markdown(rec.get("season") or "—")
                    updated = rec.get("updated_at")
                    if updated and hasattr(updated, "strftime"):
                        cols[3].caption(updated.strftime("%Y-%m-%d %H:%M"))
                    else:
                        cols[3].caption("—")

                    # Load button
                    def _make_load_callback(doc_id):
                        def _cb():
                            try:
                                loaded = firestore_client.load_tech_pack(doc_id)
                                if loaded:
                                    restore_from_dict(loaded)
                                    st.session_state["_current_doc_id"] = doc_id
                                    st.session_state["_load_status"] = ("success", doc_id)
                                else:
                                    st.session_state["_load_status"] = ("error", "Record not found")
                            except Exception as e:
                                st.session_state["_load_status"] = ("error", str(e))
                        return _cb

                    cols[4].button(
                        "Load",
                        key=f"load_{rec['id']}",
                        on_click=_make_load_callback(rec["id"]),
                        use_container_width=True,
                        help="Load this record into the Editor (your current work will be undoable).",
                    )

                    # Delete button (two-step: arm then confirm)
                    arm_key = f"arm_delete_{rec['id']}"
                    if st.session_state.get(arm_key):
                        if cols[5].button(
                            "❗ Confirm",
                            key=f"confirm_{rec['id']}",
                            use_container_width=True,
                            type="primary",
                        ):
                            try:
                                firestore_client.delete_tech_pack(rec["id"])
                                # Also clear the editing pointer if we just deleted what's open
                                if st.session_state.get("_current_doc_id") == rec["id"]:
                                    st.session_state["_current_doc_id"] = None
                                st.session_state[arm_key] = False
                                st.rerun()
                            except Exception as e:
                                st.error(f"Delete failed: {e}")
                    else:
                        if cols[5].button(
                            "Delete",
                            key=f"del_{rec['id']}",
                            use_container_width=True,
                        ):
                            st.session_state[arm_key] = True
                            st.rerun()

                    st.divider()

        # Surface result of Load action
        load_status = st.session_state.pop("_load_status", None)
        if load_status:
            kind, payload = load_status
            if kind == "success":
                st.success(f"✅ Loaded record `{payload[:8]}…` — switch to the **📝 Editor** tab to see it.")
            else:
                st.error(f"❌ Load failed: {payload}")
