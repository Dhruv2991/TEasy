from fastapi import APIRouter

from ..settings import get_settings, save_settings, has_groq_key
from ..ocr.ai_vision import has_ai_key

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def read_settings():
    data = get_settings()
    # Never send raw keys back to the frontend in full — just enough to
    # confirm one is set, so it doesn't sit in browser memory/devtools.
    masked = dict(data)
    for field in ("groq_api_key", "gemini_api_key"):
        if masked.get(field):
            key = masked[field]
            masked[f"{field}_set"] = True
            masked[f"{field}_preview"] = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else "set"
        else:
            masked[f"{field}_set"] = False
            masked[f"{field}_preview"] = ""
        del masked[field]
    return masked


@router.post("")
def update_settings(update: dict):
    saved = save_settings(update)
    return {
        "ai_provider": saved.get("ai_provider"),
        "ai_model": saved.get("ai_model"),
        "groq_api_key_set": bool(saved.get("groq_api_key")),
        "groq_vision_model": saved.get("groq_vision_model"),
        "gemini_api_key_set": bool(saved.get("gemini_api_key")),
        "gemini_vision_model": saved.get("gemini_vision_model"),
        "ollama_endpoint": saved.get("ollama_endpoint"),
        "ollama_vision_model": saved.get("ollama_vision_model"),
        "tally_host": saved.get("tally_host"),
        "tally_port": saved.get("tally_port"),
        "company_name": saved.get("company_name"),
        "gstin": saved.get("gstin"),
        "state_code": saved.get("state_code"),
        "default_gst_rate": saved.get("default_gst_rate"),
    }


@router.get("/status")
def settings_status():
    """Used by the frontend on startup to confirm processing readiness."""
    s = get_settings()
    provider = s.get("ai_provider", "offline")
    return {
        "ai_provider": provider,
        "ai_model": s.get("ai_model") or "",
        "groq_key_set": bool(s.get("groq_api_key")),
        "gemini_key_set": bool(s.get("gemini_api_key")),
        "ollama_endpoint": s.get("ollama_endpoint", "http://localhost:11434"),
        "ai_key_set": has_ai_key(),
        "offline_ready": True,
    }
