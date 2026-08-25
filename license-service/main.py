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
import asyncio
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import razorpay
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

load_dotenv()

import mailer
import models
from database import Base, SessionLocal, engine, get_db

Base.metadata.create_all(bind=engine)

# create_all() only creates missing tables, not new columns on tables that
# already exist. This adds the "plan" column (introduced for monthly/yearly
# billing) to a pre-existing licenses table without needing a full migration
# tool — safe to run every startup since IF NOT EXISTS makes it a no-op once
# the column is there.
try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS plan VARCHAR DEFAULT 'monthly'"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS trial_reminder_sent_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS device_fingerprint VARCHAR"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_licenses_device_fingerprint ON licenses (device_fingerprint)"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS tier VARCHAR DEFAULT 'silver'"))
        conn.execute(text("UPDATE licenses SET tier = 'silver' WHERE tier IS NULL"))
        conn.commit()
except Exception as e:
    print(f"[startup] Skipped column migration (likely already applied or non-Postgres DB): {e}")

TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))
GRACE_DAYS = int(os.environ.get("GRACE_DAYS", "5"))

# Per-tier concurrent-device cap, enforced in /license/check via the
# license_devices table. Mirrors TIER_DEVICE_LIMIT in the desktop app's
# license_client.py — that copy is just for UI copy, this one is the
# actual enforcement.
TIER_DEVICE_LIMIT = {"silver": 1, "gold": 3}
DEFAULT_TIER = "silver"

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# Two billing intervals x two tiers = four Razorpay Plans. The *_MONTHLY /
# *_YEARLY (untiered) env vars are kept as fallback aliases for "silver",
# so existing Render deployments configured before tiers existed keep
# working without touching their env — they just never sell "gold" until
# RAZORPAY_PLAN_ID_GOLD_* is set.
RAZORPAY_PLAN_IDS = {
    "silver": {
        "monthly": os.environ.get("RAZORPAY_PLAN_ID_SILVER_MONTHLY", os.environ.get("RAZORPAY_PLAN_ID_MONTHLY", os.environ.get("RAZORPAY_PLAN_ID", ""))),
        "yearly": os.environ.get("RAZORPAY_PLAN_ID_SILVER_YEARLY", os.environ.get("RAZORPAY_PLAN_ID_YEARLY", "")),
    },
    "gold": {
        "monthly": os.environ.get("RAZORPAY_PLAN_ID_GOLD_MONTHLY", ""),
        "yearly": os.environ.get("RAZORPAY_PLAN_ID_GOLD_YEARLY", ""),
    },
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

# Per-IP rate limits — mainly to stop /trial/start from being used to spam
# the "already registered" email at someone's inbox, and to stop
# /license/check from being hammered. Generous enough that a real user
# hitting "start trial" a couple of times by mistake never sees this.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Locked down to the landing page + the desktop app's local calls happen
# server-to-server from the user's own machine (not a browser), so this can
# stay narrow. Set ALLOWED_ORIGINS in your deploy environment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _send_trial_reminders_once():
    """Emails anyone whose trial ends in the next 2 days and hasn't already
    been reminded. Runs inside the same process on a loop (see
    _reminder_loop below) rather than as a separate cron job, since Render's
    free/hobby tiers don't include a cron product — this keeps deployment
    to a single service. Caveat: if this service spins down from
    inactivity (free tier), the loop pauses until the next request wakes
    it — acceptable for a reminder email, not acceptable for anything
    time-critical.
    """
    db = SessionLocal()
    try:
        window_start = _now()
        window_end = _now() + timedelta(days=2)
        candidates = (
            db.query(models.License)
            .filter(
                models.License.status == "trial",
                models.License.trial_ends_at.isnot(None),
                models.License.trial_ends_at > window_start,
                models.License.trial_ends_at <= window_end,
                models.License.trial_reminder_sent_at.is_(None),
            )
            .all()
        )
        for lic in candidates:
            if not lic.user:
                continue
            days_left = max(1, (_aware(lic.trial_ends_at) - _now()).days + 1)
            if mailer.send_trial_ending_soon(lic.user.email, days_left, _aware(lic.trial_ends_at)):
                lic.trial_reminder_sent_at = _now()
                db.commit()
    finally:
        db.close()


async def _reminder_loop():
    # Small delay on startup so this doesn't compete with the app coming up.
    await asyncio.sleep(30)
    while True:
        try:
            _send_trial_reminders_once()
        except Exception as e:
            print(f"[reminders] Skipped a run due to error: {e}")
        await asyncio.sleep(6 * 60 * 60)  # every 6 hours — frequent enough that "2 days out" is never missed by more than a few hours


@app.on_event("startup")
async def _start_background_jobs():
    asyncio.create_task(_reminder_loop())


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
    device_fingerprint: str | None = None


class LicenseCheckRequest(BaseModel):
    license_key: str
    device_fingerprint: str | None = None


class CreateSubscriptionRequest(BaseModel):
    license_key: str
    plan: str = "monthly"  # "monthly" or "yearly"
    tier: str = "silver"  # "silver" or "gold"


class DeactivateDeviceRequest(BaseModel):
    license_key: str
    device_fingerprint: str


@app.get("/health")
def health():
    return {"status": "ok"}


ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


@app.get("/admin/licenses")
def admin_list_licenses(token: str, db: Session = Depends(get_db)):
    """Simple read-only overview so you don't need pgAdmin open just to see
    who's trialing, who's paying, and who's about to churn. Protected by a
    single shared token (set ADMIN_TOKEN on Render) rather than real auth —
    fine for one-person access, treat the URL like a password and don't
    share it. Not linked from anywhere in the app; you hit it directly."""
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "Invalid or missing admin token")

    licenses = db.query(models.License).order_by(models.License.created_at.desc()).limit(500).all()
    rows = []
    for lic in licenses:
        rows.append({
            "email": lic.user.email if lic.user else None,
            "status": lic.status,
            "plan": lic.plan,
            "tier": lic.tier or DEFAULT_TIER,
            "device_count": len(lic.devices),
            "device_limit": TIER_DEVICE_LIMIT.get(lic.tier or DEFAULT_TIER, 1),
            "license_key": lic.license_key,
            "trial_ends_at": _aware(lic.trial_ends_at),
            "current_period_end": _aware(lic.current_period_end),
            "valid_now": _license_is_valid(lic),
            "created_at": _aware(lic.created_at),
        })

    counts = {"trial": 0, "active": 0, "past_due": 0, "cancelled": 0, "expired": 0}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    return {"total": len(rows), "counts": counts, "licenses": rows}


