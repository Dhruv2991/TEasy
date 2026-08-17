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
    "groq_api_key": "",
    "groq_vision_model": "",  # empty = use the built-in default
    "tally_host": "localhost",
    "tally_port": 9000,
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
    if current.get("tally_host"):
        os.environ["TALLY_HOST"] = str(current["tally_host"])
    if current.get("tally_port"):
        os.environ["TALLY_PORT"] = str(current["tally_port"])

    return current


def has_groq_key() -> bool:
    return bool(get_settings().get("groq_api_key"))
