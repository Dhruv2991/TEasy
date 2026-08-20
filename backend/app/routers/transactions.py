from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..money import round_rupee

router = APIRouter(prefix="/transactions", tags=["transactions"])


class BulkActionRequest(BaseModel):
    ids: list[int]


def _is_duplicate_invoice(
    db: Session,
    doc_type: str,
    party: str,
    invoice_number: str | None,
    exclude_tx_id: int | None = None,
    taxable_value: float | None = None,
) -> bool:
    """
    True if this looks like the same invoice already recorded.

    A single real invoice can legitimately appear as several rows/lines when
    it has items taxed at more than one GST rate (the GSTR-2B B2B export
    splits a multi-rate bill into one row per rate). Those rows share the
    same party + invoice number but have different taxable values, so
    matching on taxable_value too (in addition to party + invoice number)
    keeps that case from being flagged as a false "possible duplicate" while
    still catching a genuine re-upload of the exact same invoice/line.
    """
    if not invoice_number or not invoice_number.strip():
        return False
    query = db.query(models.Transaction).filter(
        models.Transaction.type == doc_type,
        models.Transaction.party == party,
        models.Transaction.invoice_number == invoice_number,
        models.Transaction.status != "REJECTED",
    )
    if taxable_value is not None:
        # Small tolerance for float/rounding noise, not for genuinely
        # different rate-lines (those differ by far more than a rupee).
        query = query.filter(
            models.Transaction.taxable_value.between(taxable_value - 1.0, taxable_value + 1.0)
        )
    if exclude_tx_id:
        query = query.filter(models.Transaction.id != exclude_tx_id)
    return db.query(query.exists()).scalar()


def _validate_sales_tx(tx: models.Transaction) -> list[str]:
    """Safety gate for handwritten sales: checks required fields, AI confidence, and tax math."""
    missing = []
    if tx.type == "SALES":
        if not tx.date:
            missing.append("date")
        if tx.taxable_value is None:
            missing.append("taxable value")
        if tx.total_value is None:
            missing.append("total value")
        if not tx.invoice_number:
            missing.append("invoice number")
        if (tx.confidence or 0) < 0.80:
            missing.append("AI confidence >= 0.80")
        tax = (tx.cgst or 0) + (tx.sgst or 0) + (tx.igst or 0)
        if (
            tx.taxable_value is not None
            and tx.total_value is not None
            and abs((tx.taxable_value + tax) - tx.total_value) > 1.50
        ):
            missing.append("taxable + GST = total reconciliation")
    return missing


@router.get("", response_model=list[schemas.TransactionOut])
def list_transactions(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Transaction)
    if status:
        q = q.filter(models.Transaction.status == status)
    return q.order_by(models.Transaction.id.desc()).all()


@router.patch("/{transaction_id}", response_model=schemas.TransactionOut)
def update_transaction(
    transaction_id: int,
    payload: schemas.TransactionUpdate,
    db: Session = Depends(get_db),
):
    tx = db.query(models.Transaction).get(transaction_id)
    if tx is None:
        raise HTTPException(404, "Transaction not found")

    for field_name, value in payload.dict(exclude_unset=True).items():
        if field_name == "total_value":
            value = round_rupee(value)
        setattr(tx, field_name, value)

    # Re-check duplicate status
    tx.possible_duplicate = _is_duplicate_invoice(
        db, tx.type, tx.party, tx.invoice_number, exclude_tx_id=tx.id, taxable_value=tx.taxable_value
    )

    db.add(
        models.AuditLog(
            transaction_id=tx.id, message="Transaction edited by user"
        )
    )
    db.commit()
    db.refresh(tx)
    return tx


