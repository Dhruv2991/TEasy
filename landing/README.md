# TEasy landing page

Plain HTML/CSS/JS — no build step, no framework. Deploys to Vercel as a
static site with zero config.

## Deploy to Vercel

1. Push this `landing/` folder to its own GitHub repo (or a repo where this
   is the project root Vercel points at).
2. vercel.com → New Project → import that repo → Vercel auto-detects it as
   a static site (no framework, no build command needed) → Deploy.
3. You'll get a `*.vercel.app` URL immediately. Add your own domain later
   under Project → Settings → Domains whenever you buy one — nothing else
   here needs to change when you do.

## Before it works for real

Open `config.js` and set:

- `LICENSE_SERVICE_URL` — the URL of the deployed license service from
  step 2 (e.g. `https://teasy-license.up.railway.app`). Until this is set
  to a real, reachable URL, the "Start free trial" form will fail with a
  network error — that's expected on a fresh deploy.
- `WINDOWS_DOWNLOAD_URL` — wherever you end up hosting the installer
  (e.g. a GitHub Releases asset link) once step 5 (packaging) is done.
  Until then the download button is a harmless dead link.

## CORS note

The license service currently allows requests from any origin
(`allow_origins=["*"]` in `license-service/main.py`), so this page can call
it straight from the browser with no extra setup. Once you have a real
domain for this landing page, it's worth tightening that to just your
domain — see the comment above that line in `license-service/main.py`.

## What's on the page

- Hero with a 7-day free trial signup (email → `POST /trial/start` on the
  license service → shows the license key + a "download for Windows" link)
- How it works, pricing (single ₹999/mo plan, links back to the trial form
  — the actual Razorpay checkout happens inside the desktop app via
  `POST /api/license/create-subscription`, once someone's on a trial and
  has the app installed)
- No routing, no state beyond the trial form — everything else is static
