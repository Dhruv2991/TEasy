import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from typing import List, Optional

import cv2
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from ..database import get_db
from .. import models, schemas
from ..settings import get_active_company_id
from ..gst_states import gstin_to_state
from ..ocr.preprocess import preprocess_pipeline
from ..ocr.bill_detector import crop_bills
from ..ocr.grid_detector import detect_four_bill_grid
from ..ocr.ai_vision import extract_bill_with_ai, extract_purchase_bill_with_ai, has_ai_key
from ..tally.config import get_tally_config

router = APIRouter(prefix="/documents", tags=["documents"])

from ..paths import get_data_dir

UPLOAD_DIR = os.path.join(get_data_dir(), "documents")
PROCESSED_DIR = os.path.join(get_data_dir(), "processed")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)

# Mandatory throttle delay between processing sub-crops of the same bill
# to respect Groq's 7,000 ITPM rolling window.
CROP_THROTTLE_DELAY_SECONDS = 25.0


def _log(db: Session, message: str, document_id: int | None = None, transaction_id: int | None = None):
    db.add(models.AuditLog(document_id=document_id, transaction_id=transaction_id, message=message))
    db.commit()


def _is_duplicate_invoice(
    db: Session,
    doc_type: str,
    party: str,
    invoice_number: str | None,
    exclude_tx_id: int | None = None,
    taxable_value: float | None = None,
    company_id: int | None = None,
    irn: str | None = None,
) -> bool:
    """
    True if another APPROVED-or-pending transaction of the same type+party
    already has this exact invoice_number or IRN hash. Scoped to the active company.
    """
    scope_cid = company_id if company_id is not None else get_active_company_id()

    # Check cryptographic IRN hash first if present
    if irn and irn.strip():
        irn_query = db.query(models.Transaction).filter(
            models.Transaction.irn == irn.strip(),
            models.Transaction.status != "REJECTED",
        )
        if scope_cid:
            irn_query = irn_query.filter(models.Transaction.company_id == scope_cid)
        if exclude_tx_id:
            irn_query = irn_query.filter(models.Transaction.id != exclude_tx_id)
        if db.query(irn_query.exists()).scalar():
            return True

    if not invoice_number or not invoice_number.strip():
        return False

    query = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.type == doc_type,
            models.Transaction.party == party,
            models.Transaction.invoice_number == invoice_number.strip(),
            models.Transaction.status != "REJECTED",
        )
    )
    if scope_cid:
        query = query.filter(models.Transaction.company_id == scope_cid)
    if taxable_value is not None:
        query = query.filter(
            models.Transaction.taxable_value.between(taxable_value - 1.0, taxable_value + 1.0)
        )
    if exclude_tx_id:
        query = query.filter(models.Transaction.id != exclude_tx_id)
    return db.query(query.exists()).scalar()


def _validate_sales_ai(ai: dict) -> tuple[bool, list[str]]:
    """Deterministic accounting checks. A transaction is never auto-approved from AI alone."""
    problems = []
    required = {
        "date": ai.get("date"),
        "taxable_value": ai.get("taxable_value"),
        "total_value": ai.get("total_value"),
    }
    for name, value in required.items():
        if value is None or (isinstance(value, (int, float)) and value < 0):
            problems.append(f"missing/invalid {name}")

    if ai.get("date") and not isinstance(ai.get("date"), str):
        problems.append("invalid date format")

    # Require the core header identity. Cash is allowed, but an invoice number
    # that cannot be read must remain a manual-review item.
    if not ai.get("invoice_number"):
        problems.append("invoice number not confidently read")

    try:
        taxable = float(ai.get("taxable_value"))
        cgst = float(ai.get("cgst") or 0)
        sgst = float(ai.get("sgst") or 0)
        igst = float(ai.get("igst") or 0)
        total = float(ai.get("total_value"))
        tax = cgst + sgst + igst
        if abs((taxable + tax) - total) > 1.50:
            problems.append(f"amounts do not reconcile: taxable + tax = {taxable + tax:.2f}, total = {total:.2f}")
        rate = float(ai.get("gst_rate") or 0)
        if rate == 0 and tax > 1.0:
            problems.append("GST amounts exist but GST rate is missing")
        if rate > 0 and taxable > 0 and tax == 0:
            problems.append("GST rate exists but tax amount is zero")
    except (TypeError, ValueError):
        problems.append("invalid numeric value")

    try:
        confidence = float(ai.get("confidence") or 0)
        if confidence < 0.80:
            problems.append(f"AI confidence below safe threshold ({confidence:.2f})")
    except (TypeError, ValueError):
        problems.append("invalid confidence")

    return not problems, problems


