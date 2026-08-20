import os
import sys
from dotenv import load_dotenv
from app.routers import bank

# In dev mode this reads backend/.env as before. In the packaged .exe, the
# working directory is unpredictable (wherever the user launched it from),
# so also check the app-data folder — that's where the README below tells
# users to put their .env when running the .exe.
load_dotenv()
if getattr(sys, "frozen", False):
    load_dotenv(os.path.join(os.environ.get("LOCALAPPDATA", ""), "TEasy", ".env"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import engine, Base, run_lightweight_migrations
from .routers import documents, transactions, gstr2b, tally, activity, settings as settings_router, reports, license as license_router
from .paths import get_data_dir, get_frontend_dir
from .security import license_client

Base.metadata.create_all(bind=engine)
run_lightweight_migrations()

app = FastAPI(title="TEasy - Phase 1 (Sales OCR Pipeline)")

# Dev mode (vite on :5173) needs CORS; the packaged .exe serves everything
# from one origin so CORS doesn't matter there, but leaving this open is
# harmless since the app only ever binds to localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = get_data_dir()
app.mount("/files", StaticFiles(directory=DATA_DIR), name="files")

app.include_router(documents.router)
app.include_router(transactions.router)
app.include_router(gstr2b.router)
app.include_router(tally.router)
app.include_router(activity.router)
app.include_router(settings_router.router)
app.include_router(bank.router)
app.include_router(reports.router)
app.include_router(license_router.router)

@app.get("/api/status")
def status():
    return {"status": "ok", "service": "TEasy Phase 1"}


# Paths the frontend needs to reach even while unlicensed: the license
# screens themselves, the basic health check, and static assets (JS/CSS/
# images) so the React app can actually load and render the activation
# screen instead of a blank page.
_LICENSE_EXEMPT_PREFIXES = ("/api/license", "/api/status", "/assets", "/files")


@app.middleware("http")
async def enforce_license(request, call_next):
    path = request.url.path
    if path == "/" or path.startswith(_LICENSE_EXEMPT_PREFIXES) or not path.startswith("/api"):
        # Non-API requests (the built frontend's index.html, JS, CSS) are
        # always allowed through — the React app itself shows the
        # activation screen and blocks feature use when unlicensed. Only
        # API routes that touch real functionality are gated here.
        return await call_next(request)

    # Fast, cache-only check — this runs on every API request, so it must
    # never itself make a network call. The cache is kept fresh by the
    # frontend's periodic call to GET /api/license/status (the full check,
    # in routers/license.py), plus the check at app startup.
    if not license_client.is_valid_fast():
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=402, content={"detail": "license_invalid", "license": license_client.get_status()})

    return await call_next(request)


# Serve the built React frontend (frontend/dist), if present, so the packaged
# .exe is a single process: FastAPI serves both the API and the UI.
_frontend_dir = get_frontend_dir()
if _frontend_dir:
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
else:
    @app.get("/")
    def root():
        return {
            "status": "ok",
            "service": "TEasy Phase 1 (dev mode - no built frontend found, run the vite dev server separately)",
        }
