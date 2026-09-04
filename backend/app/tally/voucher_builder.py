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


_BUILTIN_LEDGER_NAMES = {"cash", "profit & loss a/c", "profit and loss a/c"}


def _should_skip_master_creation(name: str) -> bool:
    return bool(name) and name.strip().lower() in _BUILTIN_LEDGER_NAMES


def _resolve_ledger_names(vch_type: str, rate: float, config: dict, ledgers: list[dict]) -> dict:
    def _clean(r: float):
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


def _stock_item_exists(stock_items: list[dict], name: str) -> bool:
    if not name:
        return False
    name_lower = name.strip().lower()
    return any(si["name"].strip().lower() == name_lower for si in stock_items)


def _create_unit_master_xml(unit_name: str) -> str:
    """Creates unit of measure master XML in Tally."""
    return f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <UNIT NAME="{xml_escape(unit_name)}" ACTION="Create">
            <NAME>{xml_escape(unit_name)}</NAME>
            <ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
          </UNIT>
        </TALLYMESSAGE>"""


def _create_stock_item_master_xml(name: str, unit: str | None) -> str:
    unit = unit or "Nos"
    return f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <STOCKITEM NAME="{xml_escape(name)}" ACTION="Create">
            <NAME.LIST>
              <NAME>{xml_escape(name)}</NAME>
            </NAME.LIST>
            <PARENT>Primary</PARENT>
            <BASEUNITS>{xml_escape(unit)}</BASEUNITS>
            <ISBATCHWISEON>No</ISBATCHWISEON>
          </STOCKITEM>
        </TALLYMESSAGE>"""


def _inventory_entry(
    stock_item: str,
    qty: float | None,
    unit: str | None,
    price: float | None,
    amount: float,
    ledger_name: str,
    is_deemed_positive: bool,
) -> str:
    amt_val = -abs(amount) if is_deemed_positive else abs(amount)
    dp_str = "Yes" if is_deemed_positive else "No"
    unit = unit or "Nos"
    qty_val = qty if qty not in (None, 0) else 1

    rate_tag = ""
    if price:
        rate_tag = f"\n          <RATE>{price:.2f}/{xml_escape(unit)}</RATE>"

    return f"""
        <ALLINVENTORYENTRIES.LIST>
          <STOCKITEMNAME>{xml_escape(stock_item)}</STOCKITEMNAME>
          <ISDEEMEDPOSITIVE>{dp_str}</ISDEEMEDPOSITIVE>{rate_tag}
          <AMOUNT>{amt_val:.2f}</AMOUNT>
          <ACTUALQTY>{qty_val} {xml_escape(unit)}</ACTUALQTY>
          <BILLEDQTY>{qty_val} {xml_escape(unit)}</BILLEDQTY>
          <ACCOUNTINGALLOCATIONS.LIST>
            <LEDGERNAME>{xml_escape(ledger_name)}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>{dp_str}</ISDEEMEDPOSITIVE>
            <AMOUNT>{amt_val:.2f}</AMOUNT>
          </ACCOUNTINGALLOCATIONS.LIST>
        </ALLINVENTORYENTRIES.LIST>"""


def _ledger_entry(name: str, amount: float, is_deemed_positive: bool, rate_pct: float | None = None) -> str:
    amt_val = -abs(amount) if is_deemed_positive else abs(amount)
    dp_str = "Yes" if is_deemed_positive else "No"
    
    if rate_pct and rate_pct > 0:
        clean_rate = int(rate_pct) if rate_pct == int(rate_pct) else round(rate_pct, 2)
        rate_tag = f"\n          <RATE>{clean_rate} %</RATE>"
    else:
        rate_tag = ""

    return f"""
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{xml_escape(name)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>{dp_str}</ISDEEMEDPOSITIVE>
          <AMOUNT>{amt_val:.2f}</AMOUNT>{rate_tag}
        </ALLLEDGERENTRIES.LIST>"""


