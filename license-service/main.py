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
from sqlalchemy import text
from sqlalchemy.orm import Session

load_dotenv()

import mailer
import models
from database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

# create_all() only creates missing tables, not new columns on tables that
# already exist. This adds the "plan" column (introduced for monthly/yearly
# billing) to a pre-existing licenses table without needing a full migration
# tool — safe to run every startup since IF NOT EXISTS makes it a no-op once
# the column is there.
try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS plan VARCHAR DEFAULT 'monthly'"))
        conn.commit()
except Exception as e:
    print(f"[startup] Skipped 'plan' column migration (likely already applied or non-Postgres DB): {e}")

TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))
GRACE_DAYS = int(os.environ.get("GRACE_DAYS", "5"))

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# Two billing intervals, two separate Razorpay Plans. RAZORPAY_PLAN_ID is
# kept as a fallback alias for RAZORPAY_PLAN_ID_MONTHLY so existing Render
# deployments configured before yearly billing existed don't break.
RAZORPAY_PLAN_IDS = {
    "monthly": os.environ.get("RAZORPAY_PLAN_ID_MONTHLY", os.environ.get("RAZORPAY_PLAN_ID", "")),
    "yearly": os.environ.get("RAZORPAY_PLAN_ID_YEARLY", ""),
}
# How many billing cycles to authorize upfront, per interval — 120 monthly
# cycles is ~10 years; 10 yearly cycles is also ~10 years. Razorpay requires
# a finite total_count, not a real cap on how long someone can stay subscribed.
RAZORPAY_TOTAL_COUNT = {"monthly": 120, "yearly": 10}

# Comma-separated list of allowed browser origins, e.g.
#   ALLOWED_ORIGINS=https://t-easy.vercel.app,https://www.teasy.in
# Set this in Render → Environment. Falls back to "*" only if unset, so
# local dev/testing still works without extra setup.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if RAZORPAY_KEY_ID else None

app = FastAPI(title="TEasy License Service")

# Locked down to the landing page + the desktop app's local calls happen
# server-to-server from the user's own machine (not a browser), so this can
# stay narrow. Set ALLOWED_ORIGINS in your deploy environment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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
    plan: str = "monthly"  # "monthly" or "yearly"


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
            # while still letting a legit user recover a lost key. Email it
            # to them instead of just handing it back in the API response,
            # since the whole point is they may not have it handy anymore.
            mailer.send_already_registered(
                email, existing.license_key, existing.status, _expiry_for(existing)
            )
            return {
                "license_key": existing.license_key,
                "status": existing.status,
                "expires_at": _expiry_for(existing),
                "is_new": False,
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

    mailer.send_trial_welcome(email, lic.license_key, TRIAL_DAYS, lic.trial_ends_at)

    return {
        "license_key": lic.license_key,
        "status": lic.status,
        "expires_at": lic.trial_ends_at,
        "is_new": True,
    }


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
    interval = body.plan if body.plan in RAZORPAY_PLAN_IDS else "monthly"
    plan_id = RAZORPAY_PLAN_IDS.get(interval)

    if not rzp_client or not plan_id:
        raise HTTPException(500, f"Billing isn't configured for the '{interval}' plan yet")

    lic = db.query(models.License).filter(models.License.license_key == body.license_key).first()
    if not lic:
        raise HTTPException(404, "Unknown license key")

    if lic.status == "active" and _license_is_valid(lic):
        raise HTTPException(400, "This license already has an active subscription.")

    if lic.razorpay_subscription_id:
        # Don't spin up a second Razorpay subscription if the user clicks
        # "Subscribe" twice, retries after a slow response, or reopens the
        # checkout tab before paying the first one — reuse the existing
        # pending subscription's checkout link instead of creating a
        # duplicate (which risked a double charge if both got paid).
        try:
            existing = rzp_client.subscription.fetch(lic.razorpay_subscription_id)
            if existing.get("status") in ("created", "authenticated", "pending"):
                return {"short_url": existing["short_url"], "subscription_id": existing["id"]}
        except Exception:
            pass  # couldn't fetch it (deleted/invalid) — fall through and create a fresh one

    subscription = rzp_client.subscription.create({
        "plan_id": plan_id,
        "customer_notify": 1,
        "total_count": RAZORPAY_TOTAL_COUNT.get(interval, 120),
        "notes": {"license_key": lic.license_key, "plan": interval},
    })

    lic.razorpay_subscription_id = subscription["id"]
    lic.plan = interval
    lic.status = "past_due"  # becomes "active" once the webhook confirms the first charge
    db.commit()

    return {"short_url": subscription["short_url"], "subscription_id": subscription["id"]}


@app.post("/billing/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not RAZORPAY_WEBHOOK_SECRET:
        # Refuse to process unsigned webhooks rather than silently trusting
        # them — without a secret, anyone who finds this URL could POST a
        # fake "subscription.charged" event and activate a license for free.
        raise HTTPException(500, "Webhook secret not configured on the server")

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
