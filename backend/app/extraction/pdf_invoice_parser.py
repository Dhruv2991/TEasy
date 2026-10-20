"""
Robust deterministic PDF tax invoice extractor using pdfplumber and OpenCV/pyzbar.
Extracts vector text, structured line-item tables, and embedded B2B e-invoice QR codes
from digital PDF invoices without calling any external AI or rasterizing to lossy images.
"""
import io
import json
import re
from typing import Optional, Dict, Any, List
import pdfplumber
from PIL import Image
import numpy as np
from dateutil import parser as dateparser

from ..money import round_rupee
from ..ocr.qr_extractor import extract_qr_from_image, decode_qr_payload
from ..gst_states import STATE_CODES

GSTIN_RE = re.compile(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b")
INVOICE_NO_RE = re.compile(
    r"(?:invoice\s*no\.?|inv\s*no\.?|invoice\s*number|bill\s*no\.?|vch\s*no\.?|bill\s*number)"
    r"[\s.:#-]*([A-Za-z0-9/\-]{2,30})",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"(?:invoice\s*date|inv\s*date|bill\s*date|dated?)"
    r"[\s.:#-]*([0-9]{1,2}[-/.][A-Za-z0-9]{2,4}[-/.][0-9]{2,4})",
    re.IGNORECASE,
)
GENERIC_DATE_RE = re.compile(r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})\b")

AMOUNT_LINE_RE = re.compile(r"([A-Za-z\s%@]+)[\s:]+(?:rs\.?|inr|₹)?\s*([0-9,]+\.[0-9]{2})", re.IGNORECASE)


def _parse_date(text: str) -> Optional[str]:
    m = DATE_RE.search(text)
    candidate = m.group(1) if m else None
    if not candidate:
        m2 = GENERIC_DATE_RE.search(text)
        candidate = m2.group(1) if m2 else None

    if not candidate:
        return None
    try:
        dt = dateparser.parse(candidate, dayfirst=True, fuzzy=True)
        # Sanity check year
        if 2000 <= dt.year <= 2035:
            return dt.date().isoformat()
    except Exception:
        pass
    return None


