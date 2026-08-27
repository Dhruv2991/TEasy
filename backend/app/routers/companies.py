"""
Company management — lets one teasy install handle books for several
businesses (e.g. a bookkeeper managing multiple clients), the same way
Tally itself lets you switch between companies on one install.

Exactly one company is "active" at a time (see settings.get_active_company_id).
Every document upload, transaction, and Tally push is scoped to whichever
company is active at that moment. See database._ensure_default_company_and_backfill
for how existing single-company installs get upgraded without losing data.
"""
import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..settings import get_settings, save_settings

router = APIRouter(prefix="/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    name: str
    gstin: str | None = None
    state_code: str | None = None
    default_gst_rate: float = 18.0
    tally_company_name: str | None = None


class CompanyUpdate(BaseModel):
    name: str | None = None
    gstin: str | None = None
    state_code: str | None = None
    default_gst_rate: float | None = None
    tally_company_name: str | None = None


class CompanyOut(BaseModel):
    id: int
    name: str
    gstin: str | None = None
    state_code: str | None = None
    default_gst_rate: float = 18.0
    tally_company_name: str | None = None
    archived: bool = False
    is_active: bool = False

    class Config:
        from_attributes = True


def _serialize(company: models.Company, active_id: int | None) -> CompanyOut:
    out = CompanyOut.model_validate(company)
    out.is_active = company.id == active_id
    return out


@router.get("", response_model=list[CompanyOut])
def list_companies(include_archived: bool = False, db: Session = Depends(get_db)):
    active_id = get_settings().get("active_company_id")
    q = db.query(models.Company)
    if not include_archived:
        q = q.filter(models.Company.archived == False)  # noqa: E712
    companies = q.order_by(models.Company.name.asc()).all()
    return [_serialize(c, active_id) for c in companies]


@router.get("/active", response_model=CompanyOut | None)
def get_active_company(db: Session = Depends(get_db)):
    active_id = get_settings().get("active_company_id")
    if not active_id:
        return None
    company = db.query(models.Company).filter(models.Company.id == active_id).first()
    if not company:
        return None
    return _serialize(company, active_id)


@router.post("", response_model=CompanyOut)
def create_company(payload: CompanyCreate, db: Session = Depends(get_db)):
    company = models.Company(
        name=payload.name.strip(),
        gstin=(payload.gstin or "").strip() or None,
        state_code=(payload.state_code or "").strip() or None,
        default_gst_rate=payload.default_gst_rate,
        tally_company_name=(payload.tally_company_name or "").strip() or None,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return _serialize(company, get_settings().get("active_company_id"))


@router.patch("/{company_id}", response_model=CompanyOut)
def update_company(company_id: int, payload: CompanyUpdate, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(404, "Company not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    db.commit()
    db.refresh(company)
    return _serialize(company, get_settings().get("active_company_id"))


@router.post("/{company_id}/activate", response_model=CompanyOut)
def activate_company(company_id: int, db: Session = Depends(get_db)):
    """Switches which company is 'open' — everything created after this
    (uploads, transactions, Tally pushes) is scoped to this company until
    it's switched again. Doesn't touch or move any existing data."""
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(404, "Company not found")
    if company.archived:
        raise HTTPException(400, "Can't activate an archived company — unarchive it first")
    save_settings({"active_company_id": company.id})
    return _serialize(company, company.id)


@router.post("/{company_id}/archive", response_model=CompanyOut)
def archive_company(company_id: int, db: Session = Depends(get_db)):
    """
    Hides a company from the switcher without deleting any of its data —
    its documents/transactions stay exactly as they are, just no longer
    selectable as the active company. If it's currently active, falls back
    to the next available company (or leaves nothing active if this was
    the only one — a very small business that will re-check next launch).
    """
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(404, "Company not found")
    company.archived = True
    db.commit()
    db.refresh(company)

    if get_settings().get("active_company_id") == company.id:
        fallback = (
            db.query(models.Company)
            .filter(models.Company.archived == False, models.Company.id != company.id)  # noqa: E712
            .order_by(models.Company.id.asc())
            .first()
        )
        save_settings({"active_company_id": fallback.id if fallback else 0})

    return _serialize(company, get_settings().get("active_company_id"))


@router.post("/{company_id}/unarchive", response_model=CompanyOut)
def unarchive_company(company_id: int, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(404, "Company not found")
    company.archived = False
    db.commit()
    db.refresh(company)
    return _serialize(company, get_settings().get("active_company_id"))
