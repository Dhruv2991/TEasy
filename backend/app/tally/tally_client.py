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
import os
import re
import requests

TALLY_HOST = os.environ.get("TALLY_HOST", "localhost")
TALLY_PORT = int(os.environ.get("TALLY_PORT", "9000"))
TALLY_URL = f"http://{TALLY_HOST}:{TALLY_PORT}"


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
        resp = requests.post(TALLY_URL, data="<ENVELOPE></ENVELOPE>", timeout=5)
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
        resp = requests.post(TALLY_URL, data=xml.encode("utf-8"), timeout=30)
    except requests.exceptions.RequestException as e:
        raise TallyConnectionError(
            f"Could not reach Tally at {TALLY_URL}. Make sure Tally Prime is running, "
            f"the company is open, and its HTTP/XML server is enabled "
            f"(F1 > Settings > Connectivity > act as Server, port {TALLY_PORT}). "
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