def _clean_number(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = re.sub(r"[^\d.]", "", str(val).replace(",", ""))
    try:
        return float(s)
    except Exception:
        return 0.0


def _extract_qr_from_pdf_page(page, doc_type: str = "PURCHASE") -> Optional[Dict[str, Any]]:
    """Inspects embedded images on a pdfplumber page for e-invoice QR codes."""
    for img_obj in page.images:
        try:
            stream = img_obj.get("stream")
            if not stream:
                continue
            raw_data = stream.get_data()
            if not raw_data:
                continue
            pil_img = Image.open(io.BytesIO(raw_data)).convert("RGB")
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            res = extract_qr_from_image(cv_img, doc_type=doc_type)
            if res:
                return res
        except Exception:
            continue
    return None


def _extract_party_name(text: str, gstin: Optional[str]) -> str:
    """Infers the supplier / billing company name from the top header lines."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    skip_keywords = (
        "tax invoice", "invoice", "original", "duplicate", "triplicate",
        "gstin", "state code", "consignee", "buyer", "bill to", "ship to",
        "phone", "email", "pan", "cin", "fssai", "e-way", "ack no"
    )

    candidates = []
    for line in lines[:10]:
        low = line.lower()
        if any(low.startswith(k) or low == k for k in skip_keywords):
            continue
        if len(line) < 3:
            continue
        # Check if line looks like an address or phone
        if re.search(r"\b(pin|road|street|nagar|floor|plot|tel|mob|dist|state)\b", low):
            continue
        if re.search(r"[A-Za-z]{3,}", line):
            candidates.append(line)

    if candidates:
        return candidates[0]
    return "Unknown Supplier"


def _extract_amounts_from_text(text: str) -> Dict[str, float]:
    """Scans text for Taxable Value, CGST, SGST, IGST, and Grand Total."""
    amounts = {
        "taxable_value": 0.0,
        "cgst": 0.0,
        "sgst": 0.0,
        "igst": 0.0,
        "total_value": 0.0,
        "gst_rate": 0.0,
    }

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        low = line.lower()
        nums = re.findall(r"(?:rs\.?|inr|₹)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", line)
        if not nums:
            continue

        last_num = _clean_number(nums[-1])
        if last_num <= 0:
            continue

        if ("taxable" in low or "assessable" in low or "sub total" in low or "subtotal" in low) and amounts["taxable_value"] == 0:
            amounts["taxable_value"] = last_num
        elif "cgst" in low and amounts["cgst"] == 0:
            amounts["cgst"] = last_num
            pct = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", line)
            if pct:
                amounts["gst_rate"] = max(amounts["gst_rate"], float(pct.group(1)) * 2)
        elif "sgst" in low and amounts["sgst"] == 0:
            amounts["sgst"] = last_num
        elif "igst" in low and amounts["igst"] == 0:
            amounts["igst"] = last_num
            pct = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", line)
            if pct:
                amounts["gst_rate"] = max(amounts["gst_rate"], float(pct.group(1)))
        elif ("grand total" in low or "invoice total" in low or "total amount" in low or "net payable" in low or "total value" in low) and amounts["total_value"] == 0:
            amounts["total_value"] = last_num

    # Fallback for total value
    if amounts["total_value"] == 0:
        calculated_tax = amounts["cgst"] + amounts["sgst"] + amounts["igst"]
        if amounts["taxable_value"] > 0:
            amounts["total_value"] = round_rupee(amounts["taxable_value"] + calculated_tax)
        else:
            all_floats = [_clean_number(n) for n in re.findall(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text)]
            if all_floats:
                amounts["total_value"] = max(all_floats)

    return amounts


def _extract_tables_from_page(page) -> List[Dict[str, Any]]:
    """Extracts line items from tabular grids in the PDF."""
    items = []
    tables = page.extract_tables()
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [str(col or "").strip().lower() for col in table[0]]
        
        # Look for description/item, qty, rate, amount columns
        desc_idx = next((i for i, h in enumerate(header) if any(k in h for k in ("item", "desc", "particulars", "product"))), None)
        qty_idx = next((i for i, h in enumerate(header) if any(k in h for k in ("qty", "quantity", "nos"))), None)
        rate_idx = next((i for i, h in enumerate(header) if any(k in h for k in ("rate", "price", "unit price"))), None)
        amt_idx = next((i for i, h in enumerate(header) if any(k in h for k in ("amount", "taxable", "total"))), None)
        hsn_idx = next((i for i, h in enumerate(header) if "hsn" in h or "sac" in h), None)

        if desc_idx is None:
            continue

        for row in table[1:]:
            if not row or len(row) <= desc_idx:
                continue
            desc = str(row[desc_idx] or "").strip()
            if not desc or desc.lower() in ("total", "sub total", "tax"):
                continue

            qty = _clean_number(row[qty_idx]) if qty_idx is not None and qty_idx < len(row) else 1.0
            rate = _clean_number(row[rate_idx]) if rate_idx is not None and rate_idx < len(row) else 0.0
            amt = _clean_number(row[amt_idx]) if amt_idx is not None and amt_idx < len(row) else (qty * rate)
            hsn = str(row[hsn_idx] or "").strip() if hsn_idx is not None and hsn_idx < len(row) else ""

            if amt > 0 or rate > 0:
                items.append({
                    "name": desc,
                    "hsn": hsn,
                    "qty": qty if qty > 0 else 1.0,
                    "unit": "Nos",
                    "price": rate,
                    "amount": amt,
                    "rate": 18.0,
                })
    return items


def parse_digital_pdf_page(page, doc_type: str = "PURCHASE") -> Optional[Dict[str, Any]]:
    """
    Parses a single PDF page deterministically:
    1. Checks for e-Invoice QR code.
    2. Extracts native text, header identities, invoice #, date, and tax calculations.
    Returns None if the page is purely graphical/scanned (triggering OCR fallback).
    """
    # 1. First, check for QR code
    qr_data = _extract_qr_from_pdf_page(page, doc_type=doc_type)
    if qr_data:
        return qr_data

    # 2. Extract vector text
    text = page.extract_text()
    if not text or len(text.strip()) < 50:
        return None  # Pure scan or raster PDF

    # Find GSTIN
    gstins = GSTIN_RE.findall(text)
    supplier_gstin = gstins[0] if gstins else None
    
    # Party name
    party = _extract_party_name(text, supplier_gstin)

    # Invoice number
    inv_match = INVOICE_NO_RE.search(text)
    invoice_number = inv_match.group(1).strip() if inv_match else None

    # Date
    date_str = _parse_date(text)

    # Amounts
    amounts = _extract_amounts_from_text(text)

    # Tables / Line items
    items = _extract_tables_from_page(page)

    # Validation & Confidence
    confidence = 0.5
    taxable = amounts["taxable_value"]
    cgst = amounts["cgst"]
    sgst = amounts["sgst"]
    igst = amounts["igst"]
    total = amounts["total_value"]
    tax_sum = cgst + sgst + igst

    if total > 0 and taxable > 0:
        if abs((taxable + tax_sum) - total) <= 2.0:
            confidence = 0.95
        else:
            confidence = 0.80
    elif total > 0:
        confidence = 0.70

    if invoice_number and party != "Unknown Supplier" and date_str:
        confidence = min(1.0, confidence + 0.05)

    return {
        "party": party,
        "party_gstin": supplier_gstin,
        "date": date_str,
        "invoice_number": invoice_number,
        "taxable_value": taxable,
        "gst_rate": amounts["gst_rate"],
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "total_value": total,
        "confidence": round(confidence, 2),
        "notes": "Extracted deterministically from digital PDF vector text",
        "items": items if items else None,
        "raw_text": text[:500],
    }


def parse_pdf_document(pdf_path: str, doc_type: str = "PURCHASE") -> List[Dict[str, Any]]:
    """
    Processes all pages of a PDF document. Returns a list of extracted invoice dicts.
    If a page has no digital text or QR, returns None for that entry so callers
    can run fallback raster OCR.
    """
    results = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            res = parse_digital_pdf_page(page, doc_type=doc_type)
            results.append(res)
    return results
