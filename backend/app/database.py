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
    if "transactions" not in inspector.get_table_names():
        return  # fresh DB, create_all() will have already made it correctly

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
        "debit": "FLOAT DEFAULT 0.0",
        "credit": "FLOAT DEFAULT 0.0",
        "narration": "TEXT",
        "approved_at": "DATETIME",
        "reconciliation_status": "VARCHAR",
        "matched_transaction_id": "INTEGER",
    }
    with engine.begin() as conn:
        for col_name, col_def in additions.items():
            if col_name not in existing_cols:
                conn.execute(sa.text(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_def}"))
