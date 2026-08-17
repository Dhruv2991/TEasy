from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models

router = APIRouter(tags=["activity"])


@router.get("/activity/recent")
def recent_activity(limit: int = 10, db: Session = Depends(get_db)):
    logs = (
        db.query(models.AuditLog)
        .order_by(models.AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {"time": l.created_at.isoformat(), "message": l.message}
        for l in logs
    ]
