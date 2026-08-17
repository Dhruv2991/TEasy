# TEasy — Phase 1 (Sales Bill Photo → OCR → Review Dashboard)

This is the first working slice of the full system described in the project
design doc: photo of a sales bill (or a page with multiple bills) → automatic
bill splitting → OCR (Tesseract) → rule-based extraction of party/date/amount/GST
→ a review dashboard where you approve, edit, or reject each transaction.

**Not included yet (by design — this is Phase 1):** Purchase pipeline, GSTR-2B
Excel import, and the Tally Prime integration. Those slot into the same
architecture next; see "Where this goes next" at the bottom.

---

## 1. Prerequisites (install these first)

You need three things installed on your Windows machine:

1. **Python 3.11+** — https://www.python.org/downloads/ (check "Add Python to PATH" during install)
2. **Node.js 18+** — https://nodejs.org/ (LTS version)
3. **A free Groq API key** (this is now the primary extraction engine — much better on handwriting
   than local OCR):
   - Sign up at https://console.groq.com/keys and create an API key
   - In `backend/`, copy `.env.example` to `.env` and paste your key in:
     ```
     GROQ_API_KEY=gsk_your_actual_key_here
     ```
   - Groq's free tier is generous and fast; no credit card needed to start.
4. **(Optional) Tesseract OCR** — kept as an automatic fallback if `GROQ_API_KEY` isn't set, or if a
   single Groq call fails. You can skip this if you're always using Groq:
   - Download the Windows installer from: https://github.com/UB-Mannheim/tesseract/wiki
   - Install it (default path is usually `C:\Program Files\Tesseract-OCR\tesseract.exe`)
   - If you use it, add that folder to your Windows PATH, or set it explicitly in
     `backend/app/ocr/ocr_engine.py`:
     ```python
     import pytesseract
     pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
     ```
5. **VS Code** with the **Python** extension (Microsoft) installed.

---

## 2. Open the project in VS Code

1. Unzip this project folder somewhere, e.g. `C:\Projects\tally-ai`
2. In VS Code: `File → Open Folder…` → select the `tally-ai` folder
3. You'll be working with two terminals side by side (View → Terminal, then click the `+` to split).

---

## 3. Run the backend (Terminal 1)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # on Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

You should see `Uvicorn running on http://127.0.0.1:8000`.
Visit http://localhost:8000/docs to see the interactive API docs (FastAPI's
built-in Swagger UI) — useful for testing upload/approve endpoints directly.

The first run auto-creates `backend/data/tally_ai.db` (SQLite) and the
`documents/` / `processed/` folders for uploaded and cropped bill images.

---

## 4. Run the frontend (Terminal 2)

```bash
cd frontend
npm install
npm run dev
```

Open the printed URL — normally **http://localhost:5173**.

---

## 5. Try it

1. Take a photo of a sales bill (or a page with a few bills on it) with your phone, transfer it to your PC.
2. Drag it onto the upload box in the browser.
3. Within a few seconds the page auto-refreshes and shows the detected bill(s) with extracted
   party / date / invoice number / total / GST%, plus a confidence score.
4. Click **Edit** to fix anything OCR got wrong (very common with handwriting — that's expected
   at this stage), then **Approve**.

Everything is stored in `backend/data/tally_ai.db`. You can inspect it with any
SQLite viewer (e.g. the "SQLite Viewer" VS Code extension) or via `/docs`.

---

## 6. How extraction works now (Groq vision, with Tesseract fallback)

Each detected bill crop is sent directly to a Groq vision-language model
(`backend/app/ocr/groq_vision.py`, currently `qwen/qwen3.6-27b` — Groq
retires model IDs periodically, so if you ever see a `model_not_found` /
404 error, check https://console.groq.com/docs/vision for the current
model name and update `GROQ_VISION_MODEL` in `.env`) with a prompt asking
it to read the bill —
including handwriting — and return structured JSON: party, date, invoice
number, taxable value, GST rate/split, total, plus its own confidence and a
note on anything ambiguous. This is a large jump in accuracy over Tesseract
for handwritten bills since the model is actually reading the bill like a
person would, not just recognizing individual characters.

