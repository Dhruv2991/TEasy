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
import re
from xml.sax.saxutils import escape as xml_escape
from datetime import datetime


def _tally_date(iso_date) -> str:
    """Tally wants dates as YYYYMMDD. Falls back to today if unparseable/missing."""
    if iso_date:
        try:
            if isinstance(iso_date, datetime):
                return iso_date.strftime("%Y%m%d")
            if hasattr(iso_date, "strftime"):  # datetime.date, pandas Timestamp, etc.
                return iso_date.strftime("%Y%m%d")
            return datetime.fromisoformat(str(iso_date)).strftime("%Y%m%d")
        except (ValueError, TypeError):
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


def find_rate_ledger(ledgers: list[dict], keyword: str, rate: float, exclude: list[str] | None = None) -> str | None:
    """
    Looks for an existing ledger whose name contains `keyword` (e.g. PURCHASE
    or SALE) and the transaction's GST rate written as e.g. '@18%'. Many real
    charts of accounts (like ones already set up for GST return filing) use
    per-rate ledgers such as 'GSTPURCHASE@18%' instead of one flat account —
    this finds the right one automatically instead of requiring manual setup.
    Skips names containing anything in `exclude` (e.g. DISCOUNT, INTERSTATE)
    unless no better match exists.
    """
    if not rate:
        return None
    rate_str = f"@{int(rate)}%" if rate == int(rate) else f"@{rate}%"
    keyword = keyword.upper()
    exclude = [e.upper() for e in (exclude or [])]

    candidates = []
    for led in ledgers:
        name_upper = led["name"].upper().replace(" ", "")
        if keyword in name_upper and rate_str.replace(" ", "") in name_upper:
            candidates.append(led["name"])

    if not candidates:
        return None
    # Prefer a candidate that doesn't contain any excluded word (e.g. skip
    # 'DISCOUNT@18%' or 'INTERSTATE PURCHASE@18%' in favour of a plain one).
    clean = [c for c in candidates if not any(ex in c.upper() for ex in exclude)]
    return (clean or candidates)[0]


def _ledger_exists(ledgers: list[dict], name: str) -> bool:
    name_lower = name.strip().lower()
    return any(led["name"].strip().lower() == name_lower for led in ledgers)


