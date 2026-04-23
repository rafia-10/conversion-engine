"""
sms_router.py — Downstream routing logic for inbound SMS replies.

This module is intentionally separated from transport concerns (i.e., the webhook
endpoint in webhook_server.py). It subscribes to the 'sms_reply' event and decides
what to do with an inbound message: update HubSpot, trigger a workflow, etc.
"""
import logging
from typing import Any, Dict

from agent.events import events
from agent.hubspot import HubSpotClient

logger = logging.getLogger(__name__)


def _classify_sms(text: str) -> str:
    """
    Lightweight classifier for inbound SMS text.
    Returns one of: 'confirm', 'cancel', 'question', 'unknown'.
    """
    t = (text or "").strip().lower()
    if t in ("yes", "y", "confirm", "ok", "sure"):
        return "confirm"
    if t in ("no", "n", "cancel", "stop"):
        return "cancel"
    if "?" in t:
        return "question"
    return "unknown"


@events.on("sms_reply")
def route_inbound_sms(data: Dict[str, Any]) -> None:
    """
    Entry point for all inbound SMS events.
    Classifies the message and routes to the appropriate handler.
    Transport-agnostic: works regardless of how the event was triggered.
    """
    sender = data.get("from")
    text = data.get("text", "")
    intent = _classify_sms(text)

    logger.info(f"Inbound SMS from {sender}: intent={intent!r}  text={text!r}")

    if intent == "confirm":
        _handle_confirmation(sender, data)
    elif intent == "cancel":
        _handle_cancellation(sender, data)
    elif intent == "question":
        _handle_question(sender, data)
    else:
        _handle_unknown(sender, data)


def _handle_confirmation(phone: str, data: Dict[str, Any]) -> None:
    """Mark lead as confirmed in HubSpot and log a note."""
    try:
        hs = HubSpotClient()
        contact = _find_contact_by_phone(hs, phone)
        if contact:
            hs.update_contact(contact["id"], outreach_status="sms_confirmed")
            hs.log_note(contact["id"], f"Lead confirmed via SMS: {data.get('text')}")
            logger.info(f"HubSpot contact {phone} marked sms_confirmed")
    except Exception as e:
        logger.error(f"SMS confirmation handler failed for {phone}: {e}")


def _handle_cancellation(phone: str, data: Dict[str, Any]) -> None:
    """Mark lead as opted-out in HubSpot."""
    try:
        hs = HubSpotClient()
        contact = _find_contact_by_phone(hs, phone)
        if contact:
            hs.update_contact(contact["id"], outreach_status="opted_out")
            hs.log_note(contact["id"], f"Lead opted out via SMS: {data.get('text')}")
            logger.info(f"HubSpot contact {phone} marked opted_out")
    except Exception as e:
        logger.error(f"SMS cancellation handler failed for {phone}: {e}")


def _handle_question(phone: str, data: Dict[str, Any]) -> None:
    """Log a note so a human can follow up."""
    try:
        hs = HubSpotClient()
        contact = _find_contact_by_phone(hs, phone)
        if contact:
            hs.log_note(
                contact["id"],
                f"Lead sent a question via SMS — requires human follow-up: {data.get('text')}"
            )
            logger.info(f"SMS question from {phone} logged for human follow-up")
    except Exception as e:
        logger.error(f"SMS question handler failed for {phone}: {e}")


def _handle_unknown(phone: str, data: Dict[str, Any]) -> None:
    """Log any unclassified SMS as a note."""
    try:
        hs = HubSpotClient()
        contact = _find_contact_by_phone(hs, phone)
        if contact:
            hs.log_note(
                contact["id"],
                f"Unclassified inbound SMS: {data.get('text')}"
            )
    except Exception as e:
        logger.error(f"SMS unknown handler failed for {phone}: {e}")


def _find_contact_by_phone(hs: HubSpotClient, phone: str) -> Dict | None:
    """Search HubSpot for a contact by phone number."""
    import os, requests
    url = f"{os.getenv('HUBSPOT_BASE_URL', 'https://api.hubapi.com')}/crm/v3/objects/contacts/search"
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "phone", "operator": "EQ", "value": phone}]}],
        "properties": ["email", "phone", "outreach_status"],
        "limit": 1,
    }
    resp = requests.post(url, json=payload, headers=hs.headers, timeout=10)
    if resp.status_code == 200:
        results = resp.json().get("results", [])
        return results[0] if results else None
    return None