@app.get("/admin/webhook-events")
def admin_list_webhook_events(token: str, db: Session = Depends(get_db)):
    """Read-only audit trail of inbound Razorpay webhooks — useful for
    tracing "why did this license's status change" or confirming a
    suspected duplicate delivery was actually deduped. Same shared-token
    protection as /admin/licenses; not linked from anywhere in the app."""
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "Invalid or missing admin token")

    events = (
        db.query(models.WebhookEvent)
        .order_by(models.WebhookEvent.received_at.desc())
        .limit(200)
        .all()
    )
    return {
        "total": len(events),
        "events": [
            {
                "event_type": e.event_type,
                "subscription_id": e.subscription_id,
                "processed": e.processed,
                "received_at": _aware(e.received_at),
                "event_hash": e.event_hash,
            }
            for e in events
        ],
    }


@app.post("/trial/start")
@limiter.limit("5/hour")
def start_trial(request: Request, body: TrialStartRequest, db: Session = Depends(get_db)):
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

    # Multi-trial-per-PC prevention: this only runs for a genuinely new
    # user (an existing email already returned above), so at this point
    # we're about to hand out a brand new trial. If this exact machine has
    # already claimed a trial under a different email, refuse rather than
    # let someone farm unlimited 7-day trials by re-registering with new
    # addresses. Deliberately vague in the response — we don't confirm or
    # deny whose trial it was.
    if body.device_fingerprint:
        existing_device = (
            db.query(models.License)
            .filter(models.License.device_fingerprint == body.device_fingerprint)
            .first()
        )
        if existing_device:
            raise HTTPException(
                403,
                "A free trial has already been used on this device. "
                "Please subscribe to continue, or contact support if you "
                "believe this is a mistake.",
            )

    lic = models.License(
        user_id=user.id,
        status="trial",
        trial_ends_at=_now() + timedelta(days=TRIAL_DAYS),
        device_fingerprint=body.device_fingerprint,
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


def _check_in_device(db: Session, lic: models.License, fingerprint: str) -> bool:
    """Registers `fingerprint` against `lic` if there's room under its
    tier's device limit, or just refreshes last_seen if it's already
    registered. Returns True if the device is (now) allowed, False if a
    *new* fingerprint would push the license over its tier's limit —
    caller is responsible for turning False into a 409 and NOT committing
    any license-validity side effects in that case.
    """
    existing = (
        db.query(models.LicenseDevice)
        .filter(
            models.LicenseDevice.license_id == lic.id,
            models.LicenseDevice.device_fingerprint == fingerprint,
        )
        .first()
    )
    if existing:
        existing.last_seen = _now()
        db.commit()
        return True

    limit = TIER_DEVICE_LIMIT.get(lic.tier or DEFAULT_TIER, 1)
    current_count = (
        db.query(models.LicenseDevice)
        .filter(models.LicenseDevice.license_id == lic.id)
        .count()
    )
    if current_count >= limit:
        return False

    db.add(models.LicenseDevice(license_id=lic.id, device_fingerprint=fingerprint))
    db.commit()
    return True


@app.post("/license/check")
@limiter.limit("60/minute")
def check_license(request: Request, body: LicenseCheckRequest, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.license_key == body.license_key).first()
    if not lic:
        raise HTTPException(404, "Unknown license key")

    # Device-limit enforcement only applies to paid tiers actually in use —
    # a trial license has no device_fingerprint concept beyond the
    # single-farm-prevention field on License itself, so skip it while
    # trialing (and if the client didn't send a fingerprint at all, treat
    # this as an older client and don't newly enforce anything on it).
    if body.device_fingerprint and lic.status != "trial":
        allowed = _check_in_device(db, lic, body.device_fingerprint)
        if not allowed:
            # NOTE: deliberately a raw JSONResponse, not `raise
            # HTTPException(detail=...)` — FastAPI nests HTTPException's
            # detail under a "detail" key, but the desktop client
            # (license_client.py) reads `tier` off the TOP LEVEL of the
            # 409 body. Keep this in sync with that client contract.
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "This key is already active on as many devices as its tier allows.",
                    "tier": lic.tier or DEFAULT_TIER,
                },
            )

    valid = _license_is_valid(lic)
    return {
        "valid": valid,
        "status": lic.status,
        "expires_at": _expiry_for(lic),
        "grace_days": GRACE_DAYS,
        "tier": lic.tier or DEFAULT_TIER,
    }


