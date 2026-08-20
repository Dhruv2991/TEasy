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
        raise HTTPException(400, f"That key isn't active (status: {result['status']}). Check your email for the right key, or subscribe from the app.")
    return result


@router.post("/create-subscription")
def create_subscription():
    key = license_client.get_license_key()
    if not key:
        raise HTTPException(400, "Start a trial or activate a license first")
    try:
        resp = requests.post(
            f"{license_client.LICENSE_SERVICE_URL}/billing/create-subscription",
            json={"license_key": key},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        raise HTTPException(503, "Couldn't reach the billing server — check your internet connection and try again")
