"""
SQLite database setup for TEasy - Phase 1 (Sales OCR pipeline).
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .paths import get_data_dir

DB_PATH = os.path.join(get_data_dir(), "tally_ai.db")

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_lightweight_migrations():
    """
    SQLite + SQLAlchemy's create_all() only creates missing TABLES, not
    missing COLUMNS on tables that already exist — so adding a new column to
    a model (like tally_status/tally_error) does nothing for an existing
    tally_ai.db without this. This adds any columns that are in the model but
    missing from the actual table, so existing data survives updates instead
    of requiring you to delete the whole database.
    """
    import sqlalchemy as sa

    inspector = sa.inspect(engine)

    # Backfill `companies` itself first, and unconditionally (regardless of
    # which branch below we take) — this table can pre-date columns like
    # gstin/state_code/tally_company_name/default_gst_rate/archived if it
    # was first created by an earlier version of the Company model, the
    # same way `transactions` can pre-date tally_status etc. Skipping this
    # was a real bug: create_all() only creates the table if it's entirely
    # missing, so an existing-but-stale `companies` table silently kept
    # missing columns forever, and every INSERT (e.g. POST /companies)
    # crashed with "table companies has no column named gstin" instead of
    # ever getting a chance to self-heal.
    if "companies" in inspector.get_table_names():
        existing_company_cols = {c["name"] for c in inspector.get_columns("companies")}
        company_additions = {
            "gstin": "VARCHAR",
            "state_code": "VARCHAR",
            "default_gst_rate": "FLOAT DEFAULT 18.0",
            "tally_company_name": "VARCHAR",
            "archived": "BOOLEAN DEFAULT 0",
        }
        with engine.begin() as conn:
            for col_name, col_def in company_additions.items():
                if col_name not in existing_company_cols:
                    conn.execute(sa.text(f"ALTER TABLE companies ADD COLUMN {col_name} {col_def}"))

    if "transactions" not in inspector.get_table_names():
        # Fresh DB — create_all() already made every table correctly
        # (including the new `companies` table), so there are no columns
        # to backfill. But a fresh install still needs a default company
        # to exist and be marked active before anything else can work, so
        # this still needs to run rather than returning early.
        _ensure_default_company_and_backfill()
        return

    existing_cols = {c["name"] for c in inspector.get_columns("transactions")}
    additions = {
        "tally_status": "VARCHAR DEFAULT 'NOT_SENT'",
        "tally_error": "VARCHAR",
        "possible_duplicate": "BOOLEAN DEFAULT 0",
        "cess": "FLOAT DEFAULT 0.0",
        "gst_rate_uncertain": "BOOLEAN DEFAULT 0",
        "manually_reviewed": "BOOLEAN DEFAULT 0",
        "rate_breakdown": "TEXT",
        "rate_breakdown_source": "VARCHAR",
        "items": "TEXT",
        "debit": "FLOAT DEFAULT 0.0",
        "credit": "FLOAT DEFAULT 0.0",
        "narration": "TEXT",
        "approved_at": "DATETIME",
        "reconciliation_status": "VARCHAR",
        "matched_transaction_id": "INTEGER",
        "balance": "FLOAT",
        "party_gstin": "VARCHAR",
        "party_state": "VARCHAR",
        "company_id": "INTEGER",
    }
    with engine.begin() as conn:
        for col_name, col_def in additions.items():
            if col_name not in existing_cols:
                conn.execute(sa.text(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_def}"))

    existing_doc_cols = {c["name"] for c in inspector.get_columns("documents")}
    if "company_id" not in existing_doc_cols:
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE documents ADD COLUMN company_id INTEGER"))

    _ensure_default_company_and_backfill()


def _ensure_default_company_and_backfill():
    """
    One-time bootstrap for the multi-company feature: if no Company exists
    yet, create a "Default Company" and assign every pre-existing
    Document/Transaction (company_id still NULL, because they predate this
    feature) to it — mirroring what Tally itself does when you introduce
    multi-company on a file that only ever had one: your existing data
    becomes "Company 1", nothing disappears or needs re-entering.

    Safe to call on every startup — it's a no-op once at least one company
    already exists (which it will, after the very first run following this
    upgrade) and once no NULL company_id rows remain.
    """
    import sqlalchemy as sa
    from .settings import get_settings, save_settings

    with engine.begin() as conn:
        company_count = conn.execute(sa.text("SELECT COUNT(*) FROM companies")).scalar()
        if company_count == 0:
            # Carry over whatever GST profile was already saved in
            # settings.json (pre-multi-company), so the default company
            # starts pre-filled instead of blank.
            s = get_settings()
            conn.execute(
                sa.text(
                    "INSERT INTO companies (name, gstin, state_code, default_gst_rate, tally_company_name, "
                    "created_at, archived) VALUES (:name, :gstin, :state_code, :rate, :tally_name, :now, 0)"
                ),
                {
                    "name": s.get("company_name") or "Default Company",
                    "gstin": s.get("gstin") or None,
                    "state_code": s.get("state_code") or None,
                    "rate": s.get("default_gst_rate") or 18.0,
                    "tally_name": s.get("company_name") or None,
                    "now": __import__("datetime").datetime.utcnow().isoformat(),
                },
            )
            default_id = conn.execute(sa.text("SELECT id FROM companies ORDER BY id ASC LIMIT 1")).scalar()
        else:
            default_id = conn.execute(sa.text("SELECT id FROM companies ORDER BY id ASC LIMIT 1")).scalar()

        conn.execute(sa.text("UPDATE documents SET company_id = :cid WHERE company_id IS NULL"), {"cid": default_id})
        conn.execute(sa.text("UPDATE transactions SET company_id = :cid WHERE company_id IS NULL"), {"cid": default_id})

    # Make sure some company is marked active — if active_company_id was
    # never set (fresh install or pre-multi-company upgrade), default to
    # whichever company ended up as id 1 above.
    s = get_settings()
    if not s.get("active_company_id"):
        with engine.begin() as conn:
            first_id = conn.execute(sa.text("SELECT id FROM companies WHERE archived = 0 ORDER BY id ASC LIMIT 1")).scalar()
        if first_id:
            save_settings({"active_company_id": first_id})
