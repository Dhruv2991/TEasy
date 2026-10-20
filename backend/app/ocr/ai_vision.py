"""
Multi-Provider AI Vision & Offline Deterministic Extraction Engine for TEasy.

Architecture:
1. Traditional documents (Excel spreadsheets, native vector PDFs, B2B e-Invoice QR codes)
   are ALWAYS parsed 100% deterministically and offline with zero AI/LLM calls.
2. Physical/handwritten bill crops support a configurable multi-provider AI Vision pipeline:
   - "offline": 100% local deterministic pipeline (TrOCR + Tesseract OCR + OpenCV math checks)
   - "groq": Groq Vision API (e.g., llama-3.2-90b-vision-preview or user-specified model)
   - "gemini": Google Gemini Vision API (e.g., gemini-1.5-flash or user-specified model)
   - "ollama": Local Ollama endpoint (e.g., http://localhost:11434 with qwen2-vl or llava)
3. Dynamic model name and provider configuration from user settings with automatic
   fallback to offline deterministic HTR if cloud calls fail or keys are unconfigured.
"""
import os
import re
import json
import base64
import logging
from typing import Optional, Dict, Any, List

import cv2
from fastapi import HTTPException
import requests
import numpy as np

from .qr_extractor import extract_qr_from_image
from .ocr_engine import run_ocr
from .field_extractor import extract_invoice_number, extract_tax_fields, extract_date_from_crop
from ..extraction.sales_extractor import extract_sales_transaction
from ..extraction.purchase_extractor import extract_purchase_transaction
from ..extraction.header_mapper import (
    map_excel_headers_deterministic,
    map_ledger_excel_headers_deterministic,
)
from .. import settings

logger = logging.getLogger("teasy.ocr.ai_vision")

HANDWRITTEN_BILL_EXTRACTION_PROMPT = """You are an expert accountant OCR assistant. Analyze this physical/handwritten bill or invoice crop and extract the transaction details.

Extract the following fields in strict JSON format:
{
  "party_name": "Name of the customer/buyer (for sales) or vendor/supplier (for purchase), or 'Cash' if cash bill",
  "invoice_number": "Handwritten or printed invoice/bill number as a clean string, or null if not found",
  "invoice_date": "Date of invoice in DD-MM-YYYY format, or null if not found",
  "taxable_value": 0.00,
  "cgst": 0.00,
  "sgst": 0.00,
  "igst": 0.00,
  "total_amount": 0.00
}

Rules:
1. Output valid JSON only, without markdown fences or additional commentary.
2. All monetary values must be numeric floats (e.g. 1500.00), not strings.
3. If intra-state taxes are shown as CGST and SGST, extract both. If inter-state, set IGST. If no tax is mentioned, set taxes to 0.00.
4. Ensure total_amount matches the grand total written on the bill.
"""


def has_ai_key() -> bool:
    """Checks whether the currently active AI provider is ready to serve."""
    return settings.has_ai_key()


def _reconciles(ai: dict) -> bool:
    """Deterministic accounting arithmetic check: Taxable + CGST + SGST + IGST == Total."""
    try:
        taxable = float(ai.get("taxable_value") or 0.0)
        total = float(ai.get("total_value") or 0.0)
        tax = (
            float(ai.get("cgst") or 0.0)
            + float(ai.get("sgst") or 0.0)
            + float(ai.get("igst") or 0.0)
        )
        return abs((taxable + tax) - total) <= 1.50
    except (TypeError, ValueError):
        return False


def _parse_json_response(raw: str) -> Optional[dict]:
    """Robustly parses JSON from LLM response text, stripping markdown code fences if present."""
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        # Search for outer { ... } block
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return None


def _image_to_base64_and_mime(image_path: str) -> tuple[str, str]:
    """Encodes local image file to base64 and determines MIME type."""
    mime_type = "image/jpeg"
    ext = os.path.splitext(image_path)[1].lower()
    if ext == ".png":
        mime_type = "image/png"
    elif ext == ".webp":
        mime_type = "image/webp"

    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return encoded, mime_type


def _extract_with_groq(image_path: str, model_name: str, api_key: str, prompt: str) -> Optional[dict]:
    """Sends image to Groq Vision API with structured JSON response format."""
    b64, mime = _image_to_base64_and_mime(image_path)
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name or "llama-3.2-90b-vision-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=35)
    if resp.status_code == 429:
        try:
            err_data = resp.json().get("error", {})
            err_msg = err_data.get("message") or resp.text
        except Exception:
            err_msg = resp.text or "Rate limit exceeded"
        logger.warning("Groq API 429 rate limit exceeded: %s", err_msg)
        raise HTTPException(
            status_code=429,
            detail=f"Groq rate limit exceeded (429): {err_msg}",
        )
    resp.raise_for_status()
    data = resp.json()
    raw = data["choices"][0]["message"]["content"]
    return _parse_json_response(raw)


