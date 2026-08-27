import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..reconciliation import reconcile_bank_transactions, get_match_candidates
from ..settings import get_active_company_id

router = APIRouter(prefix="/transactions", tags=["transactions"])


class BulkActionRequest(BaseModel):
    ids: list[int]


def _maybe_reconcile(db: Session) -> None:
    """Re-runs bank<->invoice reconciliation after a SALES/PURCHASE approval
    — a newly-approved invoice is a new candidate that might resolve a bank
    row that was previously UNMATCHED (or make an AMBIGUOUS one resolvable,
    though that case still needs a human pick either way). Best-effort:
    reconciliation is a read-only cross-check, never something that should
    turn a successful approval into a failed one."""
    try:
        reconcile_bank_transactions(db)
    except Exception:
        pass


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


def _is_duplicate_bank_row(
    db: Session,
    date: str | None,
    debit: float,
    credit: float,
    balance: float,
    company_id: int | None = None,
) -> bool:
    """
    True if a BANK row with this exact date + debit + credit + balance
    combination already exists for the same company (and isn't rejected).

    Bank rows have no invoice number to key off, so the fingerprint here is
    the running balance instead — it's the one column a bank statement
    can't repeat by coincidence the way an amount or date alone easily
    could (e.g. two unrelated ₹500 UPI payments on the same day are
    completely normal and NOT duplicates of each other, but they'll have
    different balances). The combination of date+debit+credit+balance is
    what actually pins down "this is literally the same statement line",
    which is what re-uploading the same PDF (or an overlapping date range
    across two statements) would produce.

    Scoped to company_id — two unrelated companies' banks could coincidentally
    produce an identical-looking row (same date/amount/balance is astronomically
    unlikely but not the point here; what matters is a Company A statement
    should never be flagged against Company B's data at all).
    """
    if not date or balance is None:
        return False
    q = db.query(models.Transaction).filter(
        models.Transaction.type == "BANK",
        models.Transaction.date == date,
        models.Transaction.debit == debit,
        models.Transaction.credit == credit,
        models.Transaction.balance == balance,
        models.Transaction.status != "REJECTED",
    )
    if company_id:
        q = q.filter(models.Transaction.company_id == company_id)
    return db.query(q.exists()).scalar()


def _validate_sales_tx(tx: models.Transaction) -> list[str]:
    """Safety gate for handwritten sales: checks required fields, AI confidence, and tax math.

    The AI-confidence check only applies to what the AI produced without a
    human looking at it — including the 0% rows created when AI extraction
    fails entirely and the user has to fill everything in by hand. Once the
    user has actually opened and saved an edit on this transaction (see
    manually_reviewed below), that's a human confirming the values with
    their own eyes; re-blocking approval on a stale/never-meaningful AI
    confidence score at that point would be a false gate, not a real
    safety check.
    """
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
        if not tx.manually_reviewed and (tx.confidence or 0) < 0.80:
            missing.append("AI confidence >= 0.80 (or edit & save the transaction to confirm it manually)")
        tax = (tx.cgst or 0) + (tx.sgst or 0) + (tx.igst or 0)
        if (
            tx.taxable_value is not None
            and tx.total_value is not None
            and abs((tx.taxable_value + tax) - tx.total_value) > 1.50
        ):
            missing.append("taxable + GST = total reconciliation")
    elif tx.type == "BANK":
        # A bank row has no GST/invoice-number concept — the only things
        # that actually make it postable to Tally are a real date and
        # exactly one side (debit XOR credit) of the entry.
        if not tx.date:
            missing.append("date")
        debit = tx.debit or 0.0
        credit = tx.credit or 0.0
        if debit <= 0 and credit <= 0:
            missing.append("a debit or credit amount")
        if debit > 0 and credit > 0:
            missing.append("only one of debit/credit (not both)")
        if not tx.party or not tx.party.strip():
            missing.append("counter-party ledger name")
    return missing


