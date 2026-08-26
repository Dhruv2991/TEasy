import re
from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
import pdfplumber
import requests

from ..tally.config import get_tally_config
from ..settings import get_settings
from ..reconciliation import reconcile_bank_transactions

router = APIRouter(prefix="/bank", tags=["Bank Statements"])


def _tally_url() -> str:
    s = get_settings()
    return f"http://{s.get('tally_host', 'localhost')}:{s.get('tally_port', 9000)}"


class BankTransaction(BaseModel):
    txn_date: str
    value_date: Optional[str] = None
    particulars: str
    cheque_no: Optional[str] = None
    branch_code: Optional[str] = None
    debit: Optional[float] = 0.0
    credit: Optional[float] = 0.0
    balance: Optional[float] = 0.0
    narration: Optional[str] = ""


# Flexible Date Pattern: Supports DD/MM/YYYY, DD-MM-YYYY, DD-MMM-YYYY, DD/MM/YY
DATE_REGEX = re.compile(r"^\d{1,2}[/-](?:\d{1,2}|[A-Za-z]{3})[/-]\d{2,4}$")

# Column Keyword Aliases — covers SBI, Bank of Baroda, Union Bank, Axis,
# Karnataka Bank, and HDFC's real net-banking PDF exports. Matching is
# substring-based (see detect_column_indices), so these only need to cover
# the distinctive part of each bank's actual header wording, not an exact
# full-string match.
HEADER_ALIASES = {
    "txn_date": ["txn date", "tran date", "transaction date", "value date", "posting date", "post date", "date"],
    "description": ["particulars", "narration", "description", "remarks", "transaction details", "details", "txn description"],
    "cheque_no": ["chq/ref number", "chqno", "chq no", "cheque no", "instrument no", "ref no", "reference no", "reference number", "tran id", "chq/ref no", "utr"],
    "debit": ["withdrawal amount", "withdrawals", "debit amount", "debit", "dr amount", "withdrawal", "dr"],
    "credit": ["deposit amount", "deposits", "credit amount", "credit", "cr amount", "deposit", "cr"],
    # A handful of banks (some Union Bank / Karnataka Bank exports) print a
    # single "Amount" column with a separate Dr/Cr indicator column instead
    # of two separate Debit/Credit columns.
    "amount": ["transaction amount", "amount (inr)", "amount(inr)", "amount"],
    "drcr": ["dr/cr", "cr/dr", "dr / cr", "type"],
    "balance": ["running balance", "available balance", "avl balance", "avl bal", "closing balance", "balance (rs.)", "balance", "bal"],
}


def parse_amount(val_str: Optional[str]) -> float:
    if not val_str or not str(val_str).strip():
        return 0.0

    raw = str(val_str).strip()
    is_negative = raw.startswith("-") or raw.endswith("-") or "DR" in raw.upper()

    clean = re.sub(r"[^\d.]", "", raw)
    if not clean:
        return 0.0

    try:
        val = float(clean)
        return -val if is_negative else val
    except ValueError:
        return 0.0


# UPI narrations follow a fairly standard slash-delimited shape:
# UPI/<purpose code>/<txn id>/<payer or payee name>/<bank IFSC-ish code>/<remark>/
# extract_party_name() below picks the first "name-looking" token, but
# without knowing these two token classes it was picking the purpose code
# (e.g. "P2A") or bank code as if it were the name.
_UPI_PURPOSE_CODES = {
    "P2A", "P2P", "P2M", "P2B", "B2B", "B2C", "C2B", "C2M", "M2P", "M2C",
    "VAM", "UPI", "NEFT", "RTGS", "IMPS", "DR", "CR",
}
# Common IFSC bank-code prefixes that show up standalone in UPI narrations
# (e.g. ".../THIMMAPPA/SBIN/Payment/") — these are 4-letter codes, same
# shape as some short human names, so this is an explicit finite list
# rather than a blanket "skip all 4-letter caps" rule (which would wrongly
# also skip a real name like "RAJU").
_BANK_IFSC_PREFIXES = {
    "SBIN", "HDFC", "ICIC", "UTIB", "CNRB", "PUNB", "KKBK", "IOBA", "IDFB",
    "YESB", "INDB", "UBIN", "BARB", "KARB", "FDRL", "RATN", "IBKL", "CITI",
    "HSBC", "SCBL", "DBSS", "AIRP", "PYTM", "ORBC", "ANDB", "CORP", "SYNB",
    "UCBA", "VIJB", "ALLA", "MAHB", "TMBL", "SIBL", "CSBK", "DCBL", "BKID",
    "IDIB", "PSIB", "UTBI", "AXIS",
}


