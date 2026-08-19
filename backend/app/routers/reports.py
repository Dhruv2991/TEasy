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


def _base_query(db: Session, doc_type: str | None, status: str | None):
    q = db.query(models.Transaction)
    if doc_type:
        q = q.filter(models.Transaction.type == doc_type.upper())
    if status:
        q = q.filter(models.Transaction.status == status.upper())
    else:
        # Default: exclude rejected rows from totals, they aren't real sales/purchases.
        q = q.filter(models.Transaction.status != "REJECTED")
    return q


@router.get("/summary")
def summary(
    doc_type: str | None = Query(None, description="SALES | PURCHASE | GSTR2B"),
    status: str | None = Query(None, description="NEEDS_REVIEW | APPROVED | REJECTED"),
    db: Session = Depends(get_db),
):
    """Headline totals: taxable value, tax breakup, total value, count."""
    rows = _base_query(db, doc_type, status).all()
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
    db: Session = Depends(get_db),
):
    """Taxable value + total value grouped by YYYY-MM, sorted chronologically."""
    rows = _base_query(db, doc_type, status).all()
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
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Taxable value + total value grouped by party, largest first."""
    rows = _base_query(db, doc_type, status).all()
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
    db: Session = Depends(get_db),
):
    """Taxable value + tax breakup grouped by GST rate slab — useful for a GSTR-style summary."""
    rows = _base_query(db, doc_type, status).all()
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
