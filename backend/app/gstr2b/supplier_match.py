"""Cross-checks a supplier's own invoice Excel against the Transaction that
was already created from GSTR-2B, and only then releases the per-rate
breakdown for use.

GSTR-2B remains the source of truth for *whether the purchase counts and for
how much* (that's what determines ITC eligibility). The supplier invoice is
only ever used to fill in the "which portion was taxed at which rate" detail
that GSTR-2B's invoice-level aggregate cannot carry. If the two disagree on
totals, that's a red flag worth a human look, not something to silently
paper over by trusting whichever number is more convenient.
"""
from dataclasses import dataclass

TOTAL_TOLERANCE_RUPEES = 2.0  # allow for rounding differences only


@dataclass
class MatchResult:
    matched: bool
    reason: str
    rate_breakdown: list[dict] | None = None  # [{rate, taxable_value, cgst, sgst, igst}, ...]


def reconcile_with_transaction(supplier_invoice, tx: dict) -> MatchResult:
    """`tx` is the existing GSTR-2B-derived transaction dict (taxable_value,
    cgst, sgst, igst, invoice_number, total_value)."""

    # 1. Identity check — same invoice, not a coincidence.
    tx_invoice_no = str(tx.get("invoice_number") or "").strip().lower()
    supplier_invoice_no = str(supplier_invoice.invoice_number or "").strip().lower()
    if tx_invoice_no and supplier_invoice_no and tx_invoice_no != supplier_invoice_no:
        return MatchResult(False, f"Invoice number mismatch: GSTR-2B has '{tx.get('invoice_number')}', "
                                   f"uploaded file has '{supplier_invoice.invoice_number}'.")

    # 2. Totals check — the whole point of trusting the supplier file is
    # that it's describing the exact same purchase GSTR-2B already recorded.
    by_rate = supplier_invoice.by_rate
    supplier_taxable = sum(b["taxable_value"] for b in by_rate.values())
    supplier_cgst = sum(b["cgst"] for b in by_rate.values())
    supplier_sgst = sum(b["sgst"] for b in by_rate.values())
    supplier_igst = sum(b["igst"] for b in by_rate.values())

    tx_taxable = float(tx.get("taxable_value") or 0)
    tx_cgst = float(tx.get("cgst") or 0)
    tx_sgst = float(tx.get("sgst") or 0)
    tx_igst = float(tx.get("igst") or 0)

    taxable_diff = abs(supplier_taxable - tx_taxable)
    tax_diff = abs((supplier_cgst + supplier_sgst + supplier_igst) - (tx_cgst + tx_sgst + tx_igst))

    if taxable_diff > TOTAL_TOLERANCE_RUPEES:
        return MatchResult(False, f"Taxable value doesn't reconcile: GSTR-2B ₹{tx_taxable:.2f} vs "
                                   f"supplier file ₹{supplier_taxable:.2f} (diff ₹{taxable_diff:.2f}).")
    if tax_diff > TOTAL_TOLERANCE_RUPEES:
        return MatchResult(False, f"Tax amount doesn't reconcile: GSTR-2B ₹{tx_cgst + tx_sgst + tx_igst:.2f} vs "
                                   f"supplier file ₹{supplier_cgst + supplier_sgst + supplier_igst:.2f} "
                                   f"(diff ₹{tax_diff:.2f}).")

    # 3. Totals agree -> the per-rate split is trustworthy even though it
    # can't be derived from GSTR-2B alone. Build the breakdown Tally needs.
    breakdown = [
        {"rate": rate, "taxable_value": round(b["taxable_value"], 2),
         "cgst": round(b["cgst"], 2), "sgst": round(b["sgst"], 2), "igst": round(b["igst"], 2)}
        for rate, b in sorted(by_rate.items())
    ]
    reason = "Matched on totals" + (" and invoice number" if tx_invoice_no and supplier_invoice_no else " (no invoice number to cross-check, verify manually)")
    return MatchResult(True, reason, rate_breakdown=breakdown)
