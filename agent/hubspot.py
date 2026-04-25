import os
import requests
from dotenv import load_dotenv

load_dotenv()

HUBSPOT_URL = os.getenv("HUBSPOT_BASE_URL", "https://api.hubapi.com")

# Outreach status → HubSpot hs_lead_status enum mapping
_STATUS_MAP = {
    "cold":                 "NEW",
    "warm":                 "IN_PROGRESS",
    "engaged":              "CONNECTED",
    "sms_confirmed":        "OPEN_DEAL",
    "discovery_booked":     "OPEN_DEAL",
    "opted_out":            "UNQUALIFIED",
}

# Standard writable contact fields confirmed available on this portal
_WRITABLE = {
    "email", "firstname", "lastname", "phone",
    "company", "jobtitle", "website", "industry",
    "hs_lead_status",
    "city", "state", "country", "zip", "address",
    "mobilephone", "fax", "annualrevenue", "numemployees",
    "notes_last_contacted",
}


class HubSpotClient:
    def __init__(self):
        self.token = (
            os.getenv("HUBSPOT_ACCESS_KEY")
            or os.getenv("HUPSPOT_ACESS_KEY")
            or os.getenv("HUBSPOT_API_KEY")
        )
        if not self.token:
            raise ValueError(
                "Missing HubSpot API key. Set HUBSPOT_ACCESS_KEY or HUPSPOT_ACESS_KEY."
            )
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_props(self, props: dict) -> dict:
        return {k: v for k, v in props.items() if k in _WRITABLE and v is not None}

    def _build_enrichment_note(self, enrichment: dict) -> str:
        import datetime
        ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
        lines = [f"[Tenacious Enrichment Brief — {ts}]", ""]
        for k, v in enrichment.items():
            lines.append(f"  {k:<24}: {v}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Core contact operations
    # ------------------------------------------------------------------

    def search_contact_by_email(self, email: str) -> dict | None:
        resp = requests.post(
            f"{HUBSPOT_URL}/crm/v3/objects/contacts/search",
            headers=self.headers,
            json={
                "filterGroups": [{"filters": [{
                    "propertyName": "email", "operator": "EQ", "value": email,
                }]}],
                "properties": [
                    "email", "firstname", "lastname", "company", "jobtitle",
                    "website", "industry", "hs_lead_status",
                    "createdate", "lastmodifieddate",
                ],
                "limit": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0] if results else None

    def create_contact(self, email: str, firstname: str | None = None,
                       lastname: str | None = None, **properties) -> dict:
        all_props = {"email": email, "firstname": firstname or "",
                     "lastname": lastname or "", **properties}
        resp = requests.post(
            f"{HUBSPOT_URL}/crm/v3/objects/contacts",
            json={"properties": self._safe_props(all_props)},
            headers=self.headers, timeout=10,
        )
        if resp.status_code == 400:
            # Fallback: minimal create
            resp = requests.post(
                f"{HUBSPOT_URL}/crm/v3/objects/contacts",
                json={"properties": {"email": email, "firstname": firstname or ""}},
                headers=self.headers, timeout=10,
            )
        resp.raise_for_status()
        return resp.json()

    def update_contact(self, contact_id: str, **properties) -> dict:
        safe = self._safe_props(properties)
        if not safe:
            return {"id": contact_id}
        resp = requests.patch(
            f"{HUBSPOT_URL}/crm/v3/objects/contacts/{contact_id}",
            json={"properties": safe},
            headers=self.headers, timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def upsert_contact_by_email(self, email: str, firstname: str | None = None,
                                lastname: str | None = None, **properties) -> dict:
        existing = self.search_contact_by_email(email)
        if existing and existing.get("id"):
            return self.update_contact(
                existing["id"], email=email,
                firstname=firstname, lastname=lastname, **properties,
            )
        return self.create_contact(email, firstname, lastname, **properties)

    # ------------------------------------------------------------------
    # Enrichment upsert — canonical entry point from main.py
    # ------------------------------------------------------------------

    def upsert_enriched_contact(
        self,
        email: str,
        firstname: str | None = None,
        lastname: str | None = None,
        # enrichment fields
        icp_segment: str | None = None,
        outreach_status: str | None = None,
        thread_status: str | None = None,
        ai_maturity_score: str | None = None,
        segment_confidence: str | None = None,
        outbound_variant: str | None = None,
        enrichment_signals: str | None = None,
        enrichment_timestamp: str | None = None,
        # standard fields
        company_name: str | None = None,
        contact_title: str | None = None,
        domain: str | None = None,
        **extra,
    ) -> dict:
        """
        Upsert contact with all enrichment data mapped to writable HubSpot fields.

        Mapping:
          icp_segment      → industry  (semantic match)
          outreach_status  → hs_lead_status  (NEW/IN_PROGRESS/CONNECTED/…)
          company_name     → company
          contact_title    → jobtitle
          domain           → website

        All enrichment detail (scores, confidence, signals) is written as an
        engagement note so it appears in the contact timeline.
        """
        import datetime
        ts = enrichment_timestamp or datetime.datetime.utcnow().isoformat() + "Z"
        hs_lead = _STATUS_MAP.get((outreach_status or "cold").lower(), "NEW")

        props = {
            "company":         company_name,
            "jobtitle":        contact_title,
            "website":         domain,
            "industry":        icp_segment,
            "hs_lead_status":  hs_lead,
        }
        result = self.upsert_contact_by_email(email, firstname, lastname, **props)
        contact_id = result.get("id")

        # Write full enrichment brief as a contact note
        if contact_id:
            note_data = {k: v for k, v in {
                "ICP Segment":       icp_segment,
                "Outreach Status":   outreach_status,
                "Thread Status":     thread_status,
                "AI Maturity Score": ai_maturity_score,
                "Segment Confidence": segment_confidence,
                "Outbound Variant":  outbound_variant,
                "Enrichment Time":   ts,
            }.items() if v is not None}
            if enrichment_signals:
                note_data["Signals"] = enrichment_signals[:200]
            try:
                self.log_note(contact_id, self._build_enrichment_note(note_data))
            except Exception:
                pass

        return result

    # ------------------------------------------------------------------

    def log_note(self, contact_id: str, message: str) -> dict:
        import datetime
        ts_ms = str(int(datetime.datetime.utcnow().timestamp() * 1000))
        resp = requests.post(
            f"{HUBSPOT_URL}/crm/v3/objects/notes",
            headers=self.headers,
            json={
                "properties": {
                    "hs_note_body": message,
                    "hs_timestamp": ts_ms,
                },
                "associations": [{
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                }],
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
