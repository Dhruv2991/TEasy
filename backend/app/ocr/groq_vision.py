"""
Sends a single cropped bill image straight to a Groq vision-language model
and asks it to return structured accounting fields as JSON.

This replaces the Tesseract -> regex pipeline for accuracy on handwriting.
Requires a free API key from https://console.groq.com/keys, set as the
GROQ_API_KEY environment variable (see backend/.env.example).

Model: qwen/qwen3.8-27b, Groq's current fast vision-capable model. If Groq
changes model names, update GROQ_VISION_MODEL below.
"""
import os
import base64
import json
import re
import time

import requests

from ..settings import get_settings

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def _current_key() -> str:
    """Reads the key fresh each call, so saving it in the Settings page
    takes effect immediately without restarting the app."""
    return get_settings().get("groq_api_key") or ""


def _current_model() -> str:
    return get_settings().get("groq_vision_model") or "qwen/qwen3.8-27b"


# Kept as a module attribute for any code (and any older import) that checks
# `GROQ_API_KEY` truthily — evaluated at import time only, so prefer
# `_current_key()` for anything that needs the live value.
GROQ_API_KEY = _current_key()

SALES_EXTRACTION_PROMPT = """You are an extremely careful accounting data-entry system reading ONE photo/crop of a sales bill or invoice issued BY this business TO a customer. This could be from ANY kind of business — retail shop, wholesaler, trader, services firm, restaurant, workshop, distributor, etc. — and the bill could be:
  - A fully printed/typed tax invoice from any billing software.
  - A handwritten bill-book / khata-style bill (single or multi-bill-per-page layout).
  - A mix of printed letterhead with handwritten figures filled in.
  - A cash-memo style bill with no formal invoice number.

Do not assume any specific template, business name, ink color, or field position. Read what is actually on THIS bill. Your job is transcription, not estimation — a wrong number is worse than a missing number (null).

FIELD MAP — search the whole image for each of these, they can appear anywhere depending on the bill's layout:

1. party: The CUSTOMER / buyer name — usually labeled "Sri", "M/s", "Bill to", "Buyer", "Customer Name", or similar, near the top. If the bill is a walk-in/cash sale with no customer name written or printed anywhere, return "Cash" — but only after actually checking, never assume it by default.

2. invoice_number: Look for a label like "No.", "Bill No.", "Invoice No.", "Inv No." — read the value next to it exactly as written, including any letter prefix (e.g. "A2406", "INV-1042"). If the bill uses a distinct ink color (e.g. red) for the handwritten number versus other black/blue handwriting, that is usually the invoice number and a strong visual cue — use it if present, but don't assume it always is. If genuinely illegible or absent, return null rather than guessing.

3. date: Look for "Date:" near the invoice number/header. Read the handwritten or printed date and return YYYY-MM-DD. If the year is abbreviated (e.g. "13/8/26"), infer the full year from context (bills are typically dated in the current or very recent year); if truly ambiguous, return null.

4. taxable_value: The subtotal / taxable amount BEFORE tax — usually the first "Total" figure appearing above the CGST/SGST/IGST rows in the summary box, or a "Subtotal"/"Taxable Value" row in a printed invoice's tax table.

5. GST amounts — read the rate(s) actually present, then calculate:
   a. Identify which tax structure this bill uses: CGST+SGST (intra-state), IGST (inter-state), or no GST at all (non-GST bill, common for very small businesses/unregistered sellers — this is valid, don't invent a rate).
   b. Read cgst_rate_percent / sgst_rate_percent / igst_rate_percent wherever printed or handwritten (e.g. "CGST 6%", "SGST 6%", "IGST 12%").
   c. Calculate the amounts as taxable_value * rate / 100, rounded to 2 decimals.
   d. If a rupee amount is also written/printed next to the rate and it clearly disagrees with your calculation by more than a few rupees, keep the calculated value as authoritative but say so explicitly in "notes".
   e. gst_rate: the combined GST rate (cgst_rate_percent + sgst_rate_percent, or igst_rate_percent alone), 0 if no GST on this bill.
   f. Multiple line items can carry different GST rates on one bill — if so, use the blended/weighted total across all lines for gst_rate, and sum the CGST/SGST/IGST amounts across all lines for cgst/sgst/igst. Mention in "notes" if more than one rate was present.

6. total_value: Calculate as taxable_value + cgst + sgst + igst. Cross-check against any final "Total"/"Grand Total"/"Amount Payable" figure on the bill, and against an "Amount in words" line if present — if they disagree by more than a rounding difference, trust your calculated total_value (since it's derived from the more-legible taxable_value and rate fields) but say so in "notes".

VISUAL PROCEDURE:
- First inspect the whole image to understand what kind of bill this is (printed invoice vs handwritten bill-book vs cash memo) before extracting anything.
- If the image contains more than one bill (e.g. a multi-up bill-book page), extract only the bill this crop is meant to represent — mention in "notes" if you had to guess which one.
- Distinguish visually similar digits carefully in any handwriting: 0/6, 1/7, 3/8, 4/9, 5/6.
- Do not copy numbers from GSTIN, phone numbers, PO numbers, HSN codes, or account numbers into invoice_number or the amount fields.

Return ONLY this JSON object:
{
  "party": "customer name, or 'Cash' only if genuinely no name is present",
  "date": "YYYY-MM-DD or null",
  "invoice_number": "value read exactly as written/printed, or null",
  "taxable_value": number or null,
  "gst_rate": number or null,
  "cgst": number or null,
  "sgst": number or null,
  "igst": number or null,
  "total_value": number or null,
  "confidence": number from 0 to 1,
  "notes": "specific ambiguity, if any, including any written-vs-calculated mismatch, multiple GST rates, or which bill was picked from a multi-bill page"
}

NO-GUESSING RULES:
- Never guess a digit or name you can't clearly read — return null for that field instead.
- If the image is blurry, incomplete, upside down, or not actually a sales bill, lower confidence sharply and explain why in notes.
- Do not claim 100% confidence. The final application performs a deterministic arithmetic check before anything can be approved for Tally.
"""