def _extract_with_gemini(image_path: str, model_name: str, api_key: str, prompt: str) -> Optional[dict]:
    """Sends image to Google Gemini Vision API via direct REST endpoint."""
    b64, mime = _image_to_base64_and_mime(image_path)
    model = model_name or "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": b64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1,
        },
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=35)
    resp.raise_for_status()
    data = resp.json()
    raw = data["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_json_response(raw)


def _extract_with_ollama(image_path: str, endpoint: str, model_name: str, prompt: str) -> Optional[dict]:
    """Calls local Ollama endpoint (e.g. http://localhost:11434/api/chat) for vision models."""
    b64, _ = _image_to_base64_and_mime(image_path)
    base_url = (endpoint or "http://localhost:11434").rstrip("/")
    url = f"{base_url}/api/chat"
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model_name or "qwen2-vl",
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [b64],
            }
        ],
        "stream": False,
        "format": "json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    raw = data.get("message", {}).get("content", "")
    return _parse_json_response(raw)


def _normalize_ai_fields(data: dict) -> dict:
    """Normalizes raw dictionary from AI Vision response into standard TEasy transaction schema."""
    party = str(data.get("party_name") or data.get("party") or "Cash").strip()
    inv_no = data.get("invoice_number") or data.get("invoice_no")
    inv_no = str(inv_no).strip() if inv_no is not None and str(inv_no).strip().lower() != "null" else None

    date_str = data.get("invoice_date") or data.get("date")
    date_str = str(date_str).strip() if date_str is not None and str(date_str).strip().lower() != "null" else None

    def _to_f(v):
        try:
            return float(v or 0.0)
        except (ValueError, TypeError):
            return 0.0

    taxable = _to_f(data.get("taxable_value"))
    cgst = _to_f(data.get("cgst"))
    sgst = _to_f(data.get("sgst"))
    igst = _to_f(data.get("igst"))
    total = _to_f(data.get("total_amount") or data.get("total_value"))

    # Balance defaults if taxable or total is 0
    if taxable == 0.0 and total > 0.0 and (cgst + sgst + igst) == 0.0:
        taxable = total
    elif total == 0.0 and taxable > 0.0:
        total = round(taxable + cgst + sgst + igst, 2)

    gst_rate = 0.0
    tax_sum = cgst + sgst + igst
    if taxable > 0.0 and tax_sum > 0.0:
        gst_rate = round((tax_sum / taxable) * 100.0, 1)

    return {
        "party": party,
        "date": date_str,
        "invoice_number": inv_no,
        "taxable_value": taxable,
        "gst_rate": gst_rate,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "total_value": total,
    }


def call_vision_ai(
    image_path: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    prompt: Optional[str] = None,
) -> Optional[dict]:
    """Routes handwritten image crop through the configured AI Vision provider.

    Returns structured dictionary or None if provider is offline or call fails.
    """
    current_settings = settings.get_settings()
    active_provider = (
        provider or current_settings.get("ai_provider") or os.environ.get("AI_PROVIDER") or "offline"
    ).lower().strip()

    if active_provider in ("offline", "none", ""):
        return None

    custom_model = (model or current_settings.get("ai_model") or os.environ.get("AI_MODEL") or "").strip()
    active_prompt = prompt or HANDWRITTEN_BILL_EXTRACTION_PROMPT

    try:
        raw_dict = None
        resolved_model = ""

        if active_provider == "groq":
            api_key = current_settings.get("groq_api_key") or os.environ.get("GROQ_API_KEY", "")
            if not api_key:
                logger.warning("Groq API key not configured, falling back to offline OCR.")
                return None
            resolved_model = custom_model or current_settings.get("groq_vision_model") or "llama-3.2-90b-vision-preview"
            raw_dict = _extract_with_groq(image_path, resolved_model, api_key, active_prompt)

        elif active_provider == "gemini":
            api_key = current_settings.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                logger.warning("Gemini API key not configured, falling back to offline OCR.")
                return None
            resolved_model = custom_model or current_settings.get("gemini_vision_model") or "gemini-1.5-flash"
            raw_dict = _extract_with_gemini(image_path, resolved_model, api_key, active_prompt)

        elif active_provider == "ollama":
            endpoint = (
                current_settings.get("ollama_endpoint")
                or os.environ.get("OLLAMA_ENDPOINT")
                or "http://localhost:11434"
            )
            resolved_model = custom_model or current_settings.get("ollama_vision_model") or "qwen2-vl"
            raw_dict = _extract_with_ollama(image_path, endpoint, resolved_model, active_prompt)

        else:
            logger.warning("Unknown AI vision provider '%s', falling back to offline OCR.", active_provider)
            return None

        if not raw_dict:
            return None

        normalized = _normalize_ai_fields(raw_dict)
        reconciled = _reconciles(normalized)
        confidence = 0.92 if reconciled else 0.70
        note = f"Extracted via AI Vision ({active_provider}: {resolved_model})"
        if not reconciled:
            note += " | ⚠ Math mismatch (taxable + tax != total)"

        normalized["confidence"] = confidence
        normalized["notes"] = note
        normalized["raw_text"] = json.dumps(raw_dict, indent=2)
        return normalized

    except HTTPException:
        # Re-raise rate limit or API HTTPExceptions so callers/clients know about 429
        raise
    except Exception as e:
        logger.warning(
            "AI Vision extraction via '%s' failed: %s. Falling back to local offline OCR.",
            active_provider,
            e,
        )
        return None