def _create_ledger_master_xml(name: str, under: str, gst_tax_type: str | None = None) -> str:
    """
    A LEDGER master TALLYMESSAGE with ACTION="Create". When included in the
    same import batch ahead of a VOUCHER message, Tally creates the master
    first and the voucher then succeeds — no manual ledger creation needed.
    If gst_tax_type is given (e.g. 'Central Tax'), classifies it as a GST
    duty ledger under Duties & Taxes; otherwise it's a plain ledger (used for
    party/supplier ledgers under Sundry Debtors/Creditors).
    """
    gst_fields = ""
    if gst_tax_type:
        gst_fields = f"""
            <ISBILLWISEON>No</ISBILLWISEON>
            <TAXTYPE>GST</TAXTYPE>
            <GSTDUTYHEAD>{xml_escape(gst_tax_type)}</GSTDUTYHEAD>"""
    return f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="{xml_escape(name)}" ACTION="Create">
            <NAME.LIST>
              <NAME>{xml_escape(name)}</NAME>
            </NAME.LIST>
            <PARENT>{xml_escape(under)}</PARENT>
            <ISBILLWISEON>Yes</ISBILLWISEON>{gst_fields}
          </LEDGER>
        </TALLYMESSAGE>"""


def build_sales_voucher_xml(tx: dict, config: dict, ledgers: list[dict] | None = None) -> str:
    """
    tx: dict with party, date, invoice_number, taxable_value, cgst, sgst,
        igst, total_value (matches the Transaction model's fields).
    config: dict from tally.config.get_tally_config().
    ledgers: live ledger list from tally_client.fetch_ledgers(), used to
        auto-pick rate-specific ledgers and auto-create missing ones. If
        None, falls back to the flat config-only behaviour (manual mapping).
    """
    ledgers = ledgers or []
    date = _tally_date(tx.get("date"))
    party = tx.get("party") or config["cash_ledger"]
    invoice_number = tx.get("invoice_number") or ""
    narration = f"Auto-entered by TEasy from scanned bill{' #' + invoice_number if invoice_number else ''}"
    rate = tx.get("gst_rate") or 0

    sales_ledger = find_rate_ledger(ledgers, "SALE", rate, exclude=["DISCOUNT"]) or config["sales_ledger"]

    masters = []
    if ledgers and not _ledger_exists(ledgers, party):
        masters.append(_create_ledger_master_xml(party, "Sundry Debtors"))
    if ledgers and not _ledger_exists(ledgers, sales_ledger):
        masters.append(_create_ledger_master_xml(sales_ledger, "Sales Accounts"))

    tax_ledgers = {
        "cgst": (config["output_cgst_ledger"], "Central Tax"),
        "sgst": (config["output_sgst_ledger"], "State Tax"),
        "igst": (config["output_igst_ledger"], "Integrated Tax"),
    }
    for field, (ledger_name, tax_type) in tax_ledgers.items():
        if tx.get(field) and ledgers and not _ledger_exists(ledgers, ledger_name):
            masters.append(_create_ledger_master_xml(ledger_name, "Duties & Taxes", gst_tax_type=tax_type))

    entries = []
    # Party ledger: debited (money owed TO the business) for the full total.
    entries.append(_ledger_entry(party, tx["total_value"], is_deemed_positive=True))
    # Sales account: credited for the taxable value.
    entries.append(_ledger_entry(sales_ledger, tx["taxable_value"], is_deemed_positive=False))
    if tx.get("cgst"):
        entries.append(_ledger_entry(config["output_cgst_ledger"], tx["cgst"], is_deemed_positive=False))
    if tx.get("sgst"):
        entries.append(_ledger_entry(config["output_sgst_ledger"], tx["sgst"], is_deemed_positive=False))
    if tx.get("igst"):
        entries.append(_ledger_entry(config["output_igst_ledger"], tx["igst"], is_deemed_positive=False))

    voucher = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Sales" ACTION="Create">
            <DATE>{date}</DATE>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{xml_escape(party)}</PARTYLEDGERNAME>
            <REFERENCE>{xml_escape(invoice_number)}</REFERENCE>
            <NARRATION>{xml_escape(narration)}</NARRATION>
            {''.join(entries)}
          </VOUCHER>
        </TALLYMESSAGE>"""
    return "".join(masters) + voucher


def build_purchase_voucher_xml(tx: dict, config: dict, ledgers: list[dict] | None = None) -> str:
    ledgers = ledgers or []
    date = _tally_date(tx.get("date"))
    party = tx.get("party") or "Unknown Supplier"
    invoice_number = tx.get("invoice_number") or ""
    narration = f"Auto-entered by TEasy from scanned bill{' #' + invoice_number if invoice_number else ''}"
    rate = tx.get("gst_rate") or 0

    purchase_ledger = find_rate_ledger(ledgers, "PURCHASE", rate, exclude=["DISCOUNT", "INTERSTATE"]) or config["purchase_ledger"]

    masters = []
    if ledgers and not _ledger_exists(ledgers, party):
        masters.append(_create_ledger_master_xml(party, "Sundry Creditors"))
    if ledgers and not _ledger_exists(ledgers, purchase_ledger):
        masters.append(_create_ledger_master_xml(purchase_ledger, "Purchase Accounts"))

    tax_ledgers = {
        "cgst": (config["input_cgst_ledger"], "Central Tax"),
        "sgst": (config["input_sgst_ledger"], "State Tax"),
        "igst": (config["input_igst_ledger"], "Integrated Tax"),
    }
    for field, (ledger_name, tax_type) in tax_ledgers.items():
        if tx.get(field) and ledgers and not _ledger_exists(ledgers, ledger_name):
            masters.append(_create_ledger_master_xml(ledger_name, "Duties & Taxes", gst_tax_type=tax_type))

    entries = []
    # Purchase account: debited for the taxable value.
    entries.append(_ledger_entry(purchase_ledger, tx["taxable_value"], is_deemed_positive=True))
    if tx.get("cgst"):
        entries.append(_ledger_entry(config["input_cgst_ledger"], tx["cgst"], is_deemed_positive=True))
    if tx.get("sgst"):
        entries.append(_ledger_entry(config["input_sgst_ledger"], tx["sgst"], is_deemed_positive=True))
    if tx.get("igst"):
        entries.append(_ledger_entry(config["input_igst_ledger"], tx["igst"], is_deemed_positive=True))
    # Supplier ledger: credited (money the business owes) for the full total.
    entries.append(_ledger_entry(party, tx["total_value"], is_deemed_positive=False))

    voucher = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Purchase" ACTION="Create">
            <DATE>{date}</DATE>
            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{xml_escape(party)}</PARTYLEDGERNAME>
            <REFERENCE>{xml_escape(invoice_number)}</REFERENCE>
            <NARRATION>{xml_escape(narration)}</NARRATION>
            {''.join(entries)}
          </VOUCHER>
        </TALLYMESSAGE>"""
    return "".join(masters) + voucher


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
    try:
        from .tally_client import fetch_ledgers
        ledgers = fetch_ledgers()
    except Exception:
        # If the live fetch fails for any reason, fall back to the old
        # manual-mapping-only behaviour rather than blocking the push.
        ledgers = []

    if tx["type"] == "PURCHASE":
        message = build_purchase_voucher_xml(tx, config, ledgers)
    else:
        message = build_sales_voucher_xml(tx, config, ledgers)
    return wrap_envelope(message, config["company_name"])
