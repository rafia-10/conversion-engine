import os
import requests
from dotenv import load_dotenv
from agent.hubspot import HubSpotClient

load_dotenv()


class AfricaTalkingClient:
    BASE_URL = "https://api.africastalking.com/version1/messaging"

    def __init__(self):
        self.username = os.getenv("AFRICASTALK_USERNAME", "sandbox")
        self.api_key = (
            os.getenv("AFRICASTALK_API_KEY")
            or os.getenv("AFRICA'STALK_API_KEY")
            or os.getenv("AFRICASTALK_KEY")
        )
        self.sender = os.getenv("AFRICASTALK_SENDER", "Tenacious")
        self.timeout = int(os.getenv("AFRICASTALK_TIMEOUT_SECONDS", "10"))
        self.hubspot = HubSpotClient()

        if not self.api_key:
            raise ValueError("Missing AFRICASTALK_API_KEY in environment")

    def is_warm_lead(self, to: str) -> bool:
        """
        Check if the lead is 'warm' (has replied to an email).
        Since we only have the phone number 'to', we might need to search HubSpot by phone.
        """
        # Africa's Talking 'to' is usually a phone number
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
        response = requests.post(url, json=payload, headers=self.hubspot.headers, timeout=10)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                status = results[0].get("properties", {}).get("outreach_status")
                # According to warm.md: once a prospect replies, thread moves to 'warm'
                # We expect outreach_status to be 'warm' or similar if they replied.
                return status in ["warm", "engaged", "curious"]
        return False

    def send_sms(self, to: str, message: str, bypass_gate: bool = False) -> dict:
        if not bypass_gate and not self.is_warm_lead(to):
            return {
                "status": "gate_blocked",
                "provider": "africas_talking",
                "error": "SMS blocked: lead is not warm (no prior email engagement)",
            }

        payload = {
            "username": self.username,
            "to": to,
            "message": message,
            "from": self.sender,
        }

        response = requests.post(
            self.BASE_URL,
            data=payload,
            headers={"apiKey": self.api_key, "Content-Type": "application/x-www-form-urlencoded"},
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
