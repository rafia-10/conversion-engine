# HubSpot Contact Property Schema

This document defines the canonical custom HubSpot contact properties used by the conversion-engine. All downstream consumers should reference this schema when reading from or writing to HubSpot.

> [!IMPORTANT]
> The properties below are **custom** — they must be created in HubSpot (Settings → Properties → Contact) before they appear in the API. Property names are case-sensitive.

---

## Properties

| Property name | HubSpot type | Allowed values / format | Description |
|---|---|---|---|
| `outreach_status` | Single-line text | `cold`, `warm`, `engaged`, `sms_confirmed`, `opted_out` | Current outreach lifecycle state |
| `icp_segment` | Single-line text | `segment_1_series_a_b`, `segment_2_mid_market_restructuring`, `segment_3_leadership_transitions`, `segment_4_capability_gaps`, `unknown` | ICP segment as classified by `enrichment.py` |
| `enrichment_signals` | Multi-line text | JSON string (see schema below) | Serialized enrichment output from the signal pipeline |
| `enrichment_timestamp` | Date/time | ISO-8601 UTC, e.g. `2025-04-23T20:00:00Z` | Timestamp of the last enrichment run |
| `last_booked_call_at` | Date/time | ISO-8601 UTC | Timestamp of the most recent successful Cal.com booking |

---

## `enrichment_signals` JSON Schema

```json
{
  "funding": {
    "stage": "Series A",
    "last_funding_months": 4,
    "confidence": "high"
  },
  "layoffs": {
    "event": null,
    "date": null,
    "headcount": 0,
    "percentage": 0,
    "confidence": "low"
  },
  "job_post_velocity": {
    "open_roles": 7,
    "velocity": "high",
    "focus": "ML/AI",
    "source": "https://acme.com/careers",
    "confidence": "high",
    "raw_titles": ["ML Engineer", "Senior Data Engineer"]
  },
  "leadership_change": {
    "event": "new CTO/VP Engineering",
    "date": null,
    "headline": "Acme appoints new CTO to lead AI push",
    "source": "https://news.google.com/search?q=...",
    "confidence": "medium"
  },
  "ai_maturity": {
    "score": 2,
    "confidence": "medium",
    "signal_summary": ["multiple engineering openings", "recent engineering leadership change"]
  }
}
```

---

## Write Points

Enrichment schema fields are written in these circumstances:

| Event | Fields written |
|---|---|
| Enrichment pipeline run | `icp_segment`, `enrichment_signals`, `enrichment_timestamp` |
| Email reply received (inbound webhook) | `outreach_status` → `warm` |
| SMS confirmation received | `outreach_status` → `sms_confirmed` |
| SMS opt-out received | `outreach_status` → `opted_out` |
| Cal.com booking created | `status` → `booked`, `last_booked_call_at` |

---

## Notes for Consumers

- Always check `enrichment_timestamp` to assess data freshness before acting on `enrichment_signals`.
- `outreach_status` is the gating field for SMS sends — only `warm`, `engaged`, and `sms_confirmed` contacts may receive SMS.
- Parse `enrichment_signals` as JSON before reading nested fields.
