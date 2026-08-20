# TEasy License Service

The one centrally-hosted piece of TEasy. Answers one question for the
desktop app: "is this license_key currently paid up?"

## Deploy (Railway, simplest option)

1. Push this folder as its own repo (or a subfolder Railway can point at).
2. New Railway project -> Deploy from repo -> add a Postgres plugin ->
   Railway auto-sets `DATABASE_URL` for you.
3. Add env vars from `.env.example` (Razorpay keys, `RAZORPAY_PLAN_ID`,
   `RAZORPAY_WEBHOOK_SECRET`).
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Note the public URL Railway gives you (e.g. `https://teasy-license.up.railway.app`)
   — you'll need it in both the landing page and the desktop app's build config.

Render works the same way (Web Service + a free Postgres instance).

## Razorpay setup (one-time, in the Razorpay dashboard)

1. **Plan**: Subscriptions -> Plans -> create a monthly plan at your price.
   Copy its `plan_id` into `RAZORPAY_PLAN_ID`.
2. **Webhook**: Settings -> Webhooks -> Add New Webhook
   - URL: `https://<your-deployed-service>/billing/webhook`
   - Secret: generate one, put it in `RAZORPAY_WEBHOOK_SECRET`
   - Subscribe to these events: `subscription.charged`, `subscription.cancelled`,
     `subscription.halted`, `subscription.pending`, `subscription.completed`
3. Switch from test keys to live keys when you're ready to charge real cards
   — same plan_id typically needs recreating in live mode.

## API

- `POST /trial/start {email}` -> `{license_key, status, expires_at}` — call
  this once, from the landing page or the desktop app's first-run screen.
- `POST /license/check {license_key}` -> `{valid, status, expires_at, grace_days}`
  — the desktop app calls this on every startup.
- `POST /billing/create-subscription {license_key}` -> `{short_url}` —
  open `short_url` in the browser to let the user pay; Razorpay's webhook
  does the rest.
- `POST /billing/webhook` — Razorpay calls this, you don't.

## Notes

- No hardware locking, no device fingerprinting — one license_key can in
  practice run on more than one machine. If that becomes a real problem
  later, add a `last_seen_ip`/device count check to `/license/check`; it's
  not built in now because it adds support burden for very little abuse
  prevention at this stage.
- `GRACE_DAYS` here is just metadata returned to the app — the actual
  offline grace-period enforcement happens locally, in the desktop app
  (`backend/app/security/license_client.py`), since that's what runs while
  the user has no internet.
