from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _is_duplicate_invoice(db: Session, doc_type: str, party: str, invoice_number: str | None, exclude_tx_id: int | None = None) -> bool:
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
    if exclude_tx_id:
        query = query.filter(models.Transaction.id != exclude_tx_id)
    return db.query(query.exists()).scalar()


@router.get("", response_model=list[schemas.TransactionOut])
def list_transactions(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Transaction)
    if status:
        q = q.filter(models.Transaction.status == status)
    return q.order_by(models.Transaction.id.desc()).all()


@router.patch("/{transaction_id}", response_model=schemas.TransactionOut)
def update_transaction(transaction_id: int, payload: schemas.TransactionUpdate, db: Session = Depends(get_db)):
    tx = db.query(models.Transaction).get(transaction_id)
    if tx is None:
        raise HTTPException(404, "Transaction not found")

    for field_name, value in payload.dict(exclude_unset=True).items():
        setattr(tx, field_name, value)

    # Re-check duplicate status — a manual correction may resolve a false
    # duplicate flag, or a typo fix may reveal a real one.
    tx.possible_duplicate = _is_duplicate_invoice(db, tx.type, tx.party, tx.invoice_number, exclude_tx_id=tx.id)

    db.add(models.AuditLog(transaction_id=tx.id, message="Transaction edited by user"))
    db.commit()
    db.refresh(tx)
    return tx


@router.post("/{transaction_id}/approve", response_model=schemas.TransactionOut)
def approve_transaction(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.query(models.Transaction).get(transaction_id)
    if tx is None:
        raise HTTPException(404, "Transaction not found")

    # Safety gate for handwritten sales: never allow a low-confidence or
    # mathematically inconsistent AI result into Tally. The user can edit the
    # transaction first and then approve it.
    if tx.type == "SALES":
        missing = []
        if not tx.date: missing.append("date")
        if tx.taxable_value is None: missing.append("taxable value")
        if tx.total_value is None: missing.append("total value")
        if not tx.invoice_number: missing.append("invoice number")
        if tx.confidence < 0.80: missing.append("AI confidence >= 0.80")
        tax = (tx.cgst or 0) + (tx.sgst or 0) + (tx.igst or 0)
        if tx.taxable_value is not None and tx.total_value is not None and abs((tx.taxable_value + tax) - tx.total_value) > 1.50:
            missing.append("taxable + GST = total reconciliation")
        if missing:
            raise HTTPException(400, "Sales transaction requires manual correction before approval: " + ", ".join(missing))

    tx.status = "APPROVED"
    db.add(models.AuditLog(transaction_id=tx.id, message="Transaction approved by user"))
    db.commit()
    db.refresh(tx)
    return tx


@router.post("/{transaction_id}/reject", response_model=schemas.TransactionOut)
def reject_transaction(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.query(models.Transaction).get(transaction_id)
    if tx is None:
        raise HTTPException(404, "Transaction not found")
    tx.status = "REJECTED"
    db.add(models.AuditLog(transaction_id=tx.id, message="Transaction rejected by user"))
    db.commit()
    db.refresh(tx)
    return tx
