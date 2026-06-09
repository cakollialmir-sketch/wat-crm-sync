#!/usr/bin/env python3
"""
WAT CRM Sync — Webhook Receiver (Flask)
Receives push events from Call Tools, Calendly, and Zoom.
Maps each event to GHL CRM operations via ghl_client.

Run locally:  python webhook_server.py
Deploy:       Render.com free web service
Test health:  curl http://localhost:8000/health
"""
import hashlib
import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
import ghl_client as ghl  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("webhook_server")

app = Flask(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _abort(status, message):
    return jsonify({"error": message}), status


def _verify_hmac(body: bytes, signature: str, secret: str):
    if not secret:
        return None
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lstrip("sha256=")):
        return _abort(401, "Invalid webhook signature")
    return None


def _verify_zoom_signature(body: bytes, signature: str, timestamp: str, secret: str):
    if not secret:
        return None
    message = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return _abort(401, "Invalid Zoom signature")
    return None


def _fetch_calendly_phone(invitee_uri: str):
    token = os.getenv("CALENDLY_API_TOKEN")
    if not token or not invitee_uri:
        return None
    try:
        r = requests.get(invitee_uri, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        r.raise_for_status()
        for qa in r.json().get("resource", {}).get("questions_and_answers", []):
            if "phone" in qa.get("question", "").lower():
                return qa.get("answer")
    except Exception as e:
        log.warning(f"Could not fetch Calendly invitee phone: {e}")
    return None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})


def _clean(val):
    """Return None if val is empty, None, or an unresolved Call Tools template string."""
    if not val:
        return None
    s = str(val).strip()
    if s.startswith("{%") or s.startswith("{{%"):
        return None
    return s or None


@app.post("/webhook/calltools")
def calltools_webhook():
    body = request.get_data()
    err = _verify_hmac(body, request.headers.get("X-CallTools-Signature", ""), os.getenv("CALLTOOLS_WEBHOOK_SECRET", ""))
    if err:
        return err

    payload = json.loads(body)
    event = payload.get("event", "")
    log.info(f"CallTools event: {event}")

    if event == "call_note_created":
        # Agent typed notes during a call — sync to GHL contact
        contact_data = payload.get("contact", {})
        name = _clean(contact_data.get("name")) or "Unknown"
        phone = _clean(contact_data.get("phone"))
        email = _clean(contact_data.get("email"))
        note_text = _clean(payload.get("note", ""))

        if not note_text:
            return jsonify({"accepted": False, "reason": "empty_note"})

        log.info(f"CallTools note: name={name!r} phone={phone!r} note={note_text[:60]!r}")
        try:
            contact = ghl.upsert_contact(name=name, phone=phone, email=email)
            ghl.add_note(contact["id"], f"[Call Tools Note] {note_text}")
            return jsonify({"ok": True, "contact_id": contact["id"], "note_synced": True})
        except Exception as exc:
            log.error(f"CallTools note → GHL failed: {exc}")
            return jsonify({"ok": False, "error": str(exc)}), 200

    if event not in ("call_disposition_created", "call_ended"):
        return jsonify({"accepted": False, "reason": "unhandled_event", "event": event})

    contact_data = payload.get("contact", {})
    name = _clean(contact_data.get("name")) or "Unknown"
    phone = _clean(contact_data.get("phone"))
    email = _clean(contact_data.get("email"))

    raw_disp = _clean(payload.get("disposition", "")) or ""
    disposition = ghl.normalize_disposition(raw_disp)
    duration = payload.get("call_duration_seconds", 0)
    agent_notes = (_clean(payload.get("notes", "")) or "").strip()
    notes_text = (
        f"Disposition: {raw_disp} | Duration: {duration}s\n{agent_notes}"
        if agent_notes
        else f"Disposition: {raw_disp} | Duration: {duration}s"
    )

    log.info(f"CallTools webhook: name={name!r} phone={phone!r} email={email!r} disp={raw_disp!r}")

    try:
        contact = ghl.upsert_contact(name=name, phone=phone, email=email)
        contact_id = contact["id"]

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
        return jsonify({"ok": True, "contact_id": contact_id, "disposition": disposition, "actions": list(result)})

    except Exception as exc:
        log.error(f"CallTools → GHL failed: {exc} | name={name!r} phone={phone!r} disp={raw_disp!r}")
        # Return 200 so Call Tools doesn't mark it as a webhook error — data was received,
        # GHL sync failed but that's a transient/config issue, not a bad webhook.
        return jsonify({"ok": False, "error": str(exc), "disposition": disposition}), 200


