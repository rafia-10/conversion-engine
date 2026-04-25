"""
app.py — Tenacious Conversion Engine: Webhook Gateway
Entry point for Render: uvicorn app:app --host 0.0.0.0 --port $PORT

Routes
──────
POST /webhook/resend            Resend delivery events (delivered, bounced, opened…)
POST /webhook/email/inbound     Inbound email replies (Cloudmailin JSON or Apps Script JSON)
POST /webhook/sms/incoming      Africa's Talking inbound SMS (form-encoded)
POST /webhook/calcom/booking    Cal.com BOOKING_CREATED / BOOKING_CANCELLED
POST /webhook/hubspot           HubSpot CRM property-change events
POST /webhook/test/email-reply  Dev trigger — simulate a reply without Gmail setup
GET  /health                    Liveness probe
"""

import hashlib
import hmac as _hmac
import json
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("webhook")

os.environ.setdefault("DEMO_SKIP_PLAYWRIGHT", "1")

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

import agent.sms_router  # noqa: F401 — registers sms_reply event handlers
from agent.events import events


# ── Singletons ────────────────────────────────────────────────────────────────

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
    logger.info("Webhook Gateway starting — mode=%s", os.getenv("KILL_SWITCH", "sandbox"))
    yield


app = FastAPI(title="Tenacious Conversion Engine — Webhook Gateway", version="2.2.0", lifespan=lifespan)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "mode": os.getenv("KILL_SWITCH", "sandbox"),
            "version": "2.2.0", "ts": int(time.time())}


# ── Signature verification ────────────────────────────────────────────────────

def _verify_resend(body: bytes, svix_id: str | None, svix_ts: str | None,
                   svix_sig: str | None) -> bool:
    """Resend uses Svix signing: HMAC-SHA256(svix_id.svix_ts.body, secret)."""
    secret_raw = os.getenv("RESEND_WEBHOOK_SECRET", "")
    if not secret_raw:
        return True  # permissive when not configured
    # Svix secrets are base64-encoded and prefixed with "whsec_"
    import base64
    key = base64.b64decode(secret_raw.removeprefix("whsec_"))
    signed = f"{svix_id}.{svix_ts}.{body.decode('utf-8', errors='replace')}"
    digest = _hmac.new(key, signed.encode(), hashlib.sha256).digest()
    computed = "v1," + __import__("base64").b64encode(digest).decode()
    for sig in (svix_sig or "").split(" "):
        if _hmac.compare_digest(computed, sig.strip()):
            return True
    return False


def _verify_calcom(body: bytes, sig_header: str | None) -> bool:
    """Cal.com: X-Cal-Signature-256: sha256=<hex>"""
    secret = os.getenv("CALCOM_WEBHOOK_SECRET", "")
    if not secret or not sig_header:
        return True
    expected = "sha256=" + _hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return _hmac.compare_digest(expected, sig_header.strip())


def _verify_hubspot(body: bytes, sig_header: str | None) -> bool:
    """HubSpot v1: SHA256(client_secret + raw_body)"""
    secret = os.getenv("HUBSPOT_CLIENT_SECRET", "")
    if not secret or not sig_header:
        return True
    digest = hashlib.sha256(
        (secret + body.decode("utf-8", errors="replace")).encode()
    ).hexdigest()
    return _hmac.compare_digest(digest, sig_header.strip())


# ── Email field normaliser ────────────────────────────────────────────────────

def _parse_email_address(raw) -> str:
    """Extract plain email from string or Cloudmailin object."""
    if isinstance(raw, dict):
        return raw.get("email", "")
    if isinstance(raw, str):
        # strip "Name <addr>" format
        m = re.search(r"<([^>]+)>", raw)
        return m.group(1).strip() if m else raw.strip()
    return ""


