"""
TEasy license/subscription service.

This is the one piece of TEasy that HAS to be centrally hosted: it's the
source of truth for "has this person paid this month". Everything else
(the desktop app) runs entirely on the customer's PC.

Flow:
  1. New user -> POST /trial/start {email} -> 7-day trial license_key.
  2. Desktop app calls POST /license/check {license_key} on startup and
     periodically, caches the result locally with a timestamp (see
     backend/app/security/license_client.py in the main app). If it can't
     reach this service (no internet), it trusts its own cache for up to
     GRACE_DAYS before locking.
  3. To go from trial to paid: POST /billing/create-subscription
     {license_key} -> returns a Razorpay short_url the frontend opens in
     the browser. User pays there.
  4. Razorpay calls POST /billing/webhook on charge/cancel events, which
     updates the license's status and current_period_end. No polling needed.

Deploy this anywhere that gives you a persistent Postgres + a stable HTTPS
URL (Railway and Render both work off a single `git push`). Do NOT deploy
this as a Vercel serverless function without an external Postgres (Neon
works fine) - Vercel functions have no local disk to keep SQLite in.
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import razorpay
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

load_dotenv()

import models
from database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))
GRACE_DAYS = int(os.environ.get("GRACE_DAYS", "5"))

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_PLAN_ID = os.environ.get("RAZORPAY_PLAN_ID", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if RAZORPAY_KEY_ID else None

app = FastAPI(title="TEasy License Service")

# Locked down to the landing page + the desktop app's local calls happen
# server-to-server from the user's own machine (not a browser), so this can
# stay narrow. Add your production landing page domain here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # the desktop app calls this from Python, not a browser, so CORS mostly gates the landing page; tighten to your domain once it's live
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now():
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite (used for local/dev testing) drops tzinfo on round-trip even
    # with DateTime(timezone=True); Postgres doesn't. Normalize either way.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _license_is_valid(lic: models.License) -> bool:
    if lic.status == "trial":
        end = _aware(lic.trial_ends_at)
        return bool(end) and _now() < end
    if lic.status in ("active", "past_due", "cancelled"):
        # "cancelled" stays valid until the period they already paid for ends
        end = _aware(lic.current_period_end)
        return bool(end) and _now() < end
    return False


def _expiry_for(lic: models.License) -> datetime | None:
    return _aware(lic.trial_ends_at if lic.status == "trial" else lic.current_period_end)


class TrialStartRequest(BaseModel):
    email: str


class LicenseCheckRequest(BaseModel):
    license_key: str


class CreateSubscriptionRequest(BaseModel):
    license_key: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/trial/start")
def start_trial(body: TrialStartRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Enter a valid email")

    user = db.query(models.User).filter(models.User.email == email).first()
    if user:
        existing = db.query(models.License).filter(models.License.user_id == user.id).first()
        if existing:
            # Re-issuing the same license_key for a known email prevents
            # someone from farming infinite trials with the same address,
            # while still letting a legit user recover a lost key.
            return {
                "license_key": existing.license_key,
                "status": existing.status,
                "expires_at": _expiry_for(existing),
            }
    else:
        user = models.User(email=email)
        db.add(user)
        db.flush()

    lic = models.License(
        user_id=user.id,
        status="trial",
        trial_ends_at=_now() + timedelta(days=TRIAL_DAYS),
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)

    return {"license_key": lic.license_key, "status": lic.status, "expires_at": lic.trial_ends_at}


@app.post("/license/check")
def check_license(body: LicenseCheckRequest, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.license_key == body.license_key).first()
    if not lic:
        raise HTTPException(404, "Unknown license key")

    valid = _license_is_valid(lic)
    return {
        "valid": valid,
        "status": lic.status,
        "expires_at": _expiry_for(lic),
        "grace_days": GRACE_DAYS,
    }


@app.post("/billing/create-subscription")
def create_subscription(body: CreateSubscriptionRequest, db: Session = Depends(get_db)):
    if not rzp_client or not RAZORPAY_PLAN_ID:
        raise HTTPException(500, "Billing isn't configured on the server yet")

    lic = db.query(models.License).filter(models.License.license_key == body.license_key).first()
    if not lic:
        raise HTTPException(404, "Unknown license key")

    subscription = rzp_client.subscription.create({
        "plan_id": RAZORPAY_PLAN_ID,
        "customer_notify": 1,
        "total_count": 120,  # ~10 years of monthly cycles; Razorpay requires a cap, not a real limit on how long they can stay subscribed
        "notes": {"license_key": lic.license_key},
    })

    lic.razorpay_subscription_id = subscription["id"]
    lic.status = "past_due"  # becomes "active" once the webhook confirms the first charge
    db.commit()

    return {"short_url": subscription["short_url"], "subscription_id": subscription["id"]}


@app.post("/billing/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if RAZORPAY_WEBHOOK_SECRET:
        expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(400, "Invalid webhook signature")

    payload = await request.json()
    event = payload.get("event", "")
    entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    subscription_id = entity.get("id")
    if not subscription_id:
        return {"ok": True}  # not a subscription event we care about

    lic = db.query(models.License).filter(models.License.razorpay_subscription_id == subscription_id).first()
    if not lic:
        return {"ok": True}

    if event == "subscription.charged":
        lic.status = "active"
        current_end = entity.get("current_end")  # unix timestamp of paid-through date
        if current_end:
            lic.current_period_end = datetime.fromtimestamp(current_end, tz=timezone.utc)
        else:
            lic.current_period_end = _now() + timedelta(days=31)
    elif event == "subscription.cancelled":
        lic.status = "cancelled"  # stays valid until current_period_end (already set from last charge)
    elif event in ("subscription.halted", "subscription.pending"):
        lic.status = "past_due"
    elif event == "subscription.completed":
        lic.status = "cancelled"

    db.commit()
    return {"ok": True}
