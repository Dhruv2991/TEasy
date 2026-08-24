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
import xlrd


class _XlrdSheetAdapter:
    """Thin wrapper so the rest of this module can call .cell(row, col)
    with 1-based indices and .value, matching the openpyxl API we already
    use, regardless of whether the file is legacy .xls (xlrd) or .xlsx
    (openpyxl)."""

    class _Cell:
        __slots__ = ("value",)

        def __init__(self, value):
            self.value = value

    def __init__(self, xlrd_sheet):
        self._sheet = xlrd_sheet
        self.max_row = xlrd_sheet.nrows
        self.max_column = xlrd_sheet.ncols

    def cell(self, row, col):
        # incoming row/col are 1-based (openpyxl convention); xlrd is 0-based
        if row < 1 or col < 1 or row > self.max_row or col > self.max_column:
            return self._Cell(None)
        value = self._sheet.cell_value(row - 1, col - 1)
        return self._Cell(value if value != "" else None)


def _load_first_sheet(file_path: str):
    if file_path.lower().endswith(".xls"):
        wb = xlrd.open_workbook(file_path)
        return _XlrdSheetAdapter(wb.sheet_by_index(0))
    wb = openpyxl.load_workbook(file_path, data_only=True)
    return wb[wb.sheetnames[0]]


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
    "party": ["party", "partyname", "customername", "customer", "suppliername", "supplier", "name", "buyername", "sri", "vendorname", "vendor"],
    "date": ["date", "billdate", "invoicedate", "trandate"],
    "invoice_number": ["invoiceno", "invoicenumber", "billno", "billnumber", "invno", "tranno"],
    "taxable_value": ["taxablevalue", "taxableamount", "amount", "netamount", "value"],
    "gst_rate": ["gstrate", "taxrate", "rate", "gst%"],
    "cgst_amount": ["cgstamount", "cgst", "cgstamt"],
    "sgst_amount": ["sgstamount", "sgst", "sgstutamount", "sgstamt"],
    "igst_amount": ["igstamount", "igst", "igstamt"],
    "total_value": ["totalvalue", "totalamount", "grandtotal", "total", "billamount", "invoiceamount"],
}

STANDARD_GST_RATES = [0, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 13.8, 18, 28]

# Matches header cells like "Value@5%", "CGST@2.5%", "SGST@2.5%", "IGST@5%"
_RATE_BUCKET_RE = re.compile(r"(value|cgst|sgst|igst)\s*@\s*([\d.]+)\s*%?", re.IGNORECASE)


def _closest_rate(pct):
    if pct is None:
        return 0.0
    nearest = min(STANDARD_GST_RATES, key=lambda r: abs(r - pct))
    return nearest if abs(nearest - pct) <= 0.5 else round(pct, 2)


def _find_rate_buckets(sheet, header_row):
    """Some registers (e.g. GST-input-breakup exports) list a slab-wise
    breakdown alongside the row totals: 'Value@5%', 'CGST@2.5%', 'SGST@2.5%',
    'IGST@5%', then 'Value@12%', 'CGST@6%', ... repeated per slab (and often
    a standalone 'Value@0%' with no tax columns, since 0% tax is zero).
    When one invoice spans more than one GST rate, the aggregate
    taxable/CGST/SGST columns hold a blended figure that can't be snapped to
    a single slab — but these per-slab columns tell us exactly how to split
    that invoice into one clean-slab line per rate.

    Columns are matched to a slab by RATE, not position — CGST/SGST columns
    are quoted at half the invoice rate (e.g. 'CGST@2.5%' belongs to the 5%
    slab, 'CGST@6%' to the 12% slab), IGST columns at the full rate. This
    avoids misalignment when a slab (like 0%) has no tax columns of its own.
    Returns a list of {rate, value_col, cgst_col, sgst_col, igst_col} dicts.
    """
    buckets: dict[float, dict] = {}
    for col in range(1, sheet.max_column + 1):
        header_text = str(sheet.cell(header_row, col).value or "").strip()
        m = _RATE_BUCKET_RE.fullmatch(header_text)
        if not m:
            continue
        kind, quoted_rate = m.group(1).lower(), float(m.group(2))
        rate = quoted_rate * 2 if kind in ("cgst", "sgst") else quoted_rate
        bucket = buckets.setdefault(rate, {"value_col": None, "cgst_col": None, "sgst_col": None, "igst_col": None})
        bucket[f"{kind}_col"] = col
    return [{"rate": rate, **cols} for rate, cols in sorted(buckets.items()) if cols["value_col"]]


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
    # True when this row is one slab of an invoice that spanned more than
    # one GST rate and was split into multiple clean-slab rows. Downstream
    # code can use this to, e.g., group split rows under one invoice number
    # in the review UI instead of treating them as separate bills.
    split_from_multi_rate: bool = False


