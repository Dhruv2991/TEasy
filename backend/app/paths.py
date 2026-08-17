"""
Resolves where TEasy stores its data (SQLite DB, uploaded images, processed
crops).

- In normal `python`/`uvicorn` dev mode: backend/data/  (same as before)
- In the packaged .exe (PyInstaller): PyInstaller unpacks the app into a
  temporary folder that is DELETED on exit, so we must NOT store data there
  or you'd lose everything every time you close the app. Instead we use the
  standard Windows per-user app-data folder:
      C:\\Users\\<you>\\AppData\\Local\\TEasy\\data
  This also means the database and uploaded photos survive updates/reinstalls
  of the .exe itself.
"""
import os
import sys


def get_data_dir() -> str:
    if getattr(sys, "frozen", False):
        # Running as a packaged .exe
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(base, "TEasy", "data")
    else:
        # Running from source (dev mode)
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(backend_dir, "data")

    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, "documents"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "processed"), exist_ok=True)
    return data_dir


def get_frontend_dir() -> str | None:
    """Location of the built frontend (frontend/dist) bundled into the exe."""
    if getattr(sys, "frozen", False):
        # PyInstaller exposes the extracted bundle dir as sys._MEIPASS
        bundled = os.path.join(sys._MEIPASS, "frontend_dist")
        return bundled if os.path.isdir(bundled) else None
    else:
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dev_dist = os.path.join(os.path.dirname(backend_dir), "frontend", "dist")
        return dev_dist if os.path.isdir(dev_dist) else None
