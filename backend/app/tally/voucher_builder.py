"""
Builds Tally-Prime-compatible XML for Sales, Purchase, Debit Note, and Credit Note vouchers.
All Note types (Credit Note, Debit Note, CDN) are routed directly into Tally's "Debit Note" register.
"""

from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from ..money import round_rupee


class MissingVoucherDateError(Exception):
    """Raised when a transaction has no usable date to send to Tally."""
    pass


def _tally_date(iso_date) -> str:
    """Formats date as YYYYMMDD for Tally XML.

    Deliberately does NOT fall back to today's date when the source date is
    missing or unparseable — silently mis-dating a real invoice is worse
    than failing loudly, since the user would never notice a wrong date was
    pushed. Callers must catch MissingVoucherDateError and surface it as a
    fixable issue instead of sending the voucher to Tally.
    """
    if iso_date:
        try:
            if isinstance(iso_date, datetime):
                return iso_date.strftime("%Y%m%d")
            if hasattr(iso_date, "strftime"):
                return iso_date.strftime("%Y%m%d")
            return datetime.fromisoformat(str(iso_date)).strftime("%Y%m%d")
        except (ValueError, TypeError):
            pass
    raise MissingVoucherDateError(
        "This transaction has no valid date. Open it in Review & Approve, "
        "set the correct invoice date, and try pushing again."
    )


def find_rate_ledger(ledgers: list[dict], keyword: str, rate: float, exclude: list[str] | None = None) -> str | None:
    if not rate:
        return None
    keyword = keyword.upper()
    exclude = [e.upper() for e in (exclude or [])]
    rate_clean = int(rate) if rate == int(rate) else rate
    possible_rate_patterns = [f"@{rate_clean}%", f"{rate_clean}%", f"@{rate_clean}"]

    candidates = []
    for led in ledgers:
        name_upper = led["name"].upper().replace(" ", "")
        if keyword in name_upper:
            if any(p in name_upper for p in possible_rate_patterns):
                candidates.append(led["name"])

    if not candidates:
        return None
    clean = [c for c in candidates if not any(ex in c.upper() for ex in exclude)]
    return (clean or candidates)[0]


def _ledger_exists(ledgers: list[dict], name: str) -> bool:
    if not name:
        return False
    name_lower = name.strip().lower()
    return any(led["name"].strip().lower() == name_lower for led in ledgers)


