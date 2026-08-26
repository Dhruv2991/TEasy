"""
Bank <-> invoice reconciliation.

Cross-checks bank-statement rows (type="BANK") against SALES/PURCHASE
invoices already recorded in the system, and flags which bank movements
correspond to a known invoice and which don't. This is read-only: it never
touches approval status, never edits amounts, and never pushes anything to
Tally — it only sets reconciliation_status / matched_transaction_id so
Review & Approve (and any report built on top of it) can surface the
mismatch instead of the user having to spot it by eye across two separate
screens.

Matching direction:
  - a bank CREDIT (money in) is checked against SALES invoices (money the
    business is owed) — a customer paying an invoice
  - a bank DEBIT (money out) is checked against PURCHASE invoices (money
    the business owes) — paying a supplier's bill

What counts as a match:
  - same amount, within AMOUNT_TOLERANCE (absorbs bank-fee/rounding noise)
  - dates within DATE_WINDOW_DAYS of each other (payment usually lags the
    invoice date by days to weeks, so exact-date matching would miss almost
    everything real)
  - each invoice can only be claimed by ONE bank row — greedy, closest
    date wins first, so it's stable across repeated runs

Deliberately NOT auto-matched: when 2+ invoices have the same amount and
are both plausibly close in date to a bank row, the code has no reliable
way to tell them apart (see _is_ambiguous docstring) — silently picking
one would risk reconciling a payment against the WRONG invoice, which is
worse than just leaving it for a human to resolve. Those get
reconciliation_status="AMBIGUOUS" instead of a guess.
"""
import datetime

from sqlalchemy.orm import Session

from . import models

AMOUNT_TOLERANCE = 1.0  # rupees — absorbs float/rounding noise, not a real amount difference
DATE_WINDOW_DAYS = 15   # how many days apart a bank row and an invoice can be and still be considered a candidate match

# When two candidate invoices are both within DATE_WINDOW_DAYS, only treat
# the closer one as a confident auto-match if it's at least this many days
# closer than the runner-up. Below this margin, a same-amount invoice that
# was raised a couple of days before or after another is genuinely
# ambiguous to a date-proximity heuristic — a human glancing at invoice
# numbers or narration can resolve it far more reliably than a guess.
AMBIGUITY_MARGIN_DAYS = 3

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y")


def _parse_date(raw: str | None) -> datetime.date | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def reconcile_bank_transactions(db: Session) -> dict:
    """Runs the full matching pass and returns summary counts. Safe to call
    repeatedly (e.g. a button in the UI, or automatically after every bank
    upload/approval) — already-MATCHED rows whose match is still valid are
    left untouched rather than being re-scored from scratch each time."""
    bank_txns = (
        db.query(models.Transaction)
        .filter(models.Transaction.type == "BANK")
        .filter(models.Transaction.status != "REJECTED")
        .all()
    )

    candidates_by_type: dict[str, list[models.Transaction]] = {"SALES": [], "PURCHASE": []}
    for t in (
        db.query(models.Transaction)
        .filter(models.Transaction.type.in_(["SALES", "PURCHASE"]))
        .filter(models.Transaction.status != "REJECTED")
        .all()
    ):
        candidates_by_type[t.type].append(t)

    # An invoice already claimed by a bank row whose match is still valid
    # (the invoice wasn't rejected/deleted since the last run) can't be
    # claimed again by a different bank row in this pass.
    used_candidate_ids: set[int] = set()
    for bt in bank_txns:
        if not bt.matched_transaction_id:
            continue
        still_valid = (
            db.query(models.Transaction)
            .filter(
                models.Transaction.id == bt.matched_transaction_id,
                models.Transaction.status != "REJECTED",
            )
            .first()
        )
        if still_valid:
            used_candidate_ids.add(still_valid.id)

    stats = {"matched": 0, "unmatched": 0, "ambiguous": 0, "skipped": 0, "unchanged": 0}

    for bt in bank_txns:
        if bt.matched_transaction_id and bt.matched_transaction_id in used_candidate_ids:
            stats["unchanged"] += 1
            continue  # already matched to something still valid — leave it

        amount = bt.credit if bt.credit > 0 else bt.debit
        target_type = "SALES" if bt.credit > 0 else ("PURCHASE" if bt.debit > 0 else None)
        if not target_type or amount <= 0:
            # A bank row with neither side set shouldn't normally exist
            # (extract_bank_transactions() already skips 0/0 rows), but if
            # one somehow does, there's nothing to reconcile it against.
            bt.reconciliation_status = "N/A"
            bt.matched_transaction_id = None
            stats["skipped"] += 1
            continue

        bank_date = _parse_date(bt.date)

        scored = []
        for c in candidates_by_type[target_type]:
            if c.id in used_candidate_ids:
                continue
            if abs(c.total_value - amount) > AMOUNT_TOLERANCE:
                continue
            c_date = _parse_date(c.date)
            if bank_date and c_date:
                delta_days = abs((bank_date - c_date).days)
                if delta_days > DATE_WINDOW_DAYS:
                    continue
            else:
                # Unknown/unparseable date on either side — still worth
                # considering as a candidate (amount already matched), just
                # deprioritized below anything with a confirmed close date.
                delta_days = DATE_WINDOW_DAYS + 1
            scored.append((delta_days, c))

        scored.sort(key=lambda pair: pair[0])

        if not scored:
            bt.reconciliation_status = "UNMATCHED"
            bt.matched_transaction_id = None
            stats["unmatched"] += 1
        elif len(scored) == 1 or (scored[1][0] - scored[0][0]) >= AMBIGUITY_MARGIN_DAYS:
            best = scored[0][1]
            bt.reconciliation_status = "MATCHED"
            bt.matched_transaction_id = best.id
            used_candidate_ids.add(best.id)
            stats["matched"] += 1
        else:
            bt.reconciliation_status = "AMBIGUOUS"
            bt.matched_transaction_id = None
            stats["ambiguous"] += 1

    db.commit()
    return stats


def get_match_candidates(db: Session, bank_tx: models.Transaction) -> list[models.Transaction]:
    """For an AMBIGUOUS (or UNMATCHED) bank row, returns the same-amount
    candidates within the date window so a human can pick the right one
    (or confirm none of them is right) instead of guessing blind."""
    amount = bank_tx.credit if bank_tx.credit > 0 else bank_tx.debit
    target_type = "SALES" if bank_tx.credit > 0 else "PURCHASE"
    bank_date = _parse_date(bank_tx.date)

    out = []
    for c in (
        db.query(models.Transaction)
        .filter(models.Transaction.type == target_type)
        .filter(models.Transaction.status != "REJECTED")
        .filter(models.Transaction.total_value.between(amount - AMOUNT_TOLERANCE, amount + AMOUNT_TOLERANCE))
        .all()
    ):
        c_date = _parse_date(c.date)
        if bank_date and c_date and abs((bank_date - c_date).days) > DATE_WINDOW_DAYS:
            continue
        out.append(c)
    return out
