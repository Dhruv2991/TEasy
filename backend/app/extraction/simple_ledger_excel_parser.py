"""
Parses a general-purpose "money in / money out" Excel sheet — the kind any
organisation keeps regardless of what it does: a college's fee-collection
register, a landlord's rent-received log, a trust's donation list, a
company's salary/expense payment sheet, a shop's daily cash-collection
sheet, etc. There is no GST, no invoice math, no item table here — just
one row per transaction: who, when, how much, and (optionally) how
(cash/bank) and why (narration/purpose).

This is deliberately domain-agnostic: it never assumes the payer/payee is
a "student", "tenant", "donor", or "employee" — that's just whatever name
is in the party column. The same parser produces a "Student Fees" receipt
voucher, a "Rent Received" receipt voucher, and a "Salary" payment voucher
from three completely different sheets, because to Tally they're all the
same shape: one ledger debited, one credited, for an amount, on a date.

Column names vary wildly by organisation, so — same approach as
bill_excel_parser.py — this scores header rows against known aliases
rather than assuming a fixed template.
"""
import re
from dataclasses import dataclass

from .bill_excel_parser import (
    _load_first_sheet,
    _normalize,
    _number,
    _parse_date_cell,
)

HEADER_VARIANTS = {
    "party": [
        "party", "partyname", "name", "studentname", "student", "payername",
        "payeename", "payername", "tenantname", "tenant", "donorname", "donor",
        "employeename", "employee", "customer", "customername", "receivedfrom",
        "paidto", "paidby", "from", "to",
        # Journal-voucher aliases: the ledger to be debited (see rows_to_transactions).
        "debitledger", "drledger", "debitaccount", "debitledgername",
        # Contra-voucher aliases: the ledger receiving the transferred funds.
        "toledger", "toaccount", "destinationledger", "destinationaccount", "depositto",
    ],
    "date": ["date", "transactiondate", "receiptdate", "paymentdate", "trandate", "vchdate"],
    "invoice_number": [
        "receiptno", "receiptnumber", "recno", "referenceno", "refno",
        "vchno", "transactionid", "slipno", "chequeno", "utrno", "transactionno",
    ],
    "amount": [
        "amount", "amountpaid", "amountreceived", "feeamount", "rentamount",
        "totalamount", "netamount", "value", "fees", "fee",
    ],
    "mode": [
        "mode", "paymentmode", "modeofpayment", "paidvia", "paidthrough",
        # Journal-voucher aliases: the ledger to be credited (see rows_to_transactions).
        "creditledger", "crledger", "creditaccount", "creditledgername",
        # Contra-voucher aliases: the ledger the funds are transferred out of.
        "fromledger", "fromaccount", "sourceledger", "sourceaccount", "withdrawfrom",
    ],
    "narration": ["narration", "particulars", "purpose", "remarks", "description", "reason", "note"],
    # TDS aliases — only meaningful on a Payment sheet (see rows_to_transactions).
    "tds_amount": ["tdsamount", "tdsdeducted", "tds", "taxdeductedatsource"],
    "tds_ledger": ["tdsledger", "tdsaccount", "tdspayableledger"],
}


@dataclass
class ParsedLedgerRow:
    party: str
    date: str | None
    invoice_number: str | None
    amount: float
    mode: str | None
    narration: str | None
    # Set only when the sheet mixes both directions in one file (e.g. a
    # "Type" column with Inflow/Outflow) and the AI-mapping fallback below
    # detected it — overrides the page-level Receipt/Payment toggle for
    # this one row. None for every row parsed by the normal deterministic
    # path, and for AI-mapped sheets with no such column, in which case
    # the toggle value the user picked applies to all rows as before.
    direction: str | None = None
    # Only meaningful for a Payment row — see rows_to_transactions.
    tds_amount: float = 0.0
    tds_ledger: str | None = None


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
        has_essentials = "party" in found and "amount" in found
        score = len(found)
        if has_essentials and score > best_score:
            best_row, best_cols, best_score = row, found, score
    if best_row is None:
        return None
    return best_row, best_cols


# Bank-ledger names a "mode" column commonly spells out — mapped to a
# sensible default ledger name; anything not recognised is passed through
# as-is (e.g. "HDFC Bank", "UPI - GPay") so the user's own wording survives
# straight into Tally rather than being forced into a fixed list.
_MODE_LEDGER_HINTS = {
    "cash": "Cash",
    "online": "Bank Account",
    "upi": "Bank Account",
    "neft": "Bank Account",
    "rtgs": "Bank Account",
    "cheque": "Bank Account",
    "cheq": "Bank Account",
    "card": "Bank Account",
    "bank": "Bank Account",
    "banktransfer": "Bank Account",
}


