from pydantic import BaseModel
from typing import Optional, List
import datetime


class TransactionOut(BaseModel):
    id: int
    type: str
    party: str
    date: Optional[str] = None
    invoice_number: Optional[str] = None
    taxable_value: float
    gst_rate: float
    cgst: float
    sgst: float
    igst: float
    cess: float = 0.0
    total_value: float
    confidence: float
    status: str
    possible_duplicate: bool = False
    gst_rate_uncertain: bool = False
    manually_reviewed: bool = False
    rate_breakdown: Optional[str] = None  # JSON string, see models.py
    rate_breakdown_source: Optional[str] = None
    debit: float = 0.0
    credit: float = 0.0
    narration: Optional[str] = None
    approved_at: Optional[datetime.datetime] = None
    tally_status: str = "NOT_SENT"
    tally_error: Optional[str] = None

    class Config:
        from_attributes = True


class SupplierInvoiceMatchResult(BaseModel):
    matched: bool
    reason: str
    transaction: Optional[TransactionOut] = None


class RegisterMatchRow(BaseModel):
    transaction_id: int
    invoice_number: Optional[str] = None
    party: str
    matched: bool
    resolved: bool  # True if this row's mixed rate was newly resolved
    reason: str


class PurchaseRegisterMatchResult(BaseModel):
    total_purchase_transactions: int
    uncertain_before: int
    resolved: int
    still_uncertain: int
    unmatched_register_rows: int
    rows: List[RegisterMatchRow] = []


class TransactionUpdate(BaseModel):
    party: Optional[str] = None
    date: Optional[str] = None
    invoice_number: Optional[str] = None
    taxable_value: Optional[float] = None
    gst_rate: Optional[float] = None
    cgst: Optional[float] = None
    sgst: Optional[float] = None
    igst: Optional[float] = None
    cess: Optional[float] = None
    total_value: Optional[float] = None
    status: Optional[str] = None
    debit: Optional[float] = None
    credit: Optional[float] = None
    narration: Optional[str] = None


class PushToTallyRequest(BaseModel):
    # Restrict the push to one voucher type (e.g. push only "BANK" rows).
    # Leave unset to push every approved, not-yet-sent transaction across
    # all types — still grouped by type so Tally receives clean batches
    # (see routers/tally.py).
    type: Optional[str] = None
    # Explicit transaction id order to push in — e.g. whatever order the
    # user currently has the Review & Approve table sorted by. When given,
    # only these ids are pushed (still requires APPROVED + not yet SENT),
    # in exactly this sequence, and `type` is ignored. When omitted, the
    # default order is: grouped by type, then by approval order
    # (approved_at) within each type.
    order: Optional[List[int]] = None


class BillOut(BaseModel):
    id: int
    crop_path: Optional[str] = None
    order_in_page: int
    transaction: Optional[TransactionOut] = None

    class Config:
        from_attributes = True


class DocumentOut(BaseModel):
    id: int
    file_name: str
    document_type: str
    status: str
    uploaded_at: datetime.datetime
    bills: List[BillOut] = []

    class Config:
        from_attributes = True