@app.post("/billing/create-subscription")
def create_subscription(body: CreateSubscriptionRequest, db: Session = Depends(get_db)):
    tier = body.tier if body.tier in TIER_DEVICE_LIMIT else DEFAULT_TIER
    interval = body.plan if body.plan in ("monthly", "yearly") else "monthly"
    plan_id = RAZORPAY_PLAN_IDS.get(tier, {}).get(interval)

    if not rzp_client or not plan_id:
        raise HTTPException(500, f"Billing isn't configured for the '{tier}/{interval}' plan yet")

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
            # Only reuse the pending subscription if it's for the SAME plan
            # the user is asking for now — otherwise someone who started
            # checkout on silver-monthly and then picked gold-yearly would
            # get handed the stale silver-monthly checkout link instead.
            if existing.get("status") in ("created", "authenticated", "pending") and existing.get("plan_id") == plan_id:
                return {"short_url": existing["short_url"], "subscription_id": existing["id"]}
        except Exception:
            pass  # couldn't fetch it (deleted/invalid) — fall through and create a fresh one

    subscription = rzp_client.subscription.create({
        "plan_id": plan_id,
        "customer_notify": 1,
        "total_count": RAZORPAY_TOTAL_COUNT.get(interval, 120),
        "notes": {"license_key": lic.license_key, "plan": interval, "tier": tier},
    })

    lic.razorpay_subscription_id = subscription["id"]
    lic.plan = interval
    lic.tier = tier
    lic.status = "past_due"  # becomes "active" once the webhook confirms the first charge
    db.commit()

    return {"short_url": subscription["short_url"], "subscription_id": subscription["id"]}


class CancelSubscriptionRequest(BaseModel):
    license_key: str


