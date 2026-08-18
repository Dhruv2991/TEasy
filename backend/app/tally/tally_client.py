"""
Sends XML to Tally Prime's built-in HTTP/XML server (must be enabled in Tally:
F1 > Settings > Connectivity > Client/Server configuration > act as Server,
default port 9000) and parses the response for success/failure.

Tally's response to a voucher import is itself XML, shaped roughly like:

<RESPONSE>
  <CREATED>1</CREATED>
  <ALTERED>0</ALTERED>
  <ERRORS>0</ERRORS>
  <LASTVCHID>123</LASTVCHID>
  <LASTMID>123</LASTMID>
</RESPONSE>

or, on failure, includes a <LINEERROR> with a human-readable message (most
commonly "Ledger ... does not exist" when a ledger name doesn't match).
"""
import re
import time
import requests
from xml.etree import ElementTree as ET

from ..settings import get_settings


def _tally_url() -> str:
    s = get_settings()
    return f"http://{s.get('tally_host', 'localhost')}:{s.get('tally_port', 9000)}"


class TallyConnectionError(Exception):
    pass


class TallyVoucherError(Exception):
    pass


def test_connection() -> bool:
    """
    True if Tally's HTTP server responds at all. Tally answers even a
    malformed/empty request with *some* XML rather than a connection error,
    so we just check we get a response, not that it's meaningful.
    """
    try:
        resp = requests.post(_tally_url(), data="<ENVELOPE></ENVELOPE>", timeout=5)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def send_voucher_xml(xml: str) -> dict:
    """
    POSTs voucher XML to Tally and returns a parsed summary:
    {"created": int, "altered": int, "errors": int, "error_message": str|None}

    Raises TallyConnectionError if Tally isn't reachable at all (not running,
    HTTP server not enabled, wrong port).
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
    line_error_match = re.search(r"<LINEERROR>(.*?)</LINEERROR>", text, re.DOTALL)
    if line_error_match:
        error_message = line_error_match.group(1).strip()
    elif errors and errors > 0:
        error_message = "Tally reported an error but did not include a specific message."

    return {
        "created": created or 0,
        "altered": altered or 0,
        "errors": errors or 0,
        "error_message": error_message,
        "raw_response": text,
    }


def _extract_int(text: str, tag: str) -> int | None:
    m = re.search(rf"<{tag}>(\d+)</{tag}>", text)
    return int(m.group(1)) if m else None


_LEDGER_CACHE: dict = {"company": None, "at": 0.0, "ledgers": []}
_LEDGER_CACHE_TTL_SECONDS = 60  # short-lived: masters can change mid-session


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
    [{"name": ..., "parent": ...}, ...]. Cached briefly per-company to avoid
    a round trip before every single voucher in a batch push.
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

    xml = _LEDGER_LIST_REQUEST.format(company=company)
    try:
        resp = requests.post(_tally_url(), data=xml.encode("utf-8"), timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise TallyConnectionError(f"Could not fetch ledger list from Tally: {e}")

    ledgers = []
    try:
        root = ET.fromstring(resp.text)
        for led in root.iter("LEDGER"):
            name = led.get("NAME") or (led.findtext("NAME") or "")
            parent_el = led.find("PARENT")
            parent = parent_el.text if parent_el is not None else ""
            if name:
                ledgers.append({"name": name.strip(), "parent": (parent or "").strip()})
    except ET.ParseError:
        # Tally sometimes returns not-quite-well-formed XML (raw control
        # chars etc.) — fall back to a regex scrape rather than failing hard.
        for m in re.finditer(r'<LEDGER NAME="([^"]+)"[^>]*>.*?<PARENT>([^<]*)</PARENT>', resp.text, re.DOTALL):
            ledgers.append({"name": m.group(1).strip(), "parent": m.group(2).strip()})

    _LEDGER_CACHE["company"] = company
    _LEDGER_CACHE["at"] = now
    _LEDGER_CACHE["ledgers"] = ledgers
    return ledgers