def _extract_inbound_fields(data: dict) -> tuple[str, str, str]:
    """
    Parse inbound email payload from multiple possible senders:
      - Cloudmailin JSON  (envelope.from string, reply_plain / plain body, headers.Subject)
      - Gmail Apps Script (from string, text body, subject string)
      - Generic fallback
    Returns (from_email, reply_text, subject).
    """
    # ── from address ──────────────────────────────────────────────────
    from_addr = (
        _parse_email_address(data.get("envelope", {}).get("from"))   # JSON normalised
        or _parse_email_address(data.get("envelope[from]", ""))      # multipart flat key
        or _parse_email_address(data.get("from", ""))                # Apps Script / generic
    )

    # ── reply body ────────────────────────────────────────────────────
    reply_text = (
        data.get("reply_plain")          # Cloudmailin: just the new reply, no quoted text
        or data.get("plain")             # Cloudmailin: full plain body
        or data.get("text")              # Apps Script / generic
        or data.get("body")              # alternative key
        or ""
    )
    if hasattr(reply_text, "read"):      # UploadFile from multipart
        import asyncio
        reply_text = asyncio.get_event_loop().run_until_complete(reply_text.read()).decode("utf-8", errors="replace")
    reply_text = (reply_text or "").strip()

    # ── subject ───────────────────────────────────────────────────────
    subject = (
        data.get("headers", {}).get("Subject")   # JSON normalised: headers object
        or data.get("headers[Subject]", "")      # multipart flat key
        or data.get("subject")                    # Apps Script / generic
        or ""
    )

    return from_addr, reply_text, subject


# ── Background pipeline handlers ──────────────────────────────────────────────

def _run_email_reply(contact_email: str, reply_text: str):
    try:
        engine = _get_engine()
        result = engine.handle_email_reply(contact_email, reply_text)
        logger.info("Reply pipeline: contact=%s re_qualified=%s segment=%s send=%s",
                    contact_email, result.get("re_qualified"),
                    result.get("new_segment"),
                    result.get("send_result", {}).get("status"))
        # HubSpot: mark warm + log note
        try:
            hs = _get_hubspot()
            c = hs.search_contact_by_email(contact_email)
            if c:
                hs.update_contact(c["id"], hs_lead_status="IN_PROGRESS")
                hs.log_note(c["id"],
                    f"[Email reply received — pipeline re-ran]\n\n{reply_text[:500]}")
        except Exception as e:
            logger.warning("HubSpot update after reply failed: %s", e)
    except Exception as e:
        logger.error("Email reply pipeline error for %s: %s", contact_email, e, exc_info=True)


def _run_booking_confirmed(email: str, booking_data: dict):
    try:
        engine = _get_engine()
        brief = engine.handle_booking_confirmed(email, booking_data)
        logger.info("Booking pipeline: contact=%s segment=%s", email, brief.get("segment"))
        try:
            hs = _get_hubspot()
            c = hs.search_contact_by_email(email)
            if c:
                hs.update_contact(c["id"], hs_lead_status="OPEN_DEAL")
                hs.log_note(c["id"],
                    f"[Cal.com booking confirmed]\n"
                    f"Time: {booking_data.get('start','?')}\n"
                    f"Segment: {brief.get('segment','?')}\n"
                    f"Pitch: {brief.get('pitch_angle','?')}")
        except Exception as e:
            logger.warning("HubSpot update after booking failed: %s", e)
    except Exception as e:
        logger.error("Booking pipeline error for %s: %s", email, e, exc_info=True)


# ── Resend ────────────────────────────────────────────────────────────────────

@app.post("/webhook/resend")
async def resend_webhook(
    request: Request,
    svix_id: str | None = Header(default=None, alias="svix-id"),
    svix_timestamp: str | None = Header(default=None, alias="svix-timestamp"),
    svix_signature: str | None = Header(default=None, alias="svix-signature"),
):
    """
    Resend delivery events (delivered, bounced, opened, complained).
    Resend dashboard → Webhooks → add URL: /webhook/resend
    Events to subscribe: email.delivered, email.opened, email.bounced, email.complained
    """
    body = await request.body()
    logger.info("Resend raw body: %s", body[:300])

    if not _verify_resend(body, svix_id, svix_timestamp, svix_signature):
        raise HTTPException(403, "invalid Resend/Svix signature")

    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "invalid JSON")

    event_type = data.get("type", "")
    payload    = data.get("data", {})
    to_raw     = payload.get("to", [])
    to_email   = to_raw[0] if isinstance(to_raw, list) and to_raw else str(to_raw)

    logger.info("Resend event: type=%s to=%s", event_type, to_email)
    events.trigger("resend_" + event_type.replace(".", "_"), {"to": to_email, "payload": payload})

    if event_type == "email.bounced" and to_email:
        try:
            hs = _get_hubspot()
            c = hs.search_contact_by_email(to_email)
            if c:
                hs.update_contact(c["id"], hs_lead_status="UNQUALIFIED")
        except Exception:
            pass

    return {"status": "ok", "event": event_type}


# ── Inbound email reply (Cloudmailin or Apps Script) ─────────────────────────

