"""
Reports: read-only aggregation over the existing `transactions` table.

Nothing here writes to the database or touches the OCR/Tally pipeline —
every endpoint is a GET that groups/sums rows that already exist. Safe to
add without risk to any other flow.
"""
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models

router = APIRouter(prefix="/reports", tags=["reports"])

# The UI's "GSTR-2B" filter option is meant to mean "credit/debit notes
# imported from GSTR-2B" — but Transaction.type is never literally
# "GSTR2B" (see routers/gstr2b.py: notes are saved as CREDIT_NOTE /
# DEBIT_NOTE, and B2B purchases as PURCHASE). Filtering on the literal
# string "GSTR2B" therefore always matched zero rows. This maps the UI's
# doc_type value onto the real underlying type(s).
_DOC_TYPE_ALIASES = {
    "GSTR2B": ["CREDIT_NOTE", "DEBIT_NOTE"],
}


def _base_query(
    db: Session,
    doc_type: str | None,
    status: str | None,
    date_from: str | None = None,
    date_to: str | None = None,
    state: str | None = None,
):
    q = db.query(models.Transaction)
    if doc_type:
        real_types = _DOC_TYPE_ALIASES.get(doc_type.upper(), [doc_type.upper()])
        q = q.filter(models.Transaction.type.in_(real_types))
    if status:
        q = q.filter(models.Transaction.status == status.upper())
    else:
        # Default: exclude rejected rows from totals, they aren't real sales/purchases.
        q = q.filter(models.Transaction.status != "REJECTED")
    # Transaction.date is stored as free text, ISO (YYYY-MM-DD) where the
    # source could be parsed cleanly. Lexical string comparison on ISO
    # dates sorts identically to a real date comparison, so this is safe
    # without needing a separate date column/type. Rows with a
    # non-ISO/unparseable date (rare — see _to_iso_date() callers) simply
    # won't match a from/to bound, same as they don't sort into "By month".
    if date_from:
        q = q.filter(models.Transaction.date >= date_from)
    if date_to:
        q = q.filter(models.Transaction.date <= date_to)
    if state:
        q = q.filter(models.Transaction.party_state == state)
    return q


@router.get("/summary")
def summary(
    doc_type: str | None = Query(None, description="SALES | PURCHASE | GSTR2B | BANK"),
    status: str | None = Query(None, description="NEEDS_REVIEW | APPROVED | REJECTED"),
    date_from: str | None = Query(None, description="Inclusive lower bound, YYYY-MM-DD"),
    date_to: str | None = Query(None, description="Inclusive upper bound, YYYY-MM-DD"),
    state: str | None = Query(None, description="Party's GST state, e.g. 'Karnataka' — see /reports/states for the list actually present"),
    db: Session = Depends(get_db),
):
    """Headline totals: taxable value, tax breakup, total value, count."""
    rows = _base_query(db, doc_type, status, date_from, date_to, state).all()
    out = {
        "count": len(rows),
        "taxable_value": 0.0,
        "cgst": 0.0,
        "sgst": 0.0,
        "igst": 0.0,
        "cess": 0.0,
        "total_value": 0.0,
        "needs_review": 0,
        "approved": 0,
    }
    for tx in rows:
        out["taxable_value"] += tx.taxable_value or 0.0
        out["cgst"] += tx.cgst or 0.0
        out["sgst"] += tx.sgst or 0.0
        out["igst"] += tx.igst or 0.0
        out["cess"] += tx.cess or 0.0
        out["total_value"] += tx.total_value or 0.0
        if tx.status == "NEEDS_REVIEW":
            out["needs_review"] += 1
        elif tx.status == "APPROVED":
            out["approved"] += 1
    for k in ("taxable_value", "cgst", "sgst", "igst", "cess", "total_value"):
        out[k] = round(out[k], 2)
    return out


