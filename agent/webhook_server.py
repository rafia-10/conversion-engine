import os
import logging
from flask import Flask, request, jsonify
from agent.events import events
from agent.hubspot import HubSpotClient
import agent.sms_router  # registers @events.on("sms_reply") handlers

app = Flask(__name__)
hubspot = HubSpotClient()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.route("/webhooks/resend", methods=["POST"])
def resend_webhook():
    """
    Handle Resend webhooks for replies and bounces.
    Expected payload format from Resend webhooks.
    """
    data = request.json
    if not data:
        return jsonify({"error": "No data received"}), 400

    event_type = data.get("type")
    payload = data.get("data", {})

    logger.info(f"Received Resend event: {event_type}")

    if event_type == "email.replied":
        # Robust parsing of reply events
        reply_data = {
            "from": payload.get("from"),
            "to": payload.get("to"),
            "subject": payload.get("subject"),
            "text": payload.get("text"),
            "id": payload.get("id"),
        }
        events.trigger("email_reply", reply_data)
    elif event_type == "email.bounced":
        bounce_data = {
            "from": payload.get("from"),
            "to": payload.get("to"),
            "reason": payload.get("reason"),
        }
        events.trigger("email_bounce", bounce_data)
    else:
        logger.info(f"Unhandled Resend event type: {event_type}")

    return jsonify({"status": "ok"}), 200


@app.route("/webhooks/africastalking", methods=["POST"])
def africastalking_webhook():
    """
    Handle Africa's Talking inbound SMS webhooks.
    Africa's Talking sends data in form-encoded format by default for SMS.
    """
    data = request.form
    if not data:
        # Fallback to JSON if configured differently
        data = request.json

    if not data:
        return jsonify({"error": "No data received"}), 400

    logger.info("Received Africa's Talking inbound SMS")

    # Africa's Talking SMS Callback parameters: from, to, text, date, id
    sms_data = {
        "from": data.get("from"),
        "to": data.get("to"),
        "text": data.get("text"),
        "date": data.get("date"),
        "id": data.get("id"),
        "linkId": data.get("linkId"), # For premium SMS
    }

    events.trigger("sms_reply", sms_data)

    return jsonify({"status": "ok"}), 200


# Downstream logic: Update HubSpot on email reply engagement
@events.on("email_reply")
def handle_email_engagement(data):
    """
    On receiving a reply, update the HubSpot contact to 'warm' 
    to enable SMS gating.
    """
    email = data.get("from")
    if email:
        try:
            contact = hubspot.search_contact_by_email(email)
            if contact:
                hubspot.update_contact(contact["id"], outreach_status="warm")
                logger.info(f"Updated HubSpot contact {email} status to warm")
        except Exception as e:
            logger.error(f"Failed to update HubSpot engagement for {email}: {e}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("WEBHOOK_PORT", "5000")))
