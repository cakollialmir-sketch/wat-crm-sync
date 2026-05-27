#!/usr/bin/env python3
"""
WAT CRM Sync — Webhook Receiver
Receives push events from Call Tools, Calendly, and Zoom.
Maps each event to GHL CRM operations via ghl_client.

Run locally:  uvicorn webhook_server:app --reload --port 8000
Deploy:       render.yaml (Render.com free web service)
Test health:  curl http://localhost:8000/health
"""
import hashlib
import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

# Import ghl_client from tools/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
import ghl_client as ghl  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("webhook_server")

app = FastAPI(title="WAT CRM Sync", version="1.0.0")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _verify_hmac(body: bytes, signature: str, secret: str) -> None:
    """Generic HMAC-SHA256 signature verification. Skips check if secret is empty (dev mode)."""
    if not secret:
        return
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lstrip("sha256=")):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def _verify_zoom_signature(body: bytes, signature: str, timestamp: str, secret: str) -> None:
    """Zoom signs: 'v0:{timestamp}:{body}' and prefixes result with 'v0='."""
    if not secret:
        return
    message = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid Zoom signature")


def _fetch_calendly_phone(invitee_uri: str) -> Optional[str]:
    """Secondary Calendly API call — pull phone from intake questions_and_answers."""
    token = os.getenv("CALENDLY_API_TOKEN")
    if not token or not invitee_uri:
        return None
    try:
        r = requests.get(
            invitee_uri,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        for qa in r.json().get("resource", {}).get("questions_and_answers", []):
            if "phone" in qa.get("question", "").lower():
                return qa.get("answer")
    except Exception as e:
        log.warning(f"Could not fetch Calendly invitee phone: {e}")
    return None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Liveness check — pinged by Render and UptimeRobot every 5 min."""
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/webhook/calltools")
async def calltools_webhook(request: Request):
    """
    Receives Call Tools disposition events.
    Expected payload:
    {
      "event": "call_disposition_created",
      "contact": {"name": "...", "phone": "...", "email": "..."},
      "disposition": "callback",
      "callback_at": "2026-05-28T14:00:00Z",   (optional)
      "call_duration_seconds": 142,
      "notes": "..."                             (optional)
    }
    """
    body = await request.body()
    _verify_hmac(
        body,
        request.headers.get("X-CallTools-Signature", ""),
        os.getenv("CALLTOOLS_WEBHOOK_SECRET", ""),
    )

    payload = json.loads(body)
    event = payload.get("event", "")
    log.info(f"CallTools event received: {event}")

    if event not in ("call_disposition_created", "call_ended"):
        return {"accepted": False, "reason": "unhandled_event", "event": event}

    contact_data = payload.get("contact", {})
    name = contact_data.get("name") or "Unknown"
    phone = contact_data.get("phone")
    email = contact_data.get("email")

    contact = ghl.upsert_contact(name=name, phone=phone, email=email)
    contact_id = contact["id"]

    raw_disp = payload.get("disposition", "")
    disposition = ghl.normalize_disposition(raw_disp)

    duration = payload.get("call_duration_seconds", 0)
    notes_text = payload.get("notes") or f"Call ({duration}s) — disposition: {raw_disp}"

    result = ghl.handle_disposition(
        contact_id=contact_id,
        disposition=disposition,
        callback_dt=payload.get("callback_at"),
        meeting_dt=payload.get("meeting_at"),
        meeting_end_dt=payload.get("meeting_end_at"),
        calendar_id=os.getenv("GHL_CALENDAR_ID"),
        contact_name=name,
        notes_text=notes_text,
    )

    log.info(f"CallTools → GHL: contact={contact_id} disp={disposition} actions={list(result)}")
    return {"ok": True, "contact_id": contact_id, "disposition": disposition, "actions": list(result)}


@app.post("/webhook/calendly")
async def calendly_webhook(request: Request):
    """
    Receives Calendly invitee.created events.
    Creates/updates the GHL contact, creates the appointment, moves pipeline stage,
    and fires the confirmation workflow.
    """
    body = await request.body()
    _verify_hmac(
        body,
        request.headers.get("Calendly-Webhook-Signature", ""),
        os.getenv("CALENDLY_WEBHOOK_SECRET", ""),
    )

    payload = json.loads(body)
    event_type = payload.get("event", "")
    log.info(f"Calendly event received: {event_type}")

    if event_type != "invitee.created":
        return {"accepted": False, "reason": "unhandled_event", "event": event_type}

    inv = payload.get("payload", {})
    invitee = inv.get("invitee", {})
    event = inv.get("event", {})

    name = invitee.get("name") or "Unknown"
    email = invitee.get("email")
    phone = _fetch_calendly_phone(invitee.get("uri", ""))

    contact = ghl.upsert_contact(name=name, phone=phone, email=email)
    contact_id = contact["id"]

    start_time = event.get("start_time")
    end_time = event.get("end_time")
    event_name = event.get("name") or "Sales Call"
    cal_id = os.getenv("GHL_CALENDAR_ID", "")

    actions = {}

    if start_time and cal_id:
        actions["appointment"] = ghl.create_appointment(
            contact_id=contact_id,
            calendar_id=cal_id,
            title=event_name,
            start_time=start_time,
            end_time=end_time or start_time,
        )

    stage_id = os.getenv("GHL_STAGE_APPT_SET", "")
    if stage_id:
        actions["opportunity"] = ghl.upsert_opportunity(
            contact_id=contact_id,
            stage_id=stage_id,
            name=f"Deal — {name}",
        )

    wf_id = os.getenv("GHL_CONFIRMATION_WORKFLOW_ID")
    if wf_id:
        actions["workflow"] = ghl.trigger_workflow(contact_id, wf_id)

    log.info(f"Calendly → GHL: {name} ({email}) at {start_time} — actions={list(actions)}")
    return {"ok": True, "contact_id": contact_id, "actions": list(actions)}


@app.post("/webhook/zoom")
async def zoom_webhook(request: Request):
    """
    Receives Zoom meeting.ended events.
    Also handles the one-time endpoint.url_validation challenge Zoom sends on registration.
    """
    body = await request.body()
    payload = json.loads(body)

    # Zoom endpoint validation challenge (fires once during webhook app registration)
    if payload.get("event") == "endpoint.url_validation":
        secret = os.getenv("ZOOM_WEBHOOK_SECRET", "")
        plain_token = payload.get("payload", {}).get("plainToken", "")
        encrypted = hmac.new(secret.encode(), plain_token.encode(), hashlib.sha256).hexdigest()
        return {"plainToken": plain_token, "encryptedToken": encrypted}

    _verify_zoom_signature(
        body,
        request.headers.get("x-zm-signature", ""),
        request.headers.get("x-zm-request-timestamp", ""),
        os.getenv("ZOOM_WEBHOOK_SECRET", ""),
    )

    event_type = payload.get("event", "")
    log.info(f"Zoom event received: {event_type}")

    if event_type != "meeting.ended":
        return {"accepted": False, "reason": "unhandled_event", "event": event_type}

    meeting = payload.get("payload", {}).get("object", {})
    duration = meeting.get("duration", 0)
    topic = meeting.get("topic") or "Demo Call"
    host_email = meeting.get("host_email", "")

    if not host_email:
        log.warning("Zoom meeting.ended — no host_email in payload, cannot identify contact")
        return {"ok": False, "reason": "no_host_email"}

    try:
        contact = ghl.upsert_contact(name="", email=host_email)
        contact_id = contact["id"]
    except ghl.GHLError as e:
        log.error(f"Zoom → GHL contact upsert failed: {e}")
        return {"ok": False, "reason": str(e)}

    note = f"Zoom demo completed — {duration} min. Topic: {topic}"
    ghl.add_note(contact_id, note)

    stage_id = os.getenv("GHL_STAGE_SHOW", "")
    if stage_id:
        ghl.upsert_opportunity(
            contact_id=contact_id,
            stage_id=stage_id,
            name=f"Deal — {host_email}",
        )

    log.info(f"Zoom → GHL: {host_email} — {duration} min demo logged")
    return {"ok": True, "contact_id": contact_id, "duration_min": duration}
