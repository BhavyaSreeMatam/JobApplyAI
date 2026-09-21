"""Local settings: API key and preferences, stored outside the database and outside git."""
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import Lock

BACKEND_DIR = Path(__file__).resolve().parents[2]
SETTINGS_PATH = BACKEND_DIR / "settings.json"
_LOCK = Lock()

DEFAULTS = {
    "anthropic_api_key": "",
    "model": "claude-opus-5",
    "effort": "high",
    "ats_target_score": 85,
    "max_tailor_passes": 2,
    "generate_cover_letter": True,
    "auto_accept_agreements": False,
    # Include gap skills only when supported by the resume or confirmed profile.
    "adopt_missing_skills": True,
    # How many pages the tailored resume is allowed. One is the default for
    # industry software and AI applications; academic and senior applications
    # legitimately need more, so this is configurable rather than assumed.
    "resume_page_target": 1,
    "browser_profile_dir": "browser-profiles/default",
    # "chrome" uses your installed Chrome, which Google accepts for OAuth sign-in.
    # "msedge" or "chromium" force the alternatives.
    "browser_channel": "chrome",
    "target_roles": [],
    "search_terms": [],
    "excluded_terms": [],
    "preferred_locations": [],
}

# Keys never returned to the browser in full.
SECRET_KEYS = {"anthropic_api_key"}


def load() -> dict:
    with _LOCK:
        stored = {}
        if SETTINGS_PATH.is_file():
            try:
                stored = json.loads(SETTINGS_PATH.read_text("utf-8"))
            except (ValueError, OSError):
                stored = {}
        if not isinstance(stored, dict):
            stored = {}
        merged = {**deepcopy(DEFAULTS), **{k: v for k, v in stored.items() if k in DEFAULTS}}
    if not merged["anthropic_api_key"]:
        merged["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
    return merged


def save(changes: dict) -> dict:
    with _LOCK:
        stored = {}
        if SETTINGS_PATH.is_file():
            try:
                stored = json.loads(SETTINGS_PATH.read_text("utf-8"))
            except (ValueError, OSError):
                stored = {}
        if not isinstance(stored, dict):
            stored = {}
        for key, value in changes.items():
            if key in DEFAULTS and value is not None:
                stored[key] = value
        # Write beside the destination so replacement stays atomic. A failed
        # write must not truncate the existing API key and preferences.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=SETTINGS_PATH.parent,
                prefix=".settings-", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(stored, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(SETTINGS_PATH)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return load()


def redacted() -> dict:
    """Settings safe to send to the frontend."""
    data = load()
    out = {k: v for k, v in data.items() if k not in SECRET_KEYS}
    key = data.get("anthropic_api_key") or ""
    out["anthropic_api_key_set"] = bool(key)
    out["anthropic_api_key_hint"] = ("…" + key[-4:]) if len(key) > 8 else ""
    out["anthropic_api_key_source"] = (
        "settings" if _stored_key() else "environment" if key else "missing"
    )
    return out


def _stored_key() -> str:
    if not SETTINGS_PATH.is_file():
        return ""
    try:
        stored = json.loads(SETTINGS_PATH.read_text("utf-8"))
        return stored.get("anthropic_api_key", "") if isinstance(stored, dict) else ""
    except (ValueError, OSError):
        return ""


def browser_profile_path() -> Path:
    path = BACKEND_DIR / load()["browser_profile_dir"]
    path.mkdir(parents=True, exist_ok=True)
    return path
