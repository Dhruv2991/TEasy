"""
High-speed deterministic spreadsheet header mapper and column profiler.
Replaces remote AI mapping calls with fuzzy token matching (rapidfuzz),
column data-type profiling, and user-mapping templates. 100% offline and sub-millisecond.
"""
import hashlib
import json
import os
import re
from typing import List, Dict, Any, Optional, Tuple

try:
    from rapidfuzz import fuzz
    def _fuzzy_score(s1: str, s2: str) -> float:
        return max(
            fuzz.token_set_ratio(s1, s2),
            fuzz.partial_ratio(s1, s2),
            fuzz.ratio(s1, s2),
        ) / 100.0
except ImportError:
    import difflib
    def _fuzzy_score(s1: str, s2: str) -> float:
        s1, s2 = s1.lower(), s2.lower()
        if s1 == s2 or s1 in s2 or s2 in s1:
            return 1.0
        return difflib.SequenceMatcher(None, s1, s2).ratio()

from ..paths import get_data_dir

TEMPLATES_FILE = os.path.join(get_data_dir(), "excel_templates.json")

# Canonical Field Synonym Dictionaries
BILL_FIELD_SYNONYMS = {
    "party": [
        "party", "party name", "customer", "customer name", "buyer", "supplier",
        "supplier name", "party ledger", "particulars", "ledger name", "m/s",
        "client", "vendor", "account", "account name", "billing name"
    ],
    "party_gstin": [
        "party gstin", "supplier gstin", "customer gstin", "gstin",
        "gstin/uin", "party gstin/uin", "supplier gst no", "customer gst no", "tin"
    ],
    "date": [
        "date", "invoice date", "inv date", "bill date", "vch date", "voucher date",
        "transaction date", "txn date", "doc date", "document date"
    ],
    "invoice_number": [
        "invoice no", "inv no", "invoice number", "bill no", "bill number",
        "vch no", "voucher no", "voucher number", "ref no", "reference no",
        "slip no", "doc no", "invoice#", "bill#", "inv#"
    ],
    "amount": [
        "taxable", "taxable value", "taxable amount", "assessable value",
        "sub total", "subtotal", "amount", "basic amount", "net taxable",
        "item amount", "value", "goods value"
    ],
    "gst_rate": [
        "gst rate", "gst%", "tax rate", "rate%", "gst perc", "rate of tax",
        "tax %", "slab", "gst slab", "gst percentage", "tax percentage"
    ],
    "cgst_amount": [
        "cgst", "cgst amt", "cgst amount", "central tax", "cgst value", "cgst @"
    ],
    "sgst_amount": [
        "sgst", "sgst amt", "sgst amount", "state tax", "sgst value", "utgst",
        "utgst amount", "sgst @"
    ],
    "igst_amount": [
        "igst", "igst amt", "igst amount", "integrated tax", "igst value", "igst @"
    ],
    "total_value": [
        "total", "total amount", "total value", "grand total", "invoice total",
        "bill amount", "net amount", "gross amount", "invoice value", "final amount",
        "amount payable", "net total"
    ],
    "stock_item": [
        "item", "item name", "stock item", "product", "product name",
        "description", "goods", "material", "item description", "particulars"
    ],
    "quantity": [
        "qty", "quantity", "quantities", "billed qty", "nos", "pcs", "units",
        "total qty", "weight"
    ],
    "rate": [
        "rate", "unit rate", "price", "unit price", "item rate", "mrp", "unit cost"
    ],
    "unit": [
        "unit", "uom", "per", "measure", "unit of measure", "units"
    ],
    "hsn": [
        "hsn", "hsn/sac", "sac", "hsn code", "sac code"
    ],
    "round_off": [
        "round off", "roundoff", "rounding", "rounding adjustment", "rnd off"
    ],
    "vch_type": [
        "vch type", "voucher type", "type", "invoice type", "transaction type"
    ],
    "from_godown": [
        "from godown", "source godown", "source location", "from location",
        "from warehouse", "source warehouse", "from branch", "source"
    ],
    "to_godown": [
        "to godown", "dest godown", "destination godown", "to location",
        "destination location", "to warehouse", "destination warehouse", "to branch", "destination"
    ],
}

