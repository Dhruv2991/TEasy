"""
Ledger name configuration for Tally voucher generation.

Tally identifies accounts purely by LEDGER NAME (exact string match) — there's
no stable numeric ID to key off like most databases. That means the ledger
names below must match your actual Tally company's ledger names character
for character, or Tally will reject the voucher (or worse, silently create a
new ledger with that name if auto-creation is on).

Edit config/tally_config.json (created on first run with these defaults) to
match your company's actual ledger names before pushing anything to Tally.
Party ledgers (customer/supplier names) are NOT listed here — those come
straight from each transaction's "party" field, so they must match your
Tally party ledgers exactly too (this is the "ledger matching" step flagged
as future work in the original project design — for now, a mismatch here
means Tally will error on that voucher rather than silently misfiling it).
"""
import json
import os
from ..paths import get_data_dir

DEFAULT_CONFIG = {
    "company_name": "Sarvotham Traders 2026-27",
    "sales_ledger": "Sales Account",
    "purchase_ledger": "Purchase Account",
    "output_cgst_ledger": "Output CGST",
    "output_sgst_ledger": "Output SGST",
    "output_igst_ledger": "Output IGST",
    "input_cgst_ledger": "Input CGST",
    "input_sgst_ledger": "Input SGST",
    "input_igst_ledger": "Input IGST",
    "cash_ledger": "Cash",
    "round_off_ledger": "Round Off",
}


def _config_path() -> str:
    return os.path.join(get_data_dir(), "tally_config.json")


def get_tally_config() -> dict:
    path = _config_path()
    if not os.path.exists(path):
        save_tally_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(loaded)
    return merged


def save_tally_config(config: dict) -> dict:
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    return merged
