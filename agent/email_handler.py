import os
import requests
from dotenv import load_dotenv

load_dotenv()


class ResendEmailClient:
    BASE_URL = "https://api.resend.com/emails"

    def __init__(self):
        self.api_key = os.getenv("RESEND_API_KEY")
        self.from_email = os.getenv(
            "RESEND_FROM_EMAIL", "Rafia <onboarding@resend.dev>"
        )
        self.reply_to = os.getenv("RESEND_REPLY_TO")  # e.g. rafia@10academy.org
        self.timeout = int(os.getenv("RESEND_TIMEOUT_SECONDS", "10"))

        if not self.api_key:
            raise ValueError("Missing RESEND_API_KEY in environment")

    def send_email(self, to: str, subject: str, html: str, text: str | None = None) -> dict:
        payload = {
            "from": self.from_email,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if self.reply_to:
            payload["reply_to"] = [self.reply_to]  # Resend requires array
        if text:
            payload["text"] = text

        response = requests.post(
            self.BASE_URL,
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )

        result = {
            "status_code": response.status_code,
            "provider": "resend",
            "request": payload,
        }

        try:
            result["response"] = response.json()
        except ValueError:
            result["response_text"] = response.text

        if response.status_code >= 400:
            result["error"] = True

        return result
