import os
from typing import Optional

import requests
from dotenv import load_dotenv
from agent.hubspot import HubSpotClient

load_dotenv()


class CalComClient:
    def __init__(self):
        self.base_url = os.getenv("CALCOM_URL", "http://localhost:3000")
        self.event_type_id = os.getenv("CALCOM_EVENT_TYPE_ID", "1")
        self.api_key = os.getenv("CALCOM_API_KEY")
        self.timeout = int(os.getenv("CALCOM_TIMEOUT_SECONDS", "10"))
        self.hubspot = HubSpotClient()

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
                    booking_info = response.json()
                    
                    # Sync to HubSpot
                    try:
                        # Assuming the first attendee is the lead email
                        lead_email = attendees[0] if attendees else None
                        if lead_email:
                            contact = self.hubspot.search_contact_by_email(lead_email)
                            if contact:
                                self.hubspot.update_contact(
                                    contact["id"],
                                    status="booked",
                                    last_booked_call_at=start
                                )
                    except Exception as e:
                        print(f"Error syncing to HubSpot: {e}")

                    return {
                        "status": "booking_created",
                        "platform": "cal.com-local",
                        "response_code": response.status_code,
                        "response": booking_info,
                    }
            except Exception:
                continue

        return {
            "status": "booking_failed",
            "platform": "cal.com-local",
            "error": "unable to create booking with configured Cal.com endpoints",
        }
