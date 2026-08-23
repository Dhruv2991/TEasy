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
