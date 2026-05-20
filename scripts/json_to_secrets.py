"""
Convert a Firebase service account JSON file into a Streamlit secrets.toml block.

Usage:
    python3 scripts/json_to_secrets.py <path-to-firebase-key.json>

Behaviour:
    - Writes/updates .streamlit/secrets.toml in the project root
    - Also prints a TOML block to stdout so you can paste it into Streamlit
      Cloud's app settings → Secrets section.

The script never sends or uploads anything — it just rewrites a local file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED_KEYS = (
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "client_id", "auth_uri", "token_uri",
    "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain",
)


def to_toml_block(creds: dict) -> str:
    """Serialise a service account dict into a TOML block."""
    lines = ["[firebase_service_account]"]
    for key in EXPECTED_KEYS:
        if key not in creds:
            continue
        value = creds[key]
        if key == "private_key":
            # Multi-line PEM key — TOML supports triple-quoted strings.
            lines.append(f'{key} = """{value}"""')
        else:
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/json_to_secrets.py <firebase-key.json>")
        return 1

    json_path = Path(sys.argv[1]).expanduser()
    if not json_path.exists():
        print(f"File not found: {json_path}")
        return 1

    with open(json_path, "r", encoding="utf-8") as f:
        creds = json.load(f)

    if creds.get("type") != "service_account":
        print(f"This doesn't look like a Firebase service account key "
              f"(type={creds.get('type')!r}).")
        return 1

    toml_block = to_toml_block(creds)

    # Write/replace .streamlit/secrets.toml in the project root
    project_root = Path(__file__).resolve().parent.parent
    secrets_path = project_root / ".streamlit" / "secrets.toml"
    secrets_path.parent.mkdir(exist_ok=True)
    secrets_path.write_text(toml_block, encoding="utf-8")

    print(f"✅ Wrote {secrets_path}")
    print(f"   Project ID: {creds.get('project_id')}")
    print(f"   Client email: {creds.get('client_email')}")
    print()
    print("───────────────────────────────────────────────────────────────")
    print("To deploy on Streamlit Cloud, copy the block below into")
    print("app settings → Secrets:")
    print("───────────────────────────────────────────────────────────────")
    print(toml_block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
