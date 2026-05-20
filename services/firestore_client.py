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