def extract_bill_with_ai(image_path: str, prompt: Optional[str] = None) -> Dict[str, Any]:
    """Extracts physical sales bill.

    1. Checks for official B2B e-Invoice QR code (100% offline & exact).
    2. If AI provider (Groq/Gemini/Ollama) is enabled, queries AI Vision.
    3. If offline or AI call fails, runs deterministic local pipeline (TrOCR + Tesseract).
    """
    # 1. Check for QR Code (Always offline & exact)
    try:
        qr_data = extract_qr_from_image(image_path)
        if qr_data:
            return qr_data
    except Exception:
        pass

    # 2. Check for configured AI Vision provider
    ai_vision_res = call_vision_ai(image_path, prompt=prompt)
    if ai_vision_res:
        return ai_vision_res

    # 3. Local Deterministic Pipeline (Offline TrOCR + Tesseract + OpenCV)
    img = cv2.imread(image_path)
    if img is None:
        return {
            "party": "Cash",
            "date": None,
            "invoice_number": None,
            "taxable_value": 0.0,
            "gst_rate": 0.0,
            "cgst": 0.0,
            "sgst": 0.0,
            "igst": 0.0,
            "total_value": 0.0,
            "confidence": 0.0,
            "notes": "Could not open image file for processing",
            "raw_text": "",
        }

    # Detect red handwritten invoice number
    red_inv_no = None
    try:
        red_inv_no = extract_invoice_number(img)
    except Exception:
        pass

    # Run local OCR
    raw_text, ocr_conf = "", 0.0
    try:
        raw_text, ocr_conf = run_ocr(img)
    except Exception:
        pass

    # Extract fields via sales extractor
    extracted = extract_sales_transaction(raw_text, ocr_conf)

    # Extract tax fields box via OpenCV
    tax_fields = None
    try:
        tax_fields = extract_tax_fields(img)
    except Exception:
        pass

    invoice_number = red_inv_no or extracted.invoice_number
    date_val = extracted.date
    taxable_val = extracted.taxable_value
    gst_rate_val = extracted.gst_rate
    cgst_val = extracted.cgst
    sgst_val = extracted.sgst
    igst_val = extracted.igst
    total_val = extracted.total_value

    notes_list = list(extracted.warnings)

    if tax_fields and tax_fields.taxable_value:
        if taxable_val == 0.0:
            taxable_val = tax_fields.taxable_value
        if tax_fields.cgst_amt is not None and cgst_val == 0.0:
            cgst_val = tax_fields.cgst_amt
        if tax_fields.sgst_amt is not None and sgst_val == 0.0:
            sgst_val = tax_fields.sgst_amt
        if tax_fields.computed_grand_total and total_val == 0.0:
            total_val = tax_fields.computed_grand_total
        elif tax_fields.grand_total_written and total_val == 0.0:
            total_val = tax_fields.grand_total_written
        if tax_fields.mismatches:
            notes_list.extend(tax_fields.mismatches)

    # Handwritten fallback via TrOCR on physical bill crops
    if not invoice_number:
        try:
            from .trocr_engine import recognize_handwritten_digits
            h, w = img.shape[:2]
            header_band = img[0:int(h * 0.35), 0:int(w * 0.65)]
            cand_inv = recognize_handwritten_digits(header_band)
            if cand_inv and len(cand_inv) in range(1, 12):
                invoice_number = cand_inv
                notes_list.append(f"Invoice number extracted via TrOCR: {invoice_number}")
        except Exception:
            pass

    if not date_val:
        try:
            date_val = extract_date_from_crop(img)
            if date_val:
                notes_list.append(f"Date extracted via HTR crop: {date_val}")
        except Exception:
            pass

    if total_val == 0.0:
        try:
            from .trocr_engine import recognize_handwritten_amount
            h, w = img.shape[:2]
            amount_quadrant = img[int(h * 0.55):h, int(w * 0.45):w]
            hw_amt = recognize_handwritten_amount(amount_quadrant)
            if hw_amt and hw_amt > 0:
                total_val = hw_amt
                notes_list.append(f"Total amount extracted via TrOCR: {total_val}")
        except Exception:
            pass

    tax_sum = cgst_val + sgst_val + igst_val
    if total_val > 0 and taxable_val > 0:
        if abs((taxable_val + tax_sum) - total_val) > 1.50:
            notes_list.append(f"Amounts mismatch: taxable ({taxable_val}) + tax ({tax_sum:.2f}) != total ({total_val})")
            confidence = min(extracted.confidence, 0.6)
        else:
            confidence = max(extracted.confidence, 0.85)
    elif total_val > 0:
        confidence = max(extracted.confidence, 0.6)
    else:
        confidence = 0.0

    return {
        "party": extracted.party or "Cash",
        "date": date_val,
        "invoice_number": invoice_number,
        "taxable_value": taxable_val,
        "gst_rate": gst_rate_val,
        "cgst": cgst_val,
        "sgst": sgst_val,
        "igst": igst_val,
        "total_value": total_val,
        "confidence": confidence,
        "notes": "; ".join(notes_list) if notes_list else "Processed with deterministic OCR and math checks",
        "raw_text": raw_text,
    }