# Backward-compat alias — other modules / earlier versions of this file
# imported EXTRACTION_PROMPT directly.
EXTRACTION_PROMPT = SALES_EXTRACTION_PROMPT

PURCHASE_EXTRACTION_PROMPT = """You are an accounting data-entry assistant reading a photo of ONE purchase bill / supplier tax invoice received by an Indian small business. This is a bill FROM a supplier TO this business (the reverse direction of a sales bill) — usually a printed invoice from the supplier's own billing system, not a handwritten bill-book form.

FOLLOW THIS SEARCH PROCEDURE for the two highest-risk fields, in order, before extracting anything else:

STEP 1 — Find the SUPPLIER.
Look at the very top of the document, typically top-left, often next to a logo: a company name, address, and GSTIN printed together as a letterhead. That company is the "party" — it is who ISSUED this bill. Confirm it by checking its GSTIN appears again in the tax/summary area as the seller's GSTIN. This is the only place to find the supplier name — do not use any company name that appears inside a box labeled "Consignee", "Ship to", "Buyer", or "Bill to" anywhere else on the page; those boxes always describe the RECIPIENT (this business itself), never the supplier.

STEP 2 — Find the INVOICE NUMBER.
Scan the document specifically for a label that says "Invoice No.", "Invoice Number", or "Inv. No." — this exact label, not a similar-sounding one. Read only the value printed or written directly next to or below that specific label. Some invoices print several reference-number fields close together (Ack No., IRN, e-Way Bill No., PO No., Reference No., etc.) — when several numbers are clustered together, go back to the label text itself and match strictly by which label the value sits under/next to, not by which number looks most "invoice-number-shaped" or sits closest to the top.
As a confirmation step: the invoice number is very often printed a SECOND time elsewhere on the page (e.g. in a "Remarks" line near the tax summary, like "CCS/26-27/3989 TOTAL ..."). If you find the same string in two places, that's strong confirmation you found the right value. If the value you found next to "Invoice No." does NOT appear anywhere else on the page, treat that as a signal to re-check the label you actually matched against.

Once you've completed both steps above, read the rest of the document normally:
- An itemised table of goods/services with HSN/SAC codes, quantities, rates, and amounts.
- A tax summary, which may appear TWICE in slightly different forms: once as a simple running total right after the item table, and again as a proper GST breakdown table with Taxable Value, CGST rate+amount, SGST rate+amount (or IGST for inter-state), and a final Total Tax Amount / Grand Total. The GST breakdown table's total is the authoritative one.
- An "Amount Chargeable (in words)" / "Invoice Amount in Words" / "Total Value in Words" line spelling out the grand total in English words. Some invoices ALSO print a separate "Tax Amount in Words" line (words for the tax portion only, not the total) — do NOT use that one for total_value cross-checking, only use the line that describes the full invoice/total amount. If a printed numeric total looks inconsistent with the correct (total, not tax-only) words line, trust the words version and note the discrepancy.

Read the image carefully and return ONLY a single JSON object (no markdown fences, no commentary) with exactly these fields:

{
  "party": "the SUPPLIER's business name, found via STEP 1 above",
  "date": "YYYY-MM-DD — the date printed alongside the same 'Invoice No.' field from STEP 2, else null",
  "invoice_number": "the value found via STEP 2 above, exactly as printed",
  "taxable_value": "the taxable value / subtotal before tax from the GST breakdown table, as a number",
  "gst_rate": "the combined GST rate (e.g. CGST 6% + SGST 6% = 12; IGST 12% = 12), 0 if no GST shown",
  "cgst": "CGST amount from the GST breakdown table, 0 if not applicable",
  "sgst": "SGST amount from the GST breakdown table, 0 if not applicable",
  "igst": "IGST amount, 0 if not applicable (use igst instead of cgst/sgst only for inter-state supplies)",
  "total_value": "the final grand total (tax-inclusive), cross-checked against the 'Amount Chargeable (in words)' line if present — the actual amount payable, as a number",
  "confidence": "your own confidence in this extraction, 0 to 1",
  "notes": "brief note on anything ambiguous, illegible, or noteworthy — ALWAYS mention it here if the numeric total and the amount-in-words disagreed and which one you trusted, else empty string"
}

Critical rules:
- Trust STEPS 1 and 2 above as your primary method. As a final sanity check only (not your main strategy) — if what you landed on for invoice_number happens to match an IRN, Ack No., e-Way Bill No., Reference No., Buyer's/Recipient's PO No., LR/Dispatch number, Vehicle/Wagon/Trailer No., Batch No., Plant/Depot Code, TAN No., or HSN/SAC Code, that's a strong sign you matched the wrong label — go back to STEP 2 and find the actual "Invoice No." label again.
- DO NOT FABRICATE. If you cannot clearly complete STEP 1 or STEP 2 — whether because the image is blurry, cropped wrong, upside down, not actually a tax invoice, or any other reason — do NOT invent a plausible-sounding company name or invoice number to fill the field. Set "party" and/or "invoice_number" to null, set "confidence" low (below 0.3), and explain exactly what you could and couldn't find in "notes". A null field the user can fix by hand is far better than a fabricated one that looks correct — a wrong-but-confident answer here is worse than no answer, because it will be trusted and acted on without a second look.
- Read the invoice_number character-by-character, not as a whole-word guess. Common misreads to watch for: 0 (zero) vs O (letter), 1 vs I vs l, 5 vs S, 8 vs B, 2 vs Z. If the digit/letter is genuinely ambiguous even after careful reading, do not silently pick one — lower "confidence" and say exactly which character is uncertain in "notes" (e.g. "3rd character could be '0' or 'O'").
- Do not fabricate any field. Use null for text fields you cannot determine, 0 only for numeric fields that are genuinely not applicable.
- If numbers don't perfectly reconcile (taxable_value + cgst + sgst/igst vs total_value, or a printed digit total vs the amount-in-words), trust the amount-in-words / the GST breakdown table's total over any other printed total, and mention the discrepancy in "notes".
- Numbers must be plain numbers, not strings, no currency symbols or commas.
"""


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        data = f.read()

    # Downscale very large phone-camera photos before sending. Two reasons:
    # (1) vision APIs often silently downscale oversized images server-side
    #     to a much smaller max dimension than you'd expect — if that resize
    #     is naive/low-quality, small printed text like an invoice number
    #     can blur into something the model misreads. Doing a controlled,
    #     high-quality resize ourselves avoids depending on whatever Groq
    #     does internally.
    # (2) keeps the request payload (and tokens-per-minute usage) smaller.
    # 2200px on the long side comfortably preserves invoice-number-sized
    # text while cutting a typical 4000px+ phone photo down significantly.
    import cv2
    import numpy as np

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is not None:
        h, w = img.shape[:2]
        max_dim = 3000
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            data = encoded.tobytes()

    ext = "jpeg"
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:image/{ext};base64,{b64}"


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    # qwen3.8 is a "thinking" model: it may prepend a <think>...</think>
    # reasoning block before the actual answer. Strip that out first.
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    # Safety net: if reasoning_effort=none somehow didn't apply and the
    # response got cut off mid-<think> block (no closing tag), there's no
    # JSON to find — surface a clear error instead of a confusing JSON
    # parse failure.
    if cleaned.startswith("<think>"):
        raise ValueError(
            "response was cut off during the model's reasoning step "
            "before any answer was produced (increase max_tokens or check "
            "reasoning_effort is being honored)"
        )
    # Model sometimes wraps JSON in ```json fences despite instructions; strip defensively.
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    # As a last resort, if there's still stray text around it, grab the first
    # {...} block — the outermost matching braces.
    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            cleaned = match.group(0)
    return json.loads(cleaned)