LEDGER_FIELD_SYNONYMS = {
    "party": [
        "party", "student name", "payer", "payee", "employee", "donor", "landlord",
        "tenant", "particulars", "name", "ledger", "account", "received from",
        "paid to", "debit ledger", "credit ledger", "beneficiary", "member"
    ],
    "date": [
        "date", "txn date", "transaction date", "value date", "entry date",
        "payment date", "receipt date"
    ],
    "invoice_number": [
        "receipt no", "ref no", "reference no", "cheque no", "chq no", "slip no",
        "transaction id", "txn id", "utr", "voucher no", "bill no", "invoice no"
    ],
    "amount": [
        "amount", "net amount", "fee amount", "salary", "rent", "amount paid",
        "amount received", "deposit", "withdrawal", "gross amount", "paid", "received"
    ],
    "mode": [
        "mode", "payment mode", "mode of payment", "instrument", "bank/cash",
        "bank name", "account", "type", "method"
    ],
    "narration": [
        "narration", "remarks", "description", "particulars", "notes", "memo",
        "comments", "reason", "purpose"
    ],
    "direction": [
        "direction", "type", "dr/cr", "cr/dr", "in/out", "entry type",
        "transaction type", "receipt/payment", "flow", "status"
    ],
    "tds_amount": [
        "tds", "tds amt", "tds amount", "tds deducted", "tax deducted", "tds value"
    ],
    "tds_ledger": [
        "tds ledger", "tds account", "tds section", "tds head"
    ],
}

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip().lower()
    return re.sub(r"[_\-]+", " ", s)


def _load_saved_templates() -> Dict[str, Any]:
    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_user_template(header_hash: str, mapping: Dict[str, Any]):
    templates = _load_saved_templates()
    templates[header_hash] = mapping
    try:
        os.makedirs(os.path.dirname(TEMPLATES_FILE), exist_ok=True)
        with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
            json.dump(templates, f, indent=2)
    except Exception:
        pass


def _detect_header_row(grid: List[List[Any]], synonym_dict: Dict[str, List[str]]) -> int:
    """Finds the 1-based index of the row most likely to be the column header."""
    best_row = 1
    max_score = -1

    all_synonyms = [term for syn_list in synonym_dict.values() for term in syn_list]

    for r_idx, row in enumerate(grid[:15]):
        row_str_cells = [_clean_str(c) for c in row if c not in (None, "")]
        if not row_str_cells:
            continue

        score = 0
        for cell in row_str_cells:
            if len(cell) < 2:
                continue
            for syn in all_synonyms:
                if cell == syn:
                    score += 5
                    break
                elif syn in cell:
                    score += 2
                    break
                elif _fuzzy_score(cell, syn) > 0.88:
                    score += 1
                    break

        if score > max_score:
            max_score = score
            best_row = r_idx + 1

    return best_row