def _invoice_ledger_entry(name: str, amount: float, is_deemed_positive: bool, rate_pct: float | None = None, is_party: bool = False) -> str:
    """Same as _ledger_entry, but for vouchers using
    PERSISTEDVIEW="Invoice Voucher View" (i.e. build_item_voucher_xml's
    item/inventory vouchers). Tally's XML schema uses a DIFFERENT tag for
    the non-inventory ledger lines depending on voucher view:
    <ALLLEDGERENTRIES.LIST> for "Accounting Voucher View" (plain
    build_voucher_xml, which is why that path works), and
    <LEDGERENTRIES.LIST> (no "ALL" prefix) for "Invoice Voucher View".
    Sending the wrong tag doesn't error — Tally silently drops those
    lines from the imported voucher, which is exactly what caused a
    voucher with correct inventory items but with the party/CGST/SGST/
    round-off entries missing entirely, leaving the voucher unbalanced
    and stuck in Tally's Import Exceptions with no Debit side at all.
    The party line also needs <ISPARTYLEDGER>Yes</ISPARTYLEDGER> in this
    view for Tally to recognize which ledger is the invoice's party.
    """
    amt_val = -abs(amount) if is_deemed_positive else abs(amount)
    dp_str = "Yes" if is_deemed_positive else "No"

    if rate_pct and rate_pct > 0:
        clean_rate = int(rate_pct) if rate_pct == int(rate_pct) else round(rate_pct, 2)
        rate_tag = f"\n          <RATE>{clean_rate} %</RATE>"
    else:
        rate_tag = ""

    party_tag = "\n          <ISPARTYLEDGER>Yes</ISPARTYLEDGER>" if is_party else ""

    return f"""
        <LEDGERENTRIES.LIST>
          <LEDGERNAME>{xml_escape(name)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>{dp_str}</ISDEEMEDPOSITIVE>{party_tag}
          <AMOUNT>{amt_val:.2f}</AMOUNT>{rate_tag}
        </LEDGERENTRIES.LIST>"""


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

    total = round_rupee(tx.get("total_value")) or 0.0

    rate = float(tx.get("gst_rate") or 0.0)
    if not rate and taxable > 0:
        rate = round(((cgst + sgst + igst) / taxable) * 100, 2)

    calculated = taxable + cgst + sgst + igst
    diff = round(total - calculated, 2)

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
    if ledgers and not _ledger_exists(ledgers, party) and not _should_skip_master_creation(party):
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

    # Round-off Balancing
    if abs(diff) >= 0.01:
        if not party_dp:  # Purchase
            round_off_dp = (diff > 0)
        else:             # Sales / Debit Note
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