def extract_bill_with_ai(image_path: str, prompt: str = SALES_EXTRACTION_PROMPT) -> dict:
    """
    Returns a dict with the fields described in the given prompt, plus a
    'raw_text' key holding the model's raw JSON string (stored for audit/debug,
    mirrors what the old OcrResult.raw_text held).

    `prompt` defaults to the Sales bill prompt for backward compatibility;
    pass PURCHASE_EXTRACTION_PROMPT for purchase bills (see
    extract_purchase_bill_with_ai below, which is the preferred call site).

    Raises RuntimeError if GROQ_API_KEY is missing or the API call fails, so
    the caller can fall back or surface a clear error rather than silently
    producing an empty transaction.
    """
    api_key = _current_key()
    if not api_key:
        raise RuntimeError(
            "Groq API key is not set. Get a free key at https://console.groq.com/keys "
            "and add it on the Settings page in the app."
        )

    image_data_uri = _encode_image(image_path)

    payload = {
        "model": _current_model(),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_uri}},
                ],
            }
        ],
        "temperature": 0.1,
        # qwen/qwen3.8-27b is a "thinking" model that otherwise burns most of
        # max_tokens on an internal <think>...</think> reasoning block before
        # ever getting to the JSON answer. Groq's supported way to turn this
        # off for qwen3 models is reasoning_effort="none" (NOT
        # chat_template_kwargs, which Groq's endpoint rejects with a 400).
        "reasoning_effort": "none",
        "max_tokens": 1500,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Groq's free tier has a tokens-per-minute cap shared across all bills on
    # a page — processing several bills back-to-back can hit it even though
    # each individual bill is well within limits. On a 429, Groq tells us
    # exactly how long to wait (either in the error body or Retry-After
    # header); honor that and retry rather than giving up immediately.
    max_retries = 3
    for attempt in range(max_retries + 1):
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code == 429:
            wait_s = None
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_s = float(retry_after)
                except ValueError:
                    pass
            if wait_s is None:
                match = re.search(r"try again in ([\d.]+)s", resp.text)
                if match:
                    wait_s = float(match.group(1))
            wait_s = (wait_s or 5) + 0.5  # small buffer
            if attempt < max_retries:
                time.sleep(wait_s)
                continue
        if resp.status_code != 200:
            raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:500]}")
        break

    body = resp.json()
    content = body["choices"][0]["message"]["content"]

    try:
        parsed = _extract_json(content)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise RuntimeError(f"Could not parse Groq response as JSON: {e}. Raw: {content[:300]}")

    parsed.setdefault("party", "Cash")
    parsed.setdefault("date", None)
    parsed.setdefault("invoice_number", None)
    parsed.setdefault("taxable_value", 0)
    parsed.setdefault("gst_rate", 0)
    parsed.setdefault("cgst", 0)
    parsed.setdefault("sgst", 0)
    parsed.setdefault("igst", 0)
    parsed.setdefault("total_value", 0)
    parsed.setdefault("confidence", 0.5)
    parsed.setdefault("notes", "")
    parsed["raw_text"] = content

    for numeric_field in ("taxable_value", "gst_rate", "cgst", "sgst", "igst", "total_value", "confidence"):
        try:
            parsed[numeric_field] = float(parsed[numeric_field] or 0)
        except (TypeError, ValueError):
            parsed[numeric_field] = 0.0

    return parsed


