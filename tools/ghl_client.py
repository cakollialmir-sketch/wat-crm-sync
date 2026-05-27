#!/usr/bin/env python3
"""
GoHighLevel API v2 client — shared library for all GHL operations.
Imported by webhook_server.py and log_call.py.

Base URL: https://services.leadconnectorhq.com
Auth: Bearer token (Private Integration Token) + Version header
Rate limits: 100 req/10s — tenacity handles 429s with exponential backoff
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()

GHL_BASE = "https://services.leadconnectorhq.com"
GHL_API_TOKEN = os.getenv("GHL_API_TOKEN")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_PIPELINE_ID = os.getenv("GHL_PIPELINE_ID")

log = logging.getLogger("ghl_client")


class GHLError(Exception):
    pass


_ghl_retry = retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    before_sleep=before_sleep_log(log, logging.WARNING),
)


def _headers() -> dict:
    if not GHL_API_TOKEN:
        raise GHLError("GHL_API_TOKEN not set in .env")
    return {
        "Authorization": f"Bearer {GHL_API_TOKEN}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }


def _check(response: requests.Response) -> None:
    """Raise HTTPError on 429/5xx (tenacity retries), GHLError on other 4xx."""
    if response.status_code == 429:
        response.raise_for_status()
    if response.status_code >= 400:
        raise GHLError(f"GHL API {response.status_code}: {response.text[:300]}")


@_ghl_retry
def upsert_contact(
    name: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    tags: Optional[list] = None,
) -> dict:
    """POST /contacts/ — creates or returns existing contact matched by email/phone."""
    payload = {"locationId": GHL_LOCATION_ID, "name": name}
    if phone:
        payload["phone"] = phone
    if email:
        payload["email"] = email
    if tags:
        payload["tags"] = tags
    r = requests.post(f"{GHL_BASE}/contacts/", headers=_headers(), json=payload, timeout=15)

    # GHL returns 400 when duplicate contacts are disabled — extract the existing contact ID
    if r.status_code == 400:
        body = r.json()
        existing_id = body.get("meta", {}).get("contactId")
        if existing_id:
            log.info(f"Duplicate contact, fetching existing: {existing_id}")
            return _get_contact(existing_id)

    _check(r)
    return r.json().get("contact", r.json())


@_ghl_retry
def _get_contact(contact_id: str) -> dict:
    """GET /contacts/{id}"""
    r = requests.get(f"{GHL_BASE}/contacts/{contact_id}", headers=_headers(), timeout=15)
    _check(r)
    return r.json().get("contact", r.json())


@_ghl_retry
def update_contact(contact_id: str, fields: dict) -> dict:
    """PUT /contacts/{id} — update arbitrary fields on an existing contact."""
    r = requests.put(
        f"{GHL_BASE}/contacts/{contact_id}",
        headers=_headers(),
        json=fields,
        timeout=15,
    )
    _check(r)
    return r.json().get("contact", r.json())


@_ghl_retry
def _find_open_opportunity(contact_id: str) -> Optional[dict]:
    """Search for an existing open opportunity for this contact."""
    r = requests.get(
        f"{GHL_BASE}/opportunities/search",
        headers=_headers(),
        params={
            "location_id": GHL_LOCATION_ID,
            "contact_id": contact_id,
            "status": "open",
        },
        timeout=15,
    )
    _check(r)
    opps = r.json().get("opportunities", [])
    return opps[0] if opps else None


@_ghl_retry
def _patch_opportunity(opp_id: str, fields: dict) -> dict:
    r = requests.put(
        f"{GHL_BASE}/opportunities/{opp_id}",
        headers=_headers(),
        json=fields,
        timeout=15,
    )
    _check(r)
    return r.json().get("opportunity", r.json())


@_ghl_retry
def _create_opportunity(payload: dict) -> dict:
    r = requests.post(
        f"{GHL_BASE}/opportunities/",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    _check(r)
    return r.json().get("opportunity", r.json())


def upsert_opportunity(
    contact_id: str,
    stage_id: str,
    name: str,
    status: str = "open",
) -> dict:
    """
    Move contact to a pipeline stage. Updates the existing open opportunity
    rather than creating a duplicate — keeps one deal per prospect in flight.
    """
    existing = _find_open_opportunity(contact_id)
    if existing:
        return _patch_opportunity(
            existing["id"],
            {"pipelineStageId": stage_id, "status": status},
        )
    payload = {
        "pipelineId": GHL_PIPELINE_ID,
        "locationId": GHL_LOCATION_ID,
        "name": name,
        "pipelineStageId": stage_id,
        "status": status,
        "contactId": contact_id,
    }
    return _create_opportunity(payload)


@_ghl_retry
def create_appointment(
    contact_id: str,
    calendar_id: str,
    title: str,
    start_time: str,
    end_time: str,
    notes: Optional[str] = None,
) -> dict:
    """POST /calendars/events/appointments"""
    payload = {
        "calendarId": calendar_id,
        "locationId": GHL_LOCATION_ID,
        "contactId": contact_id,
        "title": title,
        "appointmentStatus": "confirmed",
        "startTime": start_time,
        "endTime": end_time,
    }
    if notes:
        payload["notes"] = notes
    r = requests.post(
        f"{GHL_BASE}/calendars/events/appointments",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    _check(r)
    return r.json()


@_ghl_retry
def create_task(
    contact_id: str,
    title: str,
    due_date: str,
    description: Optional[str] = None,
) -> dict:
    """POST /contacts/{id}/tasks"""
    payload = {"title": title, "dueDate": due_date, "completed": False}
    # GHL tasks API uses 'body' not 'description'
    if description:
        payload["body"] = description
    r = requests.post(
        f"{GHL_BASE}/contacts/{contact_id}/tasks",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    _check(r)
    return r.json()


@_ghl_retry
def add_note(contact_id: str, body: str) -> dict:
    """POST /contacts/{id}/notes"""
    r = requests.post(
        f"{GHL_BASE}/contacts/{contact_id}/notes",
        headers=_headers(),
        json={"body": body},
        timeout=15,
    )
    _check(r)
    return r.json()


@_ghl_retry
def trigger_workflow(contact_id: str, workflow_id: str) -> dict:
    """POST /contacts/{id}/workflow/{workflowId} — fire a GHL automation."""
    r = requests.post(
        f"{GHL_BASE}/contacts/{contact_id}/workflow/{workflow_id}",
        headers=_headers(),
        json={},
        timeout=15,
    )
    _check(r)
    return r.json()


# Normalizes the many ways dialers label the same disposition
_DISP_NORMALIZE = {
    # No Answer
    "no_answer": "no_answer",
    "no-answer": "no_answer",
    "noanswer": "no_answer",
    "no answer": "no_answer",
    "no_contact": "no_answer",
    "hung_up": "no_answer",
    "customer_hang_up": "no_answer",
    # Voicemail
    "voicemail": "voicemail",
    "voicemail_left": "voicemail",
    "left_voicemail": "voicemail",
    "left voicemail": "voicemail",
    "hit_voicemail": "voicemail",
    # Callback
    "callback": "callback",
    "callback_scheduled": "callback",
    "call_back_scheduled": "callback",
    "call back": "callback",
    "busy_-_call_back_later": "callback",
    "follow_up": "callback",
    # Appointment / Meeting
    "meeting_booked": "meeting_booked",
    "appointment_set": "meeting_booked",
    "booked": "meeting_booked",
    # Not Interested / DNC
    "not_interested": "not_interested",
    "not interested": "not_interested",
    "do_not_call": "not_interested",
    "wrong_number": "not_interested",
    "dnc_all_numbers": "not_interested",
    "dnc_this_number": "not_interested",
    # Show (Zoom demo attended)
    "show": "show",
    "attended": "show",
}


def normalize_disposition(raw: str) -> str:
    return _DISP_NORMALIZE.get(raw.lower().strip().replace(" ", "_"), raw.lower().strip())


def handle_disposition(
    contact_id: str,
    disposition: str,
    *,
    callback_dt: Optional[str] = None,
    meeting_dt: Optional[str] = None,
    meeting_end_dt: Optional[str] = None,
    calendar_id: Optional[str] = None,
    contact_name: Optional[str] = None,
    notes_text: Optional[str] = None,
) -> dict:
    """
    Central dispatcher — maps a call disposition to the correct GHL operations.
    Returns a summary dict of what was created/updated (keys = action names).
    """
    stage_map = {
        "no_answer": os.getenv("GHL_STAGE_NO_ANSWER"),
        "voicemail": os.getenv("GHL_STAGE_VOICEMAIL"),
        "callback": os.getenv("GHL_STAGE_CALLBACK"),
        "meeting_booked": os.getenv("GHL_STAGE_APPT_SET"),
        "not_interested": os.getenv("GHL_STAGE_NOT_INTERESTED"),
        "show": os.getenv("GHL_STAGE_SHOW"),
    }

    disposition = normalize_disposition(disposition)
    result = {}
    label = contact_name or contact_id

    stage_id = stage_map.get(disposition)
    if stage_id:
        result["opportunity"] = upsert_opportunity(
            contact_id=contact_id,
            stage_id=stage_id,
            name=f"Deal — {label}",
        )

    if disposition == "not_interested":
        result["tags"] = update_contact(contact_id, {"tags": ["not-interested"]})

    if disposition == "callback" and callback_dt:
        result["task"] = create_task(
            contact_id=contact_id,
            title="Follow-up call",
            due_date=callback_dt,
            description="Callback scheduled from cold call.",
        )

    if disposition == "meeting_booked":
        cal_id = calendar_id or os.getenv("GHL_CALENDAR_ID", "")
        if meeting_dt and cal_id:
            result["appointment"] = create_appointment(
                contact_id=contact_id,
                calendar_id=cal_id,
                title=f"Sales Call — {label}",
                start_time=meeting_dt,
                end_time=meeting_end_dt or meeting_dt,
            )
        wf_id = os.getenv("GHL_CONFIRMATION_WORKFLOW_ID")
        if wf_id:
            result["workflow"] = trigger_workflow(contact_id, wf_id)

    if notes_text:
        result["note"] = add_note(contact_id, notes_text)

    return result
