"""
Ledger name configuration for Tally voucher generation.

Stored per-company: switching the active company (see settings.py's
get_active_company_id) now switches which saved Tally Integration config
(host/port/company name/ledger names) is in effect too, instead of every
company sharing one global tally_config.json regardless of which one is
selected.
"""

import json
import os
from ..paths import get_data_dir
from ..settings import get_active_company_id

DEFAULT_CONFIG = {
    "company_name": "",
    "sales_ledger": "Sales Account",
    "purchase_ledger": "Purchase Account",
    "output_cgst_ledger": "Output CGST",
    "output_sgst_ledger": "Output SGST",
    "output_igst_ledger": "Output IGST",
    "input_cgst_ledger": "Input CGST",
    "input_sgst_ledger": "Input SGST",
    "input_igst_ledger": "Input IGST",
    "cash_ledger": "Cash",
    "round_off_ledger": "ROUNDOFF",
    "bank_ledger": "Bank Account",
}


def _config_path(company_id: int | None = None) -> str:
    """
    One config file per company (tally_config_<id>.json), so each
    company's Tally host/port/company-name/ledger-name settings are
    independent. `company_id=None` (or a fresh install with no companies
    yet) falls back to the original single tally_config.json — this
    covers pre-existing installs that only ever had one company, so their
    saved settings aren't orphaned by this change; the first time that
    install has more than one company and switches the active one, each
    additional company simply starts from DEFAULT_CONFIG until its own
    Tally Integration page is filled in and saved.
    """
    if company_id is None:
        company_id = get_active_company_id()
    if company_id is None:
        return os.path.join(get_data_dir(), "tally_config.json")
    return os.path.join(get_data_dir(), f"tally_config_{company_id}.json")


def get_tally_config(company_id: int | None = None) -> dict:
    path = _config_path(company_id)
    if not os.path.exists(path):
        save_tally_config(DEFAULT_CONFIG, company_id=company_id)
        return dict(DEFAULT_CONFIG)

    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError):
        loaded = {}

    merged = dict(DEFAULT_CONFIG)
    merged.update(loaded)
    return merged


def save_tally_config(config: dict, company_id: int | None = None) -> dict:
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)

    path = _config_path(company_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    return merged
