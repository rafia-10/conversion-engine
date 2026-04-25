import os
from typing import Optional

import requests
from dotenv import load_dotenv
from agent.hubspot import HubSpotClient

load_dotenv()


class AfricaTalkingClient:
    PROD_URL    = "https://api.africastalking.com/version1/messaging"
    SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"

    def __init__(self):
        self.username = os.getenv("AFRICASTALK_USERNAME", "sandbox")
        self.api_key = (
            os.getenv("AFRICASTALK_API_KEY")
            or os.getenv("AFRICASTALK_KEY")
        )
        self.sender = os.getenv("AFRICASTALK_SENDER", "")  # leave blank unless registered in AT dashboard
        self.timeout = int(os.getenv("AFRICASTALK_TIMEOUT_SECONDS", "10"))
        self.hubspot = HubSpotClient()
        # Use sandbox endpoint when username is "sandbox"
        self.base_url = self.SANDBOX_URL if self.username == "sandbox" else self.PROD_URL

        if not self.api_key:
            raise ValueError("Missing AFRICASTALK_API_KEY in environment")

    def is_warm_lead(self, to: str, contact_email: Optional[str] = None) -> bool:
        """
        Warm-lead gate: returns True only if the contact has a logged email reply.

        Check order (fail-safe cascade):
          1. ConversationManager thread — authoritative local record of email replies.
             Used when `contact_email` is provided (the common case in the engine).
          2. HubSpot outreach_status — fallback for contacts where thread is absent
             (e.g. contacts imported directly into HubSpot without going through
             the engine's email flow).

        SMS is blocked until at least one of these confirms a prior email reply.
        """
        # Primary: check local email-reply log (does not require HubSpot to be live)
        if contact_email:
            try:
                from agent.conversation_manager import ConversationManager
                if ConversationManager().has_email_reply(contact_email):
                    return True
            except Exception:
                pass

        # Fallback: HubSpot outreach_status (phone-based lookup)
        url = f"{os.getenv('HUBSPOT_BASE_URL', 'https://api.hubapi.com')}/crm/v3/objects/contacts/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "phone",
                            "operator": "EQ",
                            "value": to,
                        }
                    ]
                }
            ],
            "properties": ["outreach_status"],
            "limit": 1,
        }
        try:
            response = requests.post(url, json=payload, headers=self.hubspot.headers, timeout=10)
            if response.status_code == 200:
                results = response.json().get("results", [])
                if results:
                    status = results[0].get("properties", {}).get("outreach_status")
                    return status in ["warm", "engaged", "curious"]
        except Exception:
            pass
        return False

    def send_sms(self, to: str, message: str, bypass_gate: bool = False,
                 contact_email: Optional[str] = None) -> dict:
        if not bypass_gate and not self.is_warm_lead(to, contact_email=contact_email):
            return {
                "status": "gate_blocked",
                "provider": "africas_talking",
                "reason": "SMS blocked: no email reply on record for this contact",
            }

        payload = {
            "username": self.username,
            "to": to,
            "message": message,
        }
        # Sandbox rejects custom sender IDs — only add in production
        if self.username != "sandbox" and self.sender:
            payload["from"] = self.sender

        response = requests.post(
            self.base_url,
            data=payload,
            headers={
                "apiKey": self.api_key,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=self.timeout,
        )

        result = {
            "status_code": response.status_code,
            "provider": "africas_talking",
            "request": payload,
        }

        try:
            result["response"] = response.json()
        except ValueError:
            result["response_text"] = response.text

        if response.status_code >= 400:
            result["error"] = True

        return result