- If `GROQ_API_KEY` is not set, or a specific call to Groq fails (network
  issue, bad image, etc.), that bill automatically falls back to the old
  Tesseract + regex path so the pipeline never just stops. Check the audit
  log (visible via `/docs` → `GET /documents/{id}`, or a SQLite viewer) to
  see which path was used per bill — the log entry says "(AI/Groq)" or
  "(Tesseract fallback)".
- The extraction prompt lives entirely in `EXTRACTION_PROMPT` inside
  `groq_vision.py` — this is the place to add rules specific to your bills
  (e.g. "party name is usually top-right", common item names, standard GST
  rates your business uses) if you want to improve accuracy further.
- Confidence scores now come from the model's own self-assessment rather
  than an OCR-word-confidence average — still treat anything under ~85% as
  worth a quick glance before approving.
- The bill-splitting (multi-bill-per-photo) step still uses classic edge/contour
  detection in `backend/app/ocr/bill_detector.py` — this part is unchanged and
  works best when bills have a visible border and reasonable lighting.

---

## Project structure

```
tally-ai/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app entrypoint
│   │   ├── database.py          # SQLite/SQLAlchemy setup
│   │   ├── models.py            # ORM tables (documents, bills, ocr, transactions, audit log)
│   │   ├── schemas.py           # Pydantic API schemas
│   │   ├── ocr/
│   │   │   ├── preprocess.py    # deskew + denoise before OCR
│   │   │   ├── bill_detector.py # splits a page into 1+ bill crops
│   │   │   └── ocr_engine.py    # Tesseract wrapper + confidence
│   │   ├── extraction/
│   │   │   └── sales_extractor.py  # OCR text -> structured transaction
│   │   └── routers/
│   │       ├── documents.py     # upload + processing pipeline
│   │       └── transactions.py  # review/edit/approve/reject
│   ├── data/                    # SQLite DB + uploaded/processed images (gitignored)
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── App.jsx               # upload box + review table
    │   └── main.jsx
    └── package.json
```

---

## 7. Building a Windows .exe (one app, no terminals)

This packages the frontend and backend into a single `TEasy.exe` that you
double-click, just like any normal Windows program. Do this **after** the
dev setup above is working.

### 7.1 Build the frontend once

```bash
cd frontend
npm run build
```

This creates `frontend/dist/` — the backend automatically bundles this
folder into the .exe (see `backend/app/paths.py`).

### 7.2 Build the .exe

```bash
cd backend
.venv\Scripts\activate
pip install -r requirements.txt     # now also installs pyinstaller
pyinstaller tally_ai.spec
```

This produces `backend/dist/TEasy/TEasy.exe` plus its supporting files
in the same folder (PyInstaller needs the whole folder together — don't
move just the .exe by itself, copy/zip the whole `TEasy` folder).

### 7.3 Set your Groq key for the .exe

The .exe can't read `backend/.env` (that only exists in your source folder).
Instead, create this file once:

```
%LOCALAPPDATA%\TEasy\.env
```

with the same contents as your `backend/.env`:

```
GROQ_API_KEY=gsk_your_actual_key_here
```

