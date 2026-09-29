from __future__ import annotations

import hmac
import os


def get_token() -> str | None:
    """Return this launch's auth token from OCTARIS_TOKEN, or None if unset (dev mode)."""
    return os.environ.get("OCTARIS_TOKEN") or None


def token_is_valid(candidate: str | None) -> bool:
    """True if `candidate` matches the launch token, or if auth is disabled (dev mode)."""
    token = get_token()
    if not token:
        return True
    return hmac.compare_digest(candidate or "", token)
