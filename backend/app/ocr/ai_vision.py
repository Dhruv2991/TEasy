"""
Picks between groq_vision.py and gemini_vision.py based on the
"ai_provider" setting, so the rest of the app (documents.py,
extraction/bill_excel_parser.py) only ever imports from here and never
has to know which provider is active.

Both underlying modules expose the identical interface:
    extract_bill_with_ai(image_path, prompt=...) -> dict
    extract_purchase_bill_with_ai(image_path) -> dict
    map_excel_headers(grid) -> dict
    _current_key() -> str

Switching providers is a Settings-page change (or AI_PROVIDER env var),
not a code change or restart.
"""
from ..settings import get_settings
from . import groq_vision
from . import gemini_vision

PROVIDERS = {
    "groq": groq_vision,
    "gemini": gemini_vision,
}


def _active_provider():
    name = (get_settings().get("ai_provider") or "groq").lower()
    return PROVIDERS.get(name, groq_vision)


def has_ai_key() -> bool:
    """True if the currently-selected provider has an API key configured."""
    return bool(_active_provider()._current_key())


def extract_bill_with_ai(image_path: str, prompt: str = None) -> dict:
    provider = _active_provider()
    if prompt is None:
        return provider.extract_bill_with_ai(image_path)
    return provider.extract_bill_with_ai(image_path, prompt=prompt)


def extract_purchase_bill_with_ai(image_path: str) -> dict:
    return _active_provider().extract_purchase_bill_with_ai(image_path)


def map_excel_headers(grid: list[list]) -> dict:
    """
    Text-only fallback used by bill_excel_parser.py when its deterministic
    header-alias matching can't confidently identify a spreadsheet's
    columns. `grid` is a small 2D slice from the top of the sheet (raw
    cell values, no formatting) — see groq_vision.map_excel_headers for the
    exact response shape. Raises RuntimeError if no key is configured for
    the active provider or the call fails, same as the other functions
    here, so callers can catch it and fall back to the existing "couldn't
    find a recognizable bill table" error.
    """
    return _active_provider().map_excel_headers(grid)
