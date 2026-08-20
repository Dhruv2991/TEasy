"""
FastAPI Router for Tally operations, database transaction sync, and configuration.
"""

from typing import Any, Dict
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..tally.config import get_tally_config, save_tally_config
from ..tally.voucher_builder import build_voucher_envelope
from ..tally.tally_client import (
    test_connection,
    send_voucher_xml,
    fetch_ledgers,
    TallyConnectionError,
)

router = APIRouter(prefix="/tally", tags=["tally"])


def _log(db: Session, message: str, transaction_id: int | None = None):
    try:
        db.add(models.AuditLog(transaction_id=transaction_id, message=message))
        db.commit()
    except Exception:
        db.rollback()


class PushResult(BaseModel):
    transaction_id: int
    status: str  # SENT | FAILED
    message: str


@router.get("/status")
def tally_status():
    connected = test_connection()
    return {"connected": connected}


@router.get("/config")
def get_config():
    return get_tally_config()


@router.put("/config")
@router.post("/config")
def update_config(config: dict):
    return save_tally_config(config)


@router.get("/ledgers")
def get_ledgers(force_refresh: bool = False):
    try:
        return fetch_ledgers(force_refresh=force_refresh)
    except TallyConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/push", response_model=list[PushResult])
def push_approved_transactions(
    payload: Any = Body(default=None),
    db: Session = Depends(get_db)
):
    config = get_tally_config()

    if payload and isinstance(payload, dict) and "type" in payload and "transaction_id" not in payload:
        try:
            xml = build_voucher_envelope(payload, config)
            res = send_voucher_xml(xml)
            if res.get("errors") or res.get("created", 0) == 0:
                msg = res.get("error_message") or "Bill already exists in Tally or could not be created."
                return [PushResult(transaction_id=0, status="FAILED", message=msg)]
            return [PushResult(transaction_id=0, status="SENT", message="Voucher created in Tally")]
        except Exception as e:
            return [PushResult(transaction_id=0, status="FAILED", message=str(e))]

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
            "gst_rate": getattr(tx, "gst_rate", 0.0),
        }
        try:
            xml = build_voucher_envelope(tx_dict, config)
            result = send_voucher_xml(xml)
        except TallyConnectionError as e:
            tx.tally_status = "FAILED"
            tx.tally_error = str(e)
            db.commit()
            _log(db, f"Tally push failed (connection): {e}", transaction_id=tx.id)
            results.append(PushResult(transaction_id=tx.id, status="FAILED", message=str(e)))
            break

        created = result.get("created", 0)
        altered = result.get("altered", 0)
        errors = result.get("errors", 0)

        if errors > 0 or created == 0:
            tx.tally_status = "FAILED"
            if altered > 0:
                err_msg = f"Bill already exists in Tally (Invoice #{tx.invoice_number} already recorded)."
            else:
                err_msg = result.get("error_message") or "Bill already exists in Tally or could not be created."
            tx.tally_error = err_msg
            db.commit()
            _log(db, f"Tally push blocked: {err_msg}", transaction_id=tx.id)
            results.append(PushResult(transaction_id=tx.id, status="FAILED", message=err_msg))
        else:
            tx.tally_status = "SENT"
            tx.tally_error = None
            db.commit()
            _log(db, f"Sent to Tally successfully ({tx.type} voucher, party={tx.party})", transaction_id=tx.id)
            results.append(PushResult(transaction_id=tx.id, status="SENT", message="Voucher created in Tally"))

    return results


@router.post("/push-voucher")
def push_direct_voucher(payload: Dict[str, Any] = Body(...)):
    config = get_tally_config()
    try:
        xml_data = build_voucher_envelope(payload, config)
        result = send_voucher_xml(xml_data)
        if result.get("errors") or result.get("created", 0) == 0:
            msg = result.get("error_message") or "Bill already exists in Tally."
            raise HTTPException(status_code=400, detail=msg)
        return result
    except TallyConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/push/{transaction_id}", response_model=PushResult)
def push_single_transaction(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if tx is None:
        raise HTTPException(404, "Transaction not found")
    if tx.status != "APPROVED":
        raise HTTPException(400, "Only APPROVED transactions can be pushed to Tally")

    config = get_tally_config()
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
        "gst_rate": getattr(tx, "gst_rate", 0.0),
    }
    try:
        xml = build_voucher_envelope(tx_dict, config)
        result = send_voucher_xml(xml)
    except TallyConnectionError as e:
        tx.tally_status = "FAILED"
        tx.tally_error = str(e)
        db.commit()
        _log(db, f"Tally push failed (connection): {e}", transaction_id=tx.id)
        return PushResult(transaction_id=tx.id, status="FAILED", message=str(e))

    created = result.get("created", 0)
    altered = result.get("altered", 0)
    errors = result.get("errors", 0)

    if errors > 0 or created == 0:
        tx.tally_status = "FAILED"
        if altered > 0:
            err_msg = f"Bill already exists in Tally (Invoice #{tx.invoice_number} already recorded)."
        else:
            err_msg = result.get("error_message") or "Bill already exists in Tally or could not be created."
        tx.tally_error = err_msg
        db.commit()
        _log(db, f"Tally push blocked: {err_msg}", transaction_id=tx.id)
        return PushResult(transaction_id=tx.id, status="FAILED", message=err_msg)

    tx.tally_status = "SENT"
    tx.tally_error = None
    db.commit()
    _log(db, f"Sent to Tally successfully ({tx.type} voucher, party={tx.party})", transaction_id=tx.id)
    return PushResult(transaction_id=tx.id, status="SENT", message="Voucher created in Tally")