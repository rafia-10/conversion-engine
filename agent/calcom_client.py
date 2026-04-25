import os
from typing import Optional
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()


class CalComClient:
    def __init__(self):
        self.base_url = os.getenv("CALCOM_URL", "https://cal.com")
        self.event_type_id = os.getenv("CALCOM_EVENT_TYPE_ID", "1")
        self.api_key = os.getenv("CALCOM_API_KEY")
        self.timeout = int(os.getenv("CALCOM_TIMEOUT_SECONDS", "10"))

    def create_booking(
        self,
        title: str,
        start: str,
        end: str,
        attendees: list[str],
        description: Optional[str] = None,
        event_type_id: Optional[str] = None,
    ) -> dict:
        payload = {
            "eventTypeId": int(event_type_id or self.event_type_id),
            "title": title,
            "description": description or "Auto-scheduled discovery call",
            "start": start,
            "end": end,
            "attendees": attendees,
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        paths = ["/api/bookings", "/api/booking/v1/bookings", "/api/v1/bookings"]
        for path in paths:
            try:
                response = requests.post(
                    self.base_url.rstrip("/") + path,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                if response.status_code in (200, 201):
                    return {
                        "status": "booking_created",
                        "platform": "cal.com-local",
                        "response_code": response.status_code,
                        "response": response.json(),
                    }
            except Exception:
                continue

        return {
            "status": "booking_failed",
            "platform": "cal.com-local",
            "error": "unable to create booking with configured Cal.com endpoints",
        }

    def get_booking_link(
        self,
        contact_name: str,
        contact_email: str,
        notes: str = "",
    ) -> str:
        """
        Return the Cal.com booking URL.

        If CALCOM_BOOKING_URL is set, use it directly (no params appended).
        Otherwise build: <base_url>/<username>/<event_slug>?name=...&email=...

        Env vars:
            CALCOM_BOOKING_URL — override: full URL used as-is
            CALCOM_USERNAME    — Cal.com username slug (default: "tenacious")
            CALCOM_EVENT_SLUG  — event type slug (default: "discovery-call")
        """
        override = os.getenv("CALCOM_BOOKING_URL", "").strip()
        if override:
            return override

        username   = os.getenv("CALCOM_USERNAME", "tenacious")
        event_slug = os.getenv("CALCOM_EVENT_SLUG", "discovery-call")
        base       = f"{self.base_url.rstrip('/')}/{username}/{event_slug}"
        params: dict = {"name": contact_name, "email": contact_email}
        if notes:
            params["notes"] = notes[:200]
        return f"{base}?{urlencode(params)}"