def extract_party_name(narration: str) -> str:
    if not narration:
        return "SALDEBTOR"

    clean = narration.strip()
    clean = re.sub(r"^(BY|TO)\s+TRANSFER\s*[-:]?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^CHEQUE\s+(DEPOSIT|CLEARING)\s*[-:]?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^(NEFT|RTGS|IMPS|UPI)\s*[-:/]\s*", "", clean, flags=re.IGNORECASE)

    parts = [p.strip() for p in re.split(r"[-/]", clean) if p.strip()]
    for part in parts:
        if part.isdigit() or len(part) <= 2:
            continue
        # Skip UTR/reference-style codes (letters+digits mixed, 9-11
        # chars) — but only when there's actually a digit in it. Without
        # requiring a digit, this also matched pure-letter names of the
        # same length (e.g. "THIMMAPPA" is 9 uppercase letters and would
        # otherwise get skipped as if it were a reference code).
        if re.match(r"^[A-Z0-9]{9,11}$", part) and any(ch.isdigit() for ch in part):
            continue
        if part.upper() in _UPI_PURPOSE_CODES or part.upper() in _BANK_IFSC_PREFIXES:
            continue
        return part

    return clean if clean else "SALDEBTOR"


def detect_column_indices(header_row: List[str]) -> Dict[str, int]:
    """Dynamically maps header text to column index positions."""
    mapping = {}
    for field, aliases in HEADER_ALIASES.items():
        for idx, cell_text in enumerate(header_row):
            cell_lower = str(cell_text).strip().lower()
            if any(alias == cell_lower or alias in cell_lower for alias in aliases):
                if field not in mapping:
                    mapping[field] = idx
                    break
    return mapping


# pdfplumber's default table detection ("lines" strategy) looks for actual
# drawn cell borders. Several banks' net-banking PDF exports (SBI, Bank of
# Baroda, Union Bank and Karnataka Bank in particular) print statement
# tables with faint or entirely absent vertical gridlines, which makes the
# lines strategy merge two neighbouring columns into a single cell — the
# classic symptom being one column's digits getting glued onto another's,
# producing an absurd amount like "₹3,26,21,50,49,492.00" out of what
# should have been a UTR/reference number sitting next to a normal amount.
# Axis and HDFC statements usually DO have real gridlines and work fine
# with the default strategy. Rather than hardcode per-bank behavior, try a
# couple of extraction strategies per page and keep whichever one actually
# sliced real transaction rows out cleanly.
TABLE_STRATEGIES = [
    {},  # pdfplumber default (lines-based) — correct for Axis/HDFC-style PDFs with real borders
    {"vertical_strategy": "text", "horizontal_strategy": "text"},  # for PDFs with no real gridlines at all
    {"vertical_strategy": "text", "horizontal_strategy": "lines"},  # mixed: real row lines, but column separation only inferred from text position
]


def _score_tables(tables: list) -> int:
    """Rough confidence score for one extraction attempt: how many rows
    contain a recognizable date in *any* cell. Deliberately checks every
    cell, not just the first — some banks (e.g. Union Bank's NetBanking
    export) put a serial number in column 0 and the date in column 1, so
    scoring on row[0] alone always scores 0 for those layouts and the
    scorer can't tell a correct extraction from a garbled one."""
    score = 0
    for table in tables:
        for row in table:
            if not row:
                continue
            for cell in row:
                if DATE_REGEX.match(str(cell or "").strip()):
                    score += 1
                    break
    return score


def _best_tables_for_page(page) -> list:
    best_tables, best_score = [], -1
    for settings in TABLE_STRATEGIES:
        try:
            tables = page.extract_tables(settings) if settings else page.extract_tables()
        except Exception:
            continue
        score = _score_tables(tables)
        if score > best_score:
            best_tables, best_score = tables, score
    return best_tables


def get_existing_tally_ledgers(company_name: str) -> dict:
    """Fetches all existing ledger names from Tally to verify matches."""
    request_xml = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Export Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <EXPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>List of Accounts</REPORTNAME>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SYSNAME:XML</SVEXPORTFORMAT>
          <SVCURRENTCOMPANY>{company_name}</SVCURRENTCOMPANY>
          <ACCOUNTTYPE>Ledgers</ACCOUNTTYPE>
        </STATICVARIABLES>
      </REQUESTDESC>
    </EXPORTDATA>
  </BODY>