@router.get("/by-month")
def by_month(
    doc_type: str | None = Query(None),
    status: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    state: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Taxable value + total value grouped by YYYY-MM, sorted chronologically."""
    rows = _base_query(db, doc_type, status, date_from, date_to, state).all()
    buckets = defaultdict(lambda: {"taxable_value": 0.0, "total_value": 0.0, "count": 0})
    for tx in rows:
        # date is stored as free text (ISO where possible); fall back to "Unknown"
        month_key = "Unknown"
        if tx.date and len(tx.date) >= 7 and tx.date[4] == "-":
            month_key = tx.date[:7]  # "YYYY-MM"
        b = buckets[month_key]
        b["taxable_value"] += tx.taxable_value or 0.0
        b["total_value"] += tx.total_value or 0.0
        b["count"] += 1
    result = []
    for month, vals in sorted(buckets.items()):
        result.append({
            "month": month,
            "count": vals["count"],
            "taxable_value": round(vals["taxable_value"], 2),
            "total_value": round(vals["total_value"], 2),
        })
    return result


@router.get("/by-party")
def by_party(
    doc_type: str | None = Query(None),
    status: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    state: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Taxable value + total value grouped by party, largest first."""
    rows = _base_query(db, doc_type, status, date_from, date_to, state).all()
    buckets = defaultdict(lambda: {"taxable_value": 0.0, "total_value": 0.0, "count": 0})
    for tx in rows:
        b = buckets[tx.party or "Cash"]
        b["taxable_value"] += tx.taxable_value or 0.0
        b["total_value"] += tx.total_value or 0.0
        b["count"] += 1
    result = [
        {"party": party, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in vals.items()}}
        for party, vals in buckets.items()
    ]
    result.sort(key=lambda r: r["total_value"], reverse=True)
    return result[:limit]


@router.get("/by-gst-rate")
def by_gst_rate(
    doc_type: str | None = Query(None),
    status: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    state: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Taxable value + tax breakup grouped by GST rate slab — useful for a GSTR-style summary."""
    rows = _base_query(db, doc_type, status, date_from, date_to, state).all()
    buckets = defaultdict(lambda: {
        "taxable_value": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "cess": 0.0, "count": 0,
    })
    for tx in rows:
        b = buckets[tx.gst_rate or 0.0]
        b["taxable_value"] += tx.taxable_value or 0.0
        b["cgst"] += tx.cgst or 0.0
        b["sgst"] += tx.sgst or 0.0
        b["igst"] += tx.igst or 0.0
        b["cess"] += tx.cess or 0.0
        b["count"] += 1
    result = []
    for rate, vals in sorted(buckets.items()):
        result.append({
            "gst_rate": rate,
            "count": vals["count"],
            "taxable_value": round(vals["taxable_value"], 2),
            "cgst": round(vals["cgst"], 2),
            "sgst": round(vals["sgst"], 2),
            "igst": round(vals["igst"], 2),
            "cess": round(vals["cess"], 2),
        })
    return result


@router.get("/by-state")
def by_state(
    doc_type: str | None = Query(None),
    status: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Taxable value + total value grouped by the party's GST state, largest
    first. Only meaningful for transaction types that actually carry a
    GSTIN today (GSTR-2B purchases and credit/debit notes) — plain
    OCR'd/hand-entered sales and purchases have no GSTIN captured, so they
    fall into the "Unknown" bucket rather than being silently dropped or
    guessed at.
    """
    rows = _base_query(db, doc_type, status, date_from, date_to, state=None).all()
    buckets = defaultdict(lambda: {"taxable_value": 0.0, "total_value": 0.0, "count": 0})
    for tx in rows:
        b = buckets[tx.party_state or "Unknown"]
        b["taxable_value"] += tx.taxable_value or 0.0
        b["total_value"] += tx.total_value or 0.0
        b["count"] += 1
    result = [
        {"state": state_name, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in vals.items()}}
        for state_name, vals in buckets.items()
    ]
    result.sort(key=lambda r: r["total_value"], reverse=True)
    return result


@router.get("/states")
def list_states(db: Session = Depends(get_db)):
    """Distinct party_state values actually present on non-rejected
    transactions, for populating the state filter dropdown — only states
    with at least one real transaction show up, instead of listing all ~38
    GST states/UTs regardless of whether any are relevant to this shop."""
    rows = (
        db.query(models.Transaction.party_state)
        .filter(models.Transaction.party_state.isnot(None))
        .filter(models.Transaction.status != "REJECTED")
        .distinct()
        .all()
    )
    return sorted(r[0] for r in rows if r[0])
