from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas

router = APIRouter(prefix="/transactions", tags=["transactions"])


class BulkActionRequest(BaseModel):
    ids: list[int]


def _is_duplicate_invoice(
    db: Session,
    doc_type: str,
    party: str,
    invoice_number: str | None,
    exclude_tx_id: int | None = None,
) -> bool:
    if not invoice_number or not invoice_number.strip():
        return False
    query = db.query(models.Transaction).filter(
        models.Transaction.type == doc_type,
        models.Transaction.party == party,
        models.Transaction.invoice_number == invoice_number,
        models.Transaction.status != "REJECTED",
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
        setattr(tx, field_name, value)

    # Re-check duplicate status
    tx.possible_duplicate = _is_duplicate_invoice(
        db, tx.type, tx.party, tx.invoice_number, exclude_tx_id=tx.id
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