@router.post("/upload", response_model=schemas.DocumentOut)
def upload_document(
    file: UploadFile = File(...),
    document_type: str = Form("SALES"),
    db: Session = Depends(get_db),
):
    document_type = document_type.upper()
    if document_type not in ("SALES", "PURCHASE", "RECEIPT", "PAYMENT", "JOURNAL", "CONTRA", "STOCK_JOURNAL"):
        raise HTTPException(400, f"Unsupported document_type '{document_type}' (expected SALES, PURCHASE, RECEIPT, PAYMENT, JOURNAL, CONTRA, or STOCK_JOURNAL)")

    ext = (os.path.splitext(file.filename)[1] or ".jpg").lower()
    allowed_ext = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".xlsx", ".xls"}
    if ext not in allowed_ext:
        raise HTTPException(
            400,
            f"Unsupported file type '{ext}'. Upload a bill photo (.jpg/.png), a PDF invoice, "
            "or an Excel sheet of bills (.xlsx/.xls).",
        )
    if document_type in ("RECEIPT", "PAYMENT", "JOURNAL", "CONTRA", "STOCK_JOURNAL") and ext not in (".xlsx", ".xls"):
        raise HTTPException(
            400,
            f"'{document_type}' uploads are Excel-only right now (a fee/rent/salary/journal "
            "register sheet, not a photo) — please upload an .xlsx or .xls file.",
        )

    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)

    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = models.Document(
        company_id=get_active_company_id(),
        file_name=file.filename,
        file_path=saved_path,
        document_type=document_type,
        status="UPLOADED",
        uploaded_at=datetime.utcnow(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    _log(db, f"Document '{file.filename}' uploaded ({document_type})", document_id=doc.id)

    try:
        if ext in (".xlsx", ".xls"):
            _process_excel_document(doc.id, db)
        else:
            _process_document(doc.id, db)
    except HTTPException as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Upload processing failed: {e.detail}", document_id=doc.id)
        raise

    db.refresh(doc)
    return doc


def parse_rate_limit_delay(error_msg: str, default_delay: float = 25.0, safety_buffer: float = 3.0) -> float:
    """
    Extracts required wait time in seconds from a 429 rate limit error message:
    - 'Please try again in 18.23s.' -> 18.23 + 3.0 = 21.23
    - 'Please try again in 1m15s.' -> 75.0 + 3.0 = 78.0
    - 'Please try again in 30 seconds' -> 30.0 + 3.0 = 33.0
    Falls back to default_delay + safety_buffer if no explicit duration is parsed.
    """
    if not error_msg:
        return default_delay + safety_buffer

    msg = str(error_msg).lower()

    # 1. Match "try again in Xm Ys" or "Xm"
    m_min_sec = re.search(r"try again in (?:(\d+)\s*m(?:in(?:ute)?s?)?)?\s*(?:(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?)?", msg)
    if m_min_sec and (m_min_sec.group(1) or m_min_sec.group(2)):
        minutes = float(m_min_sec.group(1) or 0)
        seconds = float(m_min_sec.group(2) or 0)
        total = minutes * 60 + seconds
        if total > 0:
            return round(total + safety_buffer, 2)

    # 2. Match general "try again in Xs" / "retry after Xs"
    m_sec = re.search(r"(?:try again in|retry after|wait)\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?", msg)
    if m_sec and m_sec.group(1):
        try:
            return round(float(m_sec.group(1)) + safety_buffer, 2)
        except ValueError:
            pass

    # 3. Match any duration like "18.23s" following "try again"
    m_simple = re.search(r"try again.*?(\d+(?:\.\d+)?)\s*s\b", msg)
    if m_simple and m_simple.group(1):
        try:
            return round(float(m_simple.group(1)) + safety_buffer, 2)
        except ValueError:
            pass

    return default_delay + safety_buffer


@router.post("/batch-upload", response_model=schemas.BatchUploadResponse)
def batch_upload_documents(
    files: List[UploadFile] = File(...),
    document_type: str = Form("SALES"),
    delay_seconds: float = Form(25.0),
    db: Session = Depends(get_db),
):
    """
    Sequentially processes a batch of bill files with a controlled throttle delay
    (default 25 seconds / 25000ms) between requests to respect Groq's 7,000 TPM
    rolling 1-minute token window.

    Includes smart HTTP 429 backoff: when rate limits occur, extracts the required
    wait time, pauses the queue with a safety buffer, and automatically retries.
    """
    results: List[schemas.BatchUploadItemOut] = []
    total = len(files)
    successful = 0
    failed = 0

    for idx, file in enumerate(files):
        # Sequential throttle delay between individual files
        if idx > 0 and delay_seconds > 0:
            logger.info(
                f"[Batch Upload] Applying {delay_seconds}s throttle delay before processing file {idx + 1}/{total} ('{file.filename}')"
            )
            time.sleep(delay_seconds)

        attempts = 0
        MAX_RETRIES = 2
        file_success = False

        while attempts <= MAX_RETRIES and not file_success:
            attempts += 1
            try:
                if hasattr(file.file, "seek"):
                    file.file.seek(0)
                doc = upload_document(file=file, document_type=document_type, db=db)
                successful += 1
                file_success = True
                results.append(
                    schemas.BatchUploadItemOut(
                        filename=file.filename,
                        status="success",
                        document=doc,
                        error=None,
                    )
                )
            except HTTPException as e:
                err_msg = str(e.detail) if hasattr(e, "detail") else str(e)
                if e.status_code == 429 and attempts <= MAX_RETRIES:
                    wait_time = parse_rate_limit_delay(err_msg, default_delay=25.0, safety_buffer=3.0)
                    logger.warning(
                        f"[Batch Upload] 429 Rate limit on file {idx + 1}/{total} ('{file.filename}'). "
                        f"Pausing queue for {wait_time}s before retry (attempt {attempts}/{MAX_RETRIES}). Detail: {err_msg}"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    failed += 1
                    logger.warning(
                        f"[Batch Upload] Failed processing file {idx + 1}/{total} ('{file.filename}'): {err_msg}"
                    )
                    results.append(
                        schemas.BatchUploadItemOut(
                            filename=file.filename,
                            status="error",
                            document=None,
                            error=err_msg,
                        )
                    )
                    break
            except Exception as e:
                failed += 1
                err_msg = str(e)
                logger.error(
                    f"[Batch Upload] Unexpected error processing file {idx + 1}/{total} ('{file.filename}'): {err_msg}",
                    exc_info=True,
                )
                results.append(
                    schemas.BatchUploadItemOut(
                        filename=file.filename,
                        status="error",
                        document=None,
                        error=err_msg,
                    )
                )
                break

    return schemas.BatchUploadResponse(
        total=total,
        successful=successful,
        failed=failed,
        throttle_delay_seconds=delay_seconds,
        results=results,
    )


def _process_ledger_excel_document(document_id: int, db: Session):
    """General-users path for a Receipt, Payment, or Journal register: a
    spreadsheet where each row is one transaction — a college's fee
    collection, a landlord's rent-received log, a payroll sheet, a
    donation register, or a plain two-ledger journal entry sheet. No GST,
    no items — see simple_ledger_excel_parser.py."""
    from ..extraction.simple_ledger_excel_parser import parse_ledger_excel, rows_to_transactions

    doc = db.query(models.Document).get(document_id)
    if doc is None:
        return

    try:
        doc.status = "PROCESSING"
        db.commit()

        parsed_rows = parse_ledger_excel(doc.file_path)
        rows = rows_to_transactions(parsed_rows, doc.document_type)
    except ValueError as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Excel parsing failed: {e}", document_id=doc.id)
        return
    except Exception as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Excel parsing failed unexpectedly: {e}", document_id=doc.id)
        return

    _log(db, f"Parsed {len(rows)} {doc.document_type.lower()} row(s) from '{doc.file_name}'", document_id=doc.id)

    for idx, row in enumerate(rows):
        bill = models.DetectedBill(document_id=doc.id, crop_path=None, bbox=None, order_in_page=idx)
        db.add(bill)
        db.commit()
        db.refresh(bill)

        tx = models.Transaction(
            bill_id=bill.id, type=row["type"], party=row["party"], date=row["date"],
            invoice_number=row["invoice_number"], total_value=row["total_value"],
            debit=row["debit"], credit=row["credit"], narration=row["narration"],
            bank_ledger=row["bank_ledger"], confidence=row["confidence"], status="NEEDS_REVIEW",
            tds_amount=row.get("tds_amount") or 0.0, tds_ledger=row.get("tds_ledger"),
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        tx.possible_duplicate = _is_duplicate_invoice(
            db, tx.type, tx.party, tx.invoice_number, exclude_tx_id=tx.id, taxable_value=tx.total_value
        )
        db.commit()
        db.refresh(tx)

        note = f"Row {idx + 1} (Excel, {doc.document_type}): {tx.party} — {tx.total_value}"
        if tx.possible_duplicate:
            note += f" | ⚠ POSSIBLE DUPLICATE reference '{tx.invoice_number}' for {tx.party}"
        _log(db, note, document_id=doc.id, transaction_id=tx.id)

    doc.status = "NEEDS_REVIEW"
    db.commit()


def _process_stock_journal_document(document_id: int, db: Session):
    """Pure inventory-movement register: godown-to-godown transfers,
    manufacturing consumption, or manufacturing production — one row per
    movement, no ledger/GST involved. See stock_journal_excel_parser.py."""
    import json as _json
    from ..extraction.stock_journal_excel_parser import parse_stock_journal_excel, rows_to_transactions

    doc = db.query(models.Document).get(document_id)
    if doc is None:
        return

    try:
        doc.status = "PROCESSING"
        db.commit()

        parsed_rows = parse_stock_journal_excel(doc.file_path)
        rows = rows_to_transactions(parsed_rows)
    except ValueError as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Excel parsing failed: {e}", document_id=doc.id)
        return
    except Exception as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Excel parsing failed unexpectedly: {e}", document_id=doc.id)
        return

    _log(db, f"Parsed {len(rows)} stock journal row(s) from '{doc.file_name}'", document_id=doc.id)

    for idx, row in enumerate(rows):
        bill = models.DetectedBill(document_id=doc.id, crop_path=None, bbox=None, order_in_page=idx)
        db.add(bill)
        db.commit()
        db.refresh(bill)

        item_name = row["items"][0]["name"] if row.get("items") else None
        tx = models.Transaction(
            bill_id=bill.id, type=row["type"], party=row["party"], date=row["date"],
            invoice_number=row["invoice_number"], total_value=row["total_value"],
            debit=row["debit"], credit=row["credit"], narration=row["narration"],
            bank_ledger=row["bank_ledger"], confidence=row["confidence"], status="NEEDS_REVIEW",
            items=_json.dumps(row["items"]) if row.get("items") else None,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        note = f"Row {idx + 1} (Excel, Stock Journal): {item_name} — {row['party'] or '?'} → {row['bank_ledger'] or '?'}"
        _log(db, note, document_id=doc.id, transaction_id=tx.id)

    doc.status = "NEEDS_REVIEW"
    db.commit()


def _process_excel_document(document_id: int, db: Session):
    """General-users path: a spreadsheet where each row is one bill (their
    own sales/purchase register), rather than a photo. No AI/OCR involved —
    the values are read directly from the sheet, so confidence is set to
    1.0 for a fully-populated row (still routed to NEEDS_REVIEW, same as
    every other source, so nothing skips human approval)."""
    from ..extraction.bill_excel_parser import parse_bill_excel

    doc = db.query(models.Document).get(document_id)
    if doc is None:
        return

    if doc.document_type in ("RECEIPT", "PAYMENT", "JOURNAL", "CONTRA"):
        return _process_ledger_excel_document(document_id, db)

    if doc.document_type == "STOCK_JOURNAL":
        return _process_stock_journal_document(document_id, db)

    try:
        doc.status = "PROCESSING"
        db.commit()

        rows = parse_bill_excel(doc.file_path)
    except ValueError as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Excel parsing failed: {e}", document_id=doc.id)
        return
    except Exception as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Excel parsing failed unexpectedly: {e}", document_id=doc.id)
        return

    _log(db, f"Parsed {len(rows)} bill row(s) from '{doc.file_name}'", document_id=doc.id)

    for idx, row in enumerate(rows):
        # No source photo for a spreadsheet row, so crop_path stays null —
        # same pattern already used for GSTR-2B-derived rows.
        bill = models.DetectedBill(document_id=doc.id, crop_path=None, bbox=None, order_in_page=idx)
        db.add(bill)
        db.commit()
        db.refresh(bill)

        party = row.party or (
            get_tally_config().get("cash_ledger", "Cash") if doc.document_type == "SALES" else "Unknown Supplier"
        )
        # Exact numbers straight from the sheet, so confidence isn't an AI
        # read-quality signal here — 1.0 unless a required field is
        # actually missing, in which case the row needs manual completion
        # like any other incomplete transaction.
        confidence = 0.5 if row.warnings else 1.0

        import json as _json

        # The item-wise Tally-template parser can carry its own Vch. Type
        # per row (Sales/Purchase/Credit Note/...) which is more reliable
        # than the blanket document_type the user picked at upload time
        # (a single spreadsheet legitimately mixes Sales and Credit Note
        # rows in Tally's own export format).
        row_type = doc.document_type
        if row.vch_type:
            vt = row.vch_type.strip().upper()
            if "CREDIT" in vt:
                row_type = "CREDIT_NOTE"
            elif "DEBIT" in vt:
                row_type = "DEBIT_NOTE"
            elif "SALE" in vt:
                row_type = "SALES"
            elif "PURCHASE" in vt:
                row_type = "PURCHASE"

        row_gstin = getattr(row, "party_gstin", None)
        tx = models.Transaction(
            bill_id=bill.id, type=row_type, party=party, date=row.date,
            invoice_number=row.invoice_number, taxable_value=row.taxable_value,
            gst_rate=row.gst_rate, cgst=row.cgst, sgst=row.sgst, igst=row.igst,
            total_value=row.total_value, confidence=confidence,
            status="NEEDS_REVIEW",
            party_gstin=row_gstin,
            party_state=gstin_to_state(row_gstin),
            company_id=doc.company_id,
            rate_breakdown=_json.dumps(row.rate_breakdown) if len(row.rate_breakdown) > 1 else None,
            items=_json.dumps(row.items) if row.items else None,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        tx.possible_duplicate = _is_duplicate_invoice(
            db, tx.type, tx.party, tx.invoice_number, exclude_tx_id=tx.id, taxable_value=tx.taxable_value, company_id=doc.company_id
        )
        db.commit()
        db.refresh(tx)

        note = f"Row {idx + 1} (Excel, {doc.document_type}): total={row.total_value}"
        if row.warnings:
            note += " | ⚠ " + "; ".join(row.warnings)
        if tx.possible_duplicate:
            note += f" | ⚠ POSSIBLE DUPLICATE invoice_number '{tx.invoice_number}' for {tx.party}"
        _log(db, note, document_id=doc.id, transaction_id=tx.id)

    doc.status = "NEEDS_REVIEW"
    db.commit()


def _pdf_pages_to_images(pdf_path: str) -> list:
    """Rasterizes each PDF page to a BGR numpy image (OpenCV format) using
    pdfplumber, which is already a project dependency — avoids adding a new
    external binary (poppler/ghostscript) requirement just for this."""
    import numpy as np
    import pdfplumber

    images = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pil_img = page.to_image(resolution=200).original.convert("RGB")
            arr = np.array(pil_img)
            images.append(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    return images


def _process_document(document_id: int, db: Session):
    doc = db.query(models.Document).get(document_id)
    if doc is None:
        return

    try:
        doc.status = "PROCESSING"
        db.commit()

        ext = os.path.splitext(doc.file_path)[1].lower()

        if ext == ".pdf":
            # A PDF invoice/bill is a printed single document per page —
            # same "whole page is one bill" treatment already used for
            # purchase photos, applied per page, then fed through the exact
            # same AI vision extraction used for photos (same accuracy
            # path, not a separate weaker one).
            try:
                page_images = _pdf_pages_to_images(doc.file_path)
            except Exception as e:
                doc.status = "FAILED"
                db.commit()
                _log(db, f"Could not open/rasterize this PDF: {e}", document_id=doc.id)
                return
            if not page_images:
                doc.status = "FAILED"
                db.commit()
                _log(db, "This PDF has no readable pages", document_id=doc.id)
                return
            box_crop_pairs = [((0, 0, im.shape[1], im.shape[0]), im) for im in page_images]
            detection_method = f"single bill per page ({len(page_images)} page(s) from PDF)"
        else:
            img = preprocess_pipeline(doc.file_path)

            if doc.document_type == "PURCHASE":
                # Purchase bills are supplier invoices — one printed invoice per
                # photo is the overwhelmingly common case (unlike the sales
                # bill-book which packs 4 handwritten forms onto one page), so
                # skip the multi-bill grid/contour splitting and treat the whole
                # photo as a single bill. This also avoids the grid detector
                # mistakenly slicing one A4 invoice into fake sub-regions.
                h, w = img.shape[:2]
                boxes = [(0, 0, w, h)]
                detection_method = "single (purchase)"
            else:
                # Sales pages in this project are 4 handwritten bill-book forms in
                # a 2x2 layout. Split deterministically into four regions. This is
                # deliberately NOT a contour guess: internal table lines and
                # handwriting must never cause one bill to be merged with another.
                boxes = detect_four_bill_grid(img)
                detection_method = "deterministic 2x2 sales grid"

            box_crop_pairs = crop_bills(img, boxes)

        _log(db, f"Detected {len(box_crop_pairs)} valid bill(s) on page via {detection_method} detection", document_id=doc.id)

        if not box_crop_pairs:
            doc.status = "FAILED"
            db.commit()
            _log(db, "No valid bill regions could be cropped from this photo — try a clearer/flatter photo", document_id=doc.id)
            return

        # If PDF, attempt native vector and QR code extraction first
        pdf_parsed_pages = []
        if ext == ".pdf":
            try:
                from ..extraction.pdf_invoice_parser import parse_pdf_document
                pdf_parsed_pages = parse_pdf_document(doc.file_path, doc_type=doc.document_type)
            except Exception as e:
                _log(db, f"PDF native parsing notice: {e} (proceeding with rasterized processing)", document_id=doc.id)

        import json as _json

        for idx, (box, crop) in enumerate(box_crop_pairs):
            # Mandatory sequential throttle delay between processing individual crops
            # of the same bill to respect Groq's 7,000 ITPM rolling window.
            if idx > 0 and CROP_THROTTLE_DELAY_SECONDS > 0:
                _log(
                    db,
                    f"Applying {CROP_THROTTLE_DELAY_SECONDS}s cooldown before extracting crop {idx + 1}/{len(box_crop_pairs)} "
                    "to respect Groq rate limits",
                    document_id=doc.id,
                )
                logger.info(
                    f"Document {doc.id}: Applying {CROP_THROTTLE_DELAY_SECONDS}s cooldown before crop {idx + 1}/{len(box_crop_pairs)}"
                )
                time.sleep(CROP_THROTTLE_DELAY_SECONDS)

            crop_filename = f"{uuid.uuid4().hex}.jpg"
            crop_path = os.path.join(PROCESSED_DIR, crop_filename)
            write_ok = cv2.imwrite(crop_path, crop)
            if not write_ok or not os.path.exists(crop_path):
                _log(db, f"Bill {idx + 1}: failed to save cropped image, skipping this bill", document_id=doc.id)
                continue

            bill = models.DetectedBill(
                document_id=doc.id,
                crop_path=crop_path,
                bbox=",".join(str(b) for b in box),
                order_in_page=idx,
            )
            db.add(bill)
            db.commit()
            db.refresh(bill)

            try:
                # Check if native PDF extractor already parsed this page
                ai = None
                if ext == ".pdf" and idx < len(pdf_parsed_pages) and pdf_parsed_pages[idx]:
                    ai = pdf_parsed_pages[idx]

                # Smart 429 wait-time parsing and auto-retry loop for this crop
                if not ai:
                    crop_attempts = 0
                    max_crop_retries = 2
                    while crop_attempts <= max_crop_retries and not ai:
                        crop_attempts += 1
                        try:
                            if doc.document_type == "PURCHASE":
                                ai = extract_purchase_bill_with_ai(crop_path)
                            else:
                                ai = extract_bill_with_ai(crop_path)
                        except HTTPException as e:
                            err_msg = str(e.detail) if hasattr(e, "detail") else str(e)
                            if e.status_code == 429 and crop_attempts <= max_crop_retries:
                                wait_time = parse_rate_limit_delay(err_msg, default_delay=25.0, safety_buffer=3.0)
                                _log(
                                    db,
                                    f"Bill crop {idx + 1}: Groq 429 rate limit hit. Pausing for {wait_time}s before auto-retry "
                                    f"(attempt {crop_attempts}/{max_crop_retries})...",
                                    document_id=doc.id,
                                )
                                logger.warning(
                                    f"Document {doc.id}, Crop {idx + 1}: Groq 429 rate limit hit. Pausing for {wait_time}s "
                                    f"before retry ({crop_attempts}/{max_crop_retries}). Detail: {err_msg}"
                                )
                                time.sleep(wait_time)
                                continue
                            else:
                                _log(
                                    db,
                                    f"Bill crop {idx + 1}: rate limit retry failed after {crop_attempts} attempts: {err_msg}",
                                    document_id=doc.id,
                                )
                                raise

                raw_text = ai.get("raw_text") or ""
                ocr_conf = float(ai.get("confidence") or 0.0)
                validation_ok, validation_problems = (
                    _validate_sales_ai(ai) if doc.document_type == "SALES" else (True, [])
                )

                ocr_result = models.OcrResult(
                    bill_id=bill.id, raw_text=raw_text, mean_confidence=ocr_conf
                )
                db.add(ocr_result)

                # Format items if extracted from e-invoice / PDF tables
                items_json = None
                if ai.get("items"):
                    items_json = _json.dumps(ai["items"]) if isinstance(ai["items"], list) else str(ai["items"])

                ai_party = ai.get("party")
                if doc.document_type == "SALES":
                    # If an actual customer name was detected from digital PDF / e-invoice QR, preserve it
                    if ai_party and ai_party.lower() not in ("cash", "unknown supplier", "supplier"):
                        tx_party = ai_party
                    else:
                        tx_party = get_tally_config().get("cash_ledger", "Cash")
                else:
                    tx_party = ai_party or "Unknown Supplier"

                extracted_gstin = ai.get("party_gstin")
                tx = models.Transaction(
                    bill_id=bill.id,
                    type=doc.document_type,
                    party=tx_party,
                    date=ai.get("date"),
                    invoice_number=ai.get("invoice_number"),
                    irn=ai.get("irn"),
                    taxable_value=float(ai.get("taxable_value") or 0.0),
                    gst_rate=float(ai.get("gst_rate") or 0.0),
                    cgst=float(ai.get("cgst") or 0.0),
                    sgst=float(ai.get("sgst") or 0.0),
                    igst=float(ai.get("igst") or 0.0),
                    cess=float(ai.get("cess") or 0.0),
                    total_value=float(ai.get("total_value") or 0.0),
                    confidence=ocr_conf,
                    status="NEEDS_REVIEW",
                    party_gstin=extracted_gstin,
                    party_state=gstin_to_state(extracted_gstin),
                    company_id=doc.company_id,
                    items=items_json,
                )
                db.add(tx)
                db.commit()
                db.refresh(tx)

                tx.possible_duplicate = _is_duplicate_invoice(
                    db, tx.type, tx.party, tx.invoice_number, exclude_tx_id=tx.id, taxable_value=tx.taxable_value, company_id=doc.company_id, irn=ai.get("irn")
                )
                db.commit()
                db.refresh(tx)

                note = f"Bill {idx + 1} (Offline Engine, {doc.document_type}): total={ai.get('total_value')}, confidence={ai.get('confidence')}"
                if ai.get("notes"):
                    note += f" | {ai['notes']}"
                if doc.document_type == "SALES" and not validation_ok:
                    note += " | ⚠ VALIDATION NOTICE: " + "; ".join(validation_problems)
                elif doc.document_type == "SALES":
                    note += " | deterministic accounting validation passed"
                if tx.possible_duplicate:
                    note += f" | ⚠ POSSIBLE DUPLICATE invoice_number '{tx.invoice_number}' for {tx.party}"
                _log(db, note, document_id=doc.id, transaction_id=tx.id)
                continue

            except HTTPException as e:
                # If rate-limit retries for this crop are exhausted or non-retryable HTTPException occurs,
                # record fallback transaction so subsequent crops can still be processed!
                _log(db, f"Bill {idx + 1}: extraction error ({e.detail}) — manual review required", document_id=doc.id)
                tx = models.Transaction(
                    bill_id=bill.id, type=doc.document_type, party="Cash",
                    date=None, invoice_number=None, taxable_value=0.0,
                    gst_rate=0.0, cgst=0.0, sgst=0.0, igst=0.0,
                    total_value=0.0, confidence=0.0, status="NEEDS_REVIEW",
                )
                db.add(tx)
                db.commit()
                db.refresh(tx)
                continue
            except Exception as e:
                _log(db, f"Bill {idx + 1}: extraction notice: {e}", document_id=doc.id)
                tx = models.Transaction(
                    bill_id=bill.id, type=doc.document_type, party="Cash",
                    date=None, invoice_number=None, taxable_value=0.0,
                    gst_rate=0.0, cgst=0.0, sgst=0.0, igst=0.0,
                    total_value=0.0, confidence=0.0, status="NEEDS_REVIEW",
                )
                db.add(tx)
                db.commit()
                db.refresh(tx)
                _log(db, f"Bill {idx + 1}: manual entry required", document_id=doc.id, transaction_id=tx.id)
                continue

        doc.status = "NEEDS_REVIEW"
        db.commit()

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - surfaced to the audit log & status
        import traceback
        tb = traceback.format_exc()
        print(f"[document {document_id}] processing failed:\n{tb}")  # visible in the uvicorn terminal
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Processing failed: {type(e).__name__}: {e}", document_id=doc.id)


@router.get("", response_model=list[schemas.DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    active_id = get_active_company_id()
    q = db.query(models.Document)
    if active_id:
        q = q.filter(models.Document.company_id == active_id)
    return q.order_by(models.Document.uploaded_at.desc()).all()


@router.get("/{document_id}", response_model=schemas.DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.query(models.Document).get(document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    # Same isolation as the list endpoint — a document ID typed/guessed
    # from outside the current company's own scoped list shouldn't be
    # fetchable. 404 (not 403) so this doesn't confirm whether a given ID
    # exists in another company at all.
    active_id = get_active_company_id()
    if active_id and doc.company_id and doc.company_id != active_id:
        raise HTTPException(404, "Document not found")
    return doc


@router.get("/{document_id}/logs")
def get_document_logs(document_id: int, db: Session = Depends(get_db)):
    logs = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.document_id == document_id)
        .order_by(models.AuditLog.created_at.asc())
        .all()
    )
    return [{"time": l.created_at.isoformat(), "message": l.message} for l in logs]