def _excel_serial_to_date(serial: float):
    # Excel/Lotus date epoch, matches both xlrd (1900 date system, the
    # default for .xls) and how openpyxl-less spreadsheets store dates as
    # plain numbers when the cell isn't formatted as a date.
    from datetime import datetime, timedelta
    try:
        return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except (OverflowError, OSError, ValueError):
        return None


def parse_bill_excel(file_path: str) -> list[ParsedBillRow]:
    sheet = _load_first_sheet(file_path)

    header = _find_header_row(sheet)
    if not header:
        raise ValueError(
            "Could not find a recognizable bill table in this Excel — need at least a party/date "
            "column plus a taxable or total amount column."
        )
    header_row, cols = header
    rate_buckets = _find_rate_buckets(sheet, header_row)

    def get(row, name):
        col = cols.get(name)
        return sheet.cell(row, col).value if col else None

    def bucket_amount(row, col):
        return _number(sheet.cell(row, col).value) if col else 0.0

    rows = []
    for row in range(header_row + 1, sheet.max_row + 1):
        taxable = _number(get(row, "taxable_value"))
        total = _number(get(row, "total_value"))
        if taxable is None and total is None:
            continue  # blank spacer row

        date_val = get(row, "date")
        if hasattr(date_val, "date"):
            date_str = str(date_val.date())
        elif isinstance(date_val, (int, float)) and date_val > 1000:
            # Bare numeric cell (common in legacy .xls exports) — treat as
            # an Excel date serial rather than a literal number.
            parsed = _excel_serial_to_date(date_val)
            date_str = str(parsed) if parsed else str(date_val).strip()
        else:
            date_str = str(date_val).strip() if date_val else None

        party = str(get(row, "party") or "").strip() or None
        invoice_number = str(get(row, "invoice_number") or "").strip() or None
        base_warnings = []
        if not date_str:
            base_warnings.append("No date found on this row — fill in manually before approving.")

        # Slab-wise split: if this invoice has a nonzero value in more than
        # one rate bucket, emit one clean-slab row per active bucket instead
        # of one row with a blended rate.
        active_buckets = []
        for b in rate_buckets:
            bval = bucket_amount(row, b["value_col"]) or 0.0
            bcgst = bucket_amount(row, b["cgst_col"]) or 0.0
            bsgst = bucket_amount(row, b["sgst_col"]) or 0.0
            bigst = bucket_amount(row, b["igst_col"]) or 0.0
            if abs(bval) > 0.004 or abs(bcgst) > 0.004 or abs(bsgst) > 0.004 or abs(bigst) > 0.004:
                active_buckets.append((b["rate"], bval, bcgst, bsgst, bigst))

        if len(active_buckets) > 1:
            for rate, bval, bcgst, bsgst, bigst in active_buckets:
                line_total = round(bval + bcgst + bsgst + bigst, 2)
                rows.append(ParsedBillRow(
                    party=party, date=date_str, invoice_number=invoice_number,
                    taxable_value=round(bval, 2), gst_rate=rate,
                    cgst=round(bcgst, 2), sgst=round(bsgst, 2), igst=round(bigst, 2),
                    total_value=line_total,
                    warnings=list(base_warnings) + [
                        f"Split from a multi-rate invoice ({len(active_buckets)} slabs) — verify against the source row."
                    ],
                    split_from_multi_rate=True,
                ))
            continue

        # Single slab (or no bucket columns at all) — same logic as before,
        # optionally using the one active bucket for a cleaner rate/amounts
        # than the blended aggregate columns would give.
        if len(active_buckets) == 1:
            rate, taxable, cgst, sgst, igst = active_buckets[0]
        else:
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

        rows.append(ParsedBillRow(
            party=party, date=date_str, invoice_number=invoice_number,
            taxable_value=round(taxable, 2), gst_rate=rate,
            cgst=round(cgst, 2), sgst=round(sgst, 2), igst=round(igst, 2),
            total_value=round(total, 2), warnings=base_warnings,
        ))

    if not rows:
        raise ValueError("Found a header row but no bill rows with a usable amount under it.")
    return rows