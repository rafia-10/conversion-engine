import os
import requests
from dotenv import load_dotenv

load_dotenv()

HUBSPOT_URL = os.getenv("HUBSPOT_BASE_URL", "https://api.hubapi.com")


class HubSpotClient:
    def __init__(self):
        self.token = (
            os.getenv("HUBSPOT_ACCESS_KEY")
            or os.getenv("HUPSPOT_ACESS_KEY")
            or os.getenv("HUBSPOT_API_KEY")
        )
        if not self.token:
            raise ValueError(
                "Missing HubSpot API key in environment. Please set HUBSPOT_ACCESS_KEY or HUPSPOT_ACESS_KEY."
            )

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def search_contact_by_email(self, email: str) -> dict | None:
        url = f"{HUBSPOT_URL}/crm/v3/objects/contacts/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": email,
                        }
                    ]
                }
            ],
            "properties": ["email", "firstname", "lastname", "phone", "company", "website", "outreach_status"],
            "limit": 1,
        }
        response = requests.post(url, json=payload, headers=self.headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        return results[0] if results else None

    def create_contact(self, email: str, firstname: str | None = None, lastname: str | None = None, **properties) -> dict:
        url = f"{HUBSPOT_URL}/crm/v3/objects/contacts"
        payload = {
            "properties": {
                "email": email,
                "firstname": firstname or "",
                "lastname": lastname or "",
                **properties,
            }
        }
        response = requests.post(url, json=payload, headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def update_contact(self, contact_id: str, **properties) -> dict:
        url = f"{HUBSPOT_URL}/crm/v3/objects/contacts/{contact_id}"
        payload = {"properties": properties}
        response = requests.patch(url, json=payload, headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def upsert_contact_by_email(self, email: str, firstname: str | None = None, lastname: str | None = None, **properties) -> dict:
        existing = self.search_contact_by_email(email)
        if existing and existing.get("id"):
            contact_id = existing["id"]
            return self.update_contact(
                contact_id,
                email=email,
                firstname=firstname,
                lastname=lastname,
                **properties,
            )
        return self.create_contact(email, firstname, lastname, **properties)

    def log_note(self, contact_id: str, message: str) -> dict:
        url = f"{HUBSPOT_URL}/crm/v3/objects/notes"
        payload = {
            "properties": {"hs_note_body": message},
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [
                        {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}
                    ],
                }
            ],
        }
        response = requests.post(url, json=payload, headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()
