"""
app.py — Tenacious Conversion Engine: Webhook Gateway
Entry point for Render: uvicorn app:app --host 0.0.0.0 --port $PORT

Inbound routes
──────────────
POST /webhook/resend            Resend delivery events (delivered, bounced, opened…)
POST /webhook/email/inbound     Real email replies forwarded from Gmail → Apps Script
POST /webhook/sms/incoming      Africa's Talking inbound SMS (form-encoded)
POST /webhook/calcom/booking    Cal.com BOOKING_CREATED / BOOKING_CANCELLED
POST /webhook/hubspot           HubSpot CRM property-change events
GET  /health                    Liveness probe (Render checks this)
"""

import hashlib
import hmac
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

# Route all Python logging to stdout so Render shows it in the dashboard
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("webhook")

os.environ.setdefault("DEMO_SKIP_PLAYWRIGHT", "1")   # no headless browser on Render

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import agent.sms_router  # noqa: F401 — registers @events.on("sms_reply") handlers
from agent.events import events


# ── Lazy engine singleton ─────────────────────────────────────────────────────

_engine = None
_hubspot = None


def _get_engine():
    global _engine
    if _engine is None:
        from agent.main import ConversionEngine
        _engine = ConversionEngine()
    return _engine


def _get_hubspot():
    global _hubspot
    if _hubspot is None:
        from agent.hubspot import HubSpotClient
        _hubspot = HubSpotClient()
    return _hubspot


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Tenacious Webhook Gateway starting — mode=%s", os.getenv("KILL_SWITCH", "sandbox"))
    yield
    logger.info("Tenacious Webhook Gateway shutting down")


app = FastAPI(
    title="Tenacious Conversion Engine — Webhook Gateway",
    version="2.1.0",
    lifespan=lifespan,
)


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": os.getenv("KILL_SWITCH", "sandbox"),
        "version": "2.1.0",
        "ts": int(time.time()),
    }


# ── Signature helpers ─────────────────────────────────────────────────────────

def _verify_calcom(body: bytes, sig_header: str | None) -> bool:
    """Cal.com sends X-Cal-Signature-256: sha256=<hex>"""
    secret = os.getenv("CALCOM_WEBHOOK_SECRET", "")
    if not secret or not sig_header:
        return True  # permissive when not configured
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def _verify_hubspot(body: bytes, sig_header: str | None) -> bool:
    """HubSpot v1 signature: SHA256(client_secret + raw_body)"""
    secret = os.getenv("HUBSPOT_CLIENT_SECRET", "")
    if not secret or not sig_header:
        return True
    digest = hashlib.sha256((secret + body.decode("utf-8", errors="replace")).encode()).hexdigest()
    return hmac.compare_digest(digest, sig_header)


def _verify_webhook_token(token_header: str | None) -> bool:
    """Generic bearer-token check for /webhook/email/inbound."""
    secret = os.getenv("WEBHOOK_SECRET", "")
    if not secret:
        return True  # open when not configured
    return hmac.compare_digest(secret, (token_header or "").removeprefix("Bearer "))


# ── Background handlers ───────────────────────────────────────────────────────

def _handle_email_reply_bg(contact_email: str, reply_text: str, from_addr: str):
    """Run engine.handle_email_reply in a background thread."""
    try:
        engine = _get_engine()
        result = engine.handle_email_reply(contact_email, reply_text)
        logger.info(
            "Reply handled: contact=%s re_qualified=%s new_segment=%s send=%s",
            contact_email,
            result.get("re_qualified"),
            result.get("new_segment"),
            result.get("send_result", {}).get("status"),
        )
        # Update HubSpot to warm
        try:
            hs = _get_hubspot()
            contact = hs.search_contact_by_email(contact_email)
            if contact:
                hs.update_contact(contact["id"], hs_lead_status="IN_PROGRESS")
                hs.log_note(contact["id"],
                    f"[Email reply received]\nFrom: {from_addr}\n\n{reply_text[:500]}")
        except Exception as e:
            logger.warning("HubSpot warm update failed: %s", e)
    except Exception as e:
        logger.error("Email reply pipeline failed for %s: %s", contact_email, e)


