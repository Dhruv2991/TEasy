"""
ORM models. Kept intentionally close to the schema in the project design doc
(documents, detected_bills, ocr_results, transactions, audit_logs) but trimmed
to what Phase 1 (Sales pipeline) actually needs.
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


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
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
    # Tally push tracking: NOT_SENT | SENT | FAILED
    tally_status = Column(String, default="NOT_SENT")
    tally_error = Column(String, nullable=True)

    bill = relationship("DetectedBill", back_populates="transaction")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=True)
    transaction_id = Column(Integer, nullable=True)
    message = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
