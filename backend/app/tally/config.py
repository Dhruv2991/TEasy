"""
Ledger name configuration for Tally voucher generation.
"""

import json
import os
from ..paths import get_data_dir

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


def _config_path() -> str:
    return os.path.join(get_data_dir(), "tally_config.json")


def get_tally_config() -> dict:
    path = _config_path()
    if not os.path.exists(path):
        save_tally_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError):
        loaded = {}

    merged = dict(DEFAULT_CONFIG)
    merged.update(loaded)
    return merged


def save_tally_config(config: dict) -> dict:
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)

    os.makedirs(os.path.dirname(_config_path()), exist_ok=True)
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    return merged