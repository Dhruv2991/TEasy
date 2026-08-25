from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import requests

from ..security import license_client

router = APIRouter(prefix="/api/license", tags=["license"])


class ActivateRequest(BaseModel):
    license_key: str


class TrialRequest(BaseModel):
    email: str


@router.get("/status")
def status():
    return license_client.get_status()


@router.get("/device")
def device():
    """This machine's id plus its current tier's device limit — shown in
    Account & Billing so a user (or support) can tell which install is
    which when a key is shared across more machines than its tier allows."""
    return license_client.device_info()


@router.post("/start-trial")
def start_trial(body: TrialRequest):
    email = body.email.strip()
    if not email or "@" not in email:
        raise HTTPException(400, "Enter a valid email")
    try:
        return license_client.start_trial(email)
    except requests.RequestException:
        raise HTTPException(503, "Couldn't reach the license server — check your internet connection and try again")


@router.post("/activate")
def activate(body: ActivateRequest):
    key = body.license_key.strip()
    if not key:
        raise HTTPException(400, "Enter a license key")
    license_client.save_license_key(key)
    result = license_client.get_status()
    if not result["valid"]:
        if result.get("status") == "device_limit_reached":
            tier_label = license_client.TIER_LABEL.get(result.get("tier"), "This")
            limit = license_client.TIER_DEVICE_LIMIT.get(result.get("tier"), 1)
            raise HTTPException(
                409,
                f"{tier_label} plan allows {limit} device(s) and this key is already active on that many. "
                f"Deactivate one elsewhere, or upgrade to Gold for more devices on one key.",
            )
        raise HTTPException(400, f"That key isn't active (status: {result['status']}). Check your email for the right key, or subscribe from the app.")
    return result


@router.post("/create-subscription")
def create_subscription(plan: str = "monthly", tier: str = "silver"):
    """plan is the billing cycle (monthly/yearly); tier is the license
    edition (silver/gold, see license_client.TIER_DEVICE_LIMIT) — kept as
    two separate params since they're independent choices in the UI."""
    if tier not in license_client.TIER_DEVICE_LIMIT:
        raise HTTPException(400, f"Unknown plan tier '{tier}' — expected one of: {', '.join(license_client.TIER_DEVICE_LIMIT)}")
    key = license_client.get_license_key()
    if not key:
        raise HTTPException(400, "Start a trial or activate a license first")
    try:
        resp = requests.post(
            f"{license_client.LICENSE_SERVICE_URL}/billing/create-subscription",
            json={"license_key": key, "plan": plan, "tier": tier},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        raise HTTPException(503, "Couldn't reach the billing server — check your internet connection and try again")


@router.post("/cancel-subscription")
def cancel_subscription():
    try:
        return license_client.cancel_subscription()
    except requests.RequestException:
        raise HTTPException(503, "Couldn't reach the billing server — check your internet connection and try again")
    except RuntimeError as e:
        raise HTTPException(400, str(e))
