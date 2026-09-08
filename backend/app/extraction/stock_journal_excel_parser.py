"""
Parses a Stock Journal Excel — one row per inventory movement that isn't a
sale or purchase: stock transferred between two godowns/warehouses, raw
material consumed, or finished goods produced. No ledger/GST math here at
all, just an item, a quantity, and where it moved from/to.

Same approach as simple_ledger_excel_parser.py: score header rows against
known aliases, and fall back to asking the AI to map an unfamiliar layout
(reusing the same header-mapping call already used for Sales/Purchase bill
Excel imports — see ocr/ai_vision.py:map_excel_headers, which already
understands stock_item/quantity/rate/unit and now from_godown/to_godown).
"""
from dataclasses import dataclass

from .bill_excel_parser import _load_first_sheet, _normalize, _number, _parse_date_cell

HEADER_VARIANTS = {
    "stock_item": ["item", "itemname", "stockitem", "stockitemname", "itemdescription", "product", "productname", "material", "materialname"],
    "quantity": ["qty", "quantity", "units", "noofunits"],
    "unit": ["unit", "uom", "unitofmeasure"],
    "rate": ["rate", "price", "unitprice", "rateperunit", "unitrate", "rateeach", "costperunit"],
    "amount": ["amount", "value", "totalvalue", "totalamount"],
    "date": ["date", "transactiondate", "vchdate"],
    "narration": ["narration", "particulars", "purpose", "remarks", "description", "reason", "note", "narrationremarks"],
    "from_godown": ["fromgodown", "sourcegodown", "godownfrom", "fromlocation", "fromwarehouse", "consumedat"],
    "to_godown": ["togodown", "destinationgodown", "godownto", "tolocation", "towarehouse", "producedat"],
}


@dataclass
class ParsedStockJournalRow:
    stock_item: str
    quantity: float
    unit: str | None
    rate: float | None
    amount: float
    date: str | None
    narration: str | None
    from_godown: str | None
    to_godown: str | None


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
        # A stock journal row needs at minimum an item and a quantity, plus
        # somewhere the stock is moving from or to — otherwise there's
        # nothing to distinguish it from a plain item list.
        has_essentials = (
            "stock_item" in found
            and "quantity" in found
            and ("from_godown" in found or "to_godown" in found)
        )
        score = len(found)
        if has_essentials and score > best_score:
            best_row, best_cols, best_score = row, found, score
    if best_row is None:
        return None
    return best_row, best_cols


_AI_MAPPING_SAMPLE_ROWS = 12


def _sheet_to_grid(sheet, max_rows: int) -> list[list]:
    return [
        [sheet.cell(r, c).value for c in range(1, sheet.max_column + 1)]
        for r in range(1, min(sheet.max_row, max_rows) + 1)
    ]


def _parse_via_ai_mapping(sheet) -> list[ParsedStockJournalRow] | None:
    """
    AI fallback for a Stock Journal sheet whose column names don't match
    the aliases above. Reuses the exact same header-mapping call already
    wired up for Sales/Purchase bill Excel imports (ocr/ai_vision.py:
    map_excel_headers) — its canonical field set already covers
    stock_item/quantity/rate/unit, plus from_godown/to_godown added
    specifically for this parser. Returns None (never raises) on any
    failure so the caller falls through to the plain "couldn't find a
    recognizable table" error.
    """
    from ..ocr.ai_vision import has_ai_key, map_excel_headers

    if not has_ai_key():
        return None

    grid = _sheet_to_grid(sheet, _AI_MAPPING_SAMPLE_ROWS)
    try:
        mapping = map_excel_headers(grid)
    except Exception:
        return None

    header_row = mapping.get("header_row")
    field_columns = mapping.get("field_columns") or {}
    item_col = field_columns.get("stock_item")
    qty_col = field_columns.get("quantity")
    from_col = field_columns.get("from_godown")
    to_col = field_columns.get("to_godown")
    if not header_row or not item_col or not qty_col or not (from_col or to_col):
        return None

    unit_col = field_columns.get("unit")
    rate_col = field_columns.get("rate")
    amount_col = field_columns.get("amount") or field_columns.get("total_value")
    date_col = field_columns.get("date")
    narration_col = field_columns.get("narration")

    def get(row, col):
        return sheet.cell(row, col).value if col else None

    rows: list[ParsedStockJournalRow] = []
    for row in range(int(header_row) + 1, sheet.max_row + 1):
        item_val = get(row, item_col)
        qty_val = _number(get(row, qty_col))
        if not item_val or not str(item_val).strip() or not qty_val:
            continue

        rate_val = _number(get(row, rate_col)) if rate_col else None
        # Prefer an actual Amount/Total-Value column when the sheet has one
        # AND it's actually filled in for this row — but a header existing
        # doesn't mean every row has it populated (this exact file has a
        # "Total Value" column that's entirely blank, relying on Rate ×
        # Quantity instead). Falling back only when amount_col is None
        # entirely (the old behaviour) silently produced ₹0.00 for every
        # row whenever the column existed-but-empty, which is the more
        # common case, not the rare one.
        amount_val = _number(get(row, amount_col)) if amount_col else None
        if not amount_val:
            amount_val = (rate_val or 0.0) * qty_val
        from_val = get(row, from_col)
        to_val = get(row, to_col)
        narration_val = get(row, narration_col) if narration_col else None

        rows.append(ParsedStockJournalRow(
            stock_item=str(item_val).strip(),
            quantity=qty_val,
            unit=str(get(row, unit_col)).strip() if unit_col and get(row, unit_col) else None,
            rate=rate_val or None,
            amount=amount_val,
            date=_parse_date_cell(get(row, date_col)) if date_col else None,
            narration=str(narration_val).strip() if narration_val not in (None, "") else None,
            from_godown=str(from_val).strip() if from_val not in (None, "") else None,
            to_godown=str(to_val).strip() if to_val not in (None, "") else None,
        ))

    return rows or None