def _resolve_ledger_names(vch_type: str, rate: float, config: dict, ledgers: list[dict]) -> dict:
    def _clean(r: float):
        # Preserve real fractional GST rates like 2.5% (half of 5%) or 6.9%
        # instead of rounding them to a whole number — CGST@2% is simply
        # wrong for a 5% invoice, it must read CGST@2.5%. Only strip the
        # decimal when the rate genuinely is a whole number (e.g. 6.0 -> 6).
        return int(r) if r == int(r) else round(r, 2)

    rate_int = _clean(rate) if rate else 0
    half_rate = rate / 2.0 if rate else 0.0
    half_rate_int = _clean(half_rate) if half_rate else 0

    if vch_type.upper() in ["PURCHASE", "DEBIT NOTE"]:
        cfg_main = config.get("purchase_ledger", "Purchase Account")
        cfg_cgst = config.get("input_cgst_ledger", "Input CGST")
        cfg_sgst = config.get("input_sgst_ledger", "Input SGST")
        cfg_igst = config.get("input_igst_ledger", "Input IGST")

        main_default = f"GSTPURCHASE@{rate_int}%" if rate_int > 0 else cfg_main
        cgst_default = f"CGST@{half_rate_int}%" if half_rate_int > 0 else cfg_cgst
        sgst_default = f"SGST@{half_rate_int}%" if half_rate_int > 0 else cfg_sgst
        igst_default = f"IGST@{rate_int}%" if rate_int > 0 else cfg_igst

        main_ledger = find_rate_ledger(ledgers, "PURCHASE", rate, exclude=["DISCOUNT", "INTERSTATE"])
        cgst_ledger = find_rate_ledger(ledgers, "CGST", half_rate)
        sgst_ledger = find_rate_ledger(ledgers, "SGST", half_rate)
        igst_ledger = find_rate_ledger(ledgers, "IGST", rate)

        if not main_ledger:
            main_ledger = cfg_main if _ledger_exists(ledgers, cfg_main) else main_default
        if not cgst_ledger:
            cgst_ledger = cfg_cgst if _ledger_exists(ledgers, cfg_cgst) else cgst_default
        if not sgst_ledger:
            sgst_ledger = cfg_sgst if _ledger_exists(ledgers, cfg_sgst) else sgst_default
        if not igst_ledger:
            igst_ledger = cfg_igst if _ledger_exists(ledgers, cfg_igst) else igst_default

    else:
        cfg_main = config.get("sales_ledger", "Sales Account")
        cfg_cgst = config.get("output_cgst_ledger", "Output CGST")
        cfg_sgst = config.get("output_sgst_ledger", "Output SGST")
        cfg_igst = config.get("output_igst_ledger", "Output IGST")

        main_default = f"GSTSALE@{rate_int}%" if rate_int > 0 else cfg_main
        cgst_default = f"CGST@{half_rate_int}%" if half_rate_int > 0 else cfg_cgst
        sgst_default = f"SGST@{half_rate_int}%" if half_rate_int > 0 else cfg_sgst
        igst_default = f"IGST@{rate_int}%" if rate_int > 0 else cfg_igst

        main_ledger = find_rate_ledger(ledgers, "SALE", rate, exclude=["DISCOUNT"])
        cgst_ledger = find_rate_ledger(ledgers, "CGST", half_rate)
        sgst_ledger = find_rate_ledger(ledgers, "SGST", half_rate)
        igst_ledger = find_rate_ledger(ledgers, "IGST", rate)

        if not main_ledger:
            main_ledger = cfg_main if _ledger_exists(ledgers, cfg_main) else main_default
        if not cgst_ledger:
            cgst_ledger = cfg_cgst if _ledger_exists(ledgers, cfg_cgst) else cgst_default
        if not sgst_ledger:
            sgst_ledger = cfg_sgst if _ledger_exists(ledgers, cfg_sgst) else sgst_default
        if not igst_ledger:
            igst_ledger = cfg_igst if _ledger_exists(ledgers, cfg_igst) else igst_default

    round_off_ledger = config.get("round_off_ledger") or "ROUNDOFF"

    return {
        "main": main_ledger,
        "cgst": cgst_ledger,
        "sgst": sgst_ledger,
        "igst": igst_ledger,
        "round_off": round_off_ledger,
    }


