import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import relationship

from database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    razorpay_customer_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    licenses = relationship("License", back_populates="user")


class License(Base):
    """
    One row per paying "seat". status meanings:
      trial      - free 7-day trial, no payment yet
      active     - paid subscription, currently in a paid period
      past_due   - Razorpay reported a failed charge; short additional grace
                   before we drop to expired (separate from offline grace)
      cancelled  - user cancelled; stays valid until current_period_end, then
                   effectively expired
      expired    - trial ran out or subscription lapsed with no payment
    """
    __tablename__ = "licenses"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    license_key = Column(String, unique=True, nullable=False, index=True, default=_uuid)

    status = Column(String, nullable=False, default="trial")
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)

    razorpay_subscription_id = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    user = relationship("User", back_populates="licenses")