@router.get("", response_model=list[schemas.TransactionOut])
def list_transactions(
    status: str | None = None,
    company_id: int | None = Query(None, description="Defaults to the currently active company; pass explicitly to see another company's data"),
    all_companies: bool = Query(False, description="Bypass company scoping entirely — for cross-company admin views only"),
    db: Session = Depends(get_db),
):
    q = db.query(models.Transaction)
    if status:
        q = q.filter(models.Transaction.status == status)
    if not all_companies:
        scope_id = company_id if company_id is not None else get_active_company_id()
        if scope_id:
            q = q.filter(models.Transaction.company_id == scope_id)
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

    changed_fields = payload.dict(exclude_unset=True)
    for field_name, value in changed_fields.items():
        # No rounding here — taxable_value/cgst/sgst/igst/total_value are
        # all stored at full precision as the user typed them. Rounding
        # total_value alone at this point (while the tax components stay
        # exact) is exactly what created spurious taxable+tax≠total
        # mismatches downstream. Rounding happens exactly once, at the very
        # end, only when the voucher is actually built for Tally (see
        # tally/voucher_builder.py) — never before.
        setattr(tx, field_name, value)

    # A real edit means a human has actually looked at this row and
    # confirmed/corrected it — that's what lets a 0%-or-low-AI-confidence
    # transaction clear the approval gate above.
    if changed_fields:
        tx.manually_reviewed = True

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
            f"This {tx.type.replace('_', ' ').title()} transaction needs manual correction before approval: "
            + ", ".join(missing),
        )

    tx.status = "APPROVED"
    tx.approved_at = datetime.datetime.utcnow()
    db.add(
        models.AuditLog(
            transaction_id=tx.id, message="Transaction approved by user"
        )
    )
    db.commit()
    if tx.type in ("SALES", "PURCHASE"):
        _maybe_reconcile(db)
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
        tx.approved_at = datetime.datetime.utcnow()
        db.add(
            models.AuditLog(
                transaction_id=tx.id, message="Transaction approved via bulk action"
            )
        )
        approved_ids.append(tx.id)

    db.commit()
    if any(tx.type in ("SALES", "PURCHASE") for tx in txs if tx.id in approved_ids):
        _maybe_reconcile(db)
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


class ManualReconcileRequest(BaseModel):
    matched_transaction_id: int


@router.post("/reconcile")
def run_reconciliation(db: Session = Depends(get_db)):
    """
    Cross-checks every bank-statement row against your recorded sales and
    purchase invoices — a credit against sales (a customer paid you), a
    debit against purchases (you paid a supplier) — by amount and date
    proximity. Read-only: sets reconciliation_status/matched_transaction_id
    on the bank rows, never touches approval status or Tally.

    Safe to call repeatedly — e.g. wire this up as a button in Review &
    Approve's Bank Statements tab, or call it automatically after a bank
    statement upload finishes and after any sales/purchase approval, so the
    reconciliation view stays current without the user having to remember
    to trigger it.
    """
    stats = reconcile_bank_transactions(db)
    return {"status": "success", **stats}


@router.get("/{transaction_id}/match-candidates")
def match_candidates(transaction_id: int, db: Session = Depends(get_db)):
    """
    For a bank row that's UNMATCHED or AMBIGUOUS, returns the same-amount
    invoices within the date window so a human can pick the right one (or
    confirm none of them is right) — this is what the "AMBIGUOUS" status
    exists for: showing the real choice instead of the code silently
    guessing between two same-amount invoices.
    """
    tx = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")
    if tx.type != "BANK":
        raise HTTPException(400, "match-candidates only applies to BANK transactions")

    candidates = get_match_candidates(db, tx)
    return {
        "bank_transaction_id": tx.id,
        "amount": tx.credit if tx.credit > 0 else tx.debit,
        "looking_for": "SALES" if tx.credit > 0 else "PURCHASE",
        "candidates": [
            {
                "id": c.id,
                "party": c.party,
                "date": c.date,
                "invoice_number": c.invoice_number,
                "total_value": c.total_value,
                "status": c.status,
            }
            for c in candidates
        ],
    }


@router.post("/{transaction_id}/reconcile-manual")
def reconcile_manual(transaction_id: int, payload: ManualReconcileRequest, db: Session = Depends(get_db)):
    """
    Manually links a bank row to a specific invoice — the resolution path
    for AMBIGUOUS rows (or any UNMATCHED row where the human spots the
    right invoice by invoice number/narration even though the automatic
    date-proximity matcher couldn't confidently pick one).
    """
    bank_tx = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not bank_tx:
        raise HTTPException(404, "Transaction not found")
    if bank_tx.type != "BANK":
        raise HTTPException(400, "reconcile-manual only applies to BANK transactions")

    target_type = "SALES" if bank_tx.credit > 0 else "PURCHASE"
    match = db.query(models.Transaction).filter(
        models.Transaction.id == payload.matched_transaction_id,
        models.Transaction.type == target_type,
        models.Transaction.status != "REJECTED",
        models.Transaction.company_id == bank_tx.company_id,  # never let a manual match cross companies
    ).first()
    if not match:
        raise HTTPException(404, f"No {target_type.lower()} transaction {payload.matched_transaction_id} found to match against (in the same company as this bank entry)")

    # Freeing up whatever this bank row was matched to before (if anything)
    # isn't needed here — matched_transaction_id is just overwritten below,
    # and the next full reconcile() pass will naturally re-derive
    # used_candidate_ids from what's actually still linked.
    bank_tx.reconciliation_status = "MATCHED"
    bank_tx.matched_transaction_id = match.id
    db.add(models.AuditLog(
        transaction_id=bank_tx.id,
        message=f"Manually reconciled against {target_type.lower()} transaction #{match.id} ({match.party}, {match.invoice_number or 'no invoice #'})",
    ))
    db.commit()
    return {"status": "success", "matched_transaction_id": match.id}