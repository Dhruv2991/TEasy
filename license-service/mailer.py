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


def send_already_registered(to_email: str, license_key: str, status: str, expires_at) -> bool:
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