def _mode_to_ledger(mode_val) -> str | None:
    if not mode_val:
        return None
    key = _normalize(mode_val)
    return _MODE_LEDGER_HINTS.get(key, str(mode_val).strip())


_AI_MAPPING_SAMPLE_ROWS = 12  # header candidates + a few data rows for context


def _sheet_to_grid(sheet, max_rows: int) -> list[list]:
    return [
        [sheet.cell(r, c).value for c in range(1, sheet.max_column + 1)]
        for r in range(1, min(sheet.max_row, max_rows) + 1)
    ]


def _direction_lookup(direction_values: dict) -> dict:
    """Builds a normalized {value: 'RECEIPT'|'PAYMENT'} lookup from the
    AI's direction_values (e.g. {"receipt_values": ["Inflow"], "payment_values": ["Outflow"]})."""
    lookup = {}
    for v in (direction_values or {}).get("receipt_values") or []:
        lookup[_normalize(v)] = "RECEIPT"
    for v in (direction_values or {}).get("payment_values") or []:
        lookup[_normalize(v)] = "PAYMENT"
    return lookup


def _parse_via_ai_mapping(sheet) -> list[ParsedLedgerRow] | None:
    """
    AI fallback for a Receipt/Payment register whose column names don't
    match any of the known HEADER_VARIANTS aliases — e.g. "Amount_INR"
    instead of "Amount", or "Received From" instead of "Party". Sends the
    top of the sheet (raw cell values, no image) to whichever AI provider
    is configured (see ocr/ai_vision.py, currently Groq) and asks it to
    map columns to canonical fields once; everything after that (reading
    cell values, building rows) is the same deterministic code used for
    every other Excel import — the model never touches a number itself.

    Returns None (never raises) on any failure — missing API key, network
    error, or a mapping the model wasn't confident enough to give — so the
    caller falls through to the normal "couldn't find a recognizable
    table" error instead of a confusing AI-specific traceback.
    """
    from ..ocr.ai_vision import has_ai_key, map_ledger_excel_headers

    if not has_ai_key():
        return None

    grid = _sheet_to_grid(sheet, _AI_MAPPING_SAMPLE_ROWS)
    try:
        mapping = map_ledger_excel_headers(grid)
    except Exception:
        return None

    header_row = mapping.get("header_row")
    field_columns = mapping.get("field_columns") or {}
    party_col = field_columns.get("party")
    amount_col = field_columns.get("amount")
    if not header_row or not party_col or not amount_col:
        return None

    date_col = field_columns.get("date")
    ref_col = field_columns.get("invoice_number")
    mode_col = field_columns.get("mode")
    narration_col = field_columns.get("narration")
    direction_col = field_columns.get("direction")
    direction_lookup = _direction_lookup(mapping.get("direction_values"))
    tds_amount_col = field_columns.get("tds_amount")
    tds_ledger_col = field_columns.get("tds_ledger")

    rows: list[ParsedLedgerRow] = []
    for row in range(int(header_row) + 1, sheet.max_row + 1):
        party_val = sheet.cell(row, party_col).value
        amount_val = sheet.cell(row, amount_col).value
        party = str(party_val).strip() if party_val not in (None, "") else None
        amount = _number(amount_val)
        if not party or not amount:
            continue

        date_val = sheet.cell(row, date_col).value if date_col else None
        ref_val = sheet.cell(row, ref_col).value if ref_col else None
        mode_val = sheet.cell(row, mode_col).value if mode_col else None
        narration_val = sheet.cell(row, narration_col).value if narration_col else None
        direction_val = sheet.cell(row, direction_col).value if direction_col else None
        direction = direction_lookup.get(_normalize(direction_val)) if direction_val else None
        tds_amount_val = sheet.cell(row, tds_amount_col).value if tds_amount_col else None
        tds_ledger_val = sheet.cell(row, tds_ledger_col).value if tds_ledger_col else None

        rows.append(ParsedLedgerRow(
            party=party,
            date=_parse_date_cell(date_val),
            invoice_number=str(ref_val).strip() if ref_val not in (None, "") else None,
            amount=amount,
            mode=_mode_to_ledger(mode_val),
            narration=str(narration_val).strip() if narration_val not in (None, "") else None,
            direction=direction,
            tds_amount=_number(tds_amount_val) or 0.0,
            tds_ledger=str(tds_ledger_val).strip() if tds_ledger_val not in (None, "") else None,
        ))

    if not rows:
        return None

    note = mapping.get("notes")
    if note:
        for r in rows:
            r.narration = r.narration or f"(AI-mapped columns: {note})"

    return rows


