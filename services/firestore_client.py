"""
Firestore client for saving / loading tech pack records.
========================================================

Data model
----------
Collection: ``tech_packs``
Document fields:
    - name           : str  - style name for display in the list
    - style_number   : str
    - product_type   : str  - "Knitwear (Sweater / Cardigan)" or "T-shirt / Jersey"
    - season         : str
    - data           : dict - the full tech pack payload (everything from collect_data())
    - created_at     : timestamp
    - updated_at     : timestamp

Authentication
--------------
Uses a Firebase service account. Credentials live in ``st.secrets`` under the
key ``firebase_service_account``. When running locally, put them in
``.streamlit/secrets.toml`` (which is gitignored).

The TOML key is ``[firebase_service_account]`` with all fields from the
downloaded service account JSON pasted in.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import streamlit as st

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False


COLLECTION = "tech_packs"

# Approved technical-drawing cache. Keyed by photo content hash so that
# re-uploading the same reference photo auto-returns the previously approved
# sketch instead of re-running the (slow, costly) AI pipeline.
APPROVED_COLLECTION = "approved_sketches"


# =============================================================================
# CLIENT BOOTSTRAP
# =============================================================================

@st.cache_resource(show_spinner=False)
def _get_client():
    """Return a Firestore client, lazily initialized once per session."""
    if not FIREBASE_AVAILABLE:
        raise RuntimeError(
            "firebase-admin is not installed. "
            "Add 'firebase-admin' to requirements.txt and reinstall."
        )

    if "firebase_service_account" not in st.secrets:
        raise RuntimeError(
            "Firebase credentials are missing. Add a [firebase_service_account] "
            "block to .streamlit/secrets.toml (local) or Streamlit Cloud secrets."
        )

    if not firebase_admin._apps:
        cred_dict = dict(st.secrets["firebase_service_account"])
        # Streamlit's TOML escapes newlines in the private_key — restore them.
        if "private_key" in cred_dict:
            cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)

    return firestore.client()


def is_configured() -> bool:
    """Quick check used by the UI to decide whether to show the History tab."""
    if not FIREBASE_AVAILABLE:
        return False
    try:
        return "firebase_service_account" in st.secrets
    except Exception:
        return False


# =============================================================================
# CRUD
# =============================================================================

def save_tech_pack(data: dict, doc_id: str | None = None) -> str:
    """Save a tech pack. Creates a new doc if ``doc_id`` is None, otherwise updates.

    Returns the document id (existing or newly created).
    Raises ValueError if the payload would exceed Firestore's 1 MB limit
    (images are the most common cause — user should remove one or more photos).
    """
    client = _get_client()
    payload = {
        "name": data.get("style_name") or "Untitled",
        "style_number": data.get("style_number") or "",
        "product_type": data.get("product_type") or "",
        "season": data.get("season") or "",
        "data": _sanitize_for_firestore(data),
        "updated_at": firestore.SERVER_TIMESTAMP,
    }

    # Guard: Firestore documents are hard-capped at 1 MB. We warn at 900 KB
    # so the error message reaches the user before the write is rejected.
    _payload_size = len(json.dumps(payload, default=str).encode())
    if _payload_size > 900_000:
        raise ValueError(
            f"Tech pack is too large to save ({_payload_size // 1024} KB — limit is ~900 KB). "
            "Remove one or more reference photos and try again."
        )

    if doc_id:
        client.collection(COLLECTION).document(doc_id).set(payload, merge=True)
        return doc_id
    else:
        payload["created_at"] = firestore.SERVER_TIMESTAMP
        ref = client.collection(COLLECTION).document()
        ref.set(payload)
        return ref.id


def list_tech_packs(limit: int = 100) -> list[dict]:
    """Return tech packs ordered by most recently updated.

    Each entry has: id, name, style_number, product_type, season,
    updated_at, created_at (no full data payload, keeps the list light).
    """
    client = _get_client()
    docs = (
        client.collection(COLLECTION)
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    out = []
    for d in docs:
        rec = d.to_dict()
        out.append({
            "id": d.id,
            "name": rec.get("name", "Untitled"),
            "style_number": rec.get("style_number", ""),
            "product_type": rec.get("product_type", ""),
            "season": rec.get("season", ""),
            "updated_at": rec.get("updated_at"),
            "created_at": rec.get("created_at"),
        })
    return out


def load_tech_pack(doc_id: str) -> dict | None:
    """Load the full tech pack data for a given document id."""
    client = _get_client()
    snap = client.collection(COLLECTION).document(doc_id).get()
    if not snap.exists:
        return None
    return snap.to_dict().get("data")


def delete_tech_pack(doc_id: str) -> None:
    """Permanently delete a tech pack record."""
    client = _get_client()
    client.collection(COLLECTION).document(doc_id).delete()


# =============================================================================
# APPROVED SKETCH CACHE  (keyed by reference photo)
# =============================================================================
#
# When a user marks an AI-generated technical drawing as "approved" for a
# given reference photo, we persist (photo_hash → drawing dict) so that
# uploading the same reference again later auto-loads the approved sketch
# instead of regenerating it. Customer pattern: PNC reviews a photo, gets
# happy with the sketch, locks it in. Next time anyone uploads the same
# reference (could be a different session, different device), the
# pre-approved sketch shows up automatically.

def compute_photo_hash(image_data_b64: str) -> str:
    """Stable hash of a photo's base64 payload.

    Same photo through the same compress_to_base64 pipeline → same hash,
    even across sessions. Truncated to 24 hex chars (~96 bits) which is
    plenty for our scale and keeps the Firestore doc IDs short.
    """
    if not image_data_b64:
        return ""
    return hashlib.sha256(image_data_b64.encode("utf-8")).hexdigest()[:24]


def save_approved_sketch(photo_hash: str, drawing: dict) -> None:
    """Persist an approved AI drawing keyed by its source photo's hash.

    We only keep the small/useful subset of the drawing dict — the full
    prompt and critique JSON would bloat the doc without adding value
    on load.
    """
    if not photo_hash:
        return
    client = _get_client()
    payload = {
        "data": drawing.get("data"),
        "mime": drawing.get("mime") or "image/png",
        "caption": drawing.get("caption") or "AI-generated technical drawing",
        "photo_description": drawing.get("photo_description") or "",
        "approved_at": firestore.SERVER_TIMESTAMP,
    }
    client.collection(APPROVED_COLLECTION).document(photo_hash).set(payload)


def load_approved_sketch(photo_hash: str) -> dict | None:
    """Return the approved drawing for this photo hash, or None.

    Returns the raw Firestore doc dict — caller is responsible for wrapping
    it back into a session-state image entry shape.
    """
    if not photo_hash:
        return None
    try:
        client = _get_client()
        snap = client.collection(APPROVED_COLLECTION).document(photo_hash).get()
        if not snap.exists:
            return None
        return snap.to_dict()
    except Exception:
        return None


def delete_approved_sketch(photo_hash: str) -> None:
    """Remove the approval for this photo (user wants to redo the sketch)."""
    if not photo_hash:
        return
    try:
        client = _get_client()
        client.collection(APPROVED_COLLECTION).document(photo_hash).delete()
    except Exception:
        pass


# =============================================================================
# HELPERS
# =============================================================================

def _sanitize_for_firestore(data: dict) -> dict:
    """Firestore can't store some Python types (e.g. date). Coerce them."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, datetime):
            out[k] = v
        elif hasattr(v, "isoformat"):  # date / time objects
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _sanitize_for_firestore(v)
        elif isinstance(v, list):
            out[k] = [
                _sanitize_for_firestore(x) if isinstance(x, dict) else x
                for x in v
            ]
        else:
            out[k] = v
    return out
