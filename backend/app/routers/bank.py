import re
from typing import List, Optional, Dict
from fastapi import APIRouter, File, HTTPException, UploadFile, Depends
from pydantic import BaseModel
import pdfplumber
import requests

from ..tally.config import get_tally_config
from ..settings import get_settings

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

# Column Keyword Aliases for HDFC, Axis, Bank of Baroda, Union Bank, Karnataka Bank, and SBI
HEADER_ALIASES = {
    "txn_date": ["txn date", "tran date", "transaction date", "value date", "post date", "date"],
    "description": ["particulars", "narration", "description", "remarks", "transaction details", "details", "txn description"],
    "cheque_no": ["chq/ref number", "chqno", "chq no", "cheque no", "instrument no", "ref no", "tran id", "chq/ref no"],
    "debit": ["withdrawal amount", "withdrawals", "debit", "dr", "dr amount", "withdrawal"],
    "credit": ["deposit amount", "deposits", "credit", "cr", "cr amount", "deposit"],
    "balance": ["closing balance", "balance", "bal", "balance (rs.)"]
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


def extract_party_name(narration: str) -> str:
    if not narration:
        return "SALDEBTOR"

    clean = narration.strip()
    clean = re.sub(r"^(BY|TO)\s+TRANSFER\s*[-:]?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^CHEQUE\s+(DEPOSIT|CLEARING)\s*[-:]?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^(NEFT|RTGS|IMPS|UPI)\s*[-:/]\s*", "", clean, flags=re.IGNORECASE)

    parts = [p.strip() for p in re.split(r"[-/]", clean) if p.strip()]
    for part in parts:
        if not part.isdigit() and len(part) > 2 and not re.match(r"^[A-Z0-9]{9,11}$", part):
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


def extract_bank_transactions(file) -> list[dict]:
    """Runs the actual PDF table extraction. Kept separate from the
    upload endpoint so the parsing logic (dynamic per-bank column
    detection, multi-line description continuation) stays exactly as
    written, independent of how the results get persisted afterwards."""
    transactions = []
    with pdfplumber.open(file) as pdf:
        col_map = {}
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or not any(row):
                        continue

                    # Clean null values and whitespace
                    clean_row = [str(cell).strip() if cell is not None else "" for cell in row]
                    row_str_full = " ".join(clean_row).lower()

                    # Detect Header Row dynamically across all 5 target banks
                    if ("date" in row_str_full or "particulars" in row_str_full or "narration" in row_str_full) and \
                       ("balance" in row_str_full or "debit" in row_str_full or "withdrawal" in row_str_full):
                        col_map = detect_column_indices(clean_row)
                        continue

                    # Inspect Date Column
                    date_col_idx = col_map.get("txn_date", 0)
                    txn_date_candidate = clean_row[date_col_idx] if date_col_idx < len(clean_row) else clean_row[0]

                    # Case A: New Transaction Row (Starts with Date)
                    if DATE_REGEX.match(txn_date_candidate):
                        d_idx = col_map.get("debit")
                        c_idx = col_map.get("credit")
                        b_idx = col_map.get("balance")
                        desc_idx = col_map.get("description", 2 if len(clean_row) > 2 else 1)
                        chq_idx = col_map.get("cheque_no", 3 if len(clean_row) > 3 else None)

                        # Fallback positional indexing if header auto-detection was bypassed
                        if d_idx is None or c_idx is None:
                            if len(clean_row) >= 8:
                                d_idx, c_idx, b_idx = 5, 6, 7
                            elif len(clean_row) >= 6:
                                d_idx, c_idx, b_idx = 3, 4, 5
                            else:
                                d_idx, c_idx, b_idx = 2, 3, 4

                        txn_date = txn_date_candidate
                        description = clean_row[desc_idx] if desc_idx < len(clean_row) else ""
                        chq_no = clean_row[chq_idx] if chq_idx is not None and chq_idx < len(clean_row) else ""

                        debit_raw = clean_row[d_idx] if d_idx is not None and d_idx < len(clean_row) else ""
                        credit_raw = clean_row[c_idx] if c_idx is not None and c_idx < len(clean_row) else ""
                        balance_raw = clean_row[b_idx] if b_idx is not None and b_idx < len(clean_row) else ""

                        debit = parse_amount(debit_raw)
                        credit = parse_amount(credit_raw)
                        balance = parse_amount(balance_raw)

                        if debit == 0 and credit == 0:
                            continue

                        particulars = extract_party_name(description)

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
                            transactions[-1]["particulars"] = extract_party_name(transactions[-1]["narration"])

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

    # Ledger-match the raw particulars against what's actually in Tally right
    # now, same as before — but done once at upload time so what the user
    # sees and edits in Review & Approve is already the real ledger name,
    # not a mismatch they'd only discover at push time. Falls back to
    # "SALDEBTOR" (same as before) when Tally isn't reachable or has no
    # matching ledger; the user can still fix it before approving.
    tally_config = get_tally_config()
    tally_ledgers = get_existing_tally_ledgers(tally_config.get("company_name", ""))

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

        raw_party = (raw.get("particulars") or "").strip()
        party = tally_ledgers.get(raw_party.lower(), raw_party or "SALDEBTOR")

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
        )
        db.add(tx)

    doc.status = "NEEDS_REVIEW"
    db.commit()
    db.refresh(doc)
    _log(db, f"Parsed {len(raw_transactions)} bank transaction(s), ready for review"
             + (f" ({skipped_zero} row(s) with no detected debit/credit amount were skipped — "
                f"check the PDF's column layout if this number looks high)" if skipped_zero else ""),
         document_id=doc.id)

    return doc
