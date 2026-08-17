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
    tally_status: str = "NOT_SENT"
    tally_error: Optional[str] = None

    class Config:
        from_attributes = True


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
