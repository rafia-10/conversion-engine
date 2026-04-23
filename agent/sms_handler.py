import os
import requests
from dotenv import load_dotenv

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

        if not self.api_key:
            raise ValueError("Missing AFRICASTALK_API_KEY in environment")

    def send_sms(self, to: str, message: str) -> dict:
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
