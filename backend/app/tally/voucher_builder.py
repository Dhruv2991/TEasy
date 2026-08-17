"""
Builds Tally-Prime-compatible XML for Sales and Purchase vouchers, in the
standard "XML Import" envelope format Tally's HTTP/XML server accepts.

Reference structure (Tally's own documented import format):

<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES><SVCURRENTCOMPANY>...</SVCURRENTCOMPANY></STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Sales" ACTION="Create">
            ...
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>

A voucher has one party-side ledger entry (debit for sales, credit for
purchase — Tally's sign convention is the opposite of plain-English "debit
the customer" bookkeeping intuition, it's ISDEEMEDPOSITIVE per entry) and one
or more ledger entries for the sales/purchase account + tax ledgers.
"""
import html
from xml.sax.saxutils import escape as xml_escape
from datetime import datetime


def _tally_date(iso_date: str | None) -> str:
    """Tally wants dates as YYYYMMDD. Falls back to today if unparseable/missing."""
    if iso_date:
        try:
            return datetime.fromisoformat(iso_date).strftime("%Y%m%d")
        except ValueError:
            pass
    return datetime.utcnow().strftime("%Y%m%d")


def _ledger_entry(ledger_name: str, amount: float, is_deemed_positive: bool) -> str:
    """
    amount should be given as a positive number; is_deemed_positive controls
    the debit/credit sign per Tally's convention (True = debit for this entry).
    """
    signed_amount = amount if is_deemed_positive else -amount
    return f"""
      <ALLLEDGERENTRIES.LIST>
        <LEDGERNAME>{xml_escape(ledger_name)}</LEDGERNAME>
        <ISDEEMEDPOSITIVE>{"Yes" if is_deemed_positive else "No"}</ISDEEMEDPOSITIVE>
        <AMOUNT>{signed_amount:.2f}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>"""


def build_sales_voucher_xml(tx: dict, config: dict) -> str:
    """
    tx: dict with party, date, invoice_number, taxable_value, cgst, sgst,
        igst, total_value (matches the Transaction model's fields).
    config: dict from tally.config.get_tally_config().
    """
    date = _tally_date(tx.get("date"))
    party = tx.get("party") or config["cash_ledger"]
    invoice_number = tx.get("invoice_number") or ""
    narration = f"Auto-entered by TEasy from scanned bill{' #' + invoice_number if invoice_number else ''}"

    entries = []
    # Party ledger: debited (money owed TO the business) for the full total.
    entries.append(_ledger_entry(party, tx["total_value"], is_deemed_positive=True))
    # Sales account: credited for the taxable value.
    entries.append(_ledger_entry(config["sales_ledger"], tx["taxable_value"], is_deemed_positive=False))
    if tx.get("cgst"):
        entries.append(_ledger_entry(config["output_cgst_ledger"], tx["cgst"], is_deemed_positive=False))
    if tx.get("sgst"):
        entries.append(_ledger_entry(config["output_sgst_ledger"], tx["sgst"], is_deemed_positive=False))
    if tx.get("igst"):
        entries.append(_ledger_entry(config["output_igst_ledger"], tx["igst"], is_deemed_positive=False))

    return f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
            <DATE>{date}</DATE>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{xml_escape(party)}</PARTYLEDGERNAME>
            <REFERENCE>{xml_escape(invoice_number)}</REFERENCE>
            <NARRATION>{xml_escape(narration)}</NARRATION>
            {''.join(entries)}
          </VOUCHER>
        </TALLYMESSAGE>"""


def build_purchase_voucher_xml(tx: dict, config: dict) -> str:
    date = _tally_date(tx.get("date"))
    party = tx.get("party") or "Unknown Supplier"
    invoice_number = tx.get("invoice_number") or ""
    narration = f"Auto-entered by TEasy from scanned bill{' #' + invoice_number if invoice_number else ''}"

    entries = []
    # Purchase account: debited for the taxable value.
    entries.append(_ledger_entry(config["purchase_ledger"], tx["taxable_value"], is_deemed_positive=True))
    if tx.get("cgst"):
        entries.append(_ledger_entry(config["input_cgst_ledger"], tx["cgst"], is_deemed_positive=True))
    if tx.get("sgst"):
        entries.append(_ledger_entry(config["input_sgst_ledger"], tx["sgst"], is_deemed_positive=True))
    if tx.get("igst"):
        entries.append(_ledger_entry(config["input_igst_ledger"], tx["igst"], is_deemed_positive=True))
    # Supplier ledger: credited (money the business owes) for the full total.
    entries.append(_ledger_entry(party, tx["total_value"], is_deemed_positive=False))

    return f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Invoice Voucher View">
            <DATE>{date}</DATE>
            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{xml_escape(party)}</PARTYLEDGERNAME>
            <REFERENCE>{xml_escape(invoice_number)}</REFERENCE>
            <NARRATION>{xml_escape(narration)}</NARRATION>
            {''.join(entries)}
          </VOUCHER>
        </TALLYMESSAGE>"""


def wrap_envelope(tally_messages_xml: str, company_name: str) -> str:
    return f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>{xml_escape(company_name)}</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        {tally_messages_xml}
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""


def build_voucher_envelope(tx: dict, config: dict) -> str:
    if tx["type"] == "PURCHASE":
        message = build_purchase_voucher_xml(tx, config)
    else:
        message = build_sales_voucher_xml(tx, config)
    return wrap_envelope(message, config["company_name"])