def extract_purchase_bill_with_ai(image_path: str) -> dict:
    """
    Same as extract_bill_with_ai, but uses the Purchase-bill prompt (reads
    the SUPPLIER as the party, tuned for printed supplier invoices rather
    than handwritten sales bill-book forms).
    """
    return extract_bill_with_ai(image_path, prompt=PURCHASE_EXTRACTION_PROMPT)


# --- Excel header mapping (text-only, no image) --------------------------
# Fallback used by bill_excel_parser.py when its deterministic header-alias
# matching can't confidently identify the columns in a user's spreadsheet
# (an unfamiliar supplier/bank export, an unusual layout, etc). Rather than
# growing an ever-longer hardcoded alias list for every new header spelling
# anyone might use, this sends the header row (plus a couple of sample data
# rows for context) to the same LLM already wired up for bill photos, and
# asks it to map columns to TEasy's canonical field names once. Everything
# downstream of that mapping (consolidation, rate-breakdown merging, item
# handling) is unchanged deterministic code — the model's only job is
# figuring out "which column is which", not touching any numbers itself.

HEADER_MAP_CANONICAL_FIELDS = """
- party: the customer (Sales) or supplier (Purchase) ledger/name column
- party_gstin: the customer's or supplier's GSTIN column, if present
- date: the invoice/voucher date column
- invoice_number: the invoice/bill/voucher number column
- amount: a per-line taxable value / amount column (before tax) — only set this if there is a SINGLE flat amount column; leave it null if the sheet instead uses several per-GST-rate columns (see rate_buckets below)
- gst_rate: an explicit GST-rate percentage column, if present as its own column
- cgst_amount / sgst_amount / igst_amount: single flat tax-amount columns, only if NOT split per rate-slab (see rate_buckets below)
- total_value: the invoice grand total (tax-inclusive) column
- stock_item: an item/product/stock-item name column, if this is an item-wise sheet
- quantity: item quantity column
- rate: item unit price/rate column
- unit: item unit-of-measure column (Nos, Kg, ...)
- hsn: HSN/SAC code column
- round_off: a rounding-adjustment column
- vch_type: a voucher-type column (Sales/Purchase/Credit Note/...), if present
- from_godown: for a stock-transfer/consumption sheet, the source godown/warehouse/location column
- to_godown: for a stock-transfer/production sheet, the destination godown/warehouse/location column
"""


