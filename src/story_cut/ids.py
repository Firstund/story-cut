from __future__ import annotations

import secrets


def make_id(prefix: str) -> str:
    """OpenReel portable-edit IDs: [a-zA-Z0-9_.:-]{1,200}."""
    safe = prefix.strip().replace(" ", "_") or "id"
    return f"{safe}_{secrets.token_hex(6)}"
