def round_rupee(value):
    """Round a monetary amount to the nearest whole rupee.

    The Total is what actually gets saved and pushed to Tally, and GST
    rounding rules expect it as a whole rupee — not the intermediate
    taxable value or tax components, just the final total. Applied at
    every point total_value is written (OCR/AI extraction, Tesseract
    fallback, GSTR-2B import, and manual edit) so it holds no matter
    which path created the transaction.
    """
    if value is None:
        return None
    return round(float(value))
