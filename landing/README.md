# TEasy landing page

Plain HTML/CSS/JS — no build step, no framework. Deploys to Vercel as a
static site plus one tiny serverless function for config (`api/config.js`),
so nothing is hardcoded in the repo.

## Deploy to Vercel

1. Push this `landing/` folder to its own GitHub repo (or a repo where this
   is the project root Vercel points at).
2. vercel.com → New Project → import that repo → Vercel auto-detects the
   static site + the `api/` function → Deploy.
3. You'll get a `*.vercel.app` URL immediately. Add your own domain later
   under Project → Settings → Domains whenever you buy one — nothing else
   here needs to change when you do.

## Before it works for real

Go to Vercel → Project → Settings → Environment Variables and set:

- `LICENSE_SERVICE_URL` — the URL of the deployed license service from
  step 2 (e.g. `https://teasy-vusw.onrender.com`). Until this is set to a
  real, reachable URL, the "Start free trial" form will show a clear
  "not configured yet" message instead of failing silently.
- `WINDOWS_DOWNLOAD_URL` — wherever you end up hosting the installer
  (e.g. a GitHub Releases asset link) once step 5 (packaging) is done.
  Until then the download button is a harmless dead link (`#`).

These are read at request time by `api/config.js`, which serves
`window.TEASY_CONFIG` to the page — no values are committed to the repo,
and changing them in Vercel takes effect within a few minutes with no
redeploy needed.

## CORS note

The license service reads its allowed browser origin(s) from the
`ALLOWED_ORIGINS` env var on its own host (see
`license-service/.env.example`) — set it to this landing page's real
domain (e.g. `https://t-easy.vercel.app`). Nothing is hardcoded there
either.

## What's on the page

- Hero with a 7-day free trial signup (email → `POST /trial/start` on the
  license service → shows the license key + a "download for Windows" link)
- How it works, pricing (single ₹999/mo plan, links back to the trial form
  — the actual Razorpay checkout happens inside the desktop app via
  `POST /api/license/create-subscription`, once someone's on a trial and
  has the app installed)
- No routing, no state beyond the trial form — everything else is static
