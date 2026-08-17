"""
Sends a single cropped bill image straight to a Groq vision-language model
and asks it to return structured accounting fields as JSON.

This replaces the Tesseract -> regex pipeline for accuracy on handwriting.
Requires a free API key from https://console.groq.com/keys, set as the
GROQ_API_KEY environment variable (see backend/.env.example).

Model: llama-4-scout, Groq's current fast vision-capable model. If Groq
changes model names, update GROQ_VISION_MODEL below.
"""
import os
import base64
import json
import re
import time

import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")

SALES_EXTRACTION_PROMPT = """You are an extremely careful accounting data-entry system reading ONE CROP from a handwritten Indian sales bill-book page. The original page contains FOUR separate bills arranged 2x2. This image is already cropped to exactly ONE bill. Never read handwriting from another quadrant.

Your job is transcription, not estimation. A wrong number is worse than a missing number.

FIELD MAP — locate the printed label first, then read the value beside that label:
1. party: handwritten customer name beside the printed "Sri" / customer field. If genuinely blank, use "Cash". Never invent a customer.
2. date: date beside the printed Date field. Return YYYY-MM-DD. If unclear, return null.
3. invoice_number: handwritten value beside the printed "No." / "No. A" field. NEVER use V.No. Read character-by-character. If one character is unclear, return null rather than guessing.
4. taxable_value: the FIRST/upper "Total" in the tax summary, BEFORE CGST/SGST/IGST.
5. cgst: the CGST AMOUNT, not the percentage.
6. sgst: the SGST AMOUNT, not the percentage.
7. igst: the IGST AMOUNT if this is clearly an IGST bill; otherwise 0.
8. gst_rate: combined GST percentage. Example CGST 6% + SGST 6% => 12. IGST 12% => 12.
9. total_value: the FINAL/last "Total" in the tax summary, AFTER tax. This is the invoice total.

IMPORTANT VISUAL PROCEDURE:
- First inspect the whole crop to understand the bill layout.
- Then zoom your attention to the header fields (No., Date, Sri).
- Then inspect the bottom tax summary for every numeric field.
- Re-read every handwritten digit once before answering.
- Do not infer a number from arithmetic. If a number is not legible, return null for that field.
- Do not copy numbers from nearby printed examples, GSTIN, phone numbers, V.No, HSN, or other fields.
- Distinguish 0/6, 1/7, 3/8, 4/9 and 5/6 carefully.

Return ONLY this JSON object:
{
  "party": "customer name or Cash",
  "date": "YYYY-MM-DD or null",
  "invoice_number": "exact handwritten invoice number or null",
  "taxable_value": number or null,
  "gst_rate": number or null,
  "cgst": number or null,
  "sgst": number or null,
  "igst": number or null,
  "total_value": number or null,
  "confidence": number from 0 to 1,
  "notes": "specific ambiguity, if any"
}

NO-GUESSING RULES:
- Never calculate a missing taxable value from total and GST.
- Never calculate a missing CGST/SGST from the GST rate.
- Never calculate a missing total from taxable + tax.
- If the source field is blank, use null for that field (use 0 only when the tax type is clearly not applicable).
- If two values conflict, preserve the values actually written and explain the conflict in notes.
- If the crop is blurry, incomplete, contains more than one bill, or a required amount cannot be read, lower confidence and return null for the uncertain field.
- Do not claim 100% confidence. The final application performs a deterministic arithmetic check before anything can be approved for Tally.
"""

# Backward-compat alias — other modules / earlier versions of this file
# imported EXTRACTION_PROMPT directly.
EXTRACTION_PROMPT = SALES_EXTRACTION_PROMPT

PURCHASE_EXTRACTION_PROMPT = """You are an accounting data-entry assistant reading a photo of ONE purchase bill / supplier tax invoice received by an Indian small business. This is a bill FROM a supplier TO this business (the reverse direction of a sales bill) — usually a printed invoice from the supplier's own billing system, not a handwritten bill-book form.

FOLLOW THIS SEARCH PROCEDURE for the two highest-risk fields, in order, before extracting anything else:

STEP 1 — Find the SUPPLIER.
Look at the very top of the document, typically top-left, often next to a logo: a company name, address, and GSTIN printed together as a letterhead. That company is the "party" — it is who ISSUED this bill. Confirm it by checking its GSTIN appears again in the tax/summary area as the seller's GSTIN. This is the only place to find the supplier name — do not use any company name that appears inside a box labeled "Consignee", "Ship to", "Buyer", or "Bill to" anywhere else on the page; those boxes always describe the RECIPIENT (this business itself, e.g. "Sarvotham Traders"), never the supplier.

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
    # qwen3.6 is a "thinking" model: it may prepend a <think>...</think>
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
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Get a free key at https://console.groq.com/keys "
            "and set it as an environment variable (see backend/.env.example)."
        )

    image_data_uri = _encode_image(image_path)

    payload = {
        "model": GROQ_VISION_MODEL,
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
        # qwen/qwen3.6-27b is a "thinking" model that otherwise burns most of
        # max_tokens on an internal <think>...</think> reasoning block before
        # ever getting to the JSON answer. Groq's supported way to turn this
        # off for qwen3 models is reasoning_effort="none" (NOT
        # chat_template_kwargs, which Groq's endpoint rejects with a 400).
        "reasoning_effort": "none",
        "max_tokens": 1500,
    }

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
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
