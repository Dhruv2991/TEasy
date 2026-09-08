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


def _other_provider(active):
    for name, mod in PROVIDERS.items():
        if mod is not active:
            return name, mod
    return None, None


def _reconciles(ai: dict) -> bool:
    """Same arithmetic check the router applies later, run early here so we
    know whether it's worth spending a second AI call on a cross-check."""
    try:
        taxable = float(ai.get("taxable_value"))
        total = float(ai.get("total_value"))
        tax = float(ai.get("cgst") or 0) + float(ai.get("sgst") or 0) + float(ai.get("igst") or 0)
        return abs((taxable + tax) - total) <= 1.50
    except (TypeError, ValueError):
        return False


def _low_confidence(ai: dict, threshold: float = 0.80) -> bool:
    try:
        return float(ai.get("confidence") or 0) < threshold
    except (TypeError, ValueError):
        return True


def _cross_check(image_path: str, prompt: str, active_name: str, active_mod, first: dict, extractor_attr: str) -> dict:
    """If the first read is shaky (low confidence or doesn't reconcile) and a
    second provider has a key configured, re-run extraction with that other
    provider and prefer whichever result is confident AND reconciles. If both
    disagree materially on the total, keep the first result but note the
    disagreement so a human catches it in Review & Approve rather than it
    silently going through on a coin-flip.
    """
    if not (_low_confidence(first) or not _reconciles(first)):
        return first

    other_name, other_mod = _other_provider(active_mod)
    if other_mod is None or not other_mod._current_key():
        return first

    try:
        extractor = getattr(other_mod, extractor_attr)
        if extractor_attr == "extract_bill_with_ai":
            second = extractor(image_path) if prompt is None else extractor(image_path, prompt=prompt)
        else:
            second = extractor(image_path)
    except Exception:
        return first

    first_ok = _reconciles(first) and not _low_confidence(first)
    second_ok = _reconciles(second) and not _low_confidence(second)

    note_prefix = f"[cross-check: {active_name} vs {other_name}] "
    if second_ok and not first_ok:
        second["notes"] = (note_prefix + f"{other_name} reconciled/confident, {active_name} did not — used {other_name}. " + str(second.get("notes") or "")).strip()
        return second
    if first_ok and not second_ok:
        first["notes"] = (note_prefix + f"{active_name} reconciled/confident, {other_name} did not — kept {active_name}. " + str(first.get("notes") or "")).strip()
        return first

    # Both shaky, or both fine but original was already flagged as low-confidence:
    # compare their totals; if they materially disagree, surface that loudly.
    try:
        t1, t2 = float(first.get("total_value")), float(second.get("total_value"))
        if abs(t1 - t2) > 2.0:
            first["notes"] = (note_prefix + f"providers disagree: {active_name} total={t1}, {other_name} total={t2} — manual check required. " + str(first.get("notes") or "")).strip()
            first["confidence"] = min(float(first.get("confidence") or 0), 0.5)
    except (TypeError, ValueError):
        pass
    return first


def extract_bill_with_ai(image_path: str, prompt: str = None) -> dict:
    active_name = (get_settings().get("ai_provider") or "groq").lower()
    provider = _active_provider()
    first = provider.extract_bill_with_ai(image_path) if prompt is None else provider.extract_bill_with_ai(image_path, prompt=prompt)
    return _cross_check(image_path, prompt, active_name, provider, first, "extract_bill_with_ai")


def extract_purchase_bill_with_ai(image_path: str) -> dict:
    active_name = (get_settings().get("ai_provider") or "groq").lower()
    provider = _active_provider()
    first = provider.extract_purchase_bill_with_ai(image_path)
    return _cross_check(image_path, None, active_name, provider, first, "extract_purchase_bill_with_ai")


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


def map_ledger_excel_headers(grid: list[list]) -> dict:
    """
    Same idea as map_excel_headers above, but for the domain-agnostic
    Receipt/Payment register (see extraction/simple_ledger_excel_parser.py).
    Used only when that parser's deterministic header-alias matching can't
    confidently find a party + amount column in an unfamiliar sheet.
    """
    return _active_provider().map_ledger_excel_headers(grid)