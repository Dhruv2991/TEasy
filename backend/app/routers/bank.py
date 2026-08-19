import re
from typing import List, Optional
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
import pdfplumber
import requests

router = APIRouter(prefix="/bank", tags=["Bank Statements"])

TALLY_URL = "http://127.0.0.1:9000"


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
    company_name: str = "Sarvotham Traders 2026-27"
    bank_ledger: str = "SBIODA/C37970668924"
    transactions: List[BankTransaction]


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
            TALLY_URL,
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
    company_name = "Sarvotham Traders 2026-27"
    bank_ledger = "SBIODA/C37970668924"

    try:
        with pdfplumber.open(file.file) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row or len(row) < 4:
                            continue

                        txn_date = (row[0] or "").strip()
                        if not re.match(r"^\d{2}[/-]\d{2}[/-]\d{4}$", txn_date):
                            continue

                        val_date = (row[1] or "").strip() if len(row) > 1 else ""
                        description = (row[2] or "").strip() if len(row) > 2 else ""
                        chq_no = (row[3] or "").strip() if len(row) > 3 else ""
                        branch_code = (row[4] or "").strip() if len(row) > 4 else ""

                        if len(row) >= 8:
                            debit_raw = row[5]
                            credit_raw = row[6]
                            balance_raw = row[7]
                        else:
                            debit_raw = row[3] if len(row) > 3 else ""
                            credit_raw = row[4] if len(row) > 4 else ""
                            balance_raw = row[5] if len(row) > 5 else ""

                        debit = parse_amount(debit_raw)
                        credit = parse_amount(credit_raw)
                        balance = parse_amount(balance_raw)

                        if debit == 0 and credit == 0:
                            continue

                        particulars = extract_party_name(description)

                        transactions.append({
                            "txn_date": txn_date,
                            "value_date": val_date,
                            "particulars": particulars,
                            "cheque_no": chq_no,
                            "branch_code": branch_code,
                            "debit": debit,
                            "credit": credit,
                            "balance": balance,
                            "narration": description,
                        })
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

    # Fetch active ledgers in Tally
    tally_ledgers = get_existing_tally_ledgers(payload.company_name)

    for tx in payload.transactions:
        raw_party = (tx.particulars or "").strip()

        # Match case-insensitively with Tally's ledgers; fallback to SALDEBTOR if missing
        if raw_party.lower() in tally_ledgers:
            tx.particulars = tally_ledgers[raw_party.lower()]
        else:
            tx.particulars = "SALDEBTOR"

        xml_data = build_journal_voucher_xml(
            payload.company_name, payload.bank_ledger, tx
        )
        try:
            res = requests.post(
                TALLY_URL,
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