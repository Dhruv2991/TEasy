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
from ..money import round_rupee
from .transactions import _is_duplicate_invoice

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

    seen_in_batch = set()

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
            total_value=round_rupee(row.note_value),
            confidence=1.0,  # deterministic parse of a structured government file, not an OCR guess
            status="NEEDS_REVIEW",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        # A duplicate can come from two places: the same invoice already
        # sitting in the DB (e.g. imported before, or added via photo/manual
        # entry), or the same invoice appearing twice within this very Excel
        # file (GST portal exports occasionally repeat rows across sheets).
        # taxable_value is included in the key/check too: a single note can
        # legitimately span more than one GST rate and appear as more than
        # one row sharing the same note number — those are different lines,
        # not duplicates.
        batch_key = (tx_type, row.supplier_name, row.note_number, round(row.taxable_value, 0))
        in_batch_dupe = row.note_number and batch_key in seen_in_batch
        in_db_dupe = _is_duplicate_invoice(db, tx_type, row.supplier_name, row.note_number, exclude_tx_id=tx.id, taxable_value=row.taxable_value)
        tx.possible_duplicate = bool(in_batch_dupe or in_db_dupe)
        if row.note_number:
            seen_in_batch.add(batch_key)
        db.commit()
        db.refresh(tx)

        note = (
            f"Note {idx + 1} ({row.source_sheet}): {row.note_type} {row.note_number} "
            f"from {row.supplier_name} ({row.supplier_gstin}), value={row.note_value}"
        )
        if row.warnings:
            note += f" | warnings: {', '.join(row.warnings)}"
        if tx.possible_duplicate:
            note += " | ⚠ possible duplicate — same party + invoice number already seen"
        _log(db, note, document_id=doc.id, transaction_id=tx.id)

    doc.status = "NEEDS_REVIEW" if rows else "FAILED"
    db.commit()
    db.refresh(doc)
    return doc

