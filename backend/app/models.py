"""
ORM models. Kept intentionally close to the schema in the project design doc
(documents, detected_bills, ocr_results, transactions, audit_logs, companies) trimmed
to what Phase 1 (Sales pipeline) and multi-company operations require.
"""
import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
)
from sqlalchemy.orm import relationship
from .database import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    document_type = Column(String, default="SALES")  # SALES | PURCHASE | GSTR2B
    status = Column(String, default="UPLOADED")
    # UPLOADED -> PROCESSING -> EXTRACTED -> NEEDS_REVIEW -> APPROVED -> FAILED
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)

    bills = relationship("DetectedBill", back_populates="document", cascade="all, delete-orphan")


class DetectedBill(Base):
    """One physical bill detected inside a (possibly multi-bill) photo."""
    __tablename__ = "detected_bills"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"))
    crop_path = Column(String, nullable=True)  # nullable: GSTR-2B-derived rows have no source photo
    bbox = Column(String)  # "x,y,w,h" in source image
    order_in_page = Column(Integer, default=0)

    document = relationship("Document", back_populates="bills")
    ocr_result = relationship("OcrResult", back_populates="bill", uselist=False, cascade="all, delete-orphan")
    transaction = relationship("Transaction", back_populates="bill", uselist=False, cascade="all, delete-orphan")


class OcrResult(Base):
    __tablename__ = "ocr_results"

    id = Column(Integer, primary_key=True, index=True)
    bill_id = Column(Integer, ForeignKey("detected_bills.id"))
    raw_text = Column(Text)
    mean_confidence = Column(Float, default=0.0)

    bill = relationship("DetectedBill", back_populates="ocr_result")


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    tally_company_name = Column(String, nullable=True)
    gstin = Column(String, nullable=True)
    state_code = Column(String, nullable=True)
    default_gst_rate = Column(Float, default=18.0)
    archived = Column(Boolean, default=False)  # Matches models.Company.archived in router
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    transactions = relationship("Transaction", back_populates="company")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    bill_id = Column(Integer, ForeignKey("detected_bills.id"))
    type = Column(String, default="SALES")
    party = Column(String, default="Cash")
    date = Column(String)  # ISO string, kept as text since OCR dates are often ambiguous
    invoice_number = Column(String)
    taxable_value = Column(Float, default=0.0)
    gst_rate = Column(Float, default=0.0)
    cgst = Column(Float, default=0.0)
    sgst = Column(Float, default=0.0)
    igst = Column(Float, default=0.0)
    cess = Column(Float, default=0.0)  # relevant mainly for GSTR-2B purchase notes (e.g. cement, coal); usually 0 for photographed bills
    total_value = Column(Float, default=0.0)
    confidence = Column(Float, default=0.0)
    status = Column(String, default="NEEDS_REVIEW")  # NEEDS_REVIEW | APPROVED | REJECTED
    # True if another transaction of the same type+party already has this
    # exact invoice_number — usually means either a real duplicate upload
    # (same bill photographed/scanned twice) or a misread invoice_number
    # that happens to collide with a real one. Either way, worth a manual
    # look before approving. See _flag_duplicate_invoice() in documents.py.
    possible_duplicate = Column(Boolean, default=False)
    gst_rate_uncertain = Column(Boolean, default=False)  # True when the source Excel doesn't cleanly determine a single GST rate (mixed-rate invoice) — the totals are still exact, only the rate label is unresolved
    # True once a user has opened this transaction in Review & Approve and
    # saved an actual change to it. A transaction that came in at 0%/low AI
    # confidence (e.g. a blank manual-entry row created when AI extraction
    # failed) is otherwise permanently blocked from Approve — but once a
    # human has actually looked at it and filled in/corrected the real
    # values, that confidence score no longer describes anything the
    # approval gate should still be enforcing.
    manually_reviewed = Column(Boolean, default=False)
    # JSON string: [{"rate": 5, "taxable_value": ..., "cgst": ..., "sgst": ..., "igst": ...}, ...]
    # Populated only when a supplier-provided invoice Excel was matched against
    # this transaction's GSTR-2B totals (see gstr2b/supplier_match.py). When
    # present, the voucher builder emits one line item per rate instead of a
    # single line at the (possibly uncertain) aggregate rate.
    rate_breakdown = Column(Text, nullable=True)
    rate_breakdown_source = Column(String, nullable=True)  # e.g. filename of the supplier invoice that supplied it
    # Bank-statement-specific fields (type == "BANK"). GST/taxable fields
    # above are left at 0 for these — a bank entry is a plain ledger-to-ledger
    # movement, never a taxed sale/purchase line. Exactly one of debit/credit
    # is non-zero per row, mirroring the statement itself.
    debit = Column(Float, default=0.0)
    credit = Column(Float, default=0.0)
    balance = Column(Float, nullable=True)  # BANK rows only — the statement's running balance after this row; used for reconciliation's balance cross-check and for duplicate-upload detection
    # Populated where the source actually carries a GSTIN (GSTR-2B purchase
    # imports and credit/debit notes) — plain OCR'd sales/purchase photos
    # and hand-entered rows won't have one, so party_state stays null for
    # those. See gst_states.py for how party_state is derived from it.
    party_gstin = Column(String, nullable=True)
    party_state = Column(String, nullable=True)
    narration = Column(Text, nullable=True)
    # Timestamp set the moment a transaction is approved (single or bulk).
    # Used as the default "approved order" when pushing to Tally — pushing
    # in the order things were actually approved is a more meaningful
    # default than raw row-insertion order, and gives every voucher type a
    # stable, explainable sequence when the user hasn't asked for a
    # specific sort order themselves.
    approved_at = Column(DateTime, nullable=True)
    # Tally push tracking: NOT_SENT | SENT | FAILED
    tally_status = Column(String, default="NOT_SENT")
    tally_error = Column(String, nullable=True)
    # Reconciliation (bank rows only — see reconciliation.py):
    # "MATCHED" once a BANK row's amount+date lines up with exactly one
    # confident SALES/PURCHASE invoice; "UNMATCHED" if nothing lines up
    # (may just mean this bank movement isn't an invoiced sale/purchase at
    # all — a bank charge, salary, GST payment, owner's drawing, etc. —
    # not necessarily an error); "AMBIGUOUS" when 2+ same-amount invoices
    # are close enough in date that auto-matching would be a guess; null
    # for non-BANK rows, since reconciliation only runs in that direction.
    reconciliation_status = Column(String, nullable=True)
    matched_transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    bill = relationship("DetectedBill", back_populates="transaction")
    company = relationship("Company", back_populates="transactions")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=True)
    transaction_id = Column(Integer, nullable=True)
    message = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)