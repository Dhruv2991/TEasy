"""Parses a general-purpose bill Excel (one row per invoice) for either
Sales or Purchase — for the case where a user has their bills already
listed in a spreadsheet instead of (or alongside) photos. Column names vary
by user/supplier, so this scores header rows against known aliases rather
than assuming a fixed template, the same approach used for the
supplier-invoice rate-matching feature.

This does NOT read multi-line-item detail (that's a different, narrower
feature) — it reads one row as one bill: party, date, invoice number,
taxable value, GST rate/amounts, total. That matches how most small
businesses actually keep a sales/purchase Excel register.
"""
import re
from dataclasses import dataclass, field

import openpyxl


def _normalize(text) -> str:
    if text is None:
        return ""
    return re.sub(r"[₹()%\s\-_/.]", "", str(text)).lower()


def _number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[₹,\s]", "", str(value))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


HEADER_VARIANTS = {
    "party": ["party", "partyname", "customername", "customer", "suppliername", "supplier", "name", "buyername", "sri"],
    "date": ["date", "billdate", "invoicedate"],
    "invoice_number": ["invoiceno", "invoicenumber", "billno", "billnumber", "invno"],
    "taxable_value": ["taxablevalue", "taxableamount", "amount", "netamount", "value"],
    "gst_rate": ["gstrate", "taxrate", "rate", "gst%"],
    "cgst_amount": ["cgstamount", "cgst"],
    "sgst_amount": ["sgstamount", "sgst", "sgstutamount"],
    "igst_amount": ["igstamount", "igst"],
    "total_value": ["totalvalue", "totalamount", "grandtotal", "total", "billamount", "invoiceamount"],
}

STANDARD_GST_RATES = [0, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 13.8, 18, 28]


def _closest_rate(pct):
    if pct is None:
        return 0.0
    nearest = min(STANDARD_GST_RATES, key=lambda r: abs(r - pct))
    return nearest if abs(nearest - pct) <= 0.5 else round(pct, 2)


def _find_header_row(sheet):
    best_row, best_cols, best_score = None, {}, 0
    for row in range(1, min(sheet.max_row, 15) + 1):
        found = {}
        for col in range(1, sheet.max_column + 1):
            value = _normalize(sheet.cell(row, col).value)
            if not value:
                continue
            for field_name, variants in HEADER_VARIANTS.items():
                if field_name not in found and value in variants:
                    found[field_name] = col
        has_amount = "taxable_value" in found or "total_value" in found
        score = len(found)
        if has_amount and score > best_score:
            best_row, best_cols, best_score = row, found, score
    if best_row is None:
        return None
    return best_row, best_cols


@dataclass
class ParsedBillRow:
    party: str | None
    date: str | None
    invoice_number: str | None
    taxable_value: float
    gst_rate: float
    cgst: float
    sgst: float
    igst: float
    total_value: float
    warnings: list = field(default_factory=list)


def parse_bill_excel(file_path: str) -> list[ParsedBillRow]:
    wb = openpyxl.load_workbook(file_path, data_only=True)
    sheet = wb[wb.sheetnames[0]]

    header = _find_header_row(sheet)
    if not header:
        raise ValueError(
            "Could not find a recognizable bill table in this Excel — need at least a party/date "
            "column plus a taxable or total amount column."
        )
    header_row, cols = header

    def get(row, name):
        col = cols.get(name)
        return sheet.cell(row, col).value if col else None

    rows = []
    for row in range(header_row + 1, sheet.max_row + 1):
        taxable = _number(get(row, "taxable_value"))
        total = _number(get(row, "total_value"))
        if taxable is None and total is None:
            continue  # blank spacer row

        cgst = _number(get(row, "cgst_amount")) or 0.0
        sgst = _number(get(row, "sgst_amount")) or 0.0
        igst = _number(get(row, "igst_amount")) or 0.0

        rate_cell = get(row, "gst_rate")
        if rate_cell is not None:
            rate = _number(rate_cell) or 0.0
            if 0 < rate <= 1:
                rate *= 100
        elif taxable:
            rate = _closest_rate(((cgst + sgst + igst) / taxable) * 100)
        else:
            rate = 0.0

        if taxable is None:
            taxable = round((total or 0) - cgst - sgst - igst, 2)
        if total is None:
            total = round(taxable + cgst + sgst + igst, 2)

        date_val = get(row, "date")
        date_str = str(date_val.date()) if hasattr(date_val, "date") else (str(date_val).strip() if date_val else None)

        warnings = []
        party = str(get(row, "party") or "").strip() or None
        invoice_number = str(get(row, "invoice_number") or "").strip() or None
        if not date_str:
            warnings.append("No date found on this row — fill in manually before approving.")

        rows.append(ParsedBillRow(
            party=party, date=date_str, invoice_number=invoice_number,
            taxable_value=round(taxable, 2), gst_rate=rate,
            cgst=round(cgst, 2), sgst=round(sgst, 2), igst=round(igst, 2),
            total_value=round(total, 2), warnings=warnings,
        ))

    if not rows:
        raise ValueError("Found a header row but no bill rows with a usable amount under it.")
    return rows