def _find_rate_buckets_in_headers(header_row_cells: List[Any]) -> List[Dict[str, Any]]:
    """Detects multi-rate columns like 'Taxable 5%', 'CGST 2.5%', 'SGST 2.5%'."""
    rate_groups: Dict[float, Dict[str, Optional[int]]] = {}

    for c_idx, cell in enumerate(header_row_cells):
        if not cell:
            continue
        text = _clean_str(cell)
        col_1based = c_idx + 1

        # Match rate percentage
        m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", text)
        if not m:
            m = re.search(r"[@:]\s*(\d{1,2}(?:\.\d+)?)", text)
        if not m:
            continue

        found_rate = float(m.group(1))

        if "cgst" in text:
            actual_slab = found_rate * 2.0 if found_rate in (2.5, 6.0, 9.0, 14.0) else found_rate
            rate_groups.setdefault(actual_slab, {"rate": actual_slab, "value_col": None, "cgst_col": None, "sgst_col": None, "igst_col": None})
            rate_groups[actual_slab]["cgst_col"] = col_1based
        elif "sgst" in text or "utgst" in text:
            actual_slab = found_rate * 2.0 if found_rate in (2.5, 6.0, 9.0, 14.0) else found_rate
            rate_groups.setdefault(actual_slab, {"rate": actual_slab, "value_col": None, "cgst_col": None, "sgst_col": None, "igst_col": None})
            rate_groups[actual_slab]["sgst_col"] = col_1based
        elif "igst" in text:
            rate_groups.setdefault(found_rate, {"rate": found_rate, "value_col": None, "cgst_col": None, "sgst_col": None, "igst_col": None})
            rate_groups[found_rate]["igst_col"] = col_1based
        elif any(k in text for k in ("taxable", "value", "amt", "amount", "sale", "purchase")):
            rate_groups.setdefault(found_rate, {"rate": found_rate, "value_col": None, "cgst_col": None, "sgst_col": None, "igst_col": None})
            rate_groups[found_rate]["value_col"] = col_1based

    return list(rate_groups.values())


def map_excel_headers_deterministic(grid: List[List[Any]]) -> Dict[str, Any]:
    """
    Deterministically maps Excel/CSV column headers to canonical bill fields:
    party, party_gstin, date, invoice_number, amount, gst_rate, cgst_amount,
    sgst_amount, igst_amount, total_value, stock_item, quantity, rate, unit,
    hsn, round_off, vch_type, from_godown, to_godown.
    """
    if not grid or not grid[0]:
        return {
            "header_row": 1,
            "field_columns": {},
            "rate_buckets": [],
            "confidence": 0.0,
            "notes": "Empty spreadsheet grid",
        }

    header_row_1based = _detect_header_row(grid, BILL_FIELD_SYNONYMS)
    header_cells = grid[header_row_1based - 1]

    # Check for cached template
    header_hash = hashlib.sha256("||".join(_clean_str(c) for c in header_cells).encode("utf-8")).hexdigest()
    saved = _load_saved_templates().get(header_hash)
    if saved:
        return saved

    field_columns: Dict[str, Optional[int]] = {k: None for k in BILL_FIELD_SYNONYMS.keys()}
    used_cols = set()

    # Rate buckets in wide layouts
    rate_buckets = _find_rate_buckets_in_headers(header_cells)
    for b in rate_buckets:
        for c in (b.get("value_col"), b.get("cgst_col"), b.get("sgst_col"), b.get("igst_col")):
            if c:
                used_cols.add(c)

    # Candidate matches: list of (score, field_name, col_1based)
    candidates: List[Tuple[float, str, int]] = []
    for c_idx, cell in enumerate(header_cells):
        col_1based = c_idx + 1
        if col_1based in used_cols:
            continue
        cell_str = _clean_str(cell)
        if not cell_str:
            continue

        for field_name, synonyms in BILL_FIELD_SYNONYMS.items():
            for syn in synonyms:
                if cell_str == syn:
                    score = 1.0
                elif cell_str.startswith(syn) or cell_str.endswith(syn):
                    score = 0.94
                elif syn in cell_str:
                    score = 0.88
                else:
                    score = _fuzzy_score(cell_str, syn)

                if score >= 0.75:
                    candidates.append((score, field_name, col_1based))

    # Sort descending by score
    candidates.sort(key=lambda x: x[0], reverse=True)
    assigned_fields = set()
    for score, field_name, col in candidates:
        if field_name not in assigned_fields and col not in used_cols:
            field_columns[field_name] = col
            assigned_fields.add(field_name)
            used_cols.add(col)

    # Column Data-Type Profiling (Sample data verification)
    sample_rows = grid[header_row_1based: header_row_1based + 15]
    for c_idx in range(len(header_cells)):
        col_1based = c_idx + 1
        if col_1based in used_cols:
            continue

        col_samples = [str(r[c_idx]).strip() for r in sample_rows if c_idx < len(r) and r[c_idx] not in (None, "")]
        if not col_samples:
            continue

        # Profile GSTIN
        if field_columns.get("party_gstin") is None:
            gstin_matches = sum(1 for s in col_samples if GSTIN_PATTERN.match(s))
            if gstin_matches / len(col_samples) >= 0.5:
                field_columns["party_gstin"] = col_1based
                used_cols.add(col_1based)

    # If rate buckets are present, amount should be null to avoid double counting
    if rate_buckets and field_columns.get("amount"):
        field_columns["amount"] = None

    confidence = 0.90 if (field_columns.get("invoice_number") and field_columns.get("party")) else 0.50

    result = {
        "header_row": header_row_1based,
        "field_columns": field_columns,
        "rate_buckets": rate_buckets,
        "confidence": confidence,
        "notes": "Mapped deterministically using fuzzy header matching and column profiling",
    }
    return result


