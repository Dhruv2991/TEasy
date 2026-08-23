"""
Device fingerprinting used ONLY for trial-abuse prevention (one free trial
per physical machine), not for hardware-locking a paid license — a paid
license is tied to a license_key the person can use on any machine, same as
before. This module never blocks anything by itself; it just gives
start_trial() a stable-ish id to send to the license service, which decides
what to do with it.

Replaces the old app/security/hwid.py, which hardcoded a specific machine's
hash and hard-exited if it didn't match — that was a dev-only lock, not a
real fingerprinting system, and didn't work outside Windows.

Design: collect a few OS-level identifiers that are stable across reboots
and reinstalls but NOT trivially spoofed by clearing app data, then hash
them together. No single identifier is required — if the "best" one for a
platform is unavailable, we fall back to weaker ones so this never throws
and never blocks a legitimate user; on native machines, no exception should
actually surface, so we log a warning rather than only failing silently.
"""
import hashlib
import logging
import platform
import subprocess
import uuid

logger = logging.getLogger(__name__)


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, timeout=3
        )
        return out.decode(errors="ignore").strip()
    except Exception:
        return None


def _windows_machine_guid() -> str | None:
    # MachineGuid is generated at Windows install time and survives app
    # reinstalls/user profile wipes, which is exactly what we want.
    out = _run(
        [
            "reg",
            "query",
            r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Cryptography",
            "/v",
            "MachineGuid",
        ]
    )
    if not out:
        return None
    for line in out.splitlines():
        if "MachineGuid" in line:
            parts = line.split()
            if parts:
                return parts[-1]
    return None


def _macos_platform_uuid() -> str | None:
    out = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
    if not out:
        return None
    for line in out.splitlines():
        if "IOPlatformUUID" in line:
            # line looks like: "IOPlatformUUID" = "XXXXXXXX-XXXX-..."
            segments = line.split('"')
            if len(segments) >= 4:
                return segments[3]
    return None


def _linux_machine_id() -> str | None:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except OSError:
            continue
    return None


def _os_machine_id() -> str | None:
    system = platform.system()
    try:
        if system == "Windows":
            return _windows_machine_guid()
        if system == "Darwin":
            return _macos_platform_uuid()
        if system == "Linux":
            return _linux_machine_id()
    except Exception:
        logger.warning("OS machine-id lookup failed", exc_info=True)
    return None


def _mac_address() -> str | None:
    # uuid.getnode() returns a MAC-derived id on most systems, or a random
    # one if it can't find a real NIC — random-per-process is useless here,
    # so detect that case via the multicast bit the RFC sets on fallback
    # values and discard it rather than let it contribute noise.
    node = uuid.getnode()
    if (node >> 40) % 2 == 1:  # locally administered/multicast bit set -> fake
        return None
    return f"{node:012x}"


def get_device_fingerprint() -> str:
    """Best-effort stable id for this machine, hex sha256, always returns
    something (falls back to hostname+MAC if OS-level ids are unavailable,
    e.g. inside some containers/CI)."""
    parts = [
        _os_machine_id(),
        _mac_address(),
        platform.node(),  # hostname, weakest signal, kept as a tiebreaker only
    ]
    seed = "|".join(p for p in parts if p) or platform.node() or "unknown"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()
