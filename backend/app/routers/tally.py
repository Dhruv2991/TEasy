from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..tally.config import get_tally_config, save_tally_config
from ..tally.voucher_builder import build_voucher_envelope
from ..tally.tally_client import test_connection, send_voucher_xml, TallyConnectionError

router = APIRouter(prefix="/tally", tags=["tally"])


def _log(db: Session, message: str, transaction_id: int | None = None):
    db.add(models.AuditLog(transaction_id=transaction_id, message=message))
    db.commit()


@router.get("/config")
def get_config():
    return get_tally_config()


@router.put("/config")
def update_config(config: dict):
    return save_tally_config(config)


@router.get("/status")
def tally_status():
    connected = test_connection()
    return {"connected": connected}


class PushResult(BaseModel):
    transaction_id: int
    status: str  # SENT | FAILED
    message: str


@router.post("/push", response_model=list[PushResult])
def push_approved_transactions(db: Session = Depends(get_db)):
    """
    Sends every APPROVED transaction that hasn't already been sent (or
    previously failed) to Tally, one voucher at a time. Each transaction's
    tally_status is updated based on Tally's actual response, so re-running
    this is always safe — already-SENT transactions are skipped, and only
    NOT_SENT / FAILED ones are retried.
    """
    config = get_tally_config()
    pending = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.status == "APPROVED",
            models.Transaction.tally_status != "SENT",
        )
        .all()
    )

    if not pending:
        return []

    results = []
    for tx in pending:
        tx_dict = {
            "type": tx.type,
            "party": tx.party,
            "date": tx.date,
            "invoice_number": tx.invoice_number,
            "taxable_value": tx.taxable_value,
            "cgst": tx.cgst,
            "sgst": tx.sgst,
            "igst": tx.igst,
            "total_value": tx.total_value,
        }
        xml = build_voucher_envelope(tx_dict, config)

        try:
            result = send_voucher_xml(xml)
        except TallyConnectionError as e:
            tx.tally_status = "FAILED"
            tx.tally_error = str(e)
            db.commit()
            _log(db, f"Tally push failed (connection): {e}", transaction_id=tx.id)
            results.append(PushResult(transaction_id=tx.id, status="FAILED", message=str(e)))
            # If Tally isn't reachable at all, stop trying the rest — they'll
            # all fail the same way, no point burning through every row.
            break

        if result["errors"] or result["created"] == 0:
            tx.tally_status = "FAILED"
            tx.tally_error = result["error_message"] or "Tally did not create the voucher (unknown reason)."
            db.commit()
            _log(db, f"Tally push failed: {tx.tally_error}", transaction_id=tx.id)
            results.append(PushResult(transaction_id=tx.id, status="FAILED", message=tx.tally_error))
        else:
            tx.tally_status = "SENT"
            tx.tally_error = None
            db.commit()
            _log(db, f"Sent to Tally successfully ({tx.type} voucher, party={tx.party})", transaction_id=tx.id)
            results.append(PushResult(transaction_id=tx.id, status="SENT", message="Voucher created in Tally"))

    return results


@router.post("/push/{transaction_id}", response_model=PushResult)
def push_single_transaction(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.query(models.Transaction).get(transaction_id)
    if tx is None:
        raise HTTPException(404, "Transaction not found")
    if tx.status != "APPROVED":
        raise HTTPException(400, "Only APPROVED transactions can be pushed to Tally")

    config = get_tally_config()
    tx_dict = {
        "type": tx.type, "party": tx.party, "date": tx.date,
        "invoice_number": tx.invoice_number, "taxable_value": tx.taxable_value,
        "cgst": tx.cgst, "sgst": tx.sgst, "igst": tx.igst, "total_value": tx.total_value,
    }
    xml = build_voucher_envelope(tx_dict, config)

    try:
        result = send_voucher_xml(xml)
    except TallyConnectionError as e:
        tx.tally_status = "FAILED"
        tx.tally_error = str(e)
        db.commit()
        _log(db, f"Tally push failed (connection): {e}", transaction_id=tx.id)
        return PushResult(transaction_id=tx.id, status="FAILED", message=str(e))

    if result["errors"] or result["created"] == 0:
        tx.tally_status = "FAILED"
        tx.tally_error = result["error_message"] or "Tally did not create the voucher (unknown reason)."
        db.commit()
        _log(db, f"Tally push failed: {tx.tally_error}", transaction_id=tx.id)
        return PushResult(transaction_id=tx.id, status="FAILED", message=tx.tally_error)

    tx.tally_status = "SENT"
    tx.tally_error = None
    db.commit()
    _log(db, f"Sent to Tally successfully ({tx.type} voucher, party={tx.party})", transaction_id=tx.id)
    return PushResult(transaction_id=tx.id, status="SENT", message="Voucher created in Tally")