def parse_ledger_excel(file_path: str) -> list[ParsedLedgerRow]:
    """
    Reads one row per transaction from any receipt/payment-style Excel.
    Raises ValueError with a clear message if no recognisable header row
    (a party column + an amount column, at minimum) can be found — the
    caller should surface that directly to the user rather than silently
    returning an empty list, since a silently-empty import is far more
    confusing than an explicit "couldn't find the right columns" error.
    """
    sheet = _load_first_sheet(file_path)
    found_header = _find_header_row(sheet)
    if found_header is None:
        # The sheet's column names don't match any of the known aliases
        # above (e.g. "Amount_INR" instead of "Amount") — rather than
        # growing that alias list forever, ask the AI to read the header
        # row itself and map the columns, same fallback pattern already
        # used for Sales/Purchase Excel imports (see
        # bill_excel_parser.py's _parse_via_ai_mapping).
        ai_rows = _parse_via_ai_mapping(sheet)
        if ai_rows is not None:
            return ai_rows
        raise ValueError(
            "Couldn't find a recognizable transaction table in this Excel. "
            "Make sure it has a header row with at least a name/party column "
            "and an amount column (e.g. 'Student Name' and 'Fee Amount', or "
            "'Paid To' and 'Amount')."
        )
    header_row, cols = found_header

    rows: list[ParsedLedgerRow] = []
    for row in range(header_row + 1, sheet.max_row + 1):
        party_val = sheet.cell(row, cols["party"]).value if "party" in cols else None
        amount_val = sheet.cell(row, cols["amount"]).value if "amount" in cols else None
        party = str(party_val).strip() if party_val not in (None, "") else None
        amount = _number(amount_val)

        # A blank/zero row (common at the bottom of a manually-kept sheet,
        # e.g. a totals row or trailing blank rows) is skipped, not an error.
        if not party or not amount:
            continue

        date_val = sheet.cell(row, cols["date"]).value if "date" in cols else None
        ref_val = sheet.cell(row, cols["invoice_number"]).value if "invoice_number" in cols else None
        mode_val = sheet.cell(row, cols["mode"]).value if "mode" in cols else None
        narration_val = sheet.cell(row, cols["narration"]).value if "narration" in cols else None
        tds_amount_val = sheet.cell(row, cols["tds_amount"]).value if "tds_amount" in cols else None
        tds_ledger_val = sheet.cell(row, cols["tds_ledger"]).value if "tds_ledger" in cols else None

        rows.append(ParsedLedgerRow(
            party=party,
            date=_parse_date_cell(date_val),
            invoice_number=str(ref_val).strip() if ref_val not in (None, "") else None,
            amount=amount,
            mode=_mode_to_ledger(mode_val),
            narration=str(narration_val).strip() if narration_val not in (None, "") else None,
            tds_amount=_number(tds_amount_val) or 0.0,
            tds_ledger=str(tds_ledger_val).strip() if tds_ledger_val not in (None, "") else None,
        ))

    if not rows:
        raise ValueError(
            "Found a header row but no data rows underneath it with both a "
            "name and an amount filled in — nothing to import."
        )
    return rows


def rows_to_transactions(rows: list[ParsedLedgerRow], vch_type: str) -> list[dict]:
    """
    Converts parsed rows into the same dict shape documents.py already uses
    to create Transaction records for other document types. vch_type is
    'RECEIPT' (money in) or 'PAYMENT' (money out) — the page-level toggle
    the user picked, and the default for any row. If a row carries its own
    `direction` (set only by the AI-mapping fallback above, when the sheet
    had its own per-row Inflow/Outflow-style column), that row's own
    direction wins instead — see build_ledger_voucher_xml's expectations
    in tally/voucher_builder.py.
    """
    vch_type = vch_type.upper()
    out = []
    for r in rows:
        row_type = (r.direction or vch_type).upper()
        tx = {
            "type": row_type,
            "party": r.party,
            "date": r.date,
            "invoice_number": r.invoice_number,
            "total_value": r.amount,
            "narration": r.narration,
            "bank_ledger": r.mode,
            "confidence": 1.0,  # Excel figures are taken as given, same as other Excel-sourced rows
        }
        if row_type == "RECEIPT":
            tx["credit"] = r.amount
            tx["debit"] = 0.0
        elif row_type in ("JOURNAL", "CONTRA"):
            # Plain two-ledger entry: `party` is whatever the sheet's
            # Debit/To Ledger column said, `bank_ledger` is the
            # Credit/From Ledger column. Journal and Contra are the same
            # shape here — the only difference is that a Contra's two
            # ledgers are both cash/bank ledgers, which is a Tally-side
            # ledger-group fact, not something this row needs to encode.
            tx["debit"] = r.amount
            tx["credit"] = 0.0
        else:  # PAYMENT
            tx["debit"] = r.amount
            tx["credit"] = 0.0
            tx["tds_amount"] = r.tds_amount or 0.0
            tx["tds_ledger"] = r.tds_ledger
        out.append(tx)
    return out