@app.post("/webhook/email/inbound")
async def email_inbound(request: Request, background_tasks: BackgroundTasks):
    """
    Receives inbound email replies forwarded by Cloudmailin or Gmail Apps Script.

    Cloudmailin setup:
      cloudmailin.com → New Address → Target URL = this endpoint → Format: JSON (Normalised)

    Apps Script setup:
      UrlFetchApp.fetch(this_url, {method:"post", contentType:"application/json",
                                   payload: JSON.stringify({from, subject, text})})
    """
    body = await request.body()
    logger.info("Email inbound raw (content-type=%s): %s",
                request.headers.get("content-type", "?"), body[:600])

    # Try JSON → URL-encoded → multipart — never return 4xx (Cloudmailin bounces on any error)
    content_type = request.headers.get("content-type", "")
    data: dict = {}
    try:
        data = json.loads(body)
        logger.info("Email inbound: parsed as JSON, keys=%s", list(data.keys()))
    except Exception:
        if "multipart/form-data" in content_type:
            try:
                form = await request.form()
                data = dict(form)
                logger.info("Email inbound: parsed as multipart, keys=%s", list(data.keys()))
            except Exception as e:
                logger.warning("Email inbound: multipart parse failed: %s", e)
                return {"status": "skipped", "reason": "multipart_parse_failed"}
        else:
            try:
                from urllib.parse import parse_qs
                qs = parse_qs(body.decode("utf-8", errors="replace"))
                data = {k: v[0] if len(v) == 1 else v for k, v in qs.items()}
                logger.info("Email inbound: parsed as url-encoded, keys=%s", list(data.keys()))
            except Exception as e:
                logger.warning("Email inbound: could not parse body (ct=%s): %s", content_type, e)
                return {"status": "skipped", "reason": "unparseable_body"}

    from_addr, reply_text, subject = _extract_inbound_fields(data)

    logger.info("Email inbound parsed: from=%r subject=%r text_len=%d",
                from_addr, subject, len(reply_text))

    # Always return 200 — Cloudmailin bounces the email if we return 4xx
    if not from_addr:
        logger.warning("Email inbound: could not determine sender. Keys: %s", list(data.keys()))
        return {"status": "skipped", "reason": "no_sender"}
    if not reply_text:
        logger.warning("Email inbound: empty body from %s. Keys: %s", from_addr, list(data.keys()))
        return {"status": "skipped", "reason": "empty_body"}

    events.trigger("email_reply", {"from": from_addr, "text": reply_text, "subject": subject})
    background_tasks.add_task(_run_email_reply, from_addr, reply_text)
    return {"status": "ok", "from": from_addr, "queued": True}


@app.post("/webhook/email/reply")
async def email_reply_alias(request: Request, background_tasks: BackgroundTasks):
    """Alias — same handler, kept for backwards compatibility."""
    return await email_inbound(request, background_tasks)


# ── Africa's Talking inbound SMS ──────────────────────────────────────────────

@app.post("/webhook/sms/incoming")
async def sms_incoming(request: Request):
    """
    Africa's Talking inbound SMS callback.
    AT sends form-encoded POST: from, to, text, date, id, linkId

    AT dashboard → SMS → Incoming Messages → Callback URL = this endpoint
    AT sandbox: sender must be whitelisted in the sandbox tester.
    """
    body = await request.body()
    logger.info("AT SMS raw body: %s", body[:300])

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = json.loads(body)
        except Exception:
            raise HTTPException(400, "invalid JSON")
    else:
        form = await request.form()
        data = dict(form)

    logger.info("AT SMS parsed: %s", data)

    sender = (
        data.get("from")
        or data.get("sender")
        or data.get("From")
    )
    text   = data.get("text") or data.get("Text") or data.get("body", "")
    to     = data.get("to")   or data.get("To")

    if not sender:
        # AT sometimes omits from in simulator — log and accept
        logger.warning("AT SMS: missing sender, accepting anyway")
        sender = "unknown"

    logger.info("Inbound SMS: from=%s to=%s text=%r", sender, to, (text or "")[:80])
    events.trigger("sms_reply", {"from": sender, "to": to, "text": text})

    # AT expects empty 200 or plain OK — do NOT return JSON
    return PlainTextResponse("", status_code=200)


# ── Cal.com booking ───────────────────────────────────────────────────────────

