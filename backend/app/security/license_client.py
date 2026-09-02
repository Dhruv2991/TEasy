"""
Local side of licensing. No hardware-locking a paid license — a license_key
works on any machine. The one exception is starting a NEW trial, which also
sends a device fingerprint so the license service can refuse to hand out a
second free trial to the same physical machine under a different email;
everything else here is just "does this license_key currently show as paid,
according to the last time we could reach the license service".

Why cache at all: this app has to keep working on a laptop with no wifi for
a few days. So every successful online check is written to
data/license_cache.json, and if a check fails because there's no internet
(NOT because the license is invalid), we trust that cache for up to
GRACE_DAYS before locking the app.

If the license service is unreachable AND we've never successfully checked
before (e.g. first run with no internet), there's nothing to fall back to,
so the app stays locked until it can reach the service at least once.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import requests

from ..paths import get_data_dir
from .fingerprint import get_device_fingerprint

LICENSE_SERVICE_URL = os.environ.get("TEASY_LICENSE_SERVICE_URL", "https://teasy-vusw.onrender.com")
GRACE_DAYS = int(os.environ.get("TEASY_LICENSE_GRACE_DAYS", "5"))
REQUEST_TIMEOUT = 5

# License editions, named after Tally's own Silver/Gold split so the
# concept is instantly familiar to anyone who's bought a Tally license
# before: Silver is a single-device seat, Gold allows a small number of
# devices on the same key (e.g. one machine at the shop counter + one at
# home/the accountant's office). The actual device-count enforcement has
# to live on the license service (this app only ever talks to it over
# HTTP) — these limits are the client's understanding of the contract so
# the UI can show the right copy; the license service is the source of
# truth and can override tier/limit per key in its response.
TIER_DEVICE_LIMIT = {"silver": 1, "gold": 3}
TIER_LABEL = {"silver": "Silver", "gold": "Gold"}


def _cache_path() -> str:
    return os.path.join(get_data_dir(), "license_cache.json")


def _load_cache() -> dict:
    path = _cache_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(data: dict) -> None:
    with open(_cache_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None):
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def device_info() -> dict:
    """Shown in the Account & Billing page so a user can quote/copy their
    device id to support, and see how many of their tier's device slots
    this specific install counts as. Purely informational on the client
    side — the license service is what actually enforces the limit."""
    cache = _load_cache()
    tier = cache.get("tier", "silver")
    return {
        "device_fingerprint": get_device_fingerprint(),
        "tier": tier,
        "device_limit": TIER_DEVICE_LIMIT.get(tier, 1),
    }


def get_license_key() -> str | None:
    return _load_cache().get("license_key")


def save_license_key(license_key: str) -> None:
    cache = _load_cache()
    cache["license_key"] = license_key
    # Force a fresh online check next time status is asked for, rather than
    # trusting any stale cached status left over from a previous key.
    cache.pop("last_online_check", None)
    cache.pop("valid", None)
    _save_cache(cache)


def start_trial(email: str) -> dict:
    """Register a new trial and store the returned license_key locally.

    If this email already has a trial/license on file, the server does NOT
    issue a new one — it re-sends the existing key by email instead. We
    surface that via is_new so the frontend can tell the user to check
    their inbox rather than silently switching them onto someone else's
    (or their own already-expired) license state.
    """
    resp = requests.post(
        f"{LICENSE_SERVICE_URL}/trial/start",
        json={"email": email, "device_fingerprint": get_device_fingerprint()},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    is_new = data.get("is_new", True)

    if not is_new:
        # Don't overwrite/activate the local cache with someone else's (or
        # a stale) key just because the email field was re-submitted.
        return {"is_new": False, "status": data.get("status"), "expires_at": data.get("expires_at")}

    save_license_key(data["license_key"])
    _remember_result(data)
    result = get_status()
    result["is_new"] = True
    return result


def _remember_result(remote: dict) -> None:
    cache = _load_cache()
    cache["status"] = remote.get("status")
    cache["expires_at"] = remote.get("expires_at")
    cache["valid"] = remote.get("valid", True)
    cache["tier"] = remote.get("tier", cache.get("tier", "silver"))
    cache["last_online_check"] = _now().isoformat()
    _save_cache(cache)


def _status_from_cache_only(cache: dict) -> dict:
    """Pure cache read, no network — the offline-grace decision, computed
    fresh every time so it's always correct even if nothing ever calls the
    online path again before the grace window closes."""
    license_key = cache.get("license_key")
    if not license_key:
        return {"activated": False, "valid": False, "status": "none", "tier": None, "source": "none"}

    last_check = _parse(cache.get("last_online_check"))
    if not last_check:
        # Never successfully verified this key online — don't grant access
        # on trust alone, since that'd make the grace period pointless.
        return {"activated": True, "valid": False, "status": "unverified", "tier": cache.get("tier"), "source": "cache"}

    grace_until = last_check + timedelta(days=GRACE_DAYS)
    within_grace = _now() < grace_until
    return {
        "activated": True,
        "valid": bool(cache.get("valid")) and within_grace,
        "status": cache.get("status", "unknown"),
        "expires_at": cache.get("expires_at"),
        "tier": cache.get("tier", "silver"),
        "source": "cache",
        "grace_until": grace_until.isoformat(),
    }


def get_status() -> dict:
    """
    Full check: talks to the license service if reachable, otherwise falls
    back to the cache (respecting the offline grace period). This is the
    "real" check — call it on app startup and from a periodic poll in the
    frontend, NOT on every single API request (see is_valid_fast below for
    that case, which just reads the cache).

    Returns {activated, valid, status, expires_at, tier, device_fingerprint, source, grace_until}
    - source is "online" if we just confirmed with the license service,
      "cache" if we're relying on a past check within the grace window,
      or "none" if there's no license_key at all yet.
    - tier is the license edition — "silver" (single device) or "gold"
      (multi-device, see TIER_DEVICE_LIMIT) — as last reported by the
      license service. Defaults to "silver" if the service hasn't sent one
      (e.g. an older key issued before tiers existed), so nothing breaks
      for existing customers.
    - device_fingerprint is this machine's id, sent with every check so
      the license service can enforce a tier's device limit; it's also
      surfaced in the Account & Billing UI for support/troubleshooting.
    """
    cache = _load_cache()
    license_key = cache.get("license_key")
    fingerprint = get_device_fingerprint()
    if not license_key:
        return {"activated": False, "valid": False, "status": "none", "tier": None, "source": "none", "device_fingerprint": fingerprint}

    try:
        resp = requests.post(
            f"{LICENSE_SERVICE_URL}/license/check",
            json={"license_key": license_key, "device_fingerprint": fingerprint},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 404:
            # The server explicitly doesn't recognize this key (e.g. it was
            # deleted/revoked) — this is a real "no", not a connectivity
            # problem, so don't let the offline-grace period paper over it.
            cache["valid"] = False
            cache["status"] = "unknown_key"
            cache["last_online_check"] = _now().isoformat()
            _save_cache(cache)
            return {"activated": True, "valid": False, "status": "unknown_key", "tier": cache.get("tier"), "source": "online", "device_fingerprint": fingerprint}

        if resp.status_code == 409:
            # Reserved for the license service to report "this key's
            # device limit is already full with other machines" once that
            # enforcement exists server-side. Surfaced distinctly from a
            # generic invalid key so the UI can point the user at
            # deactivating a device instead of just "buy a subscription".
            body = {}
            try:
                body = resp.json()
            except ValueError:
                pass
            cache["valid"] = False
            cache["status"] = "device_limit_reached"
            cache["last_online_check"] = _now().isoformat()
            _save_cache(cache)
            return {
                "activated": True, "valid": False, "status": "device_limit_reached",
                "tier": body.get("tier", cache.get("tier")), "source": "online", "device_fingerprint": fingerprint,
            }

        resp.raise_for_status()
        remote = resp.json()
        _remember_result(remote)
        return {
            "activated": True,
            "valid": remote.get("valid", False),
            "status": remote.get("status"),
            "expires_at": remote.get("expires_at"),
            "tier": remote.get("tier", "silver"),
            "source": "online",
            "device_fingerprint": fingerprint,
        }
    except requests.RequestException:
        pass  # no internet, or the service is down — fall back to cache below

    result = _status_from_cache_only(cache)
    result["device_fingerprint"] = fingerprint
    return result


def is_valid_fast() -> bool:
    """
    Cheap, network-free validity check meant to run on every API request
    (see the middleware in app/main.py). Just reads the cached result from
    the last real check_status() call and applies the same offline-grace
    rule — it does not itself contact the license service, so it can't add
    latency or load to every click in the app.
    """
    return _status_from_cache_only(_load_cache()).get("valid", False)


def clear_license() -> None:
    """Wipes the locally stored license_key so the app falls back to the
    'start a trial / activate a key' screen. Used when the stored key is a
    dead end — e.g. status 'unknown_key' (the license service has never
    heard of it, so no amount of rechecking or subscribing against it will
    ever succeed) — and the person needs to start fresh."""
    cache = _load_cache()
    cache.pop("license_key", None)
    cache.pop("last_online_check", None)
    cache.pop("valid", None)
    cache.pop("status", None)
    _save_cache(cache)


def cancel_subscription() -> dict:
    """Cancels auto-renewal on whatever license_key is currently active.
    Access continues until the period already paid for ends — same
    behavior as cancelling directly in Razorpay's dashboard, just
    reachable from inside the app."""
    license_key = get_license_key()
    if not license_key:
        raise RuntimeError("No license is currently activated.")

    resp = requests.post(
        f"{LICENSE_SERVICE_URL}/billing/cancel-subscription",
        json={"license_key": license_key},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    # Refresh the cache immediately so the UI reflects "cancelled" without
    # waiting for the next scheduled check.
    cache = _load_cache()
    cache["status"] = data.get("status")
    _save_cache(cache)
    return data
