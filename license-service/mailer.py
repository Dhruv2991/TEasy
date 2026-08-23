"""
Thin wrapper around Brevo's (formerly Sendinblue) transactional email API.

Used for two emails only:
  - welcome + license key, when a brand-new trial is created
  - "you already have a trial/license" reminder, when someone re-enters an
    email that's already registered (see main.py /trial/start)

If BREVO_API_KEY isn't set (e.g. local dev), sending is skipped and a line
is printed to the console instead, so nothing crashes when testing without
mail configured.
"""
import os

import requests

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "noreply@teasy.in")
SENDER_NAME = os.environ.get("BREVO_SENDER_NAME", "TEasy")

REQUEST_TIMEOUT = 10


def _send(to_email: str, subject: str, html_content: str) -> bool:
    if not BREVO_API_KEY:
        print(f"[mailer] BREVO_API_KEY not set — skipping email to {to_email}: {subject}")
        return False

    try:
        resp = requests.post(
            BREVO_API_URL,
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "sender": {"email": SENDER_EMAIL, "name": SENDER_NAME},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html_content,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        # Never let a mail failure break the trial/license flow — log and
        # move on. The user still gets the license_key back in the API
        # response either way.
        print(f"[mailer] Failed to send to {to_email}: {e}")
        return False


def send_trial_welcome(to_email: str, license_key: str, trial_days: int, expires_at) -> bool:
    subject = "Your TEasy 7-day free trial is active"
    expires_str = expires_at.strftime("%d %b %Y") if expires_at else ""
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #7c3aed;">Welcome to TEasy 👋</h2>
      <p>Your {trial_days}-day free trial is now active{f", and runs until <b>{expires_str}</b>" if expires_str else ""}.</p>
      <p>Your license key:</p>
      <p style="font-family: monospace; background: #f4f4f5; padding: 12px; border-radius: 8px; word-break: break-all;">
        {license_key}
      </p>
      <p>Open TEasy and paste this into the "Activate key" tab if you ever need to reactivate on a new install.</p>
      <p style="color: #64748b; font-size: 13px; margin-top: 32px;">
        Didn't request this? You can safely ignore this email.
      </p>
    </div>
    """
    return _send(to_email, subject, html)


def send_subscription_cancelled(to_email: str | None, valid_until) -> bool:
    if not to_email:
        return False
    subject = "Your TEasy subscription is cancelled"
    valid_str = valid_until.strftime("%d %b %Y") if valid_until else None
    access_line = (
        f"You'll keep full access until <b>{valid_str}</b> — the period you already paid for — then it will end."
        if valid_str
        else "Your access has ended."
    )
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #7c3aed;">Your subscription is cancelled</h2>
      <p>We've cancelled auto-renewal on your TEasy subscription, as requested.</p>
      <p>{access_line}</p>
      <p>Changed your mind? You can resubscribe anytime from inside TEasy or on our website.</p>
      <p style="color: #64748b; font-size: 13px; margin-top: 32px;">
        Didn't request this? Contact us right away at ddhruvgnayak@gmail.com.
      </p>
    </div>
    """
    return _send(to_email, subject, html)


def send_trial_ending_soon(to_email: str, days_left: int, expires_at) -> bool:
    subject = f"Your TEasy trial ends in {days_left} day{'s' if days_left != 1 else ''}"
    expires_str = expires_at.strftime("%d %b %Y") if expires_at else ""
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #7c3aed;">Your trial ends soon</h2>
      <p>Your TEasy free trial ends in <b>{days_left} day{'s' if days_left != 1 else ''}</b>{f" (on {expires_str})" if expires_str else ""}.</p>
      <p>To keep using TEasy without interruption, subscribe from inside the app or from our website — it takes under a minute.</p>
      <p style="color: #64748b; font-size: 13px; margin-top: 32px;">
        Not planning to continue? No action needed — the app will simply stop unlocking after your trial ends.
      </p>
    </div>
    """
    return _send(to_email, subject, html)


def send_payment_failed(to_email: str, retry_by) -> bool:
    subject = "TEasy: your last payment didn't go through"
    retry_str = retry_by.strftime("%d %b %Y") if retry_by else None
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #b3402b;">Payment didn't go through</h2>
      <p>Your last TEasy subscription charge failed — this usually means your card/UPI mandate needs updating.</p>
      <p>{f"Please update your payment method before <b>{retry_str}</b> to avoid losing access." if retry_str else "Please update your payment method to avoid losing access."}</p>
      <p>You can update or retry payment from inside TEasy, or by contacting us at ddhruvgnayak@gmail.com.</p>
    </div>
    """
    return _send(to_email, subject, html)
    subject = "You already have a TEasy license"
    expires_str = expires_at.strftime("%d %b %Y") if expires_at else ""
    if status == "trial":
        status_line = f"Your trial {'runs until' if expires_str else 'is active'} {expires_str}." if expires_str else "Your trial is currently active."
    elif status in ("active", "past_due", "cancelled"):
        status_line = f"Your subscription is currently <b>{status}</b>{f', valid until {expires_str}' if expires_str else ''}."
    else:
        status_line = "Your trial has ended."

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #7c3aed;">You already have a TEasy license</h2>
      <p>This email address already has an account with us. {status_line}</p>
      <p>Your license key:</p>
      <p style="font-family: monospace; background: #f4f4f5; padding: 12px; border-radius: 8px; word-break: break-all;">
        {license_key}
      </p>
      <p>Open TEasy and paste this key into the "Activate key" tab to get back in. If your trial or subscription has ended, you can subscribe from the same screen.</p>
      <p style="color: #64748b; font-size: 13px; margin-top: 32px;">
        Didn't request this? You can safely ignore this email.
      </p>
    </div>
    """
    return _send(to_email, subject, html)
