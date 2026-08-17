import os
import shutil
import uuid
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..paths import get_data_dir
from ..gstr2b.parser import parse_gstr2b_excel

router = APIRouter(prefix="/gstr2b", tags=["gstr2b"])

UPLOAD_DIR = os.path.join(get_data_dir(), "documents")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _log(db: Session, message: str, document_id: int | None = None, transaction_id: int | None = None):
    db.add(models.AuditLog(document_id=document_id, transaction_id=transaction_id, message=message))
    db.commit()


@router.post("/upload", response_model=schemas.DocumentOut)
def upload_gstr2b(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload the GSTR-2B .xlsx file downloaded from the GST portal (not an image).")

    ext = os.path.splitext(file.filename)[1] or ".xlsx"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = models.Document(
        file_name=file.filename,
        file_path=saved_path,
        document_type="GSTR2B",
        status="PROCESSING",
        uploaded_at=datetime.utcnow(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    _log(db, f"GSTR-2B file '{file.filename}' uploaded", document_id=doc.id)

    try:
        rows = parse_gstr2b_excel(saved_path)
    except ValueError as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"GSTR-2B parsing failed: {e}", document_id=doc.id)
        raise HTTPException(400, str(e))
    except Exception as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"GSTR-2B parsing failed unexpectedly: {e}", document_id=doc.id)
        raise HTTPException(500, f"Could not read this Excel file: {e}")

    _log(db, f"Parsed {len(rows)} credit/debit note row(s) from B2B-CDNR sheet(s)", document_id=doc.id)

    for idx, row in enumerate(rows):
        # No source photo for GSTR-2B rows, so DetectedBill.crop_path is left
        # null (see models.py) — the frontend shows a document icon instead
        # of an image thumbnail for these.
        bill = models.DetectedBill(
            document_id=doc.id,
            crop_path=None,
            bbox=None,
            order_in_page=idx,
        )
        db.add(bill)
        db.commit()
        db.refresh(bill)

        # GST portal's "Note type" (Credit Note / Debit Note) describes the
        # document AS ISSUED BY THE SUPPLIER. We surface it as-is rather than
        # guessing the mirrored voucher direction on the buyer's side (a
        # supplier Credit Note is usually entered as a Debit Note in the
        # buyer's own Purchase books, but the correct Tally treatment can
        # depend on the specific reason/context) — the accountant reviewing
        # this can apply the correct Tally voucher type with full context.
        # We tag the transaction type clearly so it's unambiguous in review.
        tx_type = "CREDIT_NOTE" if "credit" in row.note_type.lower() else "DEBIT_NOTE"

        tx = models.Transaction(
            bill_id=bill.id,
            type=tx_type,
            party=row.supplier_name,
            date=row.note_date,
            invoice_number=row.note_number,
            taxable_value=row.taxable_value,
            gst_rate=row.gst_rate,
            cgst=row.central_tax,
            sgst=row.state_tax,
            igst=row.integrated_tax,
            cess=row.cess,
            total_value=row.note_value,
            confidence=1.0,  # deterministic parse of a structured government file, not an OCR guess
            status="NEEDS_REVIEW",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        note = (
            f"Note {idx + 1} ({row.source_sheet}): {row.note_type} {row.note_number} "
            f"from {row.supplier_name} ({row.supplier_gstin}), value={row.note_value}"
        )
        if row.warnings:
            note += f" | warnings: {', '.join(row.warnings)}"
        _log(db, note, document_id=doc.id, transaction_id=tx.id)

    doc.status = "NEEDS_REVIEW" if rows else "FAILED"
    db.commit()
    db.refresh(doc)
    return doc

@router.post("/purchase-upload", response_model=schemas.DocumentOut)
def upload_gstr2b_purchase(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import Purchase invoices directly from GSTR-2B B2B Excel.

    No purchase photo is accepted or processed. All financial values come
    directly from the structured B2B sheet.
    """
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload the GSTR-2B .xlsx/.xls file containing the B2B purchase invoices.")

    from ..gstr2b.purchase_parser import parse_gstr2b_purchase_excel

    ext = os.path.splitext(file.filename)[1] or ".xlsx"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = models.Document(
        file_name=file.filename,
        file_path=saved_path,
        document_type="PURCHASE",
        status="PROCESSING",
        uploaded_at=datetime.utcnow(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    _log(db, f"Purchase B2B Excel '{file.filename}' uploaded", document_id=doc.id)

    try:
        rows = parse_gstr2b_purchase_excel(saved_path)
    except ValueError as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Purchase B2B parsing failed: {e}", document_id=doc.id)
        raise HTTPException(400, str(e))
    except Exception as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Purchase B2B parsing failed unexpectedly: {e}", document_id=doc.id)
        raise HTTPException(500, f"Could not read this B2B Excel file: {e}")

    for idx, row in enumerate(rows):
        bill = models.DetectedBill(
            document_id=doc.id,
            crop_path=None,
            bbox=None,
            order_in_page=idx,
        )
        db.add(bill)
        db.commit()
        db.refresh(bill)

        tx = models.Transaction(
            bill_id=bill.id,
            type="PURCHASE",
            party=row.supplier_name,
            date=row.invoice_date,
            invoice_number=row.invoice_number,
            taxable_value=row.taxable_value,
            gst_rate=row.gst_rate,
            cgst=row.central_tax,
            sgst=row.state_tax,
            igst=row.integrated_tax,
            cess=row.cess,
            total_value=row.invoice_value,
            confidence=1.0,
            status="NEEDS_REVIEW",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        note = (
            f"Purchase {idx + 1} ({row.source_sheet}): invoice {row.invoice_number} "
            f"from {row.supplier_name} ({row.supplier_gstin}), value={row.invoice_value}"
        )
        if row.warnings:
            note += f" | warnings: {', '.join(row.warnings)}"
        _log(db, note, document_id=doc.id, transaction_id=tx.id)

    doc.status = "NEEDS_REVIEW" if rows else "FAILED"
    db.commit()
    db.refresh(doc)
    return doc
