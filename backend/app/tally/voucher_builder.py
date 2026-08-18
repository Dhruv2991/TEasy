"""
Builds Tally-Prime-compatible XML for Sales, Purchase, Debit Note, and Credit Note vouchers.
All Note types (Credit Note, Debit Note, CDN) are routed directly into Tally's "Debit Note" register.
"""

from datetime import datetime
from xml.sax.saxutils import escape as xml_escape


def _tally_date(iso_date) -> str:
    """Formats date as YYYYMMDD for Tally XML."""
    if iso_date:
        try:
            if isinstance(iso_date, datetime):
                return iso_date.strftime("%Y%m%d")
            if hasattr(iso_date, "strftime"):
                return iso_date.strftime("%Y%m%d")
            return datetime.fromisoformat(str(iso_date)).strftime("%Y%m%d")
        except (ValueError, TypeError):
            pass
    return datetime.utcnow().strftime("%Y%m%d")


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
    rate_int = int(round(rate)) if rate else 0
    half_rate = rate / 2.0 if rate else 0.0
    half_rate_int = int(round(half_rate)) if half_rate else 0

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
    total = float(tx.get("total_value") or 0.0)

    rate = float(tx.get("gst_rate") or 0.0)
    if not rate and taxable > 0:
        rate = round(((cgst + sgst + igst) / taxable) * 100, 2)
    half_rate = rate / 2.0 if rate > 0 else None

    calculated = taxable + cgst + sgst + igst
    diff = round(total - calculated, 2)

    names = _resolve_ledger_names(vch_type_str, rate, config, ledgers)

    # Accounting Signs (IsDeemedPositive)
    if vch_type_str.upper() in ["PURCHASE"]:
        party_dp = False
        main_dp = True
    else:  # SALES, DEBIT NOTE
        party_dp = True
        main_dp = False

    parent_group = "Sundry Creditors" if vch_type_str.upper() in ["PURCHASE", "DEBIT NOTE"] else "Sundry Debtors"
    main_group = "Purchase Accounts" if vch_type_str.upper() in ["PURCHASE", "DEBIT NOTE"] else "Sales Accounts"

    masters = []
    if ledgers:
        if not _ledger_exists(ledgers, party):
            masters.append(_create_ledger_master_xml(party, parent_group))
        if not _ledger_exists(ledgers, names["main"]):
            masters.append(_create_ledger_master_xml(names["main"], main_group))
        if cgst > 0 and not _ledger_exists(ledgers, names["cgst"]):
            masters.append(_create_ledger_master_xml(names["cgst"], "Duties & Taxes", "Central Tax"))
        if sgst > 0 and not _ledger_exists(ledgers, names["sgst"]):
            masters.append(_create_ledger_master_xml(names["sgst"], "Duties & Taxes", "State Tax"))
        if igst > 0 and not _ledger_exists(ledgers, names["igst"]):
            masters.append(_create_ledger_master_xml(names["igst"], "Duties & Taxes", "Integrated Tax"))
        if abs(diff) >= 0.01 and not _ledger_exists(ledgers, names["round_off"]):
            masters.append(_create_ledger_master_xml(names["round_off"], "Indirect Expenses"))

    entries = []
    entries.append(_ledger_entry(party, total, is_deemed_positive=party_dp))
    entries.append(_ledger_entry(names["main"], taxable, is_deemed_positive=main_dp))
    if cgst:
        entries.append(_ledger_entry(names["cgst"], cgst, is_deemed_positive=main_dp, rate_pct=half_rate))
    if sgst:
        entries.append(_ledger_entry(names["sgst"], sgst, is_deemed_positive=main_dp, rate_pct=half_rate))
    if igst:
        entries.append(_ledger_entry(names["igst"], igst, is_deemed_positive=main_dp, rate_pct=rate))

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


def build_voucher_envelope(tx: dict, config: dict) -> str:
    try:
        from .tally_client import fetch_ledgers
        ledgers = fetch_ledgers()
    except Exception:
        ledgers = []

    raw_type = str(tx.get("type", "PURCHASE")).upper().replace("_", " ")

    if any(k in raw_type for k in ["CREDIT", "DEBIT", "NOTE", "CDN"]):
        vch_type = "Debit Note"
    elif "SALE" in raw_type:
        vch_type = "Sales"
    else:
        vch_type = "Purchase"

    message = build_voucher_xml(vch_type, tx, config, ledgers)
    return wrap_envelope(message, config["company_name"])