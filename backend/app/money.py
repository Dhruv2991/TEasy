def round_rupee(value):
    """Round a monetary amount to the nearest whole rupee.

    Called from exactly one place: tally/voucher_builder.py, right before
    a voucher is written, to produce the invoice's rounded whole-rupee
    total. It is deliberately NOT applied earlier (AI/OCR extraction,
    GSTR-2B import, or manual edit) — taxable_value, cgst/sgst/igst, and
    total_value are all stored at full precision throughout the review
    pipeline. Rounding total_value early while leaving the tax components
    exact used to create a false taxable+tax≠total mismatch that had
    nothing to do with the actual bill; rounding it only once, at the
    final Tally-writing step, avoids that entirely, and any genuine
    residual difference is posted to the ROUNDOFF ledger as before.
    """
    if value is None:
        return None
    return round(float(value))


def split_tax_amount(tax_amount: float) -> tuple[float, float]:
    """
    Splits an intra-state GST tax amount equally between CGST and SGST,
    guaranteeing that CGST + SGST == tax_amount down to the exact paisa.
    Prevents floating-point/rounding discrepancies where 55.45 / 2 might
    round both halves to 27.73 (summing to 55.46) or 27.72 (summing to 55.44).
    """
    if not tax_amount:
        return 0.0, 0.0
    tax_clean = round(float(tax_amount), 2)
    cgst = round(tax_clean / 2.0, 2)
    sgst = round(tax_clean - cgst, 2)
    return cgst, sgst

