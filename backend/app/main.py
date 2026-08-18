import os
import sys
from dotenv import load_dotenv
import sys
from app.security.hwid import verify_hardware_lock

# Run hardware check before app boot
verify_hardware_lock()

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
from .routers import documents, transactions, gstr2b, tally, activity, settings as settings_router
from .paths import get_data_dir, get_frontend_dir

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


@app.get("/api/status")
def status():
    return {"status": "ok", "service": "TEasy Phase 1"}


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