@app.post("/webhook/calcom/booking")
async def calcom_booking(
    request: Request,
    background_tasks: BackgroundTasks,
    x_cal_signature_256: str | None = Header(default=None),
):
    """
    Cal.com booking events.
    Cal.com → Settings → Developer → Webhooks → Subscriber URL = this endpoint
    Active events: BOOKING_CREATED, BOOKING_CANCELLED, BOOKING_RESCHEDULED
    """
    body = await request.body()
    logger.info("Cal.com raw body: %s", body[:400])

    if not _verify_calcom(body, x_cal_signature_256):
        raise HTTPException(403, "invalid Cal.com signature")

    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "invalid JSON")

    # Cal.com wraps event data under "payload" key
    trigger  = data.get("triggerEvent") or data.get("type", "unknown")
    payload  = data.get("payload") or data   # fallback: top-level IS the payload

    logger.info("Cal.com event: trigger=%s", trigger)

    if trigger in ("BOOKING_CREATED", "booking.created", "BOOKING_RESCHEDULED"):
        attendees = payload.get("attendees", [])
        title     = payload.get("title", "Discovery Call")
        start     = payload.get("startTime", payload.get("start_time", ""))

        for attendee in attendees:
            email = attendee.get("email")
            if not email:
                continue
            booking_data = {
                "title":          title,
                "start":          start,
                "booking_url":    payload.get("bookingUrl", payload.get("booking_url", "")),
                "attendee_name":  attendee.get("name", ""),
                "attendee_title": attendee.get("title", ""),
            }
            logger.info("Cal.com booking: email=%s title=%s start=%s", email, title, start)
            background_tasks.add_task(_run_booking_confirmed, email, booking_data)
            events.trigger("calcom_booking", {**booking_data, "email": email})

    elif trigger in ("BOOKING_CANCELLED", "booking.cancelled"):
        for attendee in (payload.get("attendees") or []):
            email = attendee.get("email")
            if email:
                events.trigger("calcom_cancelled", {"email": email})
                try:
                    hs = _get_hubspot()
                    c = hs.search_contact_by_email(email)
                    if c:
                        hs.update_contact(c["id"], hs_lead_status="IN_PROGRESS")
                        hs.log_note(c["id"], "[Cal.com booking cancelled — follow up needed]")
                except Exception:
                    pass

    return {"status": "ok", "trigger": trigger}


# ── HubSpot CRM events ────────────────────────────────────────────────────────

@app.post("/webhook/hubspot")
async def hubspot_webhook(
    request: Request,
    x_hubspot_signature: str | None = Header(default=None),
):
    """
    HubSpot contact property-change and lifecycle webhooks.
    HubSpot → Private App → Webhooks tab → add subscriptions + set target URL.
    Payload: JSON array of event objects.
    """
    body = await request.body()
    logger.info("HubSpot raw body: %s", body[:400])

    if not _verify_hubspot(body, x_hubspot_signature):
        raise HTTPException(403, "invalid HubSpot signature")

    try:
        evts = json.loads(body)
    except Exception:
        raise HTTPException(400, "invalid JSON")

    if not isinstance(evts, list):
        evts = [evts]

    for evt in evts:
        logger.info("HubSpot event: type=%s objectId=%s prop=%s val=%s",
                    evt.get("subscriptionType"), evt.get("objectId"),
                    evt.get("propertyName"), evt.get("propertyValue"))
        events.trigger("hubspot_event", evt)

    return {"status": "ok", "processed": len(evts)}


# ── Dev/demo test trigger ─────────────────────────────────────────────────────

@app.post("/webhook/test/email-reply")
async def test_email_reply(request: Request, background_tasks: BackgroundTasks):
    """
    Simulate a prospect email reply — no Gmail or Cloudmailin needed.
    Body: {"from": "email@example.com", "text": "reply text here"}
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    from_addr  = data.get("from", "").strip()
    reply_text = data.get("text", "").strip()

    if not from_addr or not reply_text:
        raise HTTPException(400, "missing 'from' or 'text'")

    logger.info("TEST trigger: email reply from=%s", from_addr)
    background_tasks.add_task(_run_email_reply, from_addr, reply_text)
    return {"status": "ok", "from": from_addr, "queued": True}


@app.post("/webhook/debug/inbound")
async def debug_inbound(request: Request):
    """Echo the raw inbound payload — use to inspect what Cloudmailin sends."""
    body = await request.body()
    try:
        data = json.loads(body)
    except Exception:
        data = body.decode("utf-8", errors="replace")
    logger.info("DEBUG inbound payload: %s", str(data)[:2000])
    return {"received": data}