def build_item_voucher_xml(
    vch_type_str: str,
    tx: dict,
    config: dict,
    ledgers: list[dict] | None = None,
    stock_items: list[dict] | None = None,
) -> str:
    """
    Item-wise version of build_voucher_xml: emits ALLINVENTORYENTRIES.LIST
    (one per stock item) instead of a single lump-sum ledger entry, for a
    transaction that carries a tx["items"] list — see models.Transaction.items.
    Falls back to build_voucher_xml if there are no items to work with.
    """
    ledgers = ledgers or []
    stock_items = stock_items or []
    items = tx.get("items") or []
    if not items:
        return build_voucher_xml(vch_type_str, tx, config, ledgers)

    date = _tally_date(tx.get("date"))
    party = tx.get("party") or "Unknown Party"
    invoice_number = tx.get("invoice_number") or ""
    narration = tx.get("narration") or f"Auto-entered by TEasy ({vch_type_str}){' #' + invoice_number if invoice_number else ''}"

    total = round_rupee(tx.get("total_value")) or 0.0

    raw_breakdown = tx.get("rate_breakdown")
    breakdown = raw_breakdown if raw_breakdown else [{
        "rate": float(tx.get("gst_rate") or 0.0),
        "taxable_value": float(tx.get("taxable_value") or 0.0),
        "cgst": float(tx.get("cgst") or 0.0),
        "sgst": float(tx.get("sgst") or 0.0),
        "igst": float(tx.get("igst") or 0.0),
    }]

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
    if not _ledger_exists(ledgers, party) and not _should_skip_master_creation(party):
        masters.append(_create_ledger_master_xml(party, parent_group))

    # One resolved sales/purchase ledger name per GST rate present, so each
    # item's inventory allocation points at the right rate-wise account.
    ledger_by_rate: dict[float, dict] = {}
    for part in breakdown:
        part_rate = float(part.get("rate") or 0.0)
        if part_rate not in ledger_by_rate:
            names = _resolve_ledger_names(vch_type_str, part_rate, config, ledgers)
            ledger_by_rate[part_rate] = names
            if not _ledger_exists(ledgers, names["main"]):
                masters.append(_create_ledger_master_xml(names["main"], main_group))

    entries = [_invoice_ledger_entry(party, total, is_deemed_positive=party_dp, is_party=True)]

    calculated = 0.0
    for item in items:
        name = item.get("name") or "Unnamed Item"
        item_rate = float(item.get("rate") or 0.0)
        amount = float(item.get("amount") or 0.0)
        calculated += amount

        names = ledger_by_rate.get(item_rate)
        if names is None:
            names = _resolve_ledger_names(vch_type_str, item_rate, config, ledgers)
            ledger_by_rate[item_rate] = names
            if not _ledger_exists(ledgers, names["main"]):
                masters.append(_create_ledger_master_xml(names["main"], main_group))

        entries.append(_inventory_entry(
            stock_item=name,
            qty=item.get("qty"),
            unit=item.get("unit"),
            price=item.get("price"),
            amount=amount,
            ledger_name=names["main"],
            is_deemed_positive=main_dp,
        ))

    # De-duplicated stock item master creation (once per distinct item name).
    seen_items = set()
    for item in items:
        name = item.get("name") or "Unnamed Item"
        if name in seen_items:
            continue
        seen_items.add(name)
        if not _stock_item_exists(stock_items, name):
            masters.append(_create_stock_item_master_xml(name, item.get("unit")))

    # Tax ledgers (CGST/SGST/IGST) sit outside the inventory allocation,
    # same as the plain ledger-only voucher.
    for part in breakdown:
        part_rate = float(part.get("rate") or 0.0)
        part_cgst = float(part.get("cgst") or 0.0)
        part_sgst = float(part.get("sgst") or 0.0)
        part_igst = float(part.get("igst") or 0.0)
        part_half_rate = part_rate / 2.0 if part_rate > 0 else None
        names = ledger_by_rate.get(part_rate) or _resolve_ledger_names(vch_type_str, part_rate, config, ledgers)

        if part_cgst > 0 and not _ledger_exists(ledgers, names["cgst"]):
            masters.append(_create_ledger_master_xml(names["cgst"], "Duties & Taxes", "Central Tax"))
        if part_sgst > 0 and not _ledger_exists(ledgers, names["sgst"]):
            masters.append(_create_ledger_master_xml(names["sgst"], "Duties & Taxes", "State Tax"))
        if part_igst > 0 and not _ledger_exists(ledgers, names["igst"]):
            masters.append(_create_ledger_master_xml(names["igst"], "Duties & Taxes", "Integrated Tax"))

        if part_cgst:
            entries.append(_invoice_ledger_entry(names["cgst"], part_cgst, is_deemed_positive=main_dp, rate_pct=part_half_rate))
            calculated += part_cgst
        if part_sgst:
            entries.append(_invoice_ledger_entry(names["sgst"], part_sgst, is_deemed_positive=main_dp, rate_pct=part_half_rate))
            calculated += part_sgst
        if part_igst:
            entries.append(_invoice_ledger_entry(names["igst"], part_igst, is_deemed_positive=main_dp, rate_pct=part_rate))
            calculated += part_igst

    diff = round(total - calculated, 2)
    if abs(diff) >= 0.01:
        if not _ledger_exists(ledgers, round_off_ledger):
            masters.append(_create_ledger_master_xml(round_off_ledger, "Indirect Expenses"))
        if not party_dp:  # Purchase
            round_off_dp = (diff > 0)
        else:             # Sales / Debit Note
            round_off_dp = (diff < 0)
        entries.append(_invoice_ledger_entry(round_off_ledger, abs(diff), is_deemed_positive=round_off_dp))

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
            <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
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
    """Journal voucher for a bank-statement row."""
    date = _tally_date(tx.get("date"))
    party = tx.get("party") or "SALDEBTOR"
    bank_ledger = tx.get("bank_ledger") or config.get("bank_ledger") or "Bank Account"
    narration = tx.get("narration") or f"Auto-entered by TEasy (Bank){' #' + tx['invoice_number'] if tx.get('invoice_number') else ''}"

    debit = float(tx.get("debit") or 0.0)
    credit = float(tx.get("credit") or 0.0)
    is_credit = credit > 0
    amount = credit if is_credit else debit

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
        if not ledgers:
            # fetch_ledgers() returning a genuinely empty list is
            # indistinguishable here from "the fetch silently failed" —
            # and treating an empty result as "nothing exists yet" is
            # dangerous: it makes the code below try to re-Create common
            # built-in ledgers (Cash, etc.) that almost certainly already
            # exist in any real Tally company, which Tally then reports
            # as an unexpected ALTER (wrong parent group) instead of the
            # voucher itself being created. Force one uncached retry
            # before accepting an empty list as real.
            ledgers = fetch_ledgers(force_refresh=True)
    except Exception:
        ledgers = []

    raw_type = str(tx.get("type", "PURCHASE")).upper().replace("_", " ")

    if raw_type == "BANK":
        message = build_bank_voucher_xml(tx, config)
        return wrap_envelope(message, config["company_name"])

    # All Note types (CREDIT, DEBIT, NOTE, CDN) route straight to Debit Note
    if any(k in raw_type for k in ["CREDIT", "DEBIT", "NOTE", "CDN"]):
        vch_type = "Debit Note"
    elif "SALE" in raw_type:
        vch_type = "Sales"
    else:
        vch_type = "Purchase"

    if tx.get("items"):
        try:
            from .tally_client import fetch_stock_items, send_voucher_xml
            stock_items = fetch_stock_items()
            if not stock_items:
                stock_items = fetch_stock_items(force_refresh=True)
        except Exception:
            stock_items = []
            send_voucher_xml = None

        # Tally quirk: creating a brand-new STOCKITEM (and/or its UNIT)
        # master in the *same* XML request as a VOUCHER that immediately
        # references it is unreliable — the voucher's inventory line can
        # get validated before the master is fully committed, failing
        # with "Stock Item '...' does not exist!" even though the master
        # creation itself reported no error. Ledgers don't have this
        # problem (Tally commits those synchronously within one request),
        # so only stock items/units need this two-step treatment.
        # Fix: create any missing unit/stock-item masters in their own
        # request FIRST, confirm Tally accepted them, then build the
        # voucher itself referencing masters that are now guaranteed to
        # already exist.
        items = tx.get("items") or []
        missing_units = []
        missing_stock_items = []
        seen_units, seen_names = set(), set()
        for item in items:
            name = item.get("name") or "Unnamed Item"
            unit = item.get("unit") or "Nos"
            if unit not in seen_units:
                seen_units.add(unit)
                missing_units.append(unit)
            if name not in seen_names:
                seen_names.add(name)
                if not _stock_item_exists(stock_items, name):
                    missing_stock_items.append(item)

        if send_voucher_xml is not None and (missing_units or missing_stock_items):
            preflight_masters = []
            for unit in missing_units:
                preflight_masters.append(_create_unit_master_xml(unit))
            for item in missing_stock_items:
                preflight_masters.append(_create_stock_item_master_xml(item.get("name") or "Unnamed Item", item.get("unit")))
            preflight_xml = wrap_envelope("".join(preflight_masters), config["company_name"])
            try:
                preflight_result = send_voucher_xml(preflight_xml)
                if preflight_result.get("errors", 0) == 0:
                    # Success — these now exist in Tally, so treat them as
                    # already-present for the voucher build below and skip
                    # re-declaring them there too.
                    stock_items = list(stock_items) + [
                        {"name": item.get("name") or "Unnamed Item", "base_unit": item.get("unit") or "Nos"}
                        for item in missing_stock_items
                    ]
            except Exception:
                # Pre-flight push failed outright (e.g. connection hiccup) —
                # fall through to the old single-request behavior below as
                # a best-effort fallback rather than blocking the push.
                pass

        message = build_item_voucher_xml(vch_type, tx, config, ledgers, stock_items)
    else:
        message = build_voucher_xml(vch_type, tx, config, ledgers)
    return wrap_envelope(message, config["company_name"])