(In File Explorer, paste `%LOCALAPPDATA%\TEasy` into the address bar,
create the folder if it doesn't exist yet, and add a `.env` text file there.)

### 7.4 Run it

Double-click `TEasy.exe`. A console window opens (this is normal — it's
the app's log and the way to stop it), and your browser opens automatically
to the app. Your database and uploaded bill images now live in
`%LOCALAPPDATA%\TEasy\data\` permanently, independent of where the .exe
itself is — safe across re-builds, moves, or reinstalls of the .exe.

To close the app: close the console window.

### 7.5 Distributing it

Zip the whole `backend/dist/TEasy/` folder and share it. On another PC,
Tesseract still needs to be installed separately if you want the Tesseract
fallback path to work there too (Groq alone works fine without it, since
it's a cloud API call). Nothing else needs installing — Python and Node
are NOT required on the machine running the .exe, only on your dev machine
building it.

### 7.6 Build a proper Setup.exe installer

For a polished install experience (Start Menu shortcut, desktop icon,
uninstaller) instead of a folder to unzip:

1. Install **Inno Setup** (free): https://jrsoftware.org/isinfo.php
2. Make sure `backend/dist/TEasy/TEasy.exe` exists (step 7.2 above).
3. Open `backend/teasy_installer.iss` in the Inno Setup Compiler and click
   **Build** — or from the command line:
   ```bash
   cd backend
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" teasy_installer.iss
   ```
4. This produces `backend/installer_output/TEasy-Setup.exe` — a normal
   Windows installer with a Start Menu entry, optional desktop icon, and an
   uninstaller. This is the single file you hand to someone to install TEasy
   on their computer.

---

## Phase 2 — Purchase pipeline (now included)

Click **Scan Purchase** instead of Scan Sales before uploading. Differences
from the Sales pipeline, same architecture otherwise:

- Uses a separate Groq prompt (`PURCHASE_EXTRACTION_PROMPT` in
  `groq_vision.py`) tuned for printed supplier invoices — it reads the
  **supplier** as `party` (not this business), matching Tally's convention
  where a Purchase voucher's party is who you bought from.
- Skips the multi-bill grid/contour splitting used for the sales bill-book —
  a purchase photo is treated as one invoice per photo (the common case).
  If you photograph multiple purchase invoices at once, upload them as
  separate photos for now.
- Falls back to `purchase_extractor.py` (regex-based) if Groq is
  unavailable — this fallback is meaningfully less reliable at reading the
  supplier name than the AI path (printed layouts vary far more than your
  bill-book), so anything that comes through Tesseract fallback is worth a
  closer look before approving.

Review works exactly the same as Sales — approve/edit/reject in the same
table, now with a Type badge (Sales/Purchase) per row.

## Phase 3 — GSTR-2B import (now included)

Click **Import GSTR-2B** and upload the actual `.xlsx` file downloaded from
the GST portal (Services > Returns > Returns Dashboard > GSTR-2B > Download
Excel) — **not a screenshot or photo of the sheet**, the real file. No OCR
is used here; it's a structured government-generated Excel file, so
`backend/app/gstr2b/parser.py` reads it directly with openpyxl, which is
far more reliable than photographing a screen.

What it does:
- Finds the **B2B-CDNR** (and **B2B-CDNRA**, if present) sheet — these hold
  Credit/Debit Notes issued by your suppliers
- Matches columns **by header name**, not position, so it keeps working even
  if the GST portal reorders columns in future export versions
- Correctly handles the portal's merged 2-row header (e.g. "GSTIN of
  supplier" spans two rows in the real file)
- Creates one transaction per note, tagged `CREDIT_NOTE` or `DEBIT_NOTE`
  matching the portal's own "Note type" column — reviewed in the same table
  as Sales/Purchase, with a distinct badge color

One nuance worth knowing: the portal's "Note type" describes the document
**as issued by the supplier**. A supplier's Credit Note is commonly entered
as a Debit Note in *your* Purchase books (it reduces what you owe), but the
correct Tally-side voucher direction can depend on context — this tool
surfaces the note as labeled by the portal and leaves the final call to you
during review, rather than guessing.

## Where this goes next (Phase 4, same architecture)

- **Phase 4 — Tally Prime integration:** Tally exposes an XML-over-HTTP interface on `localhost:9000`
  (enable it in Tally: F1 → Configure → enable ODBC/HTTP Server). An "approved" transaction gets converted
  to Tally's Voucher XML format and POSTed there — this becomes a new `tally/` module that reads
  `Transaction` rows with `status="APPROVED"` and pushes them, then verifies by querying Tally back.
- **Ledger matching / duplicate detection:** add a `parties` table + fuzzy-matching (e.g. `rapidfuzz`)
  against your existing Tally ledger names, exported once via Tally's own XML export.

## Current document sources
- **Sales:** handwritten/physical bill photo upload; existing OCR/vision extraction remains.
- **Purchase:** GSTR-2B **B2B Excel only**. No purchase photo is required or processed. Taxable value, GST and invoice total are read directly from the B2B sheet.
- **Discount:** GSTR-2B **B2B-CDNR/B2B-CDNRA Excel data**, using the existing structured parser.

A single official GSTR-2B Excel export can contain both the B2B purchase invoices and the B2B-CDNR/CDNRA discount/credit/debit-note sheets. The Purchase and Discount screens expose Excel upload rather than purchase-image upload.
