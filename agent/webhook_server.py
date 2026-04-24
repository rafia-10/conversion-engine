"""
webhook_server.py — FastAPI webhook receiver.

Routes:
  POST /webhook/email/reply       ← Resend reply events
  POST /webhook/sms/incoming      ← Africa's Talking inbound SMS
  POST /webhook/calcom/booking    ← Cal.com booking confirmations
  GET  /health                    ← Liveness probe
"""
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent.events import events
from agent.hubspot import HubSpotClient
import agent.sms_router  # noqa: F401 — registers @events.on("sms_reply") handlers

app = FastAPI(title="Tenacious Conversion Engine Webhooks")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    hubspot = HubSpotClient()
except Exception:
    hubspot = None

# Lazy-init the engine (avoids loading all models on import)
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from agent.main import ConversionEngine
        _engine = ConversionEngine()
    return _engine


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": os.getenv("KILL_SWITCH", "sandbox"),
        "version": "2.0.0",
    }


@app.post("/webhook/email/reply")
async def email_reply(request: Request):
    """Resend reply/bounce webhook. Re-qualifies with signals extracted from reply text."""
    data = await request.json()
    if not data:
        return JSONResponse({"error": "no data"}, status_code=400)

    event_type = data.get("type", "")
    payload = data.get("data", {})
    logger.info(f"Resend event: {event_type}")

    if event_type == "email.replied":
        reply_data = {
            "from": payload.get("from"),
            "to": payload.get("to"),
            "subject": payload.get("subject"),
            "text": payload.get("text", ""),
            "id": payload.get("id"),
        }
        events.trigger("email_reply", reply_data)

        # Run re-qualification + compose response
        contact_email = reply_data.get("from") or reply_data.get("to")
        reply_text = reply_data.get("text", "")
        if contact_email and reply_text:
            try:
                engine = _get_engine()
                result = engine.handle_email_reply(contact_email, reply_text)
                logger.info(
                    f"Reply handled for {contact_email}: "
                    f"re_qualified={result.get('re_qualified')} "
                    f"new_segment={result.get('new_segment')}"
                )
            except Exception as e:
                logger.error(f"Reply handling failed for {contact_email}: {e}")

        # Also update HubSpot warm status directly
        if hubspot and reply_data.get("from"):
            try:
                contact = hubspot.search_contact_by_email(reply_data["from"])
                if contact:
                    hubspot.update_contact(contact["id"], outreach_status="warm")
            except Exception as e:
                logger.error(f"HubSpot warm update failed: {e}")

    elif event_type == "email.bounced":
        events.trigger("email_bounce", {"from": payload.get("from"), "to": payload.get("to")})

    return {"status": "ok"}


@app.post("/webhook/sms/incoming")
async def sms_incoming(request: Request):
    """Africa's Talking inbound SMS webhook (form-encoded or JSON)."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    if not data:
        return JSONResponse({"error": "no data"}, status_code=400)

    sms_data = {
        "from": data.get("from"),
        "to": data.get("to"),
        "text": data.get("text"),
        "date": data.get("date"),
        "id": data.get("id"),
    }
    text_preview = (sms_data.get("text") or "")[:50]
    logger.info(f"Inbound SMS from {sms_data.get('from')}: {text_preview!r}")
    events.trigger("sms_reply", sms_data)
    return {"status": "ok"}


@app.post("/webhook/calcom/booking")
async def calcom_booking(request: Request):
    """Cal.com booking confirmation webhook. Generates context brief for discovery call."""
    data = await request.json()
    if not data:
        return JSONResponse({"error": "no data"}, status_code=400)

    title = data.get("title", "?")
    logger.info(f"Cal.com booking: {title}")
    events.trigger("calcom_booking", data)

    attendees = data.get("attendees", [])
    for attendee in attendees:
        email = attendee.get("email")
        if not email:
            continue

        # Generate context brief + update HubSpot
        try:
            engine = _get_engine()
            booking_data = {
                "title": title,
                "start": data.get("startTime", ""),
                "booking_url": data.get("bookingUrl", ""),
                "attendee_name": attendee.get("name", ""),
                "attendee_title": attendee.get("title", ""),
            }
            context_brief = engine.handle_booking_confirmed(email, booking_data)
            logger.info(
                f"Context brief generated for {email} — "
                f"segment={context_brief.get('segment')} "
                f"timezone={context_brief.get('hq_timezone')}"
            )
        except Exception as e:
            logger.error(f"Context brief generation failed for {email}: {e}")

        # Update HubSpot fallback
        if hubspot:
            try:
                contact = hubspot.search_contact_by_email(email)
                if contact:
                    hubspot.update_contact(
                        contact["id"],
                        outreach_status="engaged",
                        thread_status="discovery_call_booked",
                    )
            except Exception as e:
                logger.error(f"HubSpot booking update failed: {e}")

    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("WEBHOOK_PORT", "8000")))
