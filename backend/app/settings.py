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
    "ai_provider": "offline",  # "offline", "groq", "gemini", "ollama"
    "ai_model": "",            # Custom user-defined model name string (overrides provider default if set)
    "groq_api_key": "",
    "groq_vision_model": "llama-3.2-90b-vision-preview",
    "gemini_api_key": "",
    "gemini_vision_model": "gemini-1.5-flash",
    "ollama_endpoint": "http://localhost:11434",
    "ollama_vision_model": "qwen2-vl",
    "tally_host": "localhost",
    "tally_port": 9000,
    # Company GST profile — used to pre-fill vouchers and, later, for
    # in-app GST reports.
    "company_name": "",
    "gstin": "",
    "state_code": "",
    "default_gst_rate": 18.0,
    "active_company_id": 0,
}


def _settings_path() -> str:
    return os.path.join(get_data_dir(), "settings.json")


def get_settings() -> dict:
    path = _settings_path()
    saved = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            saved = {}

    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in saved.items() if v not in (None, "")})

    # Env vars / .env are the fallback if nothing was saved via the UI yet.
    if not merged["groq_api_key"]:
        merged["groq_api_key"] = os.environ.get("GROQ_API_KEY", "")
    if not merged["groq_vision_model"]:
        merged["groq_vision_model"] = os.environ.get("GROQ_VISION_MODEL", "llama-3.2-90b-vision-preview")
    if not saved.get("ai_provider"):
        merged["ai_provider"] = os.environ.get("AI_PROVIDER", "offline")
    if not merged.get("ai_model"):
        merged["ai_model"] = os.environ.get("AI_MODEL", "")
    if not merged["gemini_api_key"]:
        merged["gemini_api_key"] = os.environ.get("GEMINI_API_KEY", "")
    if not merged["gemini_vision_model"]:
        merged["gemini_vision_model"] = os.environ.get("GEMINI_VISION_MODEL", "gemini-1.5-flash")
    if not merged["ollama_endpoint"]:
        merged["ollama_endpoint"] = os.environ.get("OLLAMA_ENDPOINT", "http://localhost:11434")
    if not merged["ollama_vision_model"]:
        merged["ollama_vision_model"] = os.environ.get("OLLAMA_VISION_MODEL", "qwen2-vl")
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
    if "groq_api_key" in current:
        os.environ["GROQ_API_KEY"] = current.get("groq_api_key") or ""
    if "groq_vision_model" in current:
        os.environ["GROQ_VISION_MODEL"] = current.get("groq_vision_model") or ""
    if "ai_provider" in current:
        os.environ["AI_PROVIDER"] = current.get("ai_provider") or "offline"
    if "ai_model" in current:
        os.environ["AI_MODEL"] = current.get("ai_model") or ""
    if "gemini_api_key" in current:
        os.environ["GEMINI_API_KEY"] = current.get("gemini_api_key") or ""
    if "gemini_vision_model" in current:
        os.environ["GEMINI_VISION_MODEL"] = current.get("gemini_vision_model") or ""
    if "ollama_endpoint" in current:
        os.environ["OLLAMA_ENDPOINT"] = current.get("ollama_endpoint") or ""
    if "ollama_vision_model" in current:
        os.environ["OLLAMA_VISION_MODEL"] = current.get("ollama_vision_model") or ""
    if current.get("tally_host"):
        os.environ["TALLY_HOST"] = str(current["tally_host"])
    if current.get("tally_port"):
        os.environ["TALLY_PORT"] = str(current["tally_port"])

    return current


def has_groq_key() -> bool:
    return bool(get_settings().get("groq_api_key"))


def has_gemini_key() -> bool:
    return bool(get_settings().get("gemini_api_key"))


def has_ai_key() -> bool:
    s = get_settings()
    provider = s.get("ai_provider", "offline")
    if provider == "groq":
        return bool(s.get("groq_api_key"))
    elif provider == "gemini":
        return bool(s.get("gemini_api_key"))
    elif provider == "ollama":
        return True
    return True  # offline mode is always ready


def get_active_company_id() -> int | None:
    """Returns the id of whichever Company is currently active, or None if
    somehow unset (shouldn't happen after database.py's bootstrap runs,
    but callers should still treat None as 'not scoped to any company'
    rather than crash)."""
    val = get_settings().get("active_company_id")
    return int(val) if val else None
