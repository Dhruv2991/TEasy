import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Boolean, Text
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
    plan = Column(String, nullable=True, default="monthly")  # "monthly" or "yearly" — set once they actually subscribe; null during trial
    tier = Column(String, nullable=False, default="silver")  # "silver" (1 device) or "gold" (3 devices) — set from /billing/create-subscription, defaults to "silver" for older rows that predate tiers
    trial_reminder_sent_at = Column(DateTime(timezone=True), nullable=True)  # so the daily reminder job doesn't email the same person twice

    # Hash of a few OS-level machine identifiers (see backend/app/security/
    # fingerprint.py), sent only when starting a trial. Used exclusively to
    # stop the same physical machine from farming unlimited free trials
    # under different emails — never used to gate a *paid* license, which
    # stays usable on any machine. Nullable because older rows predate this
    # column and because the desktop app may fail to compute one (e.g. some
    # container/CI environments) without that blocking a legitimate trial.
    device_fingerprint = Column(String, nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    user = relationship("User", back_populates="licenses")
    devices = relationship("LicenseDevice", back_populates="license", cascade="all, delete-orphan")


class LicenseDevice(Base):
    """One row per (license, physical machine) pair that has ever checked in
    on a *paid* license. This is what makes the tier's device limit real —
    unlike License.device_fingerprint (singular, trial-abuse prevention
    only), a license can have many of these. /license/check inserts a new
    row the first time a given fingerprint shows up for a key, refuses a
    new-but-over-limit fingerprint with 409, and just bumps last_seen for
    ones it already knows. deactivate lets a user free up a slot without
    support intervention (e.g. after retiring/reformatting a machine).
    """
    __tablename__ = "license_devices"

    id = Column(String, primary_key=True, default=_uuid)
    license_id = Column(String, ForeignKey("licenses.id"), nullable=False, index=True)
    device_fingerprint = Column(String, nullable=False, index=True)

    first_seen = Column(DateTime(timezone=True), default=_now)
    last_seen = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    license = relationship("License", back_populates="devices")


class WebhookEvent(Base):
    """Audit log + idempotency guard for inbound Razorpay webhooks.

    Razorpay can and does redeliver the same webhook (retries on timeout,
    manual redelivery from their dashboard, etc). Without a dedup guard, a
    redelivered `subscription.charged` would just re-apply harmlessly, but a
    redelivered `subscription.halted` after we'd already recovered could
    incorrectly knock a license back to past_due. event_hash is
    sha256(raw request body) — Razorpay's payload doesn't reliably include
    its own unique event id across all event types, so hashing the body we
    actually received is the one dedup key guaranteed to be stable and
    unique per distinct delivery.
    """
    __tablename__ = "webhook_events"

    id = Column(String, primary_key=True, default=_uuid)
    event_hash = Column(String, unique=True, nullable=False, index=True)
    event_type = Column(String, nullable=True)
    subscription_id = Column(String, nullable=True, index=True)
    payload = Column(Text, nullable=True)  # raw JSON body, for later debugging/audit
    processed = Column(Boolean, default=False)  # False if signature/parsing failed before we could act on it
    received_at = Column(DateTime(timezone=True), default=_now)