def map_ledger_excel_headers_deterministic(grid: List[List[Any]]) -> Dict[str, Any]:
    """
    Deterministically maps Receipt/Payment register columns:
    party, date, invoice_number, amount, mode, narration, direction,
    tds_amount, tds_ledger, and infers direction values (in vs out).
    """
    if not grid or not grid[0]:
        return {
            "header_row": 1,
            "field_columns": {},
            "direction_values": {"receipt_values": [], "payment_values": []},
            "confidence": 0.0,
            "notes": "Empty ledger grid",
        }

    header_row_1based = _detect_header_row(grid, LEDGER_FIELD_SYNONYMS)
    header_cells = grid[header_row_1based - 1]

    field_columns: Dict[str, Optional[int]] = {k: None for k in LEDGER_FIELD_SYNONYMS.keys()}
    used_cols = set()

    candidates: List[Tuple[float, str, int]] = []
    for c_idx, cell in enumerate(header_cells):
        col_1based = c_idx + 1
        if col_1based in used_cols:
            continue
        cell_str = _clean_str(cell)
        if not cell_str:
            continue

        for field_name, synonyms in LEDGER_FIELD_SYNONYMS.items():
            for syn in synonyms:
                if cell_str == syn:
                    score = 1.0
                elif cell_str.startswith(syn) or cell_str.endswith(syn):
                    score = 0.94
                elif syn in cell_str:
                    score = 0.88
                else:
                    score = _fuzzy_score(cell_str, syn)

                if score >= 0.75:
                    candidates.append((score, field_name, col_1based))

    candidates.sort(key=lambda x: x[0], reverse=True)
    assigned_fields = set()
    for score, field_name, col in candidates:
        if field_name not in assigned_fields and col not in used_cols:
            field_columns[field_name] = col
            assigned_fields.add(field_name)
            used_cols.add(col)

    # Analyze direction values if direction column is found
    receipt_vals = []
    payment_vals = []
    dir_col = field_columns.get("direction")
    if dir_col:
        sample_rows = grid[header_row_1based: header_row_1based + 30]
        c_idx = dir_col - 1
        unique_vals = set(
            str(r[c_idx]).strip() for r in sample_rows if c_idx < len(r) and r[c_idx] not in (None, "")
        )
        for v in unique_vals:
            v_low = v.lower()
            if any(k in v_low for k in ("in", "receipt", "cr", "credit", "deposit", "inflow", "received")):
                receipt_vals.append(v)
            elif any(k in v_low for k in ("out", "payment", "dr", "debit", "withdrawal", "outflow", "paid")):
                payment_vals.append(v)

    confidence = 0.90 if (field_columns.get("party") and field_columns.get("amount")) else 0.50

    return {
        "header_row": header_row_1based,
        "field_columns": field_columns,
        "direction_values": {
            "receipt_values": receipt_vals,
            "payment_values": payment_vals,
        },
        "confidence": confidence,
        "notes": "Mapped deterministically using fuzzy header matching",
    }
