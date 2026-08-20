"""
Local side of licensing. No hardware-locking, no device fingerprinting —
just "does this license_key currently show as paid, according to the last
time we could reach the license service".

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

LICENSE_SERVICE_URL = os.environ.get("TEASY_LICENSE_SERVICE_URL", "https://teasy-vusw.onrender.com")
GRACE_DAYS = int(os.environ.get("TEASY_LICENSE_GRACE_DAYS", "5"))
REQUEST_TIMEOUT = 5


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
    """Register a new trial and store the returned license_key locally."""
    resp = requests.post(
        f"{LICENSE_SERVICE_URL}/trial/start",
        json={"email": email},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    save_license_key(data["license_key"])
    _remember_result(data)
    return get_status()


def _remember_result(remote: dict) -> None:
    cache = _load_cache()
    cache["status"] = remote.get("status")
    cache["expires_at"] = remote.get("expires_at")
    cache["valid"] = remote.get("valid", True)
    cache["last_online_check"] = _now().isoformat()
    _save_cache(cache)


def _status_from_cache_only(cache: dict) -> dict:
    """Pure cache read, no network — the offline-grace decision, computed
    fresh every time so it's always correct even if nothing ever calls the
    online path again before the grace window closes."""
    license_key = cache.get("license_key")
    if not license_key:
        return {"activated": False, "valid": False, "status": "none", "source": "none"}

    last_check = _parse(cache.get("last_online_check"))
    if not last_check:
        # Never successfully verified this key online — don't grant access
        # on trust alone, since that'd make the grace period pointless.
        return {"activated": True, "valid": False, "status": "unverified", "source": "cache"}

    grace_until = last_check + timedelta(days=GRACE_DAYS)
    within_grace = _now() < grace_until
    return {
        "activated": True,
        "valid": bool(cache.get("valid")) and within_grace,
        "status": cache.get("status", "unknown"),
        "expires_at": cache.get("expires_at"),
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

    Returns {activated, valid, status, expires_at, source, grace_until}
    - source is "online" if we just confirmed with the license service,
      "cache" if we're relying on a past check within the grace window,
      or "none" if there's no license_key at all yet.
    """
    cache = _load_cache()
    license_key = cache.get("license_key")
    if not license_key:
        return {"activated": False, "valid": False, "status": "none", "source": "none"}

    try:
        resp = requests.post(
            f"{LICENSE_SERVICE_URL}/license/check",
            json={"license_key": license_key},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        remote = resp.json()
        _remember_result(remote)
        return {
            "activated": True,
            "valid": remote.get("valid", False),
            "status": remote.get("status"),
            "expires_at": remote.get("expires_at"),
            "source": "online",
        }
    except requests.RequestException:
        pass  # no internet, or the service is down — fall back to cache below

    return _status_from_cache_only(cache)


def is_valid_fast() -> bool:
    """
    Cheap, network-free validity check meant to run on every API request
    (see the middleware in app/main.py). Just reads the cached result from
    the last real check_status() call and applies the same offline-grace
    rule — it does not itself contact the license service, so it can't add
    latency or load to every click in the app.
    """
    return _status_from_cache_only(_load_cache()).get("valid", False)
