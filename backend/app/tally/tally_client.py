"""
Sends XML to Tally Prime's built-in HTTP/XML server and parses responses.
"""

import re
import time
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape, unescape as xml_unescape
import requests

from ..settings import get_settings


def _tally_url() -> str:
    s = get_settings()
    return f"http://{s.get('tally_host', 'localhost')}:{s.get('tally_port', 9000)}"


class TallyConnectionError(Exception):
    pass


class TallyVoucherError(Exception):
    pass


def test_connection() -> bool:
    """True if Tally's HTTP server responds at all."""
    try:
        resp = requests.post(_tally_url(), data="<ENVELOPE></ENVELOPE>", timeout=5)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def send_voucher_xml(xml: str) -> dict:
    """
    POSTs voucher XML to Tally and returns a parsed summary:
    {"created": int, "altered": int, "errors": int, "error_message": str|None, "raw_response": str}
    """
    try:
        resp = requests.post(_tally_url(), data=xml.encode("utf-8"), timeout=30)
    except requests.exceptions.RequestException as e:
        url = _tally_url()
        raise TallyConnectionError(
            f"Could not reach Tally at {url}. Make sure Tally Prime is running, "
            f"the company is open, and its HTTP/XML server is enabled "
            f"(F1 > Settings > Connectivity > act as Server, matching port). "
            f"Underlying error: {e}"
        )

    text = resp.text

    created = _extract_int(text, "CREATED")
    altered = _extract_int(text, "ALTERED")
    errors = _extract_int(text, "ERRORS")

    error_message = None
    line_error_match = re.search(r"<LINEERROR>(.*?)</LINEERROR>", text, re.DOTALL | re.IGNORECASE)
    if line_error_match:
        # Tally's response is XML, so any apostrophes/quotes/ampersands in
        # the message come back as &apos;/&quot;/&amp; — decode them, or
        # the user sees literal "&apos;Purchase&apos;" instead of 'Purchase'.
        error_message = xml_unescape(line_error_match.group(1).strip(), {"&apos;": "'", "&quot;": '"'})
    elif errors and errors > 0:
        error_message = "Tally reported an error during voucher import but did not specify details."

    return {
        "created": created or 0,
        "altered": altered or 0,
        "errors": errors or 0,
        "error_message": error_message,
        "raw_response": text,
    }


def _extract_int(text: str, tag: str) -> int | None:
    m = re.search(rf"<{tag}>(\d+)</{tag}>", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


_LEDGER_CACHE: dict = {"company": None, "at": 0.0, "ledgers": []}
_LEDGER_CACHE_TTL_SECONDS = 60


_LEDGER_LIST_REQUEST = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>TEasyLedgerList</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="TEasyLedgerList" ISINITIALIZE="Yes">
            <TYPE>Ledger</TYPE>
            <FETCH>NAME, PARENT</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""


def fetch_ledgers(force_refresh: bool = False) -> list[dict]:
    """
    Returns the live list of ledgers in the current Tally company as
    [{"name": ..., "parent": ...}, ...]. Cached briefly per-company.
    """
    from .config import get_tally_config

    company = get_tally_config().get("company_name", "")

    now = time.time()
    if (
        not force_refresh
        and _LEDGER_CACHE["company"] == company
        and now - _LEDGER_CACHE["at"] < _LEDGER_CACHE_TTL_SECONDS
    ):
        return _LEDGER_CACHE["ledgers"]

    xml = _LEDGER_LIST_REQUEST.format(company=xml_escape(company))
    try:
        resp = requests.post(_tally_url(), data=xml.encode("utf-8"), timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise TallyConnectionError(f"Could not fetch ledger list from Tally: {e}")

    ledgers = []
    clean_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", resp.text)
    try:
        root = ET.fromstring(clean_text)
        for led in root.iter("LEDGER"):
            name = led.get("NAME") or (led.findtext("NAME") or "")
            parent_el = led.find("PARENT")
            parent = parent_el.text if parent_el is not None else ""
            if name:
                ledgers.append({"name": name.strip(), "parent": (parent or "").strip()})
    except ET.ParseError:
        for m in re.finditer(
            r'<LEDGER NAME="([^"]+)"[^>]*>.*?<PARENT[^>]*>([^<]*)</PARENT>',
            clean_text,
            re.DOTALL,
        ):
            ledgers.append({"name": m.group(1).strip(), "parent": m.group(2).strip()})

    _LEDGER_CACHE["company"] = company
    _LEDGER_CACHE["at"] = now
    _LEDGER_CACHE["ledgers"] = ledgers
    return ledgers


_STOCK_ITEM_CACHE: dict = {"company": None, "at": 0.0, "items": []}
_STOCK_ITEM_CACHE_TTL_SECONDS = 60


_STOCK_ITEM_LIST_REQUEST = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>TEasyStockItemList</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="TEasyStockItemList" ISINITIALIZE="Yes">
            <TYPE>StockItem</TYPE>
            <FETCH>NAME, BASEUNITS</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""


def fetch_stock_items(force_refresh: bool = False) -> list[dict]:
    """
    Returns the live list of stock items in the current Tally company as
    [{"name": ..., "base_unit": ...}, ...]. Cached briefly per-company, same
    pattern as fetch_ledgers — used so an item-wise voucher only creates a
    stock item master for names that don't already exist, instead of
    blindly re-creating (and risking altering) every item on every push.
    """
    from .config import get_tally_config

    company = get_tally_config().get("company_name", "")

    now = time.time()
    if (
        not force_refresh
        and _STOCK_ITEM_CACHE["company"] == company
        and now - _STOCK_ITEM_CACHE["at"] < _STOCK_ITEM_CACHE_TTL_SECONDS
    ):
        return _STOCK_ITEM_CACHE["items"]

    xml = _STOCK_ITEM_LIST_REQUEST.format(company=xml_escape(company))
    try:
        resp = requests.post(_tally_url(), data=xml.encode("utf-8"), timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise TallyConnectionError(f"Could not fetch stock item list from Tally: {e}")

    items = []
    clean_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", resp.text)
    try:
        root = ET.fromstring(clean_text)
        for si in root.iter("STOCKITEM"):
            name = si.get("NAME") or (si.findtext("NAME") or "")
            unit_el = si.find("BASEUNITS")
            base_unit = unit_el.text if unit_el is not None else ""
            if name:
                items.append({"name": name.strip(), "base_unit": (base_unit or "").strip()})
    except ET.ParseError:
        for m in re.finditer(r'<STOCKITEM NAME="([^"]+)"', clean_text):
            items.append({"name": m.group(1).strip(), "base_unit": ""})

    _STOCK_ITEM_CACHE["company"] = company
    _STOCK_ITEM_CACHE["at"] = now
    _STOCK_ITEM_CACHE["items"] = items
    return items