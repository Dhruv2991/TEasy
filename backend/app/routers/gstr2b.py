import os
import shutil
import uuid
import json
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..gst_states import gstin_to_state
from ..paths import get_data_dir
from ..gstr2b.parser import parse_gstr2b_excel
from .transactions import _is_duplicate_invoice
from ..settings import get_active_company_id

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
        company_id=get_active_company_id(),
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
            company_id=doc.company_id,
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
            party_gstin=row.supplier_gstin,
            party_state=gstin_to_state(row.supplier_gstin),
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

@router.post("/purchase-register-match", response_model=schemas.PurchaseRegisterMatchResult)
def match_purchase_register(db: Session = Depends(get_db), file: UploadFile = File(...)):
    """Upload the shop's own periodic purchase register (its billing/POS
    software's export — one row per invoice with a per-GST-rate breakup,
    e.g. Value@5%/CGST@2.5%/SGST@2.5%/IGST@5%, Value@12%/..., ...) and
    reconcile it in bulk against every Purchase transaction already on file
    from GSTR-2B.

    This replaces uploading a single supplier bill per transaction: one
    register file typically covers the whole period's purchases, so every
    mixed-rate ("gst_rate_uncertain") invoice GSTR-2B couldn't cleanly
    label gets resolved in one pass, matched by invoice number and then
    verified on totals (see gstr2b/supplier_match.py) before anything is
    written.
    """
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload the shop's purchase register as an .xlsx/.xls file.")

    from ..gstr2b.purchase_register_parser import parse_purchase_register
    from ..gstr2b.supplier_match import reconcile_with_transaction

    ext = os.path.splitext(file.filename)[1] or ".xlsx"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        register_rows = parse_purchase_register(saved_path)
    except ValueError as e:
        _log(db, f"Purchase register '{file.filename}' could not be read: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        _log(db, f"Purchase register '{file.filename}' failed unexpectedly: {e}")
        raise HTTPException(500, f"Could not read this Excel file: {e}")

    # Index register rows by invoice number. A given invoice number is
    # expected to be unique within one shop's purchase records; if the file
    # somehow repeats one (e.g. re-exported/merged periods), keep the first
    # occurrence and note the rest are ignored rather than silently
    # overwriting a match.
    by_invoice = {}
    duplicate_invoice_numbers = set()
    for row in register_rows:
        key = row.invoice_number.strip().lower()
        if key in by_invoice:
            duplicate_invoice_numbers.add(key)
            continue
        by_invoice[key] = row

    purchase_txs = (
        db.query(models.Transaction)
        .filter(models.Transaction.type == "PURCHASE")
        .all()
    )

    uncertain_before = sum(1 for tx in purchase_txs if tx.gst_rate_uncertain and not tx.rate_breakdown)
    result_rows = []
    resolved_count = 0
    matched_invoice_keys = set()

    import json

    for tx in purchase_txs:
        if not tx.invoice_number:
            continue
        key = tx.invoice_number.strip().lower()
        register_row = by_invoice.get(key)
        if register_row is None:
            continue  # this purchase isn't in the shop's register file at all — leave untouched
        matched_invoice_keys.add(key)

        # Already resolved (e.g. from an earlier register upload) — don't
        # redo the work, but still count it as matched for the summary.
        if tx.rate_breakdown:
            result_rows.append(schemas.RegisterMatchRow(
                transaction_id=tx.id, invoice_number=tx.invoice_number, party=tx.party,
                matched=True, resolved=False, reason="Already resolved from an earlier match",
            ))
            continue

        tx_dict = {
            "invoice_number": tx.invoice_number,
            "taxable_value": tx.taxable_value,
            "cgst": tx.cgst,
            "sgst": tx.sgst,
            "igst": tx.igst,
            "total_value": tx.total_value,
        }
        match = reconcile_with_transaction(register_row, tx_dict)

        if not match.matched:
            result_rows.append(schemas.RegisterMatchRow(
                transaction_id=tx.id, invoice_number=tx.invoice_number, party=tx.party,
                matched=False, resolved=False, reason=match.reason,
            ))
            _log(db, f"Purchase register row for invoice '{tx.invoice_number}' did not reconcile: {match.reason}",
                 transaction_id=tx.id)
            continue

        tx.rate_breakdown = json.dumps(match.rate_breakdown)
        tx.rate_breakdown_source = file.filename
        was_uncertain = tx.gst_rate_uncertain
        tx.gst_rate_uncertain = False
        if not tx.party_gstin and register_row.supplier_gstin:
            # Backfill for older purchases imported before party_gstin
            # existed on the model, or from a GSTR-2B row whose GSTIN cell
            # was blank — the register file sometimes has it even then.
            tx.party_gstin = register_row.supplier_gstin
            tx.party_state = gstin_to_state(register_row.supplier_gstin)
        db.commit()
        db.refresh(tx)
        resolved_count += 1
        rate_summary = ", ".join(f"{b['rate']}% on ₹{b['taxable_value']}" for b in match.rate_breakdown)
        result_rows.append(schemas.RegisterMatchRow(
            transaction_id=tx.id, invoice_number=tx.invoice_number, party=tx.party,
            matched=True, resolved=True,
            reason=f"Resolved: {rate_summary}" if was_uncertain else f"Confirmed single-rate split: {rate_summary}",
        ))
        _log(db, f"Purchase register '{file.filename}' resolved invoice '{tx.invoice_number}' "
                 f"({len(match.rate_breakdown)} rate(s)): {rate_summary}", transaction_id=tx.id)

    still_uncertain = sum(1 for tx in purchase_txs if tx.gst_rate_uncertain and not tx.rate_breakdown)
    unmatched_keys = set(by_invoice.keys()) - matched_invoice_keys
    unmatched_register_rows = len(unmatched_keys)
    unmatched_detail = [
        schemas.UnmatchedRegisterRow(
            invoice_number=by_invoice[key].invoice_number,
            supplier_name=by_invoice[key].supplier_name,
            supplier_gstin=by_invoice[key].supplier_gstin,
            invoice_date=by_invoice[key].invoice_date,
            total_value=by_invoice[key].total_value,
        )
        for key in unmatched_keys
    ]

    _log(db, f"Purchase register '{file.filename}': {resolved_count} invoice(s) resolved, "
             f"{still_uncertain} still uncertain, {unmatched_register_rows} register row(s) had no matching "
             f"GSTR-2B purchase on file — these may be invoices your supplier(s) haven't filed on GSTN yet, "
             f"which puts ITC on them at risk until they do"
             + (f" | {len(duplicate_invoice_numbers)} duplicate invoice number(s) in the register file were skipped" if duplicate_invoice_numbers else ""))

    return schemas.PurchaseRegisterMatchResult(
        total_purchase_transactions=len(purchase_txs),
        uncertain_before=uncertain_before,
        resolved=resolved_count,
        still_uncertain=still_uncertain,
        unmatched_register_rows=unmatched_register_rows,
        unmatched_register_rows_detail=unmatched_detail,
        rows=result_rows,
    )


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
        company_id=get_active_company_id(),
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
            company_id=doc.company_id,
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
            total_value=row.invoice_value,
            confidence=1.0,
            status="NEEDS_REVIEW",
            party_gstin=row.supplier_gstin,
            party_state=gstin_to_state(row.supplier_gstin),
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

    Uses the itemized bill parser to extract individual line items, amounts,
    and tax rates for accurate Tally voucher creation.
    """
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload an .xlsx/.xls sales register file.")

    # 1. Point to your fixed parser with itemized support
    from ..extraction.bill_excel_parser import parse_bill_excel # adjust import path if located elsewhere

    ext = os.path.splitext(file.filename)[1] or ".xlsx"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = models.Document(
        company_id=get_active_company_id(),
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
        # 2. Parse file using the item-wise bill excel parser
        rows = parse_bill_excel(saved_path)
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

        # Handle both dict and object structures returned by parser
        get_val = lambda k, default=None: row.get(k, default) if isinstance(row, dict) else getattr(row, k, default)

        items_data = get_val("items", [])
        rate_breakdown_data = get_val("rate_breakdown", [])

        tx = models.Transaction(
            company_id=doc.company_id,
            bill_id=bill.id,
            type="SALES",
            party=get_val("party") or get_val("supplier_name"),
            date=get_val("invoice_date") or get_val("date"),
            invoice_number=get_val("invoice_number"),
            taxable_value=get_val("taxable_value", 0.0),
            gst_rate=get_val("gst_rate", 0.0),
            cgst=get_val("cgst", 0.0),
            sgst=get_val("sgst", 0.0),
            igst=get_val("igst", 0.0),
            cess=get_val("cess", 0.0),
            total_value=get_val("total_value", 0.0),
            confidence=1.0,
            status="NEEDS_REVIEW",
            # 3. Store itemized array and rate breakdown for Tally voucher generation
            items=json.dumps(items_data) if items_data and hasattr(models.Transaction, "items") else None,
            rate_breakdown=json.dumps(rate_breakdown_data) if rate_breakdown_data and hasattr(models.Transaction, "rate_breakdown") else None,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        party_name = get_val("party") or get_val("supplier_name")
        inv_num = get_val("invoice_number")
        taxable_val = get_val("taxable_value", 0.0)

        batch_key = ("SALES", party_name, inv_num, round(taxable_val, 0))
        in_batch_dupe = inv_num and batch_key in seen_in_batch
        in_db_dupe = _is_duplicate_invoice(db, "SALES", party_name, inv_num, exclude_tx_id=tx.id, taxable_value=taxable_val)
        tx.possible_duplicate = bool(in_batch_dupe or in_db_dupe)
        if inv_num:
            seen_in_batch.add(batch_key)
        db.commit()
        db.refresh(tx)

        warnings = get_val("warnings", [])
        note = f"Sales {idx + 1}: invoice {inv_num} for {party_name}, total={get_val('total_value')}, items={len(items_data)}"
        if warnings:
            note += f" | warnings: {', '.join(warnings)}"
        if tx.possible_duplicate:
            note += " | ⚠ possible duplicate — same party + invoice number already seen"
        _log(db, note, document_id=doc.id, transaction_id=tx.id)

    doc.status = "NEEDS_REVIEW" if rows else "FAILED"
    db.commit()
    db.refresh(doc)
    return doc
