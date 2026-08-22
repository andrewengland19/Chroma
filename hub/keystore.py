# =============================================================================
# hub/keystore.py  —  Pass 4.6
# Resolve the Anthropic API key without ever putting it in config.json or git:
#   1. ANTHROPIC_API_KEY env var (override / CI), else
#   2. macOS login Keychain: security find-generic-password -s chroma-hub -a anthropic
# Store it with `lite show setkey` (which shells out to `security add-generic-password`).
# =============================================================================

import os
import subprocess

_SERVICE = "chroma-hub"
_ACCOUNT = "anthropic"


def load_anthropic_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", _SERVICE, "-a", _ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            k = out.stdout.strip()
            return k or None
    except Exception:
        pass
    return None


def store_anthropic_key(key: str) -> bool:
    """Write the key to the macOS login Keychain (-U updates if it exists)."""
    key = (key or "").strip()
    if not key:
        return False
    try:
        r = subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", _SERVICE, "-a", _ACCOUNT, "-w", key],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def has_anthropic_key() -> bool:
    return load_anthropic_key() is not None
