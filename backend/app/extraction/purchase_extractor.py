"""
Deterministic rule-based extraction for purchase bills using regular expressions and heuristics.
Works 100% locally and offline without external API dependencies.
"""
import re
from dataclasses import dataclass, field
from dateutil import parser as dateparser

from .sales_extractor import AMOUNT_RE, GST_RATE_RE, DATE_RE
from ..money import round_rupee, split_tax_amount
from ..gst_states import determine_gst_tax_routing, gstin_to_state
from ..settings import get_settings

INVOICE_RE = re.compile(
    r"(?:invoice\s*no\.?|inv\s*no\.?|invoice\s*number|bill\s*no\.?|bill\s*number|vch\s*no\.?|no\.?)[\s.:#-]*([A-Za-z0-9/\-]{2,30})",
    re.IGNORECASE,
)
GSTIN_RE = re.compile(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b")


@dataclass
class ExtractedPurchase:
    type: str = "PURCHASE"
    party: str = "Unknown Supplier"
    party_gstin: str | None = None
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
    rate_breakdown: list = field(default_factory=list)


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
    try:
        dt = dateparser.parse(m.group(0), dayfirst=True, fuzzy=True)
        if 2000 <= dt.year <= 2035:
            return dt.date().isoformat()
        return None
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


def _parse_gstin(text: str) -> str | None:
    m = GSTIN_RE.search(text)
    return m.group(1) if m else None


def _parse_supplier_name(text: str) -> str | None:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    skip_pattern = re.compile(
        r"gstin|invoice|bill\s*no|^tax\b|irn|ack\s*no|ack\s*date|e-?way\s*bill|"
        r"consignee|ship\s*to|bill\s*to|buyer|reference\s*no|dispatch",
        re.IGNORECASE,
    )
    hash_like = re.compile(r"^[a-f0-9\-]{20,}$", re.IGNORECASE)

    for line in lines[:8]:
        if skip_pattern.search(line):
            continue
        if hash_like.match(line.replace(" ", "")):
            continue
        if len(line) >= 4 and re.search(r"[A-Za-z]{3,}", line):
            return line
    return None


def extract_purchase_transaction(raw_text: str, ocr_confidence: float) -> ExtractedPurchase:
    tx = ExtractedPurchase()
    warnings = []

    amounts = _parse_amounts(raw_text)
    date = _parse_date(raw_text)
    gst_rate = _parse_gst_rate(raw_text)
    invoice_number = _parse_invoice_number(raw_text)
    supplier = _parse_supplier_name(raw_text)
    gstin = _parse_gstin(raw_text)

    if amounts:
        total = max(amounts)
        tx.total_value = round_rupee(total)
        if gst_rate > 0:
            taxable = round(total / (1 + gst_rate / 100), 2)
            gst_amount = round(total - taxable, 2)
            tx.taxable_value = taxable
            tx.gst_rate = gst_rate

            s = get_settings()
            comp_gstin = s.get("gstin")
            comp_state = s.get("state_code")
            routing = determine_gst_tax_routing(comp_gstin, comp_state, gstin)

            if routing == "INTER":
                tx.igst = gst_amount
                tx.cgst = 0.0
                tx.sgst = 0.0
            else:
                tx.cgst, tx.sgst = split_tax_amount(gst_amount)
                tx.igst = 0.0

            tx.rate_breakdown = [{
                "rate": gst_rate,
                "taxable_value": tx.taxable_value,
                "cgst": tx.cgst,
                "sgst": tx.sgst,
                "igst": tx.igst,
            }]
        else:
            tx.taxable_value = total
            tx.rate_breakdown = [{
                "rate": 0.0,
                "taxable_value": total,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 0.0
            }]
            warnings.append("No GST rate detected in scan — verify or complete in review")
    else:
        warnings.append("No amount detected in scan — manual entry required")

    tx.date = date
    if not date:
        warnings.append("No date detected in scan")

    tx.invoice_number = invoice_number
    tx.party_gstin = gstin
    tx.party = supplier or "Unknown Supplier"
    tx.party = supplier or "Unknown Supplier"
    tx.party_gstin = gstin
    if not supplier:
        warnings.append("Supplier name not confidently read — verify in review")

    parse_score = sum([
        1 if amounts else 0,
        1 if date else 0,
        1 if gst_rate else 0,
        1 if supplier else 0,
    ]) / 4.0

    tx.confidence = round(min(0.90, (0.5 * ocr_confidence) + (0.5 * parse_score)), 2)
    tx.warnings = warnings
    return tx