def _grid_to_text(grid: list[list]) -> str:
    lines = []
    for r_idx, row in enumerate(grid):
        cells = " | ".join(
            f"col{c_idx + 1}={'' if v is None else str(v)}" for c_idx, v in enumerate(row)
        )
        lines.append(f"row{r_idx + 1}: {cells}")
    return "\n".join(lines)


HEADER_MAP_PROMPT_TEMPLATE = """You are helping an accounting import tool understand an unfamiliar Excel/CSV layout (a sales, purchase, or bank-statement register exported from some accounting system, or typed by hand). Column headers can be worded in many ways ("Party Ledger" vs "Customer Name" vs "Party", "Vch No." vs "Invoice Number" vs "Bill No#", etc.) — your job is to map each column to ONE canonical field name, based on the header text and the sample data underneath it.

Canonical field names available:
{fields}

Some sheets put GST amounts in flat columns (cgst_amount/sgst_amount/igst_amount + one amount column). Others put them in a WIDE layout with a separate set of columns per GST rate slab (e.g. "TAXABLE @5%", "CGST @2.5%", "SGST @2.5%", "TAXABLE @18%", "CGST @9%", "SGST @9%", or "IGST 12%"/"CGST 6%"/"SGST 6%"). If you see this wide pattern, do NOT map those into the flat amount/cgst_amount/etc fields — instead list them under "rate_buckets", one entry per GST rate slab, each with its own value/cgst/sgst/igst column numbers (omit any that don't exist for that slab).

Here is the sheet — each row shows every column's 1-based column number and its value for the first several rows (the first row(s) are likely the header, or there may be a couple of blank/title rows above the real header — figure out which row number is the actual header row):

{grid}

Return ONLY this JSON object, nothing else:
{{
  "header_row": <1-based row number of the actual column-header row>,
  "field_columns": {{"party": <col number or null>, "party_gstin": <col number or null>, "date": <col number or null>, "invoice_number": <col number or null>, "amount": <col number or null>, "gst_rate": <col number or null>, "cgst_amount": <col number or null>, "sgst_amount": <col number or null>, "igst_amount": <col number or null>, "total_value": <col number or null>, "stock_item": <col number or null>, "quantity": <col number or null>, "rate": <col number or null>, "unit": <col number or null>, "hsn": <col number or null>, "round_off": <col number or null>, "vch_type": <col number or null>, "from_godown": <col number or null>, "to_godown": <col number or null>}},
  "rate_buckets": [{{"rate": <number>, "value_col": <col number or null>, "cgst_col": <col number or null>, "sgst_col": <col number or null>, "igst_col": <col number or null>}}],
  "confidence": <0 to 1>,
  "notes": "anything ambiguous about this mapping"
}}

Rules:
- Only map a column if you're reasonably confident from its header text and/or sample values — leave it null rather than guessing.
- A column can only be used once across field_columns and rate_buckets combined.
- amount MUST be null if rate_buckets is non-empty, and vice versa — don't double-map the same money into both.
- Do not invent columns that don't exist in the sheet.
"""


