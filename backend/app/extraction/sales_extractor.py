"""
Turns raw OCR text from a single sales bill into a structured transaction.

This is intentionally rule-based (regex + heuristics) for Phase 1 so it runs
fully offline with Tesseract. It's the natural place to later swap in an LLM
call for the low-confidence / ambiguous cases (Phase 6 in the design doc),
without changing anything else in the pipeline.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from dateutil import parser as dateparser

from ..money import round_rupee


AMOUNT_RE = re.compile(r"(?:rs\.?|inr|₹)?\s*([0-9][0-9,]*\.?[0-9]{0,2})", re.IGNORECASE)
GST_RATE_RE = re.compile(r"(\d{1,2})\s*%\s*(gst|cgst|sgst|igst)?", re.IGNORECASE)
DATE_RE = re.compile(
    r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})|(\d{1,2}\s?[A-Za-z]{3,9}\s?\d{2,4})"
)
INVOICE_RE = re.compile(r"(?:inv(?:oice)?[.\s#:-]*)([A-Za-z0-9/\-]{3,20})", re.IGNORECASE)


@dataclass
class ExtractedTransaction:
    type: str = "SALES"
    party: str = "Cash"
    date: str | None = None
    invoice_number: str | None = None
    taxable_value: float = 0.0
    gst_rate: float = 0.0
    cgst: float = 0.0
    sgst: float = 0.0
    igst: float = 0.0
    total_value: float = 0.0
    confidence: float = 0.0
    warnings: list = field(default_factory=list)


def _parse_amounts(text: str) -> list[float]:
    amounts = []
    for m in AMOUNT_RE.finditer(text):
        raw = m.group(1)
        if not raw:
            continue
        cleaned = raw.replace(",", "")
        try:
            val = float(cleaned)
        except ValueError:
            continue
        if val > 0:
            amounts.append(val)
    return amounts


def _parse_date(text: str) -> str | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    candidate = m.group(0)
    try:
        dt = dateparser.parse(candidate, dayfirst=True, fuzzy=True)
        return dt.date().isoformat()
    except (ValueError, OverflowError):
        return None


def _parse_gst_rate(text: str) -> float:
    m = GST_RATE_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0


def _parse_invoice_number(text: str) -> str | None:
    m = INVOICE_RE.search(text)
    return m.group(1) if m else None


def extract_sales_transaction(raw_text: str, ocr_confidence: float) -> ExtractedTransaction:
    tx = ExtractedTransaction()
    warnings = []

    amounts = _parse_amounts(raw_text)
    date = _parse_date(raw_text)
    gst_rate = _parse_gst_rate(raw_text)
    invoice_number = _parse_invoice_number(raw_text)

    if amounts:
        # Heuristic: largest amount on a handwritten sales bill is almost
        # always the bill total. This is the #1 thing a human should verify.
        total = max(amounts)
        tx.total_value = round_rupee(total)

        if gst_rate > 0:
            taxable = round(total / (1 + gst_rate / 100), 2)
            gst_amount = round(total - taxable, 2)
            tx.taxable_value = taxable
            tx.gst_rate = gst_rate
            tx.cgst = round(gst_amount / 2, 2)
            tx.sgst = round(gst_amount / 2, 2)
        else:
            tx.taxable_value = total
            warnings.append("No GST rate detected — assumed non-GST or needs manual entry")
    else:
        warnings.append("No amount detected — requires manual entry")

    tx.date = date
    if not date:
        warnings.append("No date detected")

    tx.invoice_number = invoice_number

    # confidence: blend OCR confidence with how much we successfully parsed
    parse_score = sum([
        1 if amounts else 0,
        1 if date else 0,
        1 if gst_rate else 0,
    ]) / 3.0
    tx.confidence = round((0.6 * ocr_confidence) + (0.4 * parse_score), 2)

    tx.warnings = warnings
    return tx