@router.post("/supplier-invoice-match/{transaction_id}", response_model=schemas.SupplierInvoiceMatchResult)
def match_supplier_invoice(transaction_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a supplier's own invoice Excel (format varies by supplier — this
    is NOT the GSTR-2B file) and, if its totals reconcile with the given
    purchase transaction, resolve that transaction's real per-rate GST split.

    GSTR-2B stays the source of truth for whether/how much this purchase
    counts for ITC. The supplier file is only trusted for the rate-split
    detail, and only after its taxable value and tax totals are checked
    against what's already on file for this transaction. If they don't
    reconcile, nothing is changed and the mismatch reason is returned so it
    can be looked into manually.
    """
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload the supplier's invoice as an .xlsx/.xls file.")

    tx = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(404, "Transaction not found.")
    if tx.type != "PURCHASE":
        raise HTTPException(400, "Rate-breakdown matching only applies to Purchase transactions.")

    from ..gstr2b.supplier_invoice_parser import parse_supplier_invoice_excel
    from ..gstr2b.supplier_match import reconcile_with_transaction

    ext = os.path.splitext(file.filename)[1] or ".xlsx"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        supplier_invoice = parse_supplier_invoice_excel(saved_path)
    except ValueError as e:
        _log(db, f"Supplier invoice '{file.filename}' could not be read: {e}", transaction_id=tx.id)
        raise HTTPException(400, str(e))
    except Exception as e:
        _log(db, f"Supplier invoice '{file.filename}' failed unexpectedly: {e}", transaction_id=tx.id)
        raise HTTPException(500, f"Could not read this Excel file: {e}")

    tx_dict = {
        "invoice_number": tx.invoice_number,
        "taxable_value": tx.taxable_value,
        "cgst": tx.cgst,
        "sgst": tx.sgst,
        "igst": tx.igst,
        "total_value": tx.total_value,
    }
    result = reconcile_with_transaction(supplier_invoice, tx_dict)

    if not result.matched:
        _log(db, f"Supplier invoice '{file.filename}' did not reconcile: {result.reason}", transaction_id=tx.id)
        return schemas.SupplierInvoiceMatchResult(matched=False, reason=result.reason, transaction=None)

    import json
    tx.rate_breakdown = json.dumps(result.rate_breakdown)
    tx.rate_breakdown_source = file.filename
    tx.gst_rate_uncertain = False  # the mixed rate is now resolved into a real per-line split
    db.commit()
    db.refresh(tx)
    _log(db, f"Supplier invoice '{file.filename}' matched: {result.reason}. Rate breakdown resolved "
             f"({len(result.rate_breakdown)} rate(s)).", transaction_id=tx.id)

    return schemas.SupplierInvoiceMatchResult(matched=True, reason=result.reason, transaction=tx)


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

    seen_in_batch = set()

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
            gst_rate_uncertain=row.gst_rate_uncertain,
            cgst=row.central_tax,
            sgst=row.state_tax,
            igst=row.integrated_tax,
            cess=row.cess,
            total_value=round_rupee(row.invoice_value),
            confidence=1.0,
            status="NEEDS_REVIEW",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        # taxable_value is part of the key/check too: a purchase invoice with
        # items at more than one GST rate is exported by the GST portal as
        # multiple rows sharing the same invoice number (one row per rate) —
        # those are legitimate separate lines of one bill, not duplicates.
        batch_key = ("PURCHASE", row.supplier_name, row.invoice_number, round(row.taxable_value, 0))
        in_batch_dupe = row.invoice_number and batch_key in seen_in_batch
        in_db_dupe = _is_duplicate_invoice(db, "PURCHASE", row.supplier_name, row.invoice_number, exclude_tx_id=tx.id, taxable_value=row.taxable_value)
        tx.possible_duplicate = bool(in_batch_dupe or in_db_dupe)
        if row.invoice_number:
            seen_in_batch.add(batch_key)
        db.commit()
        db.refresh(tx)

        note = (
            f"Purchase {idx + 1} ({row.source_sheet}): invoice {row.invoice_number} "
            f"from {row.supplier_name} ({row.supplier_gstin}), value={row.invoice_value}"
        )
        if row.warnings:
            note += f" | warnings: {', '.join(row.warnings)}"
        if tx.possible_duplicate:
            note += " | ⚠ possible duplicate — same party + invoice number already seen"
        _log(db, note, document_id=doc.id, transaction_id=tx.id)

    doc.status = "NEEDS_REVIEW" if rows else "FAILED"
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/sales-upload", response_model=schemas.DocumentOut)
def upload_sales_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import Sales invoices from the business's own sales register Excel.

    Unlike purchases, there's no government file for outward supplies before
    filing — this reads a plain sales sheet (Party, Invoice No, Date,
    Taxable Value, GST, Total). Existing photo-based sales entry is
    untouched; this is an additional bulk-import path.
    """
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload an .xlsx/.xls sales register file.")

    from ..gstr2b.sales_parser import parse_sales_excel

    ext = os.path.splitext(file.filename)[1] or ".xlsx"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = models.Document(
        file_name=file.filename,
        file_path=saved_path,
        document_type="SALES",
        status="PROCESSING",
        uploaded_at=datetime.utcnow(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    _log(db, f"Sales register Excel '{file.filename}' uploaded", document_id=doc.id)

    try:
        rows = parse_sales_excel(saved_path)
    except ValueError as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Sales Excel parsing failed: {e}", document_id=doc.id)
        raise HTTPException(400, str(e))
    except Exception as e:
        doc.status = "FAILED"
        db.commit()
        _log(db, f"Sales Excel parsing failed unexpectedly: {e}", document_id=doc.id)
        raise HTTPException(500, f"Could not read this Excel file: {e}")

    seen_in_batch = set()

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
            type="SALES",
            party=row.party,
            date=row.invoice_date,
            invoice_number=row.invoice_number,
            taxable_value=row.taxable_value,
            gst_rate=row.gst_rate,
            cgst=row.cgst,
            sgst=row.sgst,
            igst=row.igst,
            cess=row.cess,
            total_value=round_rupee(row.total_value),
            confidence=1.0,
            status="NEEDS_REVIEW",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        batch_key = ("SALES", row.party, row.invoice_number, round(row.taxable_value, 0))
        in_batch_dupe = row.invoice_number and batch_key in seen_in_batch
        in_db_dupe = _is_duplicate_invoice(db, "SALES", row.party, row.invoice_number, exclude_tx_id=tx.id, taxable_value=row.taxable_value)
        tx.possible_duplicate = bool(in_batch_dupe or in_db_dupe)
        if row.invoice_number:
            seen_in_batch.add(batch_key)
        db.commit()
        db.refresh(tx)

        note = f"Sales {idx + 1} ({row.source_sheet}): invoice {row.invoice_number} for {row.party}, total={row.total_value}"
        if row.warnings:
            note += f" | warnings: {', '.join(row.warnings)}"
        if tx.possible_duplicate:
            note += " | ⚠ possible duplicate — same party + invoice number already seen"
        _log(db, note, document_id=doc.id, transaction_id=tx.id)

    doc.status = "NEEDS_REVIEW" if rows else "FAILED"
    db.commit()
    db.refresh(doc)
    return doc