# --- Ledger (Receipt/Payment register) header mapping ---------------------
# Same idea as map_excel_headers above, but for the domain-agnostic
# money-in/money-out register uploaded on the Receipt/Payment page (see
# extraction/simple_ledger_excel_parser.py). Used only when that parser's
# deterministic header-alias matching can't confidently find a party
# column + an amount column — i.e. an unfamiliar column naming scheme, not
# one of the aliases already in HEADER_VARIANTS there.
LEDGER_HEADER_MAP_CANONICAL_FIELDS = """
- party: who the money was received from (Receipt), paid to (Payment), the ledger to be DEBITED (Journal), or the ledger RECEIVING funds (Contra transfer)
- date: the transaction date column
- invoice_number: a receipt/reference/slip/cheque/transaction-ID number column, if present
- amount: the money amount column for this transaction
- mode: how the money moved — Cash/UPI/NEFT/Cheque/Bank Transfer/Card/etc — the ledger to be CREDITED (Journal), or the ledger funds are transferred OUT OF (Contra transfer)
- narration: a purpose/particulars/remarks/description column, if present
- direction: a column that says whether EACH ROW individually is money coming IN or going OUT (e.g. a "Type" column with values like Inflow/Outflow, Credit/Debit, Received/Paid, Deposit/Withdrawal) — only set this if the sheet actually mixes both directions row-by-row; leave it null if the whole sheet is one direction (the user already tells the app that separately)
- tds_amount: on a Payment sheet only, a column giving the TDS (tax deducted at source) withheld from this payment, if the sheet tracks that separately from the gross amount
- tds_ledger: on a Payment sheet only, a column naming which TDS-payable ledger the withheld tax should be credited to, if present
"""