def extract_purchase_bill_with_ai(image_path: str) -> Dict[str, Any]:
    """Extracts physical purchase bill.

    1. Checks for B2B e-invoice QR code.
    2. If AI provider is configured, queries AI Vision.
    3. Falls back to deterministic local OCR + TrOCR.
    """
    # 1. Check for QR Code
    try:
        qr_data = extract_qr_from_image(image_path)
        if qr_data:
            return qr_data
    except Exception:
        pass

    # 2. Check for configured AI Vision provider
    ai_vision_res = call_vision_ai(image_path)
    if ai_vision_res:
        return ai_vision_res

    # 3. Local Deterministic Pipeline
    img = cv2.imread(image_path)
    if img is None:
        return {
            "party": "Unknown Supplier",
            "date": None,
            "invoice_number": None,
            "taxable_value": 0.0,
            "gst_rate": 0.0,
            "cgst": 0.0,
            "sgst": 0.0,
            "igst": 0.0,
            "total_value": 0.0,
            "confidence": 0.0,
            "notes": "Could not open image file for processing",
            "raw_text": "",
        }

    raw_text, ocr_conf = "", 0.0
    try:
        raw_text, ocr_conf = run_ocr(img)
    except Exception:
        pass

    extracted = extract_purchase_transaction(raw_text, ocr_conf)

    inv_num = extracted.invoice_number
    if not inv_num:
        try:
            from .trocr_engine import recognize_handwritten_digits
            h, w = img.shape[:2]
            header_band = img[0:int(h * 0.35), 0:w]
            cand = recognize_handwritten_digits(header_band)
            if cand and len(cand) in range(1, 12):
                inv_num = cand
        except Exception:
            pass

    date_str = extracted.date
    if not date_str:
        try:
            date_str = extract_date_from_crop(img)
        except Exception:
            pass

    return {
        "party": extracted.party,
        "date": date_str,
        "invoice_number": inv_num,
        "taxable_value": extracted.taxable_value,
        "gst_rate": extracted.gst_rate,
        "cgst": extracted.cgst,
        "sgst": extracted.sgst,
        "igst": extracted.igst,
        "total_value": extracted.total_value,
        "confidence": extracted.confidence,
        "notes": "; ".join(extracted.warnings) if extracted.warnings else "Processed with deterministic OCR",
        "raw_text": raw_text,
    }


def map_excel_headers(grid: List[List[Any]]) -> Dict[str, Any]:
    """Spreadsheet header mapper remains 100% deterministic and offline."""
    return map_excel_headers_deterministic(grid)


def map_ledger_excel_headers(grid: List[List[Any]]) -> Dict[str, Any]:
    """Ledger register mapper remains 100% deterministic and offline."""
    return map_ledger_excel_headers_deterministic(grid)