</ENVELOPE>"""

    try:
        res = requests.post(
            _tally_url(),
            data=request_xml.encode("utf-8"),
            headers={"Content-Type": "text/xml"},
            timeout=5,
        )
        if res.status_code == 200:
            names = re.findall(r"<NAME>(.*?)</NAME>", res.text)
            return {name.strip().lower(): name.strip() for name in names}
    except Exception:
        pass
    return {}


_SUMMARY_ROW_MARKERS = ("opening balance", "closing balance", "statement summary", "b/f", "brought forward", "carried forward")


def extract_bank_transactions(file) -> list[dict]:
    """Runs the actual PDF table extraction. Kept separate from the
    upload endpoint so the parsing logic (dynamic per-bank column
    detection, multi-line description continuation) stays exactly as
    written, independent of how the results get persisted afterwards.

    Two things make this resilient across very different bank PDF layouts
    (SBI, Bank of Baroda, Union Bank, Axis, Karnataka Bank, HDFC) instead
    of being tuned to just one:

    1. _best_tables_for_page() tries a few pdfplumber column-detection
       strategies per page and keeps whichever actually found real
       transaction rows — needed because several banks' statements have no
       real gridlines, which trips up the default strategy. Scoring checks
       every cell in a row for a date (not just column 0), since some
       banks put a serial number before the date column.
    2. Every row's debit/credit is cross-checked against the *running
       balance column* (current row's balance minus the previous row's).
       The balance column is by far the least likely column to ever get
       merged with its neighbour, so whichever side (debit or credit)
       doesn't match how the balance actually moved gets zeroed or
       replaced with the balance-derived amount — this is what catches
       and corrects the "digits from two columns got glued into one giant
       number" failure mode, regardless of which bank's layout caused it,
       and regardless of which side (not just the "active" one) ended up
       holding the garbage.
    """
    transactions = []
    with pdfplumber.open(file) as pdf:
        col_map = {}
        prev_balance = None  # carries across pages — it's one running account balance for the whole statement
        for page in pdf.pages:
            tables = _best_tables_for_page(page)
            for table in tables:
                for row in table:
                    if not row or not any(row):
                        continue

                    # Clean null values and whitespace
                    clean_row = [str(cell).strip() if cell is not None else "" for cell in row]
                    row_str_full = " ".join(clean_row).lower()

                    # Detect Header Row dynamically across all target banks
                    if ("date" in row_str_full or "particulars" in row_str_full or "narration" in row_str_full) and \
                       ("balance" in row_str_full or "debit" in row_str_full or "withdrawal" in row_str_full or "amount" in row_str_full):
                        col_map = detect_column_indices(clean_row)
                        continue

                    if any(marker in row_str_full for marker in _SUMMARY_ROW_MARKERS):
                        # "Opening balance" rows sometimes carry a real date
                        # and a balance figure, which would otherwise look
                        # exactly like a (zero-amount, so normally skipped)
                        # transaction — but do capture the balance itself so
                        # the very first real transaction still has
                        # something to cross-check against.
                        possible_balance = None
                        for cell in clean_row:
                            amt = parse_amount(cell)
                            if amt:
                                possible_balance = amt
                        if possible_balance is not None:
                            prev_balance = possible_balance
                        continue

                    # Inspect Date Column
                    date_col_idx = col_map.get("txn_date", 0)
                    txn_date_candidate = clean_row[date_col_idx] if date_col_idx < len(clean_row) else clean_row[0]

                    # Case A: New Transaction Row (Starts with Date)
                    if DATE_REGEX.match(txn_date_candidate):
                        d_idx = col_map.get("debit")
                        c_idx = col_map.get("credit")
                        b_idx = col_map.get("balance")
                        amt_idx = col_map.get("amount")
                        drcr_idx = col_map.get("drcr")
                        desc_idx = col_map.get("description", 2 if len(clean_row) > 2 else 1)
                        chq_idx = col_map.get("cheque_no", 3 if len(clean_row) > 3 else None)

                        # Fallback positional indexing if header auto-detection was bypassed
                        if d_idx is None and c_idx is None and amt_idx is None:
                            if len(clean_row) >= 8:
                                d_idx, c_idx, b_idx = 5, 6, 7
                            elif len(clean_row) >= 6:
                                d_idx, c_idx, b_idx = 3, 4, 5
                            else:
                                d_idx, c_idx, b_idx = 2, 3, 4

                        txn_date = txn_date_candidate
                        description = clean_row[desc_idx] if desc_idx < len(clean_row) else ""
                        chq_no = clean_row[chq_idx] if chq_idx is not None and chq_idx < len(clean_row) else ""

                        balance_raw = clean_row[b_idx] if b_idx is not None and b_idx < len(clean_row) else ""
                        balance = parse_amount(balance_raw)

                        if amt_idx is not None and d_idx is None and c_idx is None:
                            # Single "Amount" column + separate Dr/Cr indicator layout
                            amt_val = parse_amount(clean_row[amt_idx]) if amt_idx < len(clean_row) else 0.0
                            indicator = (clean_row[drcr_idx] if drcr_idx is not None and drcr_idx < len(clean_row) else "").strip().upper()
                            if amt_val < 0 or indicator.startswith("D"):
                                debit, credit = abs(amt_val), 0.0
                            else:
                                debit, credit = 0.0, abs(amt_val)
                        else:
                            debit_raw = clean_row[d_idx] if d_idx is not None and d_idx < len(clean_row) else ""
                            credit_raw = clean_row[c_idx] if c_idx is not None and c_idx < len(clean_row) else ""
                            debit = parse_amount(debit_raw)
                            credit = parse_amount(credit_raw)

                        # Cross-check against how the running balance actually
                        # moved. Balance is the least likely column to ever
                        # get merged with its neighbour, so it's the most
                        # trustworthy signal available.
                        #
                        # IMPORTANT: don't just check whether the "active"
                        # side (whichever of debit/credit is non-zero)
                        # matches the delta — also force the OTHER side to
                        # zero once it does. Otherwise a garbled value that
                        # leaked into the "wrong" column survives untouched
                        # whenever the correct column already happens to
                        # match the balance movement (this is exactly what
                        # let a corrupted debit slip through even after the
                        # first version of this cross-check was added).
                        if prev_balance is not None and balance:
                            delta = round(balance - prev_balance, 2)
                            if abs(delta) > 0:
                                if credit > 0 and abs(credit - delta) <= 2.0:
                                    debit = 0.0
                                elif debit > 0 and abs(-debit - delta) <= 2.0:
                                    credit = 0.0
                                else:
                                    # Neither side's column-extracted value
                                    # matches how the balance actually moved
                                    # — trust the balance movement entirely
                                    # rather than either column value.
                                    debit = -delta if delta < 0 else 0.0
                                    credit = delta if delta > 0 else 0.0
                        if balance:
                            prev_balance = balance

                        if debit == 0 and credit == 0:
                            continue

                        # Deliberately NOT calling extract_party_name() here.
                        # Guessing a party/ledger name out of a bank
                        # narration is unreliable across bank formats (UPI
                        # purpose codes, bank IFSC codes, and reference
                        # numbers all look enough like names to fool a
                        # heuristic — see extract_party_name()'s own
                        # comments for the false-positive cases already
                        # found). Rather than risk silently mis-filing a
                        # transaction under the wrong ledger, every bank
                        # transaction gets the same placeholder party and
                        # the FULL original narration is preserved as-is —
                        # the actual party gets assigned by a human during
                        # Review & Approve, same as any other row that
                        # can't be confidently auto-matched.
                        particulars = "SALDEBTOR"

                        transactions.append({
                            "txn_date": txn_date,
                            "value_date": txn_date,
                            "particulars": particulars,
                            "cheque_no": chq_no,
                            "branch_code": "",
                            "debit": debit,
                            "credit": credit,
                            "balance": balance,
                            "narration": description,
                        })

                    # Case B: Multi-line Description Continuation Line
                    elif transactions:
                        desc_idx = col_map.get("description", 2 if len(clean_row) > 2 else 1)
                        extra_text = clean_row[desc_idx] if desc_idx < len(clean_row) else " ".join(clean_row)

                        # Filter out page headers and footers
                        if extra_text and not any(k in extra_text.lower() for k in ["page", "statement", "total", "opening balance", "closing balance"]):
                            transactions[-1]["narration"] = (transactions[-1]["narration"] + " " + extra_text).strip()
                            # particulars stays "SALDEBTOR" — see comment above;
                            # continuation lines just extend the narration text.

    return transactions


# ------------------------------------------------------------------
# Persisted upload: bank rows go through the same review pipeline as
# Sales/Purchase/GSTR-2B (Document -> DetectedBill -> Transaction, status
# NEEDS_REVIEW) instead of being pushed to Tally straight off the PDF.
# ------------------------------------------------------------------

import os
import shutil
import uuid
from datetime import datetime as dt

from dateutil import parser as dateparser
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..paths import get_data_dir

UPLOAD_DIR = os.path.join(get_data_dir(), "documents")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _log(db: Session, message: str, document_id: int | None = None, transaction_id: int | None = None):
    db.add(models.AuditLog(document_id=document_id, transaction_id=transaction_id, message=message))
    db.commit()


def _to_iso_date(raw: str) -> str | None:
    if not raw or not raw.strip():
        return None
    try:
        return dateparser.parse(raw.strip(), dayfirst=True, fuzzy=True).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


@router.post("/upload", response_model=schemas.DocumentOut)
async def upload_bank_statement(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF bank statement.")

    ext = os.path.splitext(file.filename)[1] or ".pdf"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = models.Document(
        file_name=file.filename,
        file_path=saved_path,
        document_type="BANK",
        status="PROCESSING",
        uploaded_at=dt.utcnow(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    _log(db, f"Bank statement '{file.filename}' uploaded", document_id=doc.id)

    try:
        with open(saved_path, "rb") as f:
            raw_transactions = extract_bank_transactions(f)
    except Exception as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Bank statement parsing failed: {e}", document_id=doc.id)
        raise HTTPException(status_code=500, detail=f"Failed to parse PDF: {e}")

    if not raw_transactions:
        doc.status = "FAILED"
        db.commit()
        _log(db, "No transaction rows recognized in this PDF's tables.", document_id=doc.id)
        raise HTTPException(
            status_code=400,
            detail="Couldn't find any transaction rows in this PDF — check it's a text-based bank "
                   "statement export (not a scanned image) and try again.",
        )

    # NOTE: extract_bank_transactions() no longer tries to guess a
    # party/ledger name from the narration — every row comes back with
    # particulars="SALDEBTOR" by design (see the comment at its call site).
    # So there's nothing meaningful left to Tally-ledger-match here; the
    # party gets set by a human in Review & Approve instead. Skipping the
    # get_existing_tally_ledgers() call also means one less Tally API round
    # trip (and one less thing that can fail) on every bank statement upload.
    skipped_zero = 0
    for idx, raw in enumerate(raw_transactions):
        debit = float(raw.get("debit") or 0.0)
        credit = float(raw.get("credit") or 0.0)
        if debit <= 0 and credit <= 0:
            # Belt-and-suspenders: extract_bank_transactions() already
            # skips 0/0 rows itself, but if a future column-detection
            # tweak ever lets one slip through, don't persist a
            # transaction that can never be approved anyway — it'd just
            # sit stuck in Review & Approve with no way to fix it short of
            # deleting it by hand.
            skipped_zero += 1
            continue

        bill = models.DetectedBill(
            document_id=doc.id,
            crop_path=None,
            bbox=None,
            order_in_page=idx,
        )
        db.add(bill)
        db.commit()
        db.refresh(bill)

        party = (raw.get("particulars") or "SALDEBTOR").strip() or "SALDEBTOR"

        tx = models.Transaction(
            bill_id=bill.id,
            type="BANK",
            party=party,
            date=_to_iso_date(raw.get("txn_date")),
            invoice_number=raw.get("cheque_no") or None,
            taxable_value=0.0,
            gst_rate=0.0,
            cgst=0.0,
            sgst=0.0,
            igst=0.0,
            total_value=credit if credit > 0 else debit,
            debit=debit,
            credit=credit,
            narration=raw.get("narration") or "",
            confidence=1.0,  # extracted directly from the statement table, not AI-inferred
            status="NEEDS_REVIEW",
            reconciliation_status="UNMATCHED",  # updated by POST /transactions/reconcile
        )
        db.add(tx)

    doc.status = "NEEDS_REVIEW"
    db.commit()
    db.refresh(doc)
    _log(db, f"Parsed {len(raw_transactions)} bank transaction(s), ready for review"
             + (f" ({skipped_zero} row(s) with no detected debit/credit amount were skipped — "
                f"check the PDF's column layout if this number looks high)" if skipped_zero else ""),
         document_id=doc.id)

    # Auto-reconcile against whatever sales/purchase invoices already exist
    # so the Reconciled column is already populated by the time the user
    # opens Review & Approve, instead of showing everything as "Unmatched"
    # until they remember to click the button themselves. Wrapped in
    # try/except: reconciliation is a nice-to-have cross-check, not part of
    # the upload contract — a bug or edge case in it should never turn a
    # successful statement upload into a failed one.
    try:
        stats = reconcile_bank_transactions(db)
        _log(db, f"Auto-reconciled against existing invoices: {stats['matched']} matched, "
                 f"{stats['ambiguous']} ambiguous, {stats['unmatched']} unmatched",
             document_id=doc.id)
    except Exception as e:
        _log(db, f"Auto-reconciliation skipped due to an error (statement upload itself succeeded): {e}",
             document_id=doc.id)

    return doc