@router.post("/{transaction_id}/approve", response_model=schemas.TransactionOut)
def approve_transaction(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.query(models.Transaction).get(transaction_id)
    if tx is None:
        raise HTTPException(404, "Transaction not found")

    missing = _validate_sales_tx(tx)
    if missing:
        raise HTTPException(
            400,
            "Sales transaction requires manual correction before approval: "
            + ", ".join(missing),
        )

    tx.status = "APPROVED"
    db.add(
        models.AuditLog(
            transaction_id=tx.id, message="Transaction approved by user"
        )
    )
    db.commit()
    db.refresh(tx)
    return tx


@router.post("/{transaction_id}/reject", response_model=schemas.TransactionOut)
def reject_transaction(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.query(models.Transaction).get(transaction_id)
    if tx is None:
        raise HTTPException(404, "Transaction not found")

    tx.status = "REJECTED"
    db.add(
        models.AuditLog(
            transaction_id=tx.id, message="Transaction rejected by user"
        )
    )
    db.commit()
    db.refresh(tx)
    return tx


# ------------------------------------------------------------------
# BULK OPERATIONS
# ------------------------------------------------------------------


@router.post("/bulk-approve")
def bulk_approve_transactions(
    payload: BulkActionRequest, db: Session = Depends(get_db)
):
    if not payload.ids:
        return {"status": "success", "approved_count": 0, "errors": []}

    txs = (
        db.query(models.Transaction)
        .filter(models.Transaction.id.in_(payload.ids))
        .all()
    )

    approved_ids = []
    errors = []

    for tx in txs:
        missing = _validate_sales_tx(tx)
        if missing:
            errors.append(
                f"Tx #{tx.id} ({tx.invoice_number or 'No Inv'}): Requires correction ({', '.join(missing)})"
            )
            continue

        tx.status = "APPROVED"
        db.add(
            models.AuditLog(
                transaction_id=tx.id, message="Transaction approved via bulk action"
            )
        )
        approved_ids.append(tx.id)

    db.commit()
    return {
        "status": "success",
        "approved_count": len(approved_ids),
        "approved_ids": approved_ids,
        "errors": errors,
    }


@router.post("/bulk-reject")
def bulk_reject_transactions(
    payload: BulkActionRequest, db: Session = Depends(get_db)
):
    if not payload.ids:
        return {"status": "success", "rejected_count": 0}

    txs = (
        db.query(models.Transaction)
        .filter(models.Transaction.id.in_(payload.ids))
        .all()
    )

    rejected_ids = []
    for tx in txs:
        tx.status = "REJECTED"
        db.add(
            models.AuditLog(
                transaction_id=tx.id, message="Transaction rejected via bulk action"
            )
        )
        rejected_ids.append(tx.id)

    db.commit()
    return {
        "status": "success",
        "rejected_count": len(rejected_ids),
        "rejected_ids": rejected_ids,
    }


@router.post("/bulk-delete")
def bulk_delete_transactions(
    payload: BulkActionRequest, db: Session = Depends(get_db)
):
    if not payload.ids:
        return {"status": "success", "deleted_count": 0}

    # Clean up foreign key child records first (audit logs)
    db.query(models.AuditLog).filter(
        models.AuditLog.transaction_id.in_(payload.ids)
    ).delete(synchronize_session=False)

    # Delete transactions
    deleted_count = (
        db.query(models.Transaction)
        .filter(models.Transaction.id.in_(payload.ids))
        .delete(synchronize_session=False)
    )

    db.commit()
    return {"status": "success", "deleted_count": deleted_count}


# ------------------------------------------------------------------
# PARTIES & LEDGERS
#
# This does NOT touch the Tally push flow (routers/tally.py, voucher_builder,
# tally_client) at all. It only helps clean up the free-text `party` field
# on transactions *before* a push is attempted, since Tally requires an
# exact ledger-name match. Renaming here just updates existing rows —
# same mechanism as editing a transaction via PATCH, applied in bulk.
# ------------------------------------------------------------------


class RenamePartyRequest(BaseModel):
    old_name: str
    new_name: str
    doc_type: str | None = None  # optional: only rename within SALES/PURCHASE/GSTR2B


@router.get("/parties")
def list_parties(db: Session = Depends(get_db)):
    """
    Distinct party names currently used across transactions, with counts —
    read-only, used to build a party cleanup / ledger-matching view.
    """
    rows = (
        db.query(models.Transaction.party, models.Transaction.type, models.Transaction.tally_status)
        .filter(models.Transaction.status != "REJECTED")
        .all()
    )
    buckets: dict[str, dict] = {}
    for party, tx_type, tally_status in rows:
        name = party or "Cash"
        b = buckets.setdefault(name, {"party": name, "count": 0, "sent_to_tally": 0, "types": set()})
        b["count"] += 1
        b["types"].add(tx_type)
        if tally_status == "SENT":
            b["sent_to_tally"] += 1
    result = []
    for b in buckets.values():
        result.append({
            "party": b["party"],
            "count": b["count"],
            "sent_to_tally": b["sent_to_tally"],
            "types": sorted(b["types"]),
        })
    result.sort(key=lambda r: r["count"], reverse=True)
    return result


@router.post("/rename-party")
def rename_party(payload: RenamePartyRequest, db: Session = Depends(get_db)):
    """
    Renames every transaction currently using `old_name` to `new_name`
    (e.g. matching a fuzzy-matched name to the exact Tally ledger name).
    Transactions already SENT to Tally are left untouched — that voucher
    was already posted under the old name, renaming it here wouldn't
    change what's in Tally and could be confusing in the audit trail.
    """
    if not payload.old_name.strip() or not payload.new_name.strip():
        raise HTTPException(400, "Both old_name and new_name are required")

    q = db.query(models.Transaction).filter(
        models.Transaction.party == payload.old_name,
        models.Transaction.tally_status != "SENT",
    )
    if payload.doc_type:
        q = q.filter(models.Transaction.type == payload.doc_type.upper())

    txs = q.all()
    updated_ids = []
    for tx in txs:
        tx.party = payload.new_name.strip()
        tx.possible_duplicate = _is_duplicate_invoice(
            db, tx.type, tx.party, tx.invoice_number, exclude_tx_id=tx.id, taxable_value=tx.taxable_value
        )
        db.add(models.AuditLog(
            transaction_id=tx.id,
            message=f"Party renamed from '{payload.old_name}' to '{payload.new_name}' via Parties & Ledgers",
        ))
        updated_ids.append(tx.id)

    db.commit()
    return {"status": "success", "updated_count": len(updated_ids), "updated_ids": updated_ids}