def _handle_calcom_booking_bg(email: str, booking_data: dict):
    """Run engine.handle_booking_confirmed in a background thread."""
    try:
        engine = _get_engine()
        brief = engine.handle_booking_confirmed(email, booking_data)
        logger.info(
            "Booking confirmed: contact=%s segment=%s tz=%s",
            email, brief.get("segment"), brief.get("hq_timezone"),
        )
        try:
            hs = _get_hubspot()
            contact = hs.search_contact_by_email(email)
            if contact:
                hs.update_contact(contact["id"], hs_lead_status="OPEN_DEAL")
                hs.log_note(contact["id"],
                    f"[Cal.com booking confirmed]\nTime: {booking_data.get('start','?')}\n"
                    f"Segment: {brief.get('segment','?')}\n"
                    f"Pitch angle: {brief.get('pitch_angle','?')}")
        except Exception as e:
            logger.warning("HubSpot booking update failed: %s", e)
    except Exception as e:
        logger.error("Booking pipeline failed for %s: %s", email, e)


# ── Resend ────────────────────────────────────────────────────────────────────

@app.post("/webhook/resend")
async def resend_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Resend delivery-event webhook.
    Handles: email.delivered  email.opened  email.bounced  email.complained

    Configure in Resend dashboard → Webhooks → add https://<your-render-url>/webhook/resend
    """
    body = await request.body()
    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "invalid JSON")

    event_type = data.get("type", "")
    payload = data.get("data", {})
    to_list = payload.get("to", [])
    to_email = to_list[0] if isinstance(to_list, list) and to_list else str(to_list)

    logger.info("Resend event: type=%s to=%s", event_type, to_email)

    if event_type == "email.delivered":
        events.trigger("email_delivered", {"to": to_email, "id": payload.get("email_id")})

    elif event_type == "email.bounced":
        events.trigger("email_bounce", {"to": to_email})
        if to_email:
            try:
                hs = _get_hubspot()
                c = hs.search_contact_by_email(to_email)
                if c:
                    hs.update_contact(c["id"], hs_lead_status="UNQUALIFIED")
            except Exception:
                pass

    elif event_type == "email.complained":
        events.trigger("email_spam", {"to": to_email})

    elif event_type == "email.opened":
        events.trigger("email_opened", {"to": to_email})

    return {"status": "ok", "event": event_type}


# ── Gmail inbound reply bridge ────────────────────────────────────────────────

@app.post("/webhook/email/inbound")
async def email_inbound(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """
    Real email reply handler.
    Triggered by Gmail Apps Script (see /docs/gmail_webhook_setup.md).

    Expected JSON body:
      {
        "from":      "rafiakedir22@gmail.com",
        "to":        "rafia@10academy.org",
        "subject":   "Re: ...",
        "text":      "Thanks for the note ...",
        "messageId": "<abc@gmail.com>"
      }

    Authorization: Bearer <WEBHOOK_SECRET>
    """
    if not _verify_webhook_token(authorization):
        raise HTTPException(403, "invalid token")

    body = await request.body()
    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "invalid JSON")

    from_addr   = data.get("from", "")
    reply_text  = data.get("text") or data.get("body") or ""
    subject     = data.get("subject", "")

    if not from_addr or not reply_text:
        raise HTTPException(400, "missing 'from' or 'text'")

    logger.info("Inbound email reply: from=%s subject=%r len=%d", from_addr, subject, len(reply_text))
    events.trigger("email_reply", {"from": from_addr, "text": reply_text, "subject": subject})
    background_tasks.add_task(_handle_email_reply_bg, from_addr, reply_text, from_addr)

    return {"status": "ok", "queued": True}


# Alias: some Resend setups post replied events here
@app.post("/webhook/email/reply")
async def email_reply_alias(request: Request, background_tasks: BackgroundTasks):
    return await email_inbound(request, background_tasks)


# ── Africa's Talking SMS ──────────────────────────────────────────────────────

@app.post("/webhook/sms/incoming")
async def sms_incoming(request: Request):
    """
    Africa's Talking inbound SMS.
    AT sends form-encoded POST with: from, to, text, date, id, linkId

    Set in AT dashboard → SMS → Incoming Messages → Callback URL
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    sender  = data.get("from") or data.get("sender")
    text    = data.get("text") or data.get("body", "")
    to      = data.get("to")
    msg_id  = data.get("id") or data.get("messageId")

    if not sender:
        raise HTTPException(400, "missing 'from'")

    logger.info("Inbound SMS: from=%s text=%r", sender, (text or "")[:60])

    sms_data = {"from": sender, "to": to, "text": text, "id": msg_id}
    events.trigger("sms_reply", sms_data)

    # Africa's Talking expects a plain-text or empty 200
    return PlainTextResponse("OK")


