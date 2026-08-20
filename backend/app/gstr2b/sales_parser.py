"""Parse sales invoices from a plain sales-register Excel file.

Unlike purchases, there is no government-issued "GSTR-2B for sales" — GSTR-1
is an outward-supply *return*, not something a business has lying around
before filing. So this reads a simple, flexible sales register instead: one
row per invoice, with a handful of recognizable column headers. Column
names are matched case/spacing-insensitively so minor header variations
(e.g. "Customer" vs "Party Name") still work.
"""
import re
from dataclasses import dataclass, field

from .purchase_parser import _normalize, _merged_grid, _cell, _number, _date


@dataclass
class SalesRow:
    party: str
    invoice_number: str
    invoice_date: str | None
    total_value: float
    taxable_value: float
    gst_rate: float
    cgst: float
    sgst: float
    igst: float
    cess: float
    source_sheet: str
    warnings: list = field(default_factory=list)


HEADER_VARIANTS = {
    "party": ["party", "partyname", "customer", "customername", "buyer", "buyername", "name"],
    "invoice_number": ["invoicenumber", "invoiceno", "billno", "billnumber", "invno"],
    "invoice_date": ["invoicedate", "date", "billdate"],
    "taxable_value": ["taxablevalue", "taxablevaluer", "taxableamount"],
    "gst_rate": ["rate", "ratepercent", "gstrate"],
    "cgst": ["cgst", "centraltax", "centraltaxr"],
    "sgst": ["sgst", "stateuttax", "stateuttaxr", "statetax"],
    "igst": ["igst", "integratedtax", "integratedtaxr"],
    "cess": ["cess", "cessr"],
    "total_value": ["totalvalue", "invoicevalue", "total", "billamount", "grandtotal"],
}

REQUIRED_HEADERS = {"party", "invoice_number", "invoice_date", "taxable_value", "total_value"}


def _find_header(sheet, grid):
    for row in range(1, min(sheet.max_row, 20) + 1):
        found = {}
        for col in range(1, sheet.max_column + 1):
            value = _normalize(_cell(sheet, grid, row, col))
            if not value:
                continue
            for field_name, variants in HEADER_VARIANTS.items():
                if field_name not in found and value in variants:
                    found[field_name] = col
        if REQUIRED_HEADERS.issubset(found):
            return row, found
    return None


def parse_sales_excel(file_path: str) -> list[SalesRow]:
    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=False)
    results = []
    sheets_found = 0

    for name in wb.sheetnames:
        sheet = wb[name]
        grid = _merged_grid(sheet)
        header = _find_header(sheet, grid)
        if not header:
            continue
        sheets_found += 1
        header_row, cols = header

        for row_idx in range(header_row + 1, sheet.max_row + 1):
            party = _cell(sheet, grid, row_idx, cols["party"])
            invoice_no = _cell(sheet, grid, row_idx, cols["invoice_number"])
            if not party or not str(party).strip():
                continue
            if not invoice_no or not str(invoice_no).strip():
                continue

            def get(field_name):
                col = cols.get(field_name)
                return _cell(sheet, grid, row_idx, col) if col else None

            item = SalesRow(
                party=str(party).strip(),
                invoice_number=str(invoice_no).strip(),
                invoice_date=_date(get("invoice_date")),
                total_value=_number(get("total_value")),
                taxable_value=_number(get("taxable_value")),
                gst_rate=_number(get("gst_rate")),
                cgst=_number(get("cgst")),
                sgst=_number(get("sgst")),
                igst=_number(get("igst")),
                cess=_number(get("cess")),
                source_sheet=name,
            )

            if not item.invoice_date:
                item.warnings.append("Could not parse invoice date")

            total_tax = item.cgst + item.sgst + item.igst
            if item.gst_rate == 0 and total_tax > 0 and item.taxable_value > 0:
                item.gst_rate = round((total_tax / item.taxable_value) * 100, 2)
                item.warnings.append(f"Rate column missing/zero — derived {item.gst_rate}% from tax amounts")

            if item.total_value == 0 and (item.taxable_value > 0 or total_tax > 0):
                item.total_value = round(item.taxable_value + total_tax + item.cess, 2)
                item.warnings.append("Total value column missing/zero — derived from taxable + tax")

            expected = round(item.taxable_value + total_tax + item.cess, 2)
            if item.total_value and abs(expected - item.total_value) > 1.0:
                item.warnings.append(
                    f"Total ({item.total_value}) differs from taxable + tax ({expected}) — verify manually"
                )
            results.append(item)

    if sheets_found == 0:
        raise ValueError(
            "Could not find a sheet with recognizable sales columns. Expected headers like "
            "Party/Customer, Invoice Number, Invoice Date, Taxable Value, and Total Value."
        )
    return results
