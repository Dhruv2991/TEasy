import re
from typing import List, Optional, Dict
from fastapi import APIRouter, File, HTTPException, UploadFile
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


class PushToTallyRequest(BaseModel):
    company_name: Optional[str] = None
    bank_ledger: Optional[str] = None
    transactions: List[BankTransaction]


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


def build_journal_voucher_xml(
    company_name: str, bank_ledger: str, tx: BankTransaction
) -> str:
    raw_date = tx.txn_date or ""
    parts = re.split(r"[/-]", raw_date)
    date_str = f"{parts[2]}{parts[1]}{parts[0]}" if len(parts) == 3 else raw_date

    credit_amt = tx.credit or 0.0
    debit_amt = tx.debit or 0.0
    is_credit = credit_amt > 0
    amount = credit_amt if is_credit else debit_amt

    party_ledger = tx.particulars or "SALDEBTOR"
    narration = tx.narration or "Bank Voucher Entry"

    if is_credit:
        debit_ledger = bank_ledger
        credit_ledger = party_ledger
    else:
        debit_ledger = party_ledger
        credit_ledger = bank_ledger

    return f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>{company_name}</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Journal" ACTION="Create" OBJVIEW="Accounting Voucher View">
            <DATE>{date_str}</DATE>
            <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
            <NARRATION>{narration}</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{debit_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>YES</ISDEEMEDPOSITIVE>
              <AMOUNT>-{amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{credit_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>NO</ISDEEMEDPOSITIVE>
              <AMOUNT>{amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""


@router.post("/upload")
async def upload_bank_statement(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF bank statement.")

    transactions = []
    tally_config = get_tally_config()
    company_name = tally_config.get("company_name", "")
    bank_ledger = tally_config.get("bank_ledger", "")

    try:
        with pdfplumber.open(file.file) as pdf:
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse PDF: {str(e)}")

    return {
        "status": "success",
        "company_name": company_name,
        "bank_ledger": bank_ledger,
        "transactions": transactions,
    }


@router.post("/push-to-tally")
async def push_to_tally(payload: PushToTallyRequest):
    pushed = 0
    errors = []

    tally_config = get_tally_config()
    company_name = payload.company_name or tally_config.get("company_name", "")
    bank_ledger = payload.bank_ledger or tally_config.get("bank_ledger", "")

    # Fetch active ledgers in Tally
    tally_ledgers = get_existing_tally_ledgers(company_name)

    for tx in payload.transactions:
        raw_party = (tx.particulars or "").strip()

        # Match case-insensitively with Tally's ledgers; fallback to SALDEBTOR if missing
        if raw_party.lower() in tally_ledgers:
            tx.particulars = tally_ledgers[raw_party.lower()]
        else:
            tx.particulars = "SALDEBTOR"

        xml_data = build_journal_voucher_xml(
            company_name, bank_ledger, tx
        )
        try:
            res = requests.post(
                _tally_url(),
                data=xml_data.encode("utf-8"),
                headers={"Content-Type": "text/xml"},
                timeout=5,
            )
            if "<CREATED>1</CREATED>" in res.text or "<ALTERED>1</ALTERED>" in res.text:
                pushed += 1
            else:
                errors.append(f"Date {tx.txn_date} ({tx.particulars}): Tally rejected entry")
        except Exception as e:
            errors.append(f"Tally connection error: {str(e)}")

    return {
        "status": "success",
        "pushed": pushed,
        "failed": len(errors),
        "errors": errors,
    }