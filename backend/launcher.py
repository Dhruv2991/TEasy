"""
Entry point used to build the Windows .exe (via PyInstaller).

Double-clicking the .exe:
  1. Starts the FastAPI/uvicorn server in this same process (serving both
     the API and the built React frontend, see app/main.py).
  2. Opens your default browser to it automatically.
  3. Keeps running until you close the console window (closing it stops
     the server; your data is safe in %LOCALAPPDATA%\\TEasy\\data — see
     app/paths.py).

This file is NOT used in normal dev mode — dev mode still uses
`uvicorn app.main:app --reload` directly, per the README.

IMPORTANT: everything that could fail — including importing app.main
itself — must run INSIDE the try/except below. If an import error (e.g. a
missing dependency that wasn't bundled into the .exe) happens at module
level, PyInstaller's console window closes immediately with no visible
error, which looks like the app "silently crashing". Keeping the import
inside try/except is what lets us print the traceback and pause instead.
"""
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 8000


def _open_browser_when_ready():
    time.sleep(1.5)  # give uvicorn a moment to bind the port
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    try:
        import uvicorn
        from app.main import app

        threading.Thread(target=_open_browser_when_ready, daemon=True).start()
        print("Starting TEasy... your browser will open automatically.")
        print(f"If it doesn't, go to http://{HOST}:{PORT}")
        print("Keep this window open while using TEasy. Close it to stop the app.")
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    except Exception:
        import traceback
        print("\nTEasy hit an error on startup:\n")
        traceback.print_exc()
        print("\nPress Enter to close this window...")
        input()