@app.post("/webhook/calendly")
def calendly_webhook():
    body = request.get_data()
    err = _verify_hmac(body, request.headers.get("Calendly-Webhook-Signature", ""), os.getenv("CALENDLY_WEBHOOK_SECRET", ""))
    if err:
        return err

    payload = json.loads(body)
    event_type = payload.get("event", "")
    log.info(f"Calendly event: {event_type}")

    if event_type != "invitee.created":
        return jsonify({"accepted": False, "reason": "unhandled_event", "event": event_type})

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
    cal_id = os.getenv("GHL_CALENDAR_ID", "")
    actions = {}

    if start_time and cal_id:
        actions["appointment"] = ghl.create_appointment(
            contact_id=contact_id, calendar_id=cal_id,
            title=event.get("name") or "Sales Call",
            start_time=start_time, end_time=end_time or start_time,
        )

    stage_id = os.getenv("GHL_STAGE_APPT_SET", "")
    if stage_id:
        actions["opportunity"] = ghl.upsert_opportunity(contact_id=contact_id, stage_id=stage_id, name=f"Deal — {name}")

    wf_id = os.getenv("GHL_CONFIRMATION_WORKFLOW_ID")
    if wf_id:
        actions["workflow"] = ghl.trigger_workflow(contact_id, wf_id)

    log.info(f"Calendly → GHL: {name} ({email}) at {start_time}")
    return jsonify({"ok": True, "contact_id": contact_id, "actions": list(actions)})


@app.post("/webhook/zoom")
def zoom_webhook():
    body = request.get_data()
    payload = json.loads(body)

    if payload.get("event") == "endpoint.url_validation":
        secret = os.getenv("ZOOM_WEBHOOK_SECRET", "")
        plain_token = payload.get("payload", {}).get("plainToken", "")
        encrypted = hmac.new(secret.encode(), plain_token.encode(), hashlib.sha256).hexdigest()
        return jsonify({"plainToken": plain_token, "encryptedToken": encrypted})

    err = _verify_zoom_signature(body, request.headers.get("x-zm-signature", ""), request.headers.get("x-zm-request-timestamp", ""), os.getenv("ZOOM_WEBHOOK_SECRET", ""))
    if err:
        return err

    event_type = payload.get("event", "")
    log.info(f"Zoom event: {event_type}")

    if event_type != "meeting.ended":
        return jsonify({"accepted": False, "reason": "unhandled_event", "event": event_type})

    meeting = payload.get("payload", {}).get("object", {})
    duration = meeting.get("duration", 0)
    topic = meeting.get("topic") or "Demo Call"
    host_email = meeting.get("host_email", "")

    if not host_email:
        return jsonify({"ok": False, "reason": "no_host_email"})

    try:
        contact = ghl.upsert_contact(name="", email=host_email)
        contact_id = contact["id"]
    except ghl.GHLError as e:
        return jsonify({"ok": False, "reason": str(e)}), 500

    ghl.add_note(contact_id, f"Zoom demo completed — {duration} min. Topic: {topic}")

    stage_id = os.getenv("GHL_STAGE_DEMO_COMPLETED") or os.getenv("GHL_STAGE_SHOW", "")
    if stage_id:
        ghl.upsert_opportunity(contact_id=contact_id, stage_id=stage_id, name=f"Deal — {host_email}")

    log.info(f"Zoom → GHL: {host_email} — {duration} min demo logged")
    return jsonify({"ok": True, "contact_id": contact_id, "duration_min": duration})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
