import os
import shutil
import time
import uuid
from datetime import datetime

import cv2
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..money import round_rupee
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


def _log(db: Session, message: str, document_id: int | None = None, transaction_id: int | None = None):
    db.add(models.AuditLog(document_id=document_id, transaction_id=transaction_id, message=message))
    db.commit()


def _is_duplicate_invoice(db: Session, doc_type: str, party: str, invoice_number: str | None, exclude_tx_id: int | None = None, taxable_value: float | None = None) -> bool:
    """
    True if another APPROVED-or-pending transaction of the same type+party
    already has this exact invoice_number. A blank/null invoice_number is
    never flagged (too common — e.g. cash sales usually have no number, and
    flagging every one of those as a "duplicate" would be pure noise).

    When taxable_value is given, it's matched too (within a small rounding
    tolerance) so that a genuine multi-GST-rate invoice — which can appear
    as more than one line/transaction sharing the same invoice number — is
    not mistaken for a duplicate.
    """
    if not invoice_number or not invoice_number.strip():
        return False
    query = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.type == doc_type,
            models.Transaction.party == party,
            models.Transaction.invoice_number == invoice_number,
            models.Transaction.status != "REJECTED",
        )
    )
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
    if document_type not in ("SALES", "PURCHASE"):
        raise HTTPException(400, f"Unsupported document_type '{document_type}' (expected SALES or PURCHASE)")

    ext = os.path.splitext(file.filename)[1] or ".jpg"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)

    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = models.Document(
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

    _process_document(doc.id, db)

    db.refresh(doc)
    return doc


def _process_document(document_id: int, db: Session):
    doc = db.query(models.Document).get(document_id)
    if doc is None:
        return

    try:
        doc.status = "PROCESSING"
        db.commit()

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
        _log(db, f"Detected {len(box_crop_pairs)} valid bill(s) on page via {detection_method} detection (from {len(boxes)} candidate region(s))", document_id=doc.id)

        if not box_crop_pairs:
            doc.status = "FAILED"
            db.commit()
            _log(db, "No valid bill regions could be cropped from this photo — try a clearer/flatter photo", document_id=doc.id)
            return

        for idx, (box, crop) in enumerate(box_crop_pairs):
            # Small gap between AI calls when a page has multiple bills —
            # avoids bursting through the provider's free-tier per-minute
            # limit in the first place (the retry-with-backoff in
            # groq_vision.py/gemini_vision.py is still there as a backstop
            # if this isn't enough).
            if idx > 0 and has_ai_key():
                time.sleep(2)

            crop_filename = f"{uuid.uuid4().hex}.jpg"
            crop_path = os.path.join(PROCESSED_DIR, crop_filename)
            write_ok = cv2.imwrite(crop_path, crop)
            if not write_ok or not os.path.exists(crop_path):
                # Don't create any DB rows for a bill whose image we can't
                # actually save — that's what previously caused 404s on
                # /files/processed/... for entries the frontend tried to show.
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

            if has_ai_key():
                # Primary path: the selected AI provider (Groq or Gemini)
                # reads the crop directly. Much stronger on handwriting
                # than Tesseract + regex.
                try:
                    if doc.document_type == "PURCHASE":
                        ai = extract_purchase_bill_with_ai(crop_path)
                    else:
                        ai = extract_bill_with_ai(crop_path)
                    raw_text = ai["raw_text"]
                    ocr_conf = ai["confidence"]
                    validation_ok, validation_problems = (
                        _validate_sales_ai(ai) if doc.document_type == "SALES" else (True, [])
                    )

                    ocr_result = models.OcrResult(
                        bill_id=bill.id, raw_text=raw_text, mean_confidence=ocr_conf
                    )
                    db.add(ocr_result)

                    tx = models.Transaction(
                        bill_id=bill.id,
                        type=doc.document_type,
                        # For sales bills, party is always the configured Cash
                        # Ledger — this bill-book format's "Sri" (customer)
                        # field is essentially always left blank for cash
                        # sales, so we don't ask the AI to read it at all
                        # (one less thing that can be misread), and we don't
                        # hardcode the literal string "Cash" either, since
                        # that would silently create a stray duplicate ledger
                        # for any user whose actual Cash Ledger is named
                        # something else in Tally.
                        party=(
                            get_tally_config().get("cash_ledger", "Cash")
                            if doc.document_type == "SALES"
                            else (ai["party"] or "Unknown Supplier")
                        ),
                        date=ai["date"],
                        invoice_number=ai["invoice_number"],
                        taxable_value=ai["taxable_value"],
                        gst_rate=ai["gst_rate"],
                        cgst=ai["cgst"],
                        sgst=ai["sgst"],
                        igst=ai["igst"],
                        total_value=round_rupee(ai["total_value"]),
                        confidence=ai["confidence"],
                        status="NEEDS_REVIEW",
                    )
                    db.add(tx)
                    db.commit()
                    db.refresh(tx)

                    tx.possible_duplicate = _is_duplicate_invoice(db, tx.type, tx.party, tx.invoice_number, exclude_tx_id=tx.id, taxable_value=tx.taxable_value)
                    db.commit()
                    db.refresh(tx)

                    note = f"Bill {idx + 1} (AI/Groq, {doc.document_type}): total={ai['total_value']}, confidence={ai['confidence']}"
                    if ai.get("notes"):
                        note += f" | model notes: {ai['notes']}"
                    if doc.document_type == "SALES" and not validation_ok:
                        note += " | ⚠ VALIDATION FAILED: " + "; ".join(validation_problems)
                    elif doc.document_type == "SALES":
                        note += " | deterministic accounting validation passed"
                    if tx.possible_duplicate:
                        note += f" | ⚠ POSSIBLE DUPLICATE invoice_number '{tx.invoice_number}' for {tx.party}"
                    _log(db, note, document_id=doc.id, transaction_id=tx.id)
                    continue
                except RuntimeError as e:
                    # Accuracy-first rule: never replace a failed vision read
                    # with heuristic OCR that can manufacture amounts. Create a
                    # blank review row instead, so the user fixes the source or
                    # enters the value manually.
                    _log(db, f"Bill {idx + 1}: AI extraction failed — no guessing/fallback values were generated: {e}", document_id=doc.id)
                    tx = models.Transaction(
                        bill_id=bill.id, type=doc.document_type, party="Cash",
                        date=None, invoice_number=None, taxable_value=0.0,
                        gst_rate=0.0, cgst=0.0, sgst=0.0, igst=0.0,
                        total_value=0.0, confidence=0.0, status="NEEDS_REVIEW",
                    )
                    db.add(tx)
                    db.commit()
                    db.refresh(tx)
                    _log(db, f"Bill {idx + 1}: manual entry required because AI could not read it", document_id=doc.id, transaction_id=tx.id)
                    continue

            # No API key configured for the active provider: do not run the
            # old amount heuristics for handwritten sales. A heuristic such
            # as 'largest number = total' is exactly the kind of silent
            # guessing this accounting workflow must avoid.
            if not has_ai_key():
                tx = models.Transaction(
                    bill_id=bill.id, type=doc.document_type, party="Cash",
                    date=None, invoice_number=None, taxable_value=0.0,
                    gst_rate=0.0, cgst=0.0, sgst=0.0, igst=0.0,
                    total_value=0.0, confidence=0.0, status="NEEDS_REVIEW",
                )
                db.add(tx)
                db.commit()
                db.refresh(tx)
                _log(db, f"Bill {idx + 1}: GROQ_API_KEY missing — manual entry required; no guessed values generated", document_id=doc.id, transaction_id=tx.id)
                continue

        doc.status = "NEEDS_REVIEW"
        db.commit()

    except Exception as e:  # noqa: BLE001 - surfaced to the audit log & status
        import traceback
        tb = traceback.format_exc()
        print(f"[document {document_id}] processing failed:\n{tb}")  # visible in the uvicorn terminal
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Processing failed: {type(e).__name__}: {e}", document_id=doc.id)


@router.get("", response_model=list[schemas.DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    return db.query(models.Document).order_by(models.Document.uploaded_at.desc()).all()


@router.get("/{document_id}", response_model=schemas.DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.query(models.Document).get(document_id)
    if doc is None:
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