def parse_stock_journal_excel(file_path: str) -> list[ParsedStockJournalRow]:
    """
    Reads one row per inventory movement. Raises ValueError with a clear
    message if no recognisable header row can be found, same pattern as
    every other parser here.
    """
    sheet = _load_first_sheet(file_path)
    found_header = _find_header_row(sheet)
    if found_header is None:
        ai_rows = _parse_via_ai_mapping(sheet)
        if ai_rows is not None:
            return ai_rows
        raise ValueError(
            "Couldn't find a recognizable stock journal table in this Excel. "
            "Make sure it has a header row with at least an Item column, a "
            "Quantity column, and a From Godown and/or To Godown column."
        )
    header_row, cols = found_header

    def get(row, field_name):
        col = cols.get(field_name)
        return sheet.cell(row, col).value if col else None

    rows: list[ParsedStockJournalRow] = []
    for row in range(header_row + 1, sheet.max_row + 1):
        item_val = get(row, "stock_item")
        qty_val = _number(get(row, "quantity"))
        if not item_val or not str(item_val).strip() or not qty_val:
            continue

        rate_val = _number(get(row, "rate")) or None
        amount_val = _number(get(row, "amount")) or ((rate_val or 0.0) * qty_val)
        from_val = get(row, "from_godown")
        to_val = get(row, "to_godown")
        narration_val = get(row, "narration")

        rows.append(ParsedStockJournalRow(
            stock_item=str(item_val).strip(),
            quantity=qty_val,
            unit=str(get(row, "unit")).strip() if get(row, "unit") else None,
            rate=rate_val,
            amount=amount_val,
            date=_parse_date_cell(get(row, "date")),
            narration=str(narration_val).strip() if narration_val not in (None, "") else None,
            from_godown=str(from_val).strip() if from_val not in (None, "") else None,
            to_godown=str(to_val).strip() if to_val not in (None, "") else None,
        ))

    if not rows:
        raise ValueError(
            "Found a header row but no item rows under it with a recognizable item name and quantity."
        )
    return rows


def rows_to_transactions(rows: list[ParsedStockJournalRow]) -> list[dict]:
    """
    Converts parsed rows into the same dict shape documents.py uses to
    create Transaction records. Reuses two existing generic columns rather
    than adding new ones: `party` holds the From Godown, `bank_ledger`
    holds the To Godown (same reuse pattern as Journal/Contra ledgers),
    and `items` (the same JSON column item-wise Sales/Purchase invoices
    already use) holds the single stock-item line — see
    voucher_builder.build_stock_journal_xml for how these are read back.
    """
    out = []
    for r in rows:
        out.append({
            "type": "STOCK_JOURNAL",
            # "" not None: Transaction.party has a DB-level default of
            # "Cash" that fires on None even when explicitly passed — fine
            # for a Receipt/Payment row (blank party really does often mean
            # a cash sale), completely wrong for a Stock Journal, where a
            # blank From Godown means "pure production, no source godown at
            # all", not "Cash". An empty string displays as blank instead
            # of silently becoming a fake "Cash" godown.
            "party": r.from_godown or "",
            "bank_ledger": r.to_godown,
            "date": r.date,
            "invoice_number": None,
            "total_value": r.amount,
            "narration": r.narration,
            "confidence": 1.0,
            "debit": 0.0,
            "credit": 0.0,
            "items": [{
                "name": r.stock_item,
                "qty": r.quantity,
                "unit": r.unit,
                "price": r.rate,
                "amount": r.amount,
            }],
        })
    return out