def _create_ledger_master_xml(name: str, under: str, gst_duty_type: str | None = None) -> str:
    gst_fields = ""
    if gst_duty_type:
        gst_fields = f"""
            <TAXTYPE>GST</TAXTYPE>
            <GSTDUTYHEAD>{xml_escape(gst_duty_type)}</GSTDUTYHEAD>"""
    return f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="{xml_escape(name)}" ACTION="Create">
            <NAME.LIST>
              <NAME>{xml_escape(name)}</NAME>
            </NAME.LIST>
            <PARENT>{xml_escape(under)}</PARENT>
            <ISBILLWISEON>No</ISBILLWISEON>{gst_fields}
          </LEDGER>
        </TALLYMESSAGE>"""


def _ledger_entry(name: str, amount: float, is_deemed_positive: bool, rate_pct: float | None = None) -> str:
    amt_val = -abs(amount) if is_deemed_positive else abs(amount)
    dp_str = "Yes" if is_deemed_positive else "No"
    rate_tag = f"\n          <RATE>{int(round(rate_pct))} %</RATE>" if rate_pct and rate_pct > 0 else ""

    return f"""
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{xml_escape(name)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>{dp_str}</ISDEEMEDPOSITIVE>
          <AMOUNT>{amt_val:.2f}</AMOUNT>{rate_tag}
        </ALLLEDGERENTRIES.LIST>"""


def build_voucher_xml(vch_type_str: str, tx: dict, config: dict, ledgers: list[dict] | None = None) -> str:
    ledgers = ledgers or []
    date = _tally_date(tx.get("date"))
    party = tx.get("party") or "Unknown Party"
    invoice_number = tx.get("invoice_number") or ""
    narration = f"Auto-entered by TEasy ({vch_type_str}){' #' + invoice_number if invoice_number else ''}"

    taxable = float(tx.get("taxable_value") or 0.0)
    cgst = float(tx.get("cgst") or 0.0)
    sgst = float(tx.get("sgst") or 0.0)
    igst = float(tx.get("igst") or 0.0)

    # This is the ONLY place total_value gets rounded to a whole rupee.
    # Every earlier stage (AI extraction, GSTR-2B import, manual edit)
    # stores the exact value as read/typed — taxable_value and the GST
    # components are never rounded early either. Rounding total_value
    # early while leaving the tax components exact is exactly what used
    # to create a spurious mismatch between "taxable + tax" and "total"
    # that had nothing to do with the actual bill. Rounding happens here,
    # once, right before the voucher is actually written, and any real
    # residual difference goes to the ROUNDOFF ledger below — same as
    # Tally's own convention of rounding the invoice total, not its
    # components.
    total = round_rupee(tx.get("total_value")) or 0.0

    rate = float(tx.get("gst_rate") or 0.0)
    if not rate and taxable > 0:
        rate = round(((cgst + sgst + igst) / taxable) * 100, 2)

    calculated = taxable + cgst + sgst + igst
    diff = round(total - calculated, 2)

    # A per-rate breakdown (from a reconciled supplier invoice, see
    # gstr2b/supplier_match.py) means this is a genuinely mixed-rate
    # invoice: several line items at several GST rates on one bill. Tally
    # represents that as ONE voucher with multiple main/CGST/SGST/IGST line
    # sets -- never as several separate vouchers for a single physical bill.
    raw_breakdown = tx.get("rate_breakdown")
    breakdown = raw_breakdown if raw_breakdown else [
        {"rate": rate, "taxable_value": taxable, "cgst": cgst, "sgst": sgst, "igst": igst}
    ]

    # Accounting Signs (IsDeemedPositive)
    if vch_type_str.upper() in ["PURCHASE"]:
        party_dp = False
        main_dp = True
    else:  # SALES, DEBIT NOTE
        party_dp = True
        main_dp = False

    parent_group = "Sundry Creditors" if vch_type_str.upper() in ["PURCHASE", "DEBIT NOTE"] else "Sundry Debtors"
    main_group = "Purchase Accounts" if vch_type_str.upper() in ["PURCHASE", "DEBIT NOTE"] else "Sales Accounts"
    round_off_ledger = config.get("round_off_ledger") or "ROUNDOFF"

    masters = []
    entries = [_ledger_entry(party, total, is_deemed_positive=party_dp)]
    if ledgers and not _ledger_exists(ledgers, party):
        masters.append(_create_ledger_master_xml(party, parent_group))

    for part in breakdown:
        part_rate = float(part.get("rate") or 0.0)
        part_taxable = float(part.get("taxable_value") or 0.0)
        part_cgst = float(part.get("cgst") or 0.0)
        part_sgst = float(part.get("sgst") or 0.0)
        part_igst = float(part.get("igst") or 0.0)
        part_half_rate = part_rate / 2.0 if part_rate > 0 else None

        names = _resolve_ledger_names(vch_type_str, part_rate, config, ledgers)

        if ledgers:
            if not _ledger_exists(ledgers, names["main"]):
                masters.append(_create_ledger_master_xml(names["main"], main_group))
            if part_cgst > 0 and not _ledger_exists(ledgers, names["cgst"]):
                masters.append(_create_ledger_master_xml(names["cgst"], "Duties & Taxes", "Central Tax"))
            if part_sgst > 0 and not _ledger_exists(ledgers, names["sgst"]):
                masters.append(_create_ledger_master_xml(names["sgst"], "Duties & Taxes", "State Tax"))
            if part_igst > 0 and not _ledger_exists(ledgers, names["igst"]):
                masters.append(_create_ledger_master_xml(names["igst"], "Duties & Taxes", "Integrated Tax"))

        entries.append(_ledger_entry(names["main"], part_taxable, is_deemed_positive=main_dp))
        if part_cgst:
            entries.append(_ledger_entry(names["cgst"], part_cgst, is_deemed_positive=main_dp, rate_pct=part_half_rate))
        if part_sgst:
            entries.append(_ledger_entry(names["sgst"], part_sgst, is_deemed_positive=main_dp, rate_pct=part_half_rate))
        if part_igst:
            entries.append(_ledger_entry(names["igst"], part_igst, is_deemed_positive=main_dp, rate_pct=part_rate))

    if ledgers and abs(diff) >= 0.01 and not _ledger_exists(ledgers, round_off_ledger):
        masters.append(_create_ledger_master_xml(round_off_ledger, "Indirect Expenses"))
    names = {"round_off": round_off_ledger}

    # CORRECTED ROUND-OFF BALANCING
    if abs(diff) >= 0.01:
        if not party_dp:  # Purchase / Debit Note
            round_off_dp = (diff > 0)
        else:             # Sales / Credit Note
            round_off_dp = (diff < 0)
        entries.append(_ledger_entry(names["round_off"], abs(diff), is_deemed_positive=round_off_dp))

    vch_no_tag = f"<VOUCHERNUMBER>{xml_escape(invoice_number)}</VOUCHERNUMBER>" if invoice_number else ""

    voucher = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="{vch_type_str}" ACTION="Create">
            <DATE>{date}</DATE>
            <VOUCHERTYPENAME>{vch_type_str}</VOUCHERTYPENAME>
            {vch_no_tag}
            <PARTYLEDGERNAME>{xml_escape(party)}</PARTYLEDGERNAME>
            <REFERENCE>{xml_escape(invoice_number)}</REFERENCE>
            <ISINVOICE>Yes</ISINVOICE>
            <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
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


def build_bank_voucher_xml(tx: dict, config: dict) -> str:
    """Journal voucher for a bank-statement row: bank ledger on one side,
    the matched counter-party ledger on the other. Mirrors the standalone
    logic bank.py used to build inline before pushing straight to Tally —
    now driven off the reviewed/approved Transaction instead of the raw
    parsed PDF row."""
    date = _tally_date(tx.get("date"))
    party = tx.get("party") or "SALDEBTOR"
    bank_ledger = tx.get("bank_ledger") or config.get("bank_ledger") or "Bank Account"
    narration = tx.get("narration") or f"Auto-entered by TEasy (Bank){' #' + tx['invoice_number'] if tx.get('invoice_number') else ''}"

    debit = float(tx.get("debit") or 0.0)
    credit = float(tx.get("credit") or 0.0)
    is_credit = credit > 0
    amount = credit if is_credit else debit

    # A credit in the statement (money coming IN) means the bank ledger is
    # debited and the counter-party is credited, and vice versa for a
    # debit/withdrawal — standard journal-entry convention.
    if is_credit:
        debit_ledger, credit_ledger = bank_ledger, party
    else:
        debit_ledger, credit_ledger = party, bank_ledger

    entries = (
        _ledger_entry(debit_ledger, amount, is_deemed_positive=True)
        + _ledger_entry(credit_ledger, amount, is_deemed_positive=False)
    )

    return f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Journal" ACTION="Create" OBJVIEW="Accounting Voucher View">
            <DATE>{date}</DATE>
            <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
            <NARRATION>{xml_escape(narration)}</NARRATION>
            {entries}
          </VOUCHER>
        </TALLYMESSAGE>"""


def build_voucher_envelope(tx: dict, config: dict) -> str:
    try:
        from .tally_client import fetch_ledgers
        ledgers = fetch_ledgers()
    except Exception:
        ledgers = []

    raw_type = str(tx.get("type", "PURCHASE")).upper().replace("_", " ")

    if raw_type == "BANK":
        message = build_bank_voucher_xml(tx, config)
        return wrap_envelope(message, config["company_name"])

    if any(k in raw_type for k in ["CREDIT", "DEBIT", "NOTE", "CDN"]):
        vch_type = "Debit Note"
    elif "SALE" in raw_type:
        vch_type = "Sales"
    else:
        vch_type = "Purchase"

    message = build_voucher_xml(vch_type, tx, config, ledgers)
    return wrap_envelope(message, config["company_name"])