# ── Cal.com ───────────────────────────────────────────────────────────────────

@app.post("/webhook/calcom/booking")
async def calcom_booking(
    request: Request,
    background_tasks: BackgroundTasks,
    x_cal_signature_256: str | None = Header(default=None),
):
    """
    Cal.com booking webhook.
    Events: BOOKING_CREATED  BOOKING_CANCELLED  BOOKING_RESCHEDULED

    Set in Cal.com → Settings → Developer → Webhooks → Subscriber URL
    """
    body = await request.body()
    if not _verify_calcom(body, x_cal_signature_256):
        raise HTTPException(403, "invalid Cal.com signature")

    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "invalid JSON")

    trigger = data.get("triggerEvent") or data.get("type", "unknown")
    payload = data.get("payload", data)   # Cal.com wraps in payload; some versions don't

    logger.info("Cal.com event: trigger=%s", trigger)

    if trigger in ("BOOKING_CREATED", "booking.created"):
        attendees = payload.get("attendees", [])
        title     = payload.get("title", "Discovery Call")
        start     = payload.get("startTime", "")

        for attendee in attendees:
            email = attendee.get("email")
            if not email:
                continue
            booking_data = {
                "title":          title,
                "start":          start,
                "booking_url":    payload.get("bookingUrl", ""),
                "attendee_name":  attendee.get("name", ""),
                "attendee_title": attendee.get("title", ""),
            }
            background_tasks.add_task(_handle_calcom_booking_bg, email, booking_data)
            events.trigger("calcom_booking", {**booking_data, "email": email})

    elif trigger in ("BOOKING_CANCELLED", "booking.cancelled"):
        attendees = payload.get("attendees", [])
        for attendee in attendees:
            email = attendee.get("email")
            if email:
                events.trigger("calcom_cancelled", {"email": email})
                try:
                    hs = _get_hubspot()
                    c = hs.search_contact_by_email(email)
                    if c:
                        hs.update_contact(c["id"], hs_lead_status="IN_PROGRESS")
                        hs.log_note(c["id"], "[Cal.com booking cancelled]")
                except Exception:
                    pass

    return {"status": "ok", "trigger": trigger}


# ── HubSpot ───────────────────────────────────────────────────────────────────

@app.post("/webhook/hubspot")
async def hubspot_webhook(
    request: Request,
    x_hubspot_signature: str | None = Header(default=None),
):
    """
    HubSpot CRM property-change and contact-creation webhook.
    Payload is an array of event objects.

    Set in HubSpot → Settings → Integrations → Private Apps → Webhooks
    """
    body = await request.body()
    if not _verify_hubspot(body, x_hubspot_signature):
        raise HTTPException(403, "invalid HubSpot signature")

    try:
        events_list = json.loads(body)
    except Exception:
        raise HTTPException(400, "invalid JSON")

    if not isinstance(events_list, list):
        events_list = [events_list]

    for evt in events_list:
        sub_type  = evt.get("subscriptionType", "")
        object_id = evt.get("objectId")
        prop      = evt.get("propertyName")
        value     = evt.get("propertyValue")
        logger.info("HubSpot event: type=%s objectId=%s %s=%s", sub_type, object_id, prop, value)
        events.trigger("hubspot_event", evt)

    return {"status": "ok", "processed": len(events_list)}


# ── Manual test trigger (dev/demo only) ───────────────────────────────────────

@app.post("/webhook/test/email-reply")
async def test_email_reply(request: Request, background_tasks: BackgroundTasks):
    """
    Dev/demo endpoint — simulate a prospect reply without Gmail forwarding.
    No auth required (intended for demo only; protected by obscurity of URL).

    Body: { "from": "email", "text": "reply text" }
    """
    data = await request.json()
    from_addr  = data.get("from", "")
    reply_text = data.get("text", "")
    if not from_addr or not reply_text:
        raise HTTPException(400, "missing 'from' or 'text'")

    logger.info("TEST email reply trigger: from=%s", from_addr)
    background_tasks.add_task(_handle_email_reply_bg, from_addr, reply_text, from_addr)
    return {"status": "ok", "queued": True, "from": from_addr}
