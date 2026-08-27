"""
User-editable app settings (Groq API key, Tally host/port), stored in
data/settings.json so they can be entered from the Settings page in the app
itself — no .env file editing required, and no restart needed after saving.

Precedence: settings.json (set via the UI) overrides environment variables
/ .env, which are still supported as a fallback for people who prefer that.
"""
import json
import os
from .paths import get_data_dir

DEFAULTS = {
    "ai_provider": "groq",  # "groq" or "gemini" — which vision API extract_bill_with_ai routes to
    "groq_api_key": "",
    "groq_vision_model": "",  # empty = use the built-in default
    "gemini_api_key": "",
    "gemini_vision_model": "",  # empty = use the built-in default
    "tally_host": "localhost",
    "tally_port": 9000,
    # Company GST profile — used to pre-fill vouchers and, later, for
    # in-app GST reports. Purely informational at this stage: nothing in
    # the OCR/Tally push pipeline reads these yet, so saving them cannot
    # affect any existing flow.
    "company_name": "",
    "gstin": "",
    "state_code": "",
    "default_gst_rate": 18.0,
    "active_company_id": 0,  # 0 = not set yet; database.py's bootstrap picks a default on first run
}


def _settings_path() -> str:
    return os.path.join(get_data_dir(), "settings.json")


def get_settings() -> dict:
    path = _settings_path()
    saved = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)

    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in saved.items() if v not in (None, "")})

    # Env vars / .env are the fallback if nothing was saved via the UI yet.
    if not merged["groq_api_key"]:
        merged["groq_api_key"] = os.environ.get("GROQ_API_KEY", "")
    if not merged["groq_vision_model"]:
        merged["groq_vision_model"] = os.environ.get("GROQ_VISION_MODEL", "")
    if not saved.get("ai_provider"):
        merged["ai_provider"] = os.environ.get("AI_PROVIDER", "groq")
    if not merged["gemini_api_key"]:
        merged["gemini_api_key"] = os.environ.get("GEMINI_API_KEY", "")
    if not merged["gemini_vision_model"]:
        merged["gemini_vision_model"] = os.environ.get("GEMINI_VISION_MODEL", "")
    if not saved.get("tally_host"):
        merged["tally_host"] = os.environ.get("TALLY_HOST", "localhost")
    if not saved.get("tally_port"):
        merged["tally_port"] = int(os.environ.get("TALLY_PORT", "9000"))

    return merged


def save_settings(update: dict) -> dict:
    current = get_settings()
    current.update({k: v for k, v in update.items() if k in DEFAULTS})
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)

    # Push into the current process's environment too, so already-imported
    # modules that read os.environ at call-time (not just import-time) pick
    # the new values up immediately without an app restart.
    if current.get("groq_api_key"):
        os.environ["GROQ_API_KEY"] = current["groq_api_key"]
    if current.get("groq_vision_model"):
        os.environ["GROQ_VISION_MODEL"] = current["groq_vision_model"]
    if current.get("ai_provider"):
        os.environ["AI_PROVIDER"] = current["ai_provider"]
    if current.get("gemini_api_key"):
        os.environ["GEMINI_API_KEY"] = current["gemini_api_key"]
    if current.get("gemini_vision_model"):
        os.environ["GEMINI_VISION_MODEL"] = current["gemini_vision_model"]
    if current.get("tally_host"):
        os.environ["TALLY_HOST"] = str(current["tally_host"])
    if current.get("tally_port"):
        os.environ["TALLY_PORT"] = str(current["tally_port"])

    return current


def has_groq_key() -> bool:
    """Kept for backward compatibility with any older call sites — checks
    the Groq key specifically, regardless of which provider is active.
    For a provider-aware check, use ai_vision.has_ai_key() instead."""
    return bool(get_settings().get("groq_api_key"))


def has_gemini_key() -> bool:
    return bool(get_settings().get("gemini_api_key"))


def get_active_company_id() -> int | None:
    """Returns the id of whichever Company is currently active, or None if
    somehow unset (shouldn't happen after database.py's bootstrap runs,
    but callers should still treat None as 'not scoped to any company'
    rather than crash)."""
    val = get_settings().get("active_company_id")
    return int(val) if val else None
