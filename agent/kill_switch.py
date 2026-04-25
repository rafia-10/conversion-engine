"""
kill_switch.py — Routes all outbound traffic to a sandbox sink when KILL_SWITCH != "live".

Default is SANDBOX. You must explicitly set KILL_SWITCH=live to send real emails/SMS.
This file must be imported before any Resend or Africa's Talking calls are made.

IMPORTANT: Setting KILL_SWITCH=live sends real emails and SMS to real addresses.
Do not set live mode unless program staff and Tenacious have approved deployment.
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

KILL_SWITCH = os.getenv("KILL_SWITCH", "sandbox").strip().lower()
IS_LIVE = KILL_SWITCH == "live"

SANDBOX_SINK = Path(os.getenv("SANDBOX_SINK_PATH", "outputs/sandbox_sink.jsonl"))


def _write_sink(record: dict) -> None:
    SANDBOX_SINK.parent.mkdir(parents=True, exist_ok=True)
    with SANDBOX_SINK.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    logger.info(f"[SANDBOX] Intercepted outbound → {SANDBOX_SINK}")


def send_email(client, to: str, subject: str, html: str, text: str | None = None) -> dict:
    """Wrapper: in sandbox mode, writes to sink instead of sending."""
    if IS_LIVE:
        raw = client.send_email(to=to, subject=subject, html=html, text=text)
        ok = raw.get("status_code", 0) < 400
        return {
            "status": "sent" if ok else "send_error",
            "mode": "live",
            "to": to,
            "resend_id": raw.get("response", {}).get("id"),
            "status_code": raw.get("status_code"),
        }

    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "channel": "email",
        "mode": "sandbox",
        "to": to,
        "subject": subject,
        "html_length": len(html),
        "text_preview": (text or html)[:200],
    }
    _write_sink(record)
    return {"status": "sandbox_intercepted", "mode": "sandbox", "to": to}


def send_sms(client, to: str, message: str, bypass_gate: bool = False) -> dict:
    """Wrapper: in sandbox mode, writes to sink instead of sending."""
    if IS_LIVE:
        return client.send_sms(to=to, message=message, bypass_gate=bypass_gate)

    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "channel": "sms",
        "mode": "sandbox",
        "to": to,
        "message_preview": message[:200],
    }
    _write_sink(record)
    return {"status": "sandbox_intercepted", "mode": "sandbox", "to": to}


def mode_label() -> str:
    return "LIVE" if IS_LIVE else "SANDBOX"


if not IS_LIVE:
    logger.warning(
        "KILL_SWITCH is not set to 'live' — all outbound email and SMS are routed to "
        f"{SANDBOX_SINK} and will NOT reach real recipients. "
        "Set KILL_SWITCH=live (with program-staff approval) to enable real sends."
    )
