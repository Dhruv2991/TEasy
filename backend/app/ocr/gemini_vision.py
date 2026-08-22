"""
Sends a single cropped bill image to Google's Gemini API (free tier) and
asks it to return the same structured accounting fields as groq_vision.py.

This is a drop-in alternative to groq_vision.py, not a replacement for it —
switch between them via Settings ("AI provider": Groq or Gemini) without
touching any calling code, since both modules expose the identical
extract_bill_with_ai / extract_purchase_bill_with_ai interface and share
the same prompts (imported from groq_vision.py, not duplicated).

Get a free key at https://aistudio.google.com/apikey — no credit card
required. Free tier limits (subject to change on Google's side): roughly
1,500 requests/day, 15 requests/minute on Gemini 2.5 Flash as of mid-2026.
Free-tier prompts may be used by Google to improve their models — keep
that in mind since these are real customer documents.
"""
import json
import re
import time

import requests

from ..settings import get_settings
from .groq_vision import (
    SALES_EXTRACTION_PROMPT,
    PURCHASE_EXTRACTION_PROMPT,
    _encode_image,
)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _current_key() -> str:
    """Reads the key fresh each call, so saving it in Settings takes effect
    immediately without restarting the app."""
    return get_settings().get("gemini_api_key") or ""


def _current_model() -> str:
    return get_settings().get("gemini_vision_model") or "gemini-2.5-flash"


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            cleaned = match.group(0)
    return json.loads(cleaned)


def extract_bill_with_ai(image_path: str, prompt: str = SALES_EXTRACTION_PROMPT) -> dict:
    """
    Same contract as groq_vision.extract_bill_with_ai: returns a dict with
    party/date/invoice_number/taxable_value/gst_rate/cgst/sgst/igst/
    total_value/confidence/notes/raw_text. Raises RuntimeError if the key
    is missing or the API call fails.
    """
    api_key = _current_key()
    if not api_key:
        raise RuntimeError(
            "Gemini API key is not set. Get a free key at "
            "https://aistudio.google.com/apikey and add it on the Settings page in the app."
        )

    # _encode_image returns a data URI ("data:image/jpeg;base64,XXXX"); Gemini
    # wants the raw base64 payload and mime type as separate fields.
    data_uri = _encode_image(image_path)
    mime_type, b64_data = data_uri.split(";base64,", 1)
    mime_type = mime_type.replace("data:", "")

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": b64_data}},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1500,
        },
    }

    url = f"{GEMINI_API_BASE}/{_current_model()}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

    # Gemini's free tier has its own per-minute request cap — same retry
    # pattern as groq_vision.py: on a 429, back off and retry rather than
    # failing the whole page immediately.
    max_retries = 3
    for attempt in range(max_retries + 1):
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code == 429:
            wait_s = 5.5
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_s = float(retry_after) + 0.5
                except ValueError:
                    pass
            if attempt < max_retries:
                time.sleep(wait_s)
                continue
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:500]}")
        break

    body = resp.json()
    try:
        content = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        # Gemini returns this shape (no "text" part) when the response was
        # blocked by a safety filter — surface that clearly instead of a
        # confusing KeyError.
        finish_reason = body.get("candidates", [{}])[0].get("finishReason", "unknown")
        raise RuntimeError(f"Gemini returned no usable response (finishReason={finish_reason}): {e}")

    try:
        parsed = _extract_json(content)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise RuntimeError(f"Could not parse Gemini response as JSON: {e}. Raw: {content[:300]}")

    parsed.setdefault("party", "Cash")
    parsed.setdefault("date", None)
    parsed.setdefault("invoice_number", None)
    parsed.setdefault("taxable_value", 0)
    parsed.setdefault("gst_rate", 0)
    parsed.setdefault("cgst", 0)
    parsed.setdefault("sgst", 0)
    parsed.setdefault("igst", 0)
    parsed.setdefault("total_value", 0)
    parsed.setdefault("confidence", 0.5)
    parsed.setdefault("notes", "")
    parsed["raw_text"] = content

    for numeric_field in ("taxable_value", "gst_rate", "cgst", "sgst", "igst", "total_value", "confidence"):
        try:
            parsed[numeric_field] = float(parsed[numeric_field] or 0)
        except (TypeError, ValueError):
            parsed[numeric_field] = 0.0

    return parsed


def extract_purchase_bill_with_ai(image_path: str) -> dict:
    """Same as extract_bill_with_ai, but with the Purchase-bill prompt."""
    return extract_bill_with_ai(image_path, prompt=PURCHASE_EXTRACTION_PROMPT)