LEDGER_HEADER_MAP_PROMPT_TEMPLATE = """You are helping an accounting import tool understand an unfamiliar Excel/CSV layout for a general money-in/money-out register — a college's fee-collection sheet, a landlord's rent log, a company's salary/expense sheet, a donation register, anything with a party, a date, and an amount, no GST/item math involved. Column headers can be worded in many ways ("Student Name" vs "Payer" vs "Received From", "Fee Amount" vs "Amount Paid" vs "Net Amount", etc.) — your job is to map each column to ONE canonical field name, based on the header text and the sample data underneath it.

Canonical field names available:
{fields}

Here is the sheet — each row shows every column's 1-based column number and its value for the first several rows (the first row(s) are likely the header, or there may be a couple of blank/title rows above the real header — figure out which row number is the actual header row):

{grid}

Return ONLY this JSON object, nothing else:
{{
  "header_row": <1-based row number of the actual column-header row>,
  "field_columns": {{"party": <col number or null>, "date": <col number or null>, "invoice_number": <col number or null>, "amount": <col number or null>, "mode": <col number or null>, "narration": <col number or null>, "direction": <col number or null>, "tds_amount": <col number or null>, "tds_ledger": <col number or null>}},
  "direction_values": {{"receipt_values": ["<value(s) in the direction column that mean money IN, exactly as they appear in the sheet>"], "payment_values": ["<value(s) that mean money OUT, exactly as they appear>"]}},
  "confidence": <0 to 1>,
  "notes": "anything ambiguous about this mapping"
}}

Rules:
- Only map a column if you're reasonably confident from its header text and/or sample values — leave it null rather than guessing.
- A column can only be used for one field.
- party and amount are required — if you can't confidently find both, set them null and explain why in notes rather than guessing.
- Only fill "direction_values" if you also mapped a "direction" column; otherwise leave both lists empty.
- Do not invent columns that don't exist in the sheet.
"""


def map_ledger_excel_headers(grid: list[list]) -> dict:
    """
    Groq counterpart used by simple_ledger_excel_parser.py's AI fallback.
    Same contract as map_excel_headers (raises RuntimeError on missing key
    / API failure), just a different prompt/canonical-field set tuned for
    a Receipt/Payment register instead of a GST bill register.
    """
    api_key = _current_key()
    if not api_key:
        raise RuntimeError(
            "Groq API key is not set. Get a free key at https://console.groq.com/keys "
            "and add it on the Settings page in the app."
        )

    prompt = LEDGER_HEADER_MAP_PROMPT_TEMPLATE.format(
        fields=LEDGER_HEADER_MAP_CANONICAL_FIELDS, grid=_grid_to_text(grid)
    )

    payload = {
        "model": _current_model(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "reasoning_effort": "none",
        "max_tokens": 1000,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    max_retries = 3
    for attempt in range(max_retries + 1):
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code == 429:
            wait_s = None
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_s = float(retry_after)
                except ValueError:
                    pass
            wait_s = (wait_s or 5) + 0.5
            if attempt < max_retries:
                time.sleep(wait_s)
                continue
        if resp.status_code != 200:
            raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:500]}")
        break

    body = resp.json()
    content = body["choices"][0]["message"]["content"]
    try:
        parsed = _extract_json(content)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise RuntimeError(f"Could not parse Groq ledger header-mapping response as JSON: {e}. Raw: {content[:300]}")
    return parsed


def map_excel_headers(grid: list[list]) -> dict:
    """
    grid: a small 2D list (list of rows, each a list of cell values) taken
    from the top of the sheet — header row candidates plus a few data rows
    for context. Column numbering in the response is 1-based to match how
    bill_excel_parser.py already indexes columns.

    Returns the parsed JSON dict described in HEADER_MAP_PROMPT_TEMPLATE.
    Raises RuntimeError on missing key / API failure, same contract as
    extract_bill_with_ai, so callers can fall back to a clear error.
    """
    api_key = _current_key()
    if not api_key:
        raise RuntimeError(
            "Groq API key is not set. Get a free key at https://console.groq.com/keys "
            "and add it on the Settings page in the app."
        )

    prompt = HEADER_MAP_PROMPT_TEMPLATE.format(
        fields=HEADER_MAP_CANONICAL_FIELDS, grid=_grid_to_text(grid)
    )

    payload = {
        "model": _current_model(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "reasoning_effort": "none",
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    max_retries = 3
    for attempt in range(max_retries + 1):
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code == 429:
            wait_s = None
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_s = float(retry_after)
                except ValueError:
                    pass
            wait_s = (wait_s or 5) + 0.5
            if attempt < max_retries:
                time.sleep(wait_s)
                continue
        if resp.status_code != 200:
            raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:500]}")
        break

    body = resp.json()
    content = body["choices"][0]["message"]["content"]
    try:
        parsed = _extract_json(content)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise RuntimeError(f"Could not parse Groq header-mapping response as JSON: {e}. Raw: {content[:300]}")
    return parsed