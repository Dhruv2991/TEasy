"""
Field-level extraction for a single cropped bill:
  1. Locate and OCR the handwritten RED invoice number (e.g. "2407").
  2. OCR the CGST% / SGST% and their amounts, and the taxable (pre-tax) total.
  3. Auto-calculate the expected tax and grand total, and flag mismatches
     against what's printed/written so bad OCR reads don't get silently
     trusted.

This module works on a single already-cropped bill image (i.e. the output
of grid_detector / bill_detector), not the full 4-up page.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
import pytesseract
from pytesseract import Output


# ---------------------------------------------------------------------------
# 1. Red invoice-number detection
# ---------------------------------------------------------------------------

def _red_ink_mask(img: np.ndarray) -> np.ndarray:
    """Isolate red/blue-red handwritten ink (the invoice number is written in
    red pen) from black printed text and the white/cream page background."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # Red wraps around the hue circle, so two ranges are needed.
    lower1, upper1 = np.array([0, 70, 50]), np.array([10, 255, 255])
    lower2, upper2 = np.array([165, 70, 50]), np.array([180, 255, 255])
    mask1 = cv2.inRange(hsv, lower1, upper1)
    mask2 = cv2.inRange(hsv, lower2, upper2)
    mask = cv2.bitwise_or(mask1, mask2)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def extract_invoice_number(bill_crop: np.ndarray) -> Optional[str]:
    """Find the handwritten red invoice number anywhere in a single bill crop
    and OCR it as digits. Returns None if no confident red region is found.
    """
    h, w = bill_crop.shape[:2]
    # The invoice number ("No. A ____") is always in the top band of the
    # bill, left/centre of the header row — restrict the search there first
    # to avoid picking up stray red ink elsewhere (e.g. underlines).
    top_band = bill_crop[0:int(h * 0.30), 0:w]
    mask = _red_ink_mask(top_band)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Merge all red contours in the band into one bounding box — handwritten
    # digits often break into several disconnected blobs.
    xs, ys, xe, ye = w, h, 0, 0
    found = False
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw * ch < 20:  # ignore specks
            continue
        found = True
        xs, ys = min(xs, x), min(ys, y)
        xe, ye = max(xe, x + cw), max(ye, y + ch)
    if not found:
        return None

    pad = 8
    xs, ys = max(0, xs - pad), max(0, ys - pad)
    xe, ye = min(top_band.shape[1], xe + pad), min(top_band.shape[0], ye + pad)
    region = top_band[ys:ye, xs:xe]
    if region.size == 0:
        return None

    # Isolate the red strokes on a clean white background for OCR — mixing
    # in black printed text nearby would otherwise confuse digit recognition.
    region_mask = _red_ink_mask(region)
    ocr_input = np.full_like(region, 255)
    ocr_input[region_mask > 0] = (0, 0, 0)
    ocr_input = cv2.cvtColor(ocr_input, cv2.COLOR_BGR2GRAY)
    ocr_input = cv2.resize(ocr_input, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    text = pytesseract.image_to_string(
        ocr_input, config="--psm 7 -c tessedit_char_whitelist=0123456789"
    )
    digits = re.sub(r"[^0-9]", "", text)
    return digits or None


# ---------------------------------------------------------------------------
# 2. GST% / taxable value extraction
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(r"[\d,]+\.?\d*")


def _to_float(token: str) -> Optional[float]:
    token = token.replace(",", "").strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None


@dataclass
class TaxFields:
    taxable_value: Optional[float] = None
    cgst_pct: Optional[float] = None
    sgst_pct: Optional[float] = None
    cgst_amt: Optional[float] = None
    sgst_amt: Optional[float] = None
    grand_total_written: Optional[float] = None

    computed_cgst_amt: Optional[float] = None
    computed_sgst_amt: Optional[float] = None
    computed_grand_total: Optional[float] = None
    mismatches: list = field(default_factory=list)


def _ocr_lines(img: np.ndarray) -> list[str]:
    text = pytesseract.image_to_string(img, config="--psm 6")
    return [l.strip() for l in text.splitlines() if l.strip()]


def extract_tax_fields(bill_crop: np.ndarray) -> TaxFields:
    """OCR the amount box of a single bill (Total / CGST% / SGST% / Total)
    and auto-calculate + cross-check the tax math."""
    lines = _ocr_lines(bill_crop)
    result = TaxFields()

    for line in lines:
        low = line.lower()
        nums = _MONEY_RE.findall(line)

        if "total" in low and result.taxable_value is None and "cgst" not in low and "sgst" not in low:
            if nums:
                result.taxable_value = _to_float(nums[-1])

        if "cgst" in low:
            pct_match = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", line)
            if pct_match:
                result.cgst_pct = float(pct_match.group(1))
            if nums:
                result.cgst_amt = _to_float(nums[-1])

        if "sgst" in low:
            pct_match = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", line)
            if pct_match:
                result.sgst_pct = float(pct_match.group(1))
            if nums:
                result.sgst_amt = _to_float(nums[-1])

        if "total" in low and (result.cgst_amt is not None or result.sgst_amt is not None):
            # The second "Total" row (after CGST/SGST) is the grand total.
            if nums:
                candidate = _to_float(nums[-1])
                if candidate and candidate != result.taxable_value:
                    result.grand_total_written = candidate

    # --- Auto-calculate expected values from taxable value + GST% ---
    if result.taxable_value is not None:
        if result.cgst_pct is not None:
            result.computed_cgst_amt = round(result.taxable_value * result.cgst_pct / 100, 2)
        if result.sgst_pct is not None:
            result.computed_sgst_amt = round(result.taxable_value * result.sgst_pct / 100, 2)
        if result.computed_cgst_amt is not None and result.computed_sgst_amt is not None:
            result.computed_grand_total = round(
                result.taxable_value + result.computed_cgst_amt + result.computed_sgst_amt, 2
            )

    # --- Cross-check OCR'd amounts against computed amounts ---
    tolerance = 2.0  # rupees of slack for OCR/rounding noise
    if result.computed_cgst_amt is not None and result.cgst_amt is not None:
        if abs(result.computed_cgst_amt - result.cgst_amt) > tolerance:
            result.mismatches.append(
                f"CGST amount mismatch: OCR read {result.cgst_amt}, "
                f"expected {result.computed_cgst_amt} from {result.cgst_pct}% of {result.taxable_value}"
            )
    if result.computed_sgst_amt is not None and result.sgst_amt is not None:
        if abs(result.computed_sgst_amt - result.sgst_amt) > tolerance:
            result.mismatches.append(
                f"SGST amount mismatch: OCR read {result.sgst_amt}, "
                f"expected {result.computed_sgst_amt} from {result.sgst_pct}% of {result.taxable_value}"
            )
    if result.computed_grand_total is not None and result.grand_total_written is not None:
        if abs(result.computed_grand_total - result.grand_total_written) > tolerance:
            result.mismatches.append(
                f"Grand total mismatch: written {result.grand_total_written}, "
                f"computed {result.computed_grand_total}"
            )

    return result


# ---------------------------------------------------------------------------
# 3. Convenience: run both on one bill crop
# ---------------------------------------------------------------------------

def extract_bill_summary(bill_crop: np.ndarray) -> dict:
    invoice_no = extract_invoice_number(bill_crop)
    tax = extract_tax_fields(bill_crop)
    return {
        "invoice_no": invoice_no,
        "taxable_value": tax.taxable_value,
        "cgst_pct": tax.cgst_pct,
        "sgst_pct": tax.sgst_pct,
        "cgst_amt_written": tax.cgst_amt,
        "sgst_amt_written": tax.sgst_amt,
        "cgst_amt_computed": tax.computed_cgst_amt,
        "sgst_amt_computed": tax.computed_sgst_amt,
        "grand_total_written": tax.grand_total_written,
        "grand_total_computed": tax.computed_grand_total,
        "mismatches": tax.mismatches,
    }