@app.post("/billing/cancel-subscription")
def cancel_subscription(body: CancelSubscriptionRequest, db: Session = Depends(get_db)):
    """Lets the app or landing page cancel a subscription without going
    through the Razorpay dashboard. Mirrors what already happens when a
    customer cancels their UPI mandate directly — the license stays valid
    through whatever period they already paid for (current_period_end),
    it just won't auto-renew. The webhook (subscription.cancelled) will
    also fire and re-confirm this, so this is safe to call even if the
    webhook is briefly delayed."""
    lic = db.query(models.License).filter(models.License.license_key == body.license_key).first()
    if not lic:
        raise HTTPException(404, "Unknown license key")

    if not lic.razorpay_subscription_id:
        raise HTTPException(400, "This license has no active subscription to cancel.")

    if lic.status == "cancelled":
        return {"status": "cancelled", "current_period_end": _aware(lic.current_period_end)}

    if not rzp_client:
        raise HTTPException(500, "Billing isn't configured on the server")

    try:
        # cancel_at_cycle_end=1: don't yank access immediately, let it run
        # out the period they already paid for — matches how a bank-side
        # mandate cancellation behaves, and what the refund policy promises.
        rzp_client.subscription.cancel(lic.razorpay_subscription_id, {"cancel_at_cycle_end": 1})
    except Exception as e:
        raise HTTPException(502, f"Razorpay couldn't cancel this subscription: {e}")

    lic.status = "cancelled"
    db.commit()

    mailer.send_subscription_cancelled(lic.user.email if lic.user else None, _aware(lic.current_period_end))

    return {"status": "cancelled", "current_period_end": _aware(lic.current_period_end)}


@app.get("/license/devices")
@limiter.limit("30/minute")
def list_devices(request: Request, license_key: str, db: Session = Depends(get_db)):
    """Self-serve view of which devices currently hold a slot on this key,
    so the desktop app's "This device" panel can show the user what to
    deactivate instead of just quoting a raw limit number."""
    lic = db.query(models.License).filter(models.License.license_key == license_key).first()
    if not lic:
        raise HTTPException(404, "Unknown license key")

    devices = (
        db.query(models.LicenseDevice)
        .filter(models.LicenseDevice.license_id == lic.id)
        .order_by(models.LicenseDevice.last_seen.desc())
        .all()
    )
    limit = TIER_DEVICE_LIMIT.get(lic.tier or DEFAULT_TIER, 1)
    return {
        "tier": lic.tier or DEFAULT_TIER,
        "device_limit": limit,
        "devices": [
            {
                "device_fingerprint": d.device_fingerprint,
                "first_seen": _aware(d.first_seen),
                "last_seen": _aware(d.last_seen),
            }
            for d in devices
        ],
    }


@app.post("/license/devices/deactivate")
@limiter.limit("30/minute")
def deactivate_device(request: Request, body: DeactivateDeviceRequest, db: Session = Depends(get_db)):
    """Frees up a device slot on a key — e.g. after retiring/reformatting a
    machine — without needing to go through support. Anyone who has the
    license_key can call this (same trust model as every other endpoint
    here: the key itself is the credential), so the desktop app is free to
    expose it directly from the Account & Billing page.
    """
    lic = db.query(models.License).filter(models.License.license_key == body.license_key).first()
    if not lic:
        raise HTTPException(404, "Unknown license key")

    device = (
        db.query(models.LicenseDevice)
        .filter(
            models.LicenseDevice.license_id == lic.id,
            models.LicenseDevice.device_fingerprint == body.device_fingerprint,
        )
        .first()
    )
    if not device:
        raise HTTPException(404, "That device isn't registered on this license")

    db.delete(device)
    db.commit()
    return {"ok": True}


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

    # Idempotency: Razorpay redelivers webhooks (timeouts, manual redelivery
    # from their dashboard), and re-applying an already-processed event can
    # do real damage — e.g. a stale redelivered "halted" landing after
    # we've already recovered would incorrectly knock a live subscription
    # back to past_due. event_hash = sha256(raw body) is the one dedup key
    # guaranteed unique per distinct delivery, since Razorpay doesn't
    # reliably include its own event id across every event type.
    event_hash = hashlib.sha256(raw_body).hexdigest()
    already_seen = db.query(models.WebhookEvent).filter(models.WebhookEvent.event_hash == event_hash).first()
    if already_seen:
        return {"ok": True, "duplicate": True}

    payload = await request.json()
    event = payload.get("event", "")
    entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    subscription_id = entity.get("id")

    log_entry = models.WebhookEvent(
        event_hash=event_hash,
        event_type=event,
        subscription_id=subscription_id,
        payload=raw_body.decode(errors="ignore")[:20000],  # cap size, this is an audit trail not a blob store
        processed=False,
    )
    db.add(log_entry)
    db.commit()

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
        was_already_past_due = lic.status == "past_due"
        lic.status = "past_due"
        if not was_already_past_due and lic.user:
            # Only email on the transition into past_due, not every retry
            # webhook Razorpay might send for the same ongoing failure.
            mailer.send_payment_failed(lic.user.email, _aware(lic.current_period_end))
    elif event == "subscription.completed":
        lic.status = "cancelled"

    log_entry.processed = True
    db.commit()
    return {"ok": True}
