"""
FastAPI Router for Tally operations, database transaction sync, and configuration.
"""

import json
from datetime import datetime
from typing import Any, Dict
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..tally.config import get_tally_config, save_tally_config
from ..tally.voucher_builder import build_voucher_envelope, MissingVoucherDateError
from ..tally.tally_client import (
    test_connection,
    send_voucher_xml,
    fetch_ledgers,
    TallyConnectionError,
)
from ..settings import get_active_company_id

router = APIRouter(prefix="/tally", tags=["tally"])


def _log(db: Session, message: str, transaction_id: int | None = None):
    try:
        db.add(models.AuditLog(transaction_id=transaction_id, message=message))
        db.commit()
    except Exception:
        db.rollback()


def _effective_tally_config(db: Session, company_id: int | None = None) -> tuple[dict, models.Company | None]:
    """
    Returns the tally config to use for this push, with company_name
    overridden to the target company's own tally_company_name where one is
    set. Also returns that Company row itself so callers can scope which
    transactions are eligible to push.

    `company_id` lets a caller pin this to a SPECIFIC company (e.g. "push
    this one transaction using its own company's Tally name, regardless of
    which company happens to be active in the switcher right now") —
    defaults to the currently active company when omitted, which is what
    the bulk /push endpoint wants.

    Without this override, every push — regardless of which company is
    active in the switcher — used a single global company_name from
    tally_config.json, meaning switching companies never actually changed
    which Tally company received the voucher. That's the whole point of
    multi-company support, so this is the one override that actually makes
    it real rather than cosmetic.

    Falls back to the global company_name when the target company has none
    set (e.g. an older company created before this field existed) — same
    behavior as before for anyone not using multi-company at all.
    """
    config = dict(get_tally_config())
    target_id = company_id if company_id is not None else get_active_company_id()
    company = None
    if target_id:
        company = db.query(models.Company).filter(models.Company.id == target_id).first()
        if company and company.tally_company_name:
            config["company_name"] = company.tally_company_name
    return config, company


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
    payload: schemas.PushToTallyRequest | None = Body(default=None),
    db: Session = Depends(get_db)
):
    config, active_company = _effective_tally_config(db)

    # Legacy direct-voucher path: a raw voucher dict with no transaction_id,
    # used by a couple of older callers to push one ad-hoc voucher without a
    # DB-backed transaction at all. Left untouched.
    if payload and isinstance(payload, dict) and "type" in payload and "transaction_id" not in payload:
        try:
            xml = build_voucher_envelope(payload, config)
            res = send_voucher_xml(xml)
            if res.get("errors") or res.get("created", 0) == 0:
                msg = res.get("error_message") or "Bill already exists in Tally or could not be created."
                return [PushResult(transaction_id=0, status="FAILED", message=msg)]
            return [PushResult(transaction_id=0, status="SENT", message="Voucher created in Tally")]
        except MissingVoucherDateError as e:
            return [PushResult(transaction_id=0, status="FAILED", message=str(e))]
        except Exception as e:
            return [PushResult(transaction_id=0, status="FAILED", message=str(e))]

    push_type = None
    explicit_order = None
    if isinstance(payload, schemas.PushToTallyRequest):
        push_type = payload.type
        explicit_order = payload.order

    base_query = db.query(models.Transaction).filter(
        models.Transaction.status == "APPROVED",
        models.Transaction.tally_status != "SENT",
    )
    # Only push the ACTIVE company's approved transactions. Without this, a
    # single /push call would push every company's pending approvals in one
    # go, and — combined with SVCURRENTCOMPANY now correctly pointing at
    # the active company (see _effective_tally_config) — Company A's
    # sales would get created inside Company B's Tally data if Company B
    # happened to be the one currently open. Falls back to unscoped (old
    # single-company behavior) if no company is set up yet.
    if active_company:
        base_query = base_query.filter(models.Transaction.company_id == active_company.id)

    if explicit_order:
        # The caller (typically a Review & Approve page) already knows the
        # exact order it wants — usually "whatever order the table is
        # currently sorted by" — so push in exactly that sequence rather
        # than re-deriving one. Ids that aren't actually APPROVED/unsent
        # (already pushed, rejected since the page loaded, etc.) are just
        # skipped rather than erroring the whole batch.
        by_id = {tx.id: tx for tx in base_query.filter(models.Transaction.id.in_(explicit_order)).all()}
        pending = [by_id[i] for i in explicit_order if i in by_id]
    else:
        if push_type:
            base_query = base_query.filter(models.Transaction.type == push_type.upper())
        # Default order: grouped by voucher type so Tally receives clean,
        # same-type batches back to back instead of an interleaved mix —
        # then, within each type, in the order the transactions were
        # actually approved (falling back to insertion order for any older
        # rows approved before approved_at existed). "Approved order" is a
        # more meaningful default than raw id/date order when no explicit
        # sort was requested.
        type_priority = {"SALES": 0, "PURCHASE": 1, "DEBIT_NOTE": 2, "CREDIT_NOTE": 3, "BANK": 4}
        pending = base_query.all()
        pending.sort(key=lambda tx: (
            type_priority.get(tx.type, 99),
            tx.approved_at or datetime.min,
            tx.id,
        ))

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
            "rate_breakdown": json.loads(tx.rate_breakdown) if getattr(tx, "rate_breakdown", None) else None,
            "debit": getattr(tx, "debit", 0.0),
            "credit": getattr(tx, "credit", 0.0),
            "narration": getattr(tx, "narration", None),
            "bank_ledger": config.get("bank_ledger"),
        }
        try:
            xml = build_voucher_envelope(tx_dict, config)
        except MissingVoucherDateError as e:
            tx.tally_status = "FAILED"
            tx.tally_error = str(e)
            db.commit()
            _log(db, f"Tally push blocked (missing date): {e}", transaction_id=tx.id)
            results.append(PushResult(transaction_id=tx.id, status="FAILED", message=str(e)))
            continue

        try:
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

    config, _active_company = _effective_tally_config(db, company_id=tx.company_id)
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
        "rate_breakdown": json.loads(tx.rate_breakdown) if getattr(tx, "rate_breakdown", None) else None,
        "debit": getattr(tx, "debit", 0.0),
        "credit": getattr(tx, "credit", 0.0),
        "narration": getattr(tx, "narration", None),
        "bank_ledger": config.get("bank_ledger"),
    }
    try:
        xml = build_voucher_envelope(tx_dict, config)
    except MissingVoucherDateError as e:
        tx.tally_status = "FAILED"
        tx.tally_error = str(e)
        db.commit()
        _log(db, f"Tally push blocked (missing date): {e}", transaction_id=tx.id)
        return PushResult(transaction_id=tx.id, status="FAILED", message=str(e))

    try:
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