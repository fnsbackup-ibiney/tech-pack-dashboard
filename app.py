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

import base64
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
    PLACKET_INTERLINING,
    PRINT_EMBROIDERY,
    KNITWEAR_SUB_CATEGORIES,
    PRODUCT_TYPES,
    RIB_STRUCTURES,
    SEASONS,
    SIZE_RANGES,
    SLEEVE_LENGTHS,
    SLEEVE_TYPES,
    STITCHING_TYPES,
    SUPPLIER_ACTIONS,
    TSHIRT_DYE_METHODS,
    TSHIRT_MEASUREMENT_POINTS,
    TSHIRT_SUB_CATEGORIES,
    WASH_FINISHING,
    YARN_COUNTS,
    YARN_TYPES,
)
from exporters.docx_exporter import generate_docx
from exporters.pdf_exporter import generate_pdf
from sample_data.cardigan_sample import CARDIGAN_SAMPLE
from services import firestore_client, market_pricing, photo_analyzer
from services.ai_drawing import (
    build_prompt as build_drawing_prompt,
    generate_drawing,
    is_demo_mode as ai_demo_mode,
    _describe_for_sketch as describe_for_sketch,
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
    cardigan demo data AND unlock it (user sees the full form). Otherwise
    the form starts blank AND locked — only the image-upload section is
    visible, and the rest unfolds after AI analyzes a photo (or the user
    chooses to skip and fill in manually).
    """
    if "initialized" not in st.session_state:
        if LOAD_SAMPLE_ON_FIRST_VISIT:
            for key, value in CARDIGAN_SAMPLE.items():
                if key not in WIDGET_ONLY_KEYS:
                    st.session_state[key] = value
            st.session_state["_form_unlocked"] = True
        else:
            st.session_state["_form_unlocked"] = False
        st.session_state["initialized"] = True


# Keys we must NOT touch via session_state — Streamlit forbids it for
# some widgets (e.g. data_editor, button, file_uploader, form_submit_button).
# Touching them throws StreamlitValueAssignmentNotAllowedError.
# Widgets whose state Streamlit refuses to let us assign. We can `del` them
# but Streamlit also internally caches their state, so for file_uploader in
# particular `del` alone doesn't fully clear the visible filename — see the
# uploader_version trick below.
WIDGET_ONLY_KEYS = {"measurements_editor", "_image_uploader"}
# Counter that bumps on every reset_to_blank. We compose the file_uploader's
# key as f"_image_uploader_{version}" so a reset gives the widget a brand
# new key, forcing Streamlit to rebuild it empty. The version key itself is
# protected from being cleared by the reset loop.
PROTECTED_KEYS = {"initialized", "_snapshot", "_uploader_version", "_measurements_version"} | WIDGET_ONLY_KEYS


def _snapshot():
    """Save the current state so the user can Undo later."""
    st.session_state["_snapshot"] = {
        k: v for k, v in st.session_state.items() if k not in PROTECTED_KEYS
    }


def reset_to_blank():
    """Clear all fields to start a fresh tech pack (everything becomes BLANK).

    Used as an on_click callback so it runs BEFORE widgets are re-instantiated.
    Also re-locks the form so the user sees the "upload a photo first" prompt.
    """
    _snapshot()
    for k in [k for k in st.session_state.keys() if k not in PROTECTED_KEYS]:
        del st.session_state[k]
    # Bump the uploader version so the file_uploader gets a NEW key on the
    # next render. Streamlit caches widget state internally and `del` on the
    # widget key alone doesn't fully clear the visible filename — but giving
    # the widget a different key forces a clean rebuild.
    st.session_state["_uploader_version"] = (
        st.session_state.get("_uploader_version", 0) + 1
    )
    # Also bump the measurements-editor version so its data_editor rebuilds
    # from the cleared session_state["measurements"] (= empty dict).
    st.session_state["_measurements_version"] = (
        st.session_state.get("_measurements_version", 0) + 1
    )
    # Also try to delete the OLD widget keys (belt-and-suspenders — harmless
    # even if they're already gone).
    for v in range(st.session_state["_uploader_version"]):
        try:
            del st.session_state[f"_image_uploader_{v}"]
        except KeyError:
            pass
    try:
        del st.session_state["measurements_editor"]
    except KeyError:
        pass
    st.session_state["_form_unlocked"] = False
    st.session_state["_just_reset"] = True


def load_sample():
    """Reload the cardigan sample data.

    Used as an on_click callback so it runs BEFORE widgets are re-instantiated.
    Unlocks the form (demo data is already complete, no need to gate behind AI).
    """
    _snapshot()
    for key, value in CARDIGAN_SAMPLE.items():
        if key not in WIDGET_ONLY_KEYS:
            st.session_state[key] = value
    st.session_state["_form_unlocked"] = True


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
    Unlocks the form because loaded records are already populated.
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
    st.session_state["_form_unlocked"] = True


def ai_autofill_callback():
    """on_click callback for the '🔍 Auto-fill from photo' button.

    Reads the first uploaded image, calls Gemini Vision, applies suggestions
    to session_state, and unlocks the rest of the form. Must run as an
    on_click callback (NOT inline) so it modifies session_state BEFORE the
    form widgets are re-instantiated this run.
    """
    import base64
    images = st.session_state.get("images") or []
    # Only consider true reference uploads — skip AI-generated drawings and
    # camera-captured photos (those are for discussion only, not analysis).
    reference_images = [
        i for i in images
        if "ai_generated" not in (i.get("source") or "")
        and (i.get("source") or "") != "captured"
    ]
    if not reference_images:
        st.session_state["_ai_autofill_result"] = {
            "error": "No reference photo uploaded. Drop one onto the uploader first."
        }
        return
    try:
        first_image = reference_images[0]
        suggestions = photo_analyzer.analyze_garment_photo(
            base64.b64decode(first_image["data"]),
            mime_type=first_image.get("mime", "image/jpeg"),
        )
        applied = photo_analyzer.apply_suggestions(
            suggestions, st.session_state, overwrite=False
        )

        # Stakeholder ask: "populate with MC to keep it realistic". After the
        # photo_analyzer has filled VISUAL fields, look up the closest item in
        # the Marie Lund catalog and use its brand-convention spec (composition,
        # measurements, size range) to fill the CONSTRUCTION fields the photo
        # can't reveal. The user only edits what's actually different.
        brand_applied = {}
        brand_debug = {}
        if not market_pricing.is_available():
            brand_debug["is_available"] = False
        else:
            try:
                # Build the match query from what photo_analyzer just filled
                form_snapshot = {
                    "garment_sub_category": st.session_state.get("garment_sub_category"),
                    "color_name": st.session_state.get("color_name"),
                    "product_type": st.session_state.get("product_type"),
                }
                brand_debug["form_snapshot"] = form_snapshot
                match = market_pricing.find_similar_item(form_snapshot)
                brand_debug["matched_sku"] = match.get("sku") if match else None
                if match:
                    brand_applied = market_pricing.apply_brand_defaults(
                        match, st.session_state, overwrite=False
                    )
                    brand_debug["applied_keys"] = list(brand_applied.keys())
                else:
                    brand_debug["error"] = "find_similar_item returned None"
            except Exception as _e:
                # Stop swallowing this silently — surface the error to UI so
                # we can actually diagnose why brand defaults aren't firing.
                import traceback
                brand_debug["exception"] = f"{type(_e).__name__}: {_e}"
                brand_debug["traceback"] = traceback.format_exc()

        st.session_state["_ai_autofill_result"] = {
            "applied": applied,
            "brand_applied": brand_applied,
            "brand_debug": brand_debug,
            "raw": suggestions,
        }
        # Bump the measurements-editor version so the data_editor picks up
        # the new measurements we just wrote into session_state. Without this
        # the data_editor keeps its own widget state and ignores our update.
        if isinstance(brand_applied.get("measurements"), dict) and brand_applied["measurements"]:
            st.session_state["_measurements_version"] = (
                st.session_state.get("_measurements_version", 0) + 1
            )
        # Unlock the rest of the form so the user can review what AI filled.
        st.session_state["_form_unlocked"] = True
    except Exception as e:
        st.session_state["_ai_autofill_result"] = {"error": str(e)}


def unlock_form_callback():
    """on_click for 'Skip AI — fill manually'. Just opens up the rest of the form."""
    st.session_state["_form_unlocked"] = True


def relock_form_callback():
    """on_click for 'Start over with a new photo'. Re-hides the rest of the form."""
    st.session_state["_form_unlocked"] = False


def collect_data() -> dict:
    """Pull all form values from session_state into a single dict for export."""
    fields = [
        "product_type", "garment_sub_category", "style_name", "style_number", "season", "gender",
        "fit", "size_range", "color_name", "pantone_code", "composition",
        "yarn_type", "yarn_count", "gauge", "knit_structure", "rib_structure",
        "fabric_structure", "fabric_weight_gsm", "dye_method",
        "neckline", "neckline_rib_cm", "sleeve_length", "sleeve_type",
        "hem_style", "hem_height_cm", "cuff_style", "cuff_height_cm",
        "placket", "placket_interlining", "button_size_l", "button_count", "button_material",
        "button_color", "print_embroidery", "wash_finishing", "stitching_type",
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
        "🆕 Start new",
        on_click=reset_to_blank,
        use_container_width=True,
        help=(
            "Clear all fields, photos, and the AI sketch so you can build a "
            "fresh tech pack. The previous state is kept in Undo."
        ),
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
    # Header on the left, market-pricing reference card on the right.
    # Stakeholder asked for a "top-right tool" — Streamlit doesn't do floating
    # widgets, so we approximate with a two-column header row.
    # Show a brief confirmation after a reset (set by reset_to_blank).
    # Use pop() so it disappears on the next render.
    if st.session_state.pop("_just_reset", False):
        st.success("✅ Cleared. Ready for the next tech pack — drop a new photo above.")

    _hdr_col, _mkt_col = st.columns([3, 1])
    with _hdr_col:
        _title_col, _new_col = st.columns([3, 2])
        _title_col.header("Build your tech pack")
        # Prominent "start a new tech pack" button so customers don't have to
        # hunt for it in the sidebar. Undo (snapshot) still catches accidents.
        _new_col.button(
            "🆕 Start a new tech pack",
            on_click=reset_to_blank,
            use_container_width=True,
            help=(
                "Clears everything (photo, form fields, AI sketch) so you can "
                "build the next tech pack. The previous state is kept in Undo — "
                "click '↩ Undo' in the sidebar to restore if needed."
            ),
        )

        # Reference photo preview — keeps the garment the user is spec'ing
        # in view while they scroll through the form. Helps catch mistakes
        # like "wait, this isn't the right photo" without scrolling back to
        # the upload section. Skip AI-generated images here — they have
        # their own dedicated display in the Technical Drawing area.
        _ref_img = next(
            (i for i in (st.session_state.get("images") or [])
             if "ai_generated" not in (i.get("source") or "")), None
        )
        if _ref_img:
            _img_col, _gap_col = st.columns([2, 3])
            with _img_col:
                st.caption("📷 Your reference photo")
                st.image(to_data_url(_ref_img), use_container_width=True)
                _ai_drawing = st.session_state.get("technical_drawing")
                if _ai_drawing and _ai_drawing.get("data"):
                    st.caption("🎨 Latest AI sketch")
                    st.image(to_data_url(_ai_drawing), use_container_width=True)
    with _mkt_col:
        if market_pricing.is_available():
            # Pull what we need for matching. Category and color drive the
            # text scoring; we pass extra fields too so Vision can use them
            # as confirmed spec.
            _form_snapshot = {
                "garment_sub_category": st.session_state.get("garment_sub_category"),
                "color_name": st.session_state.get("color_name"),
                "fit": st.session_state.get("fit"),
                "sleeve_length": st.session_state.get("sleeve_length"),
                "neckline": st.session_state.get("neckline"),
            }

            # === User-configurable filters ===
            # Stakeholder ask: let customer constrain the closest-match by
            # price and material composition. Filters live above the widget
            # so the displayed match always reflects them.
            with st.expander("🔧 Filters", expanded=False):
                _price_range = st.slider(
                    "Price (USD)", 0, 200, (0, 200), step=5,
                    key="_filter_price",
                    help="Closest item must fall inside this price range.",
                )
                _materials_filter = st.multiselect(
                    "Material contains",
                    market_pricing.FILTER_MATERIALS,
                    key="_filter_materials",
                    help="Closest item must contain ALL selected fibres. "
                         "Empty = no material constraint.",
                )
                _filters_active = (
                    _price_range[0] > 0 or _price_range[1] < 200 or bool(_materials_filter)
                )
                if _filters_active and st.button("Clear filters", use_container_width=True):
                    st.session_state.pop("_filter_price", None)
                    st.session_state.pop("_filter_materials", None)
                    st.rerun()

            _filters = {
                "price_min": _price_range[0] if _price_range[0] > 0 else None,
                "price_max": _price_range[1] if _price_range[1] < 200 else None,
                "materials": _materials_filter or None,
            }

            # Reuse last vision result if user already paid for it this session
            # AND the form snapshot hasn't changed. Hash via JSON for stability.
            # Include the filter dict so applying a different filter forces
            # a fresh match instead of returning the stale vision pick.
            _snapshot_key = json.dumps({**_form_snapshot, "_filters": _filters}, sort_keys=True)
            _vision_cache = st.session_state.get("_market_vision_cache", {})
            _vision_match = _vision_cache.get(_snapshot_key)

            _match = _vision_match or market_pricing.find_similar_item(_form_snapshot, filters=_filters)

            # Handle the "no-match-after-filtering" sentinel separately —
            # show a friendly message rather than rendering a fake match.
            if _match and _match.get("_no_filter_match"):
                st.caption("💰 **Closest item selling now**")
                st.warning(
                    f"No items match the active filters within this category "
                    f"({_match.get('category_size', 0)} items in category, "
                    "0 after filters). Loosen the filters above."
                )
                _match = None  # skip the rest of the rendering block

            if _match:
                _filter_caption = ""
                if _match.get("filter_pool_size") is not None and _match.get("category_pool_size") is not None:
                    pool = _match["filter_pool_size"]
                    total = _match["category_pool_size"]
                    if pool < total:
                        _filter_caption = f" · {pool}/{total} after filters"
                st.caption(f"💰 **Closest item selling now**{_filter_caption}")
                if _match.get("image_url"):
                    try:
                        st.image(_match["image_url"], use_container_width=True)
                    except Exception:
                        pass
                st.metric("Price", f"${_match['price_usd']:.2f}")
                _color_label = _match.get("color") or "—"
                st.caption(f"{_color_label} · {_match['match_reason']}")
                if _match.get("url"):
                    st.markdown(f"[🔗 View on PNC]({_match['url']})")

                # Vision-refine button — only show if not already refined for
                # this snapshot AND there's a photo + category to work with.
                _first_user_image = next(
                    (i for i in (st.session_state.get("images") or [])
                     if "ai_generated" not in (i.get("source") or "")), None
                )
                _can_refine = (
                    _form_snapshot["garment_sub_category"]
                    and _first_user_image
                    and not _vision_match
                )
                if _can_refine:
                    if st.button(
                        "🔬 Refine with AI Vision",
                        use_container_width=True,
                        help="Sends your photo + top text candidates to Gemini "
                             "Vision to pick the most visually similar one. "
                             "Takes 5-10 seconds and uses some API quota.",
                    ):
                        with st.spinner("AI Vision comparing your photo to top candidates…"):
                            _refined = market_pricing.find_similar_item_vision(
                                _form_snapshot,
                                user_photo_data=_first_user_image["data"],
                                user_photo_mime=_first_user_image.get("mime", "image/jpeg"),
                                filters=_filters,
                            )
                        if _refined:
                            _vision_cache[_snapshot_key] = _refined
                            st.session_state["_market_vision_cache"] = _vision_cache
                            st.rerun()

                _meta = market_pricing.get_metadata()
                _fx = _match.get("fx_rate")
                _src = f"PNC, {_meta['pulled_on']}" if _meta.get("pulled_on") else "PNC"
                _method = "AI Vision" if _match.get("match_method") == "vision" else "text match"
                if _fx:
                    st.caption(f"_Source: {_src} · matched by {_method} · USD via EUR×{_fx:.2f}_")
                else:
                    st.caption(f"_Source: {_src} · matched by {_method}_")
            else:
                st.caption("💰 **Closest item selling now**")
                st.caption("Pick a *garment sub-category* (and optionally a *color*) above to see the closest match in our backend catalog.")

    form_unlocked = st.session_state.get("_form_unlocked", False)

    # === Always-visible: Image upload + AI auto-fill ===
    # The rest of the form is hidden until the user either lets AI analyze the
    # photo or clicks "skip and fill manually". Keeps first impressions clean.
    images = st.session_state.get("images") or []
    ai_ready = photo_analyzer.is_configured()
    has_image = bool(images)

    expander_label = (
        "📷 Step 1 — Upload a reference photo"
        if not form_unlocked
        else "📷 Images & References"
    )
    with st.expander(expander_label, expanded=True):
        st.caption(
            "Drop a photo of the garment (a product shot, mood board, sketch — "
            "anything works). Then click **🔍 Auto-fill from first photo** to "
            "let AI pre-fill the form, or skip and fill it in yourself."
            if not form_unlocked else
            "Upload reference photos, technical drawings, mood boards, etc. "
            "Images are auto-resized to 800 px and compressed before saving."
        )

        # Versioned key: each reset_to_blank bumps _uploader_version, which
        # changes this key and forces Streamlit to rebuild the widget clean.
        # Without this, "Start a new tech pack" leaves the old filename
        # showing in the uploader.
        _uploader_key = f"_image_uploader_{st.session_state.get('_uploader_version', 0)}"
        uploaded_files = st.file_uploader(
            "Drop images here or click to browse",
            accept_multiple_files=True,
            type=["png", "jpg", "jpeg", "webp", "gif"],
            key=_uploader_key,
            label_visibility="collapsed",
        )
        if uploaded_files:
            already_added = st.session_state.get("_uploaded_filenames", set())
            for f in uploaded_files:
                fkey = f"{f.name}::{f.size}"
                if fkey not in already_added:
                    try:
                        add_image(f.getvalue(), f.name)
                        already_added.add(fkey)
                    except Exception as e:
                        st.error(f"Couldn't add {f.name}: {e}")
            st.session_state["_uploaded_filenames"] = already_added

        # Refresh — uploads above may have added a new image.
        images = st.session_state.get("images") or []
        has_image = bool(images)

        # === AUTO-TRIGGER photo analysis when a NEW first image arrives ===
        # Customer-facing app: people won't think to click "Auto-fill". We
        # detect new uploads by tracking image IDs we've already analyzed.
        # We do TWO things in this pass:
        #   1. photo_analyzer to fill the form dropdowns (sub-category, fit…)
        #   2. describe_for_sketch to produce a plain-text reading of the
        #      photo that the user can later review and edit before
        #      generating the sketch.
        # Find the first true REFERENCE image — skip captures and AI outputs.
        _reference_imgs = [
            i for i in images
            if "ai_generated" not in (i.get("source") or "")
            and (i.get("source") or "") != "captured"
        ]
        if ai_ready and _reference_imgs:
            _first_id = _reference_imgs[0].get("id")
            _analyzed_ids = st.session_state.get("_autoanalyzed_ids", set())
            if _first_id and _first_id not in _analyzed_ids:
                with st.spinner("🔍 AI is reading your photo to pre-fill the form…"):
                    ai_autofill_callback()
                    try:
                        _first = _reference_imgs[0]
                        _desc = describe_for_sketch(
                            _first["data"],
                            _first.get("mime", "image/jpeg"),
                        )
                        if _desc:
                            st.session_state["_photo_description"] = _desc
                    except Exception:
                        # Non-fatal — description editor will just be empty
                        pass

                    # Check the approved-sketch cache: if this exact photo
                    # already has a sketch the user marked as approved in a
                    # past session, auto-load it instead of making the user
                    # regenerate. Saves 25+ seconds and an API call.
                    if firestore_client.is_configured():
                        try:
                            _ph = firestore_client.compute_photo_hash(_reference_imgs[0]["data"])
                            _saved = firestore_client.load_approved_sketch(_ph)
                            if _saved and _saved.get("data"):
                                st.session_state["technical_drawing"] = {
                                    "id": _reference_imgs[0]["id"] + "_approved",
                                    "data": _saved["data"],
                                    "mime": _saved.get("mime") or "image/png",
                                    "caption": _saved.get("caption") or "✅ Loaded from your approved version for this photo",
                                    "source": "ai_generated_approved",
                                    "photo_description": _saved.get("photo_description") or "",
                                    "_approved": True,
                                    "_photo_hash": _ph,
                                }
                                # Match the description editor too
                                if _saved.get("photo_description"):
                                    st.session_state["_photo_description"] = _saved["photo_description"]
                        except Exception:
                            pass

                st.session_state["_autoanalyzed_ids"] = _analyzed_ids | {_first_id}
                st.rerun()

        if not ai_ready:
            ai_help = "🔒 AI auto-fill is off until a Gemini key is added to secrets."
        elif not has_image:
            ai_help = "Upload a photo first — AI will read it automatically and pre-fill the form."
        else:
            ai_help = (
                "AI reads the first uploaded photo automatically when you upload it. "
                "Click this button to re-trigger (e.g. after changing the first image). "
                "Only empty fields get filled — your existing selections aren't overwritten."
            )

        ai_col_a, ai_col_b = st.columns([2, 3])
        ai_col_a.button(
            "🔍 Re-analyze first photo",
            on_click=ai_autofill_callback,
            disabled=(not ai_ready) or (not has_image),
            use_container_width=True,
            help=ai_help,
        )
        ai_col_b.caption(ai_help)

        # Surface the last AI run result.
        last = st.session_state.pop("_ai_autofill_result", None)
        if last:
            if last.get("error"):
                st.error(f"❌ AI couldn't analyze the photo: {last['error']}")
            elif last.get("applied") or last.get("brand_applied"):
                # Two distinct sources: photo-vision (visual fields) and
                # MC brand defaults (composition / measurements / sizes).
                # Show them separately so the user knows which signals came
                # from where.
                lines = []
                applied = last.get("applied") or {}
                if applied:
                    lines.append(f"**From the photo** ({len(applied)} field"
                                 f"{'s' if len(applied) != 1 else ''}):")
                    for k, v in applied.items():
                        lines.append(f"- {k.replace('_', ' ').title()}: {v}")
                brand_applied = last.get("brand_applied") or {}
                if brand_applied:
                    if lines:
                        lines.append("")
                    lines.append(
                        f"**From the closest Marie Lund item** "
                        f"({len(brand_applied)} field"
                        f"{'s' if len(brand_applied) != 1 else ''} pre-filled):"
                    )
                    for k, v in brand_applied.items():
                        if k == "measurements" and isinstance(v, dict):
                            preview = ", ".join(f"{n} {val}" for n, val in v.items())
                            lines.append(f"- Measurements: {preview}")
                        else:
                            lines.append(f"- {k.replace('_', ' ').title()}: {v}")
                total = len(applied) + len(brand_applied)
                st.success(
                    f"✅ AI filled in {total} field"
                    f"{'s' if total != 1 else ''}. Scroll down to review and adjust.\n\n"
                    + "\n".join(lines)
                )
            else:
                st.info(
                    "AI didn't have high-confidence suggestions for any empty field "
                    "(or everything was already filled in)."
                )
            if last.get("raw", {}).get("confidence_notes"):
                st.caption(f"AI notes: _{last['raw']['confidence_notes']}_")
            # Surface brand-defaults debug info so we can see what happened
            # when the brand-defaults block didn't fire as expected.
            _bd = last.get("brand_debug") or {}
            if _bd:
                with st.expander("🔧 Brand-defaults debug (what the MC lookup did)"):
                    st.json(_bd)

        # Image list with per-image controls.
        # Skip "captured" photos here — they live in the Quick photo capture
        # expander below and shouldn't clutter the reference-photo list.
        _ref_only_images = [i for i in images if (i.get("source") or "") != "captured"]
        if not _ref_only_images:
            st.info("No reference images yet. Drop one onto the uploader above.")
        else:
            st.markdown(f"**{len(_ref_only_images)}** image{'s' if len(_ref_only_images) != 1 else ''}")
            for idx, img in enumerate(_ref_only_images):
                cols = st.columns([1, 4, 1, 1, 1])
                cols[0].image(to_data_url(img), width=110)
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
                    disabled=(idx == len(_ref_only_images) - 1),
                )
                cols[4].button(
                    "🗑️",
                    key=f"_del_{img['id']}",
                    on_click=delete_image,
                    args=(img["id"],),
                    use_container_width=True,
                    help="Remove this image",
                )

    # === Camera capture — separate workflow, NOT tied to AI analysis ===
    # Stakeholder ask: a quick-camera widget so customers can capture photos
    # in-app and keep them with the tech pack for later discussion. No AI
    # analysis on these — they're for record-keeping only.
    with st.expander("📸 Quick photo capture (for discussion, not AI)", expanded=False):
        st.caption(
            "Take a photo with the device camera. Saved with the tech pack so you can "
            "come back to it, but **not** sent to AI for analysis. Useful for capturing "
            "physical samples, swatches, factory details, etc. that you want to discuss "
            "later but don't want auto-classified."
        )

        # Versioned key so once a photo is saved, the widget resets clean
        # instead of holding onto the last shot forever.
        _cam_version = st.session_state.get("_camera_version", 0)
        _cam_key = f"_camera_input_{_cam_version}"
        captured = st.camera_input(
            "📷 Tap to open camera",
            key=_cam_key,
            label_visibility="visible",
        )
        if captured is not None:
            import hashlib as _h
            _bytes = captured.getvalue()
            _hash = _h.md5(_bytes).hexdigest()[:12]
            _processed = st.session_state.get("_processed_captures", set())
            if _hash not in _processed:
                from datetime import datetime as _dt
                _ts = _dt.now().strftime("%Y-%m-%d %H:%M")
                entry = make_image_entry(
                    _bytes,
                    filename=f"capture_{_hash}.jpg",
                    caption=f"Captured {_ts}",
                )
                entry["source"] = "captured"
                st.session_state["images"] = (st.session_state.get("images") or []) + [entry]
                _processed.add(_hash)
                st.session_state["_processed_captures"] = _processed
                # Bump version so camera_input widget rebuilds clean next render.
                st.session_state["_camera_version"] = _cam_version + 1
                st.rerun()

        # Dedicated list of captured photos with delete controls
        _captures = [i for i in (st.session_state.get("images") or [])
                     if (i.get("source") or "") == "captured"]
        if _captures:
            st.markdown(
                f"**{len(_captures)} captured photo{'s' if len(_captures) != 1 else ''}** "
                "(saved with the tech pack)"
            )
            for cap_img in _captures:
                cc = st.columns([2, 4, 1])
                cc[0].image(to_data_url(cap_img), use_container_width=True)
                cc[1].markdown(f"**{cap_img.get('caption', '—')}**")
                cc[1].caption(f"~{approximate_size_kb(cap_img):.0f} KB · id `{cap_img['id']}`")
                if cc[2].button("🗑️", key=f"_del_cap_{cap_img['id']}",
                                use_container_width=True,
                                help="Remove this captured photo"):
                    st.session_state["images"] = [
                        i for i in st.session_state["images"] if i["id"] != cap_img["id"]
                    ]
                    st.rerun()
        else:
            st.caption("_No captured photos yet — tap the camera button above to take one._")

    # === Gate: rest of the form only shows once unlocked ===
    if not form_unlocked:
        st.divider()
        gate_col_a, gate_col_b = st.columns([1, 3])
        gate_col_a.button(
            "Skip AI — fill manually",
            on_click=unlock_form_callback,
            use_container_width=True,
        )
        gate_col_b.caption(
            "No photo? Click **Skip AI** to open the empty form and fill it in yourself."
        )
        st.stop()

    # The user either unlocked manually or AI just ran. Give them a clear
    # exit if they want to restart with a different photo.
    unlock_cols = st.columns([3, 1])
    unlock_cols[0].caption(
        "✏️ Form unlocked. Review what's below — AI suggestions are pre-filled "
        "where confident, dropdowns marked _— Not specified —_ are still empty."
    )
    unlock_cols[1].button(
        "🔄 Start over",
        on_click=relock_form_callback,
        use_container_width=True,
        help="Re-hides the form below. Doesn't delete anything — your photo and fields stay.",
    )

    st.caption(
        "Every dropdown has a **— Not specified —** option at the top — leave it "
        "there if you don't want to lock that detail down yet."
    )
    is_knitwear = st.session_state.get("product_type", PRODUCT_TYPES[0]).startswith("Knitwear")

    # --- Section 1: Style Overview ---
    st.divider()
    st.header("1. Style Overview")
    # Sub-category is conditional on product type — show only the relevant
    # set. Categories were derived from the customer's actual brand catalog
    # (Marie Lund / similar P&C house brands).
    sub_options = KNITWEAR_SUB_CATEGORIES if is_knitwear else TSHIRT_SUB_CATEGORIES
    sub_opts_blank = with_blank(sub_options)
    c1, c2 = st.columns([1, 2])
    c1.selectbox(
        "Garment sub-category",
        sub_opts_blank,
        index=safe_index(sub_opts_blank, st.session_state.get("garment_sub_category")),
        key="garment_sub_category",
        help="Specific style within the broader product type. Pulled from the customer's own brand catalog.",
    )
    c2.caption(
        "💡 Sub-categories follow the customer's brand taxonomy "
        f"(e.g. {sub_options[0]}, {sub_options[1]}, {sub_options[2]}, ...)"
    )

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
    # Composition uses dynamic options: brand defaults often write non-standard
    # values like "67% Cotton / 33% Polyester" or "2/26Nm 100% RWS Wool" that
    # aren't in COMPOSITIONS. If the current value isn't in the standard list,
    # append it so the selectbox can render it.
    opts = with_blank(COMPOSITIONS)
    _cur_comp = st.session_state.get("composition")
    if _cur_comp and _cur_comp not in opts:
        opts = opts + [_cur_comp]
    c3.selectbox("Composition", opts, index=safe_index(opts, _cur_comp), key="composition")

    # --- AI Technical Drawing section ---
    st.divider()
    st.header("🎨 Technical Drawing (AI-generated)")
    ai_cols = st.columns([3, 1])
    ai_cols[0].caption(
        "Click the button below — AI reads the fields you've filled in "
        "above and generates a flat technical sketch (front + back view). "
        "Use this in place of a hand-drawn tech illustration."
    )
    if ai_demo_mode():
        ai_cols[1].caption("🧪 **Demo mode** — placeholder output")

    # Editable photo description — what AI reads from the photo. Lets the
    # user fix any misreadings (wrong button count, wrong sleeve length)
    # BEFORE generating, instead of regenerating + adjusting in a loop.
    _has_user_photo = any(
        ("ai_generated" not in (i.get("source") or ""))
        for i in (st.session_state.get("images") or [])
    )
    if _has_user_photo and ai_ready:
        with st.expander("📝 What AI sees in your photo — edit before generating", expanded=True):
            st.caption(
                "AI's plain-text reading of your photo. The sketch is generated from "
                "this description, so editing here is the fastest way to correct any "
                "misreading (wrong button count, wrong length, wrong knit pattern). "
                "Leave blank to have AI re-read from scratch on next generation."
            )
            st.text_area(
                "Photo description (used for sketch generation)",
                value=st.session_state.get("_photo_description", ""),
                key="_photo_description_edit",
                height=180,
                label_visibility="collapsed",
            )

    # Generate / re-generate button
    existing_drawing = st.session_state.get("technical_drawing")

    def _do_generate():
        try:
            data_now = collect_data()
            # Pass the user-edited description (if any) to override the
            # auto-generated one inside generate_drawing.
            edited_desc = (st.session_state.get("_photo_description_edit") or "").strip()
            if edited_desc:
                data_now["_photo_description_override"] = edited_desc
            drawing = generate_drawing(data_now)
            st.session_state["technical_drawing"] = drawing
            st.session_state["_drawing_status"] = ("success", None)
            # Keep _photo_description in sync with what was actually used so
            # the editor shows the latest version next render.
            if drawing.get("photo_description"):
                st.session_state["_photo_description"] = drawing["photo_description"]
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

    # Define callbacks for the approve / un-approve buttons. Need closures
    # over the existing_drawing so they can read its photo hash.
    def _do_approve():
        # Hash the current first user photo so we can store the drawing
        # against it. Re-uploading the same photo later will auto-load.
        imgs = st.session_state.get("images") or []
        ref = next((i for i in imgs if "ai_generated" not in (i.get("source") or "")), None)
        drawing = st.session_state.get("technical_drawing")
        if not (ref and drawing and firestore_client.is_configured()):
            return
        ph = firestore_client.compute_photo_hash(ref["data"])
        firestore_client.save_approved_sketch(ph, drawing)
        drawing["_approved"] = True
        drawing["_photo_hash"] = ph
        st.session_state["technical_drawing"] = drawing
        st.session_state["_approval_status"] = ("saved", None)

    def _do_unapprove():
        drawing = st.session_state.get("technical_drawing") or {}
        ph = drawing.get("_photo_hash")
        if ph and firestore_client.is_configured():
            firestore_client.delete_approved_sketch(ph)
        # Keep the drawing on screen but clear the approval flag
        drawing.pop("_approved", None)
        drawing.pop("_photo_hash", None)
        st.session_state["technical_drawing"] = drawing
        st.session_state["_approval_status"] = ("unapproved", None)

    # Display the current drawing
    if existing_drawing:
        disp_cols = st.columns([2, 3])
        with disp_cols[0]:
            st.image(to_data_url(existing_drawing), use_container_width=True)
            st.caption(existing_drawing.get("caption") or "—")

            # === Approve / un-approve state ===
            # Only available when Firestore is configured. Without it we have
            # nowhere to persist the approval.
            if firestore_client.is_configured():
                _approval_status = st.session_state.pop("_approval_status", None)
                if _approval_status:
                    if _approval_status[0] == "saved":
                        st.success("✅ Saved as the approved version for this photo.")
                    elif _approval_status[0] == "unapproved":
                        st.info("Un-approved. You can mark a different generation later.")

                if existing_drawing.get("_approved"):
                    st.success(
                        "✅ **This drawing is the approved version for this photo.** "
                        "It will auto-appear next time the same photo is uploaded."
                    )
                    st.button(
                        "🔄 Un-approve (allows a fresh generation)",
                        on_click=_do_unapprove,
                        use_container_width=True,
                    )
                else:
                    st.button(
                        "👍 Looks good — save as the approved version",
                        on_click=_do_approve,
                        use_container_width=True,
                        type="primary",
                        help=(
                            "Saves this drawing keyed to your reference photo. "
                            "Re-uploading the same photo later (any session, any "
                            "device) will auto-load this exact sketch instead of "
                            "re-running the AI pipeline."
                        ),
                    )

        with disp_cols[1]:
            # AI self-critique transparency. If a critique was run, show what
            # the second pass spotted and whether it triggered a re-draw.
            # Demystifies what the AI is doing under the hood.
            _crit = existing_drawing.get("critique")
            if _crit:
                _devs = _crit.get("deviations") or []
                _faithful = _crit.get("faithful", True)
                if _devs and not _faithful:
                    _crit_label = f"🔬 AI self-critique — re-drew to fix {len(_devs)} issue(s)"
                    _expanded = True
                else:
                    _crit_label = "🔬 AI self-critique — first pass matched"
                    _expanded = False
                with st.expander(_crit_label, expanded=_expanded):
                    if _devs and not _faithful:
                        st.markdown("**Issues the AI spotted in its first sketch and re-drew to fix:**")
                        for d in _devs:
                            st.markdown(f"- {d}")
                        if _crit.get("correction_note"):
                            st.caption(f"_Summary: {_crit['correction_note']}_")
                    elif _devs:
                        # Faithful overall but had minor notes
                        st.markdown("**Minor observations (not re-drawn):**")
                        for d in _devs:
                            st.markdown(f"- {d}")
                    else:
                        st.markdown(
                            "First-pass sketch matched the photo and description "
                            "well enough — no second pass needed."
                        )
            with st.expander("🔍 View AI prompt that was used", expanded=False):
                st.code(existing_drawing.get("prompt") or "(no prompt recorded)", language="text")

    # --- Section 2: Construction (Conditional on Product Type) ---
    st.divider()
    st.header("2. Construction — Material & Knit/Fabric")
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
    st.divider()
    st.header("3. Construction — Style Details")
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
        help="Set-in = standard. Raglan = diagonal seam. Dropped Shoulder = relaxed.",
    )

    c1, c2, c3, c4 = st.columns(4)
    opts = with_blank(HEM_STYLES)
    c1.selectbox(
        "Hem style", opts,
        index=safe_index(opts, st.session_state.get("hem_style")),
        key="hem_style",
        help=(
            "'Ribbed hem' = a SEPARATE ribbed band at the bottom (e.g. 3 cm strip "
            "of tighter ribbing). If the body fabric itself is ribbed all over and "
            "just ends without a separate band, pick 'Clean finish' instead."
        ),
    )
    c2.number_input("Hem rib height (cm)", min_value=0.0, max_value=20.0, step=0.5, key="hem_height_cm")
    opts = with_blank(CUFF_STYLES)
    c3.selectbox(
        "Cuff style", opts,
        index=safe_index(opts, st.session_state.get("cuff_style")),
        key="cuff_style",
        help=(
            "'Ribbed cuff' = a SEPARATE ribbed band at the wrist. If the sleeve "
            "fabric itself is ribbed and just ends at the wrist without a separate "
            "tighter band, pick 'Clean finish' instead."
        ),
    )
    c4.number_input("Cuff rib height (cm)", min_value=0.0, max_value=20.0, step=0.5, key="cuff_height_cm")

    c1, c2 = st.columns(2)
    opts = with_blank(PLACKETS)
    c1.selectbox("Placket / closure", opts, index=safe_index(opts, st.session_state.get("placket")), key="placket")
    opts = with_blank(PLACKET_INTERLINING)
    c2.selectbox(
        "Placket interlining / construction", opts,
        index=safe_index(opts, st.session_state.get("placket_interlining")),
        key="placket_interlining",
        help="How the placket is built — fabric type + interlining layer.",
    )

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
    opts = with_blank(STITCHING_TYPES)
    c3.selectbox(
        "Stitching / construction", opts,
        index=safe_index(opts, st.session_state.get("stitching_type")),
        key="stitching_type",
        help="How the panels are joined. 'Standard knitwear construction' is the customer's default.",
    )

    st.checkbox("Shoulder reinforcement required", key="shoulder_reinforcement")

    # --- Section 4: Measurements ---
    st.divider()
    st.header("4. Measurements (Base size)")
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

    # Versioned key so external writes to st.session_state["measurements"]
    # (e.g. apply_brand_defaults filling from a matched MC item) actually
    # show up in the editor. Without this, the data_editor's internal
    # widget state takes precedence once it's been rendered, and our
    # programmatic update is silently ignored.
    _meas_version = st.session_state.get("_measurements_version", 0)
    _meas_key = f"measurements_editor_{_meas_version}"
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
        key=_meas_key,
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
    st.divider()
    st.header("5. Labels & Packing")
    c1, c2 = st.columns(2)
    c1.multiselect("Labels to include", LABEL_TYPES, default=st.session_state.get("labels", []), key="labels")
    opts = with_blank(PACKING)
    c2.selectbox("Packing method", opts, index=safe_index(opts, st.session_state.get("packing")), key="packing")

    # --- Section 6: Supplier Actions ---
    st.divider()
    st.header("6. Supplier Actions Required")
    st.multiselect(
        "What we need from the supplier",
        SUPPLIER_ACTIONS,
        default=st.session_state.get("supplier_actions", []),
        key="supplier_actions",
    )

    # --- Section 7: Commercial ---
    st.divider()
    st.header("7. Commercial Information")
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
    cols[1].markdown(f"**Placket interlining:** {data.get('placket_interlining') or '—'}")
    if "button" in (data.get("placket") or "").lower():
        cols = st.columns(2)
        cols[0].markdown(
            f"**Buttons:** {data.get('button_count') or 0} × {data.get('button_size_l') or '—'} "
            f"{data.get('button_material') or ''} ({data.get('button_color') or ''})"
        )
    cols = st.columns(3)
    cols[0].markdown(f"**Print / Embroidery:** {data.get('print_embroidery') or '—'}")
    cols[1].markdown(f"**Wash / Finishing:** {data.get('wash_finishing') or '—'}")
    cols[2].markdown(f"**Stitching:** {data.get('stitching_type') or '—'}")
    cols = st.columns(3)
    cols[0].markdown(f"**Shoulder Reinforcement:** {'Yes' if data.get('shoulder_reinforcement') else 'No'}")

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
