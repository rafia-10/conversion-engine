"""
outreach_composer.py — Builds the first outbound email from the hiring signal brief.

Grounded-honesty rule: every factual claim maps to a key in the signal brief,
or it is rewritten as a question. Calls bench_gate before finalising.
"""
import json
import logging
import re
from typing import Dict, List, Optional

from agent.bench_gate import BenchGate
from agent.llm import LLMClient
from agent.qualifier import pitch_language

logger = logging.getLogger(__name__)

_bench_gate = BenchGate()

_STYLE_SNIPPET = """
TENACIOUS TONE RULES (non-negotiable):
- Direct: subject line starts with Request / Follow-up / Context / Question
- Grounded: every factual claim must be in the signal brief; weak signals → ask not assert
- Honest: never claim "aggressive hiring" with <5 roles; never over-commit bench capacity
- Professional: no "bench" (use "engineering team"), no offshore clichés, no emojis
- Non-condescending: gap brief is a research finding, never a failure judgment
- Max 120 words in body. One clear ask. Subject under 60 chars.
- Signature: [Name] / Research Partner / Tenacious Intelligence Corporation / gettenacious.com
"""


def compose_cold_email(
    hiring_signal_brief: Dict,
    segment: str,
    segment_confidence: float,
    ai_maturity: Dict,
    contact_name: str = "there",
    contact_title: str = "",
    llm: Optional[LLMClient] = None,
    drift_examples: Optional[List[str]] = None,
    booking_link: Optional[str] = None,
) -> Dict:
    """
    Compose the first cold outreach email.
    Returns: {"subject": str, "body": str, "html": str, "bench_approved": bool}
    """
    company = hiring_signal_brief.get("company_name", "your company")
    funding = hiring_signal_brief.get("funding", {})
    layoffs = hiring_signal_brief.get("layoffs", {})
    job_vel = hiring_signal_brief.get("job_post_velocity", {})
    leadership = hiring_signal_brief.get("leadership_change", {})
    ai_score = ai_maturity.get("score", 0)
    ai_conf = ai_maturity.get("confidence", "low")
    comp_gap = hiring_signal_brief.get("competitor_gap", {})

    # Build the grounded signal summary (only claim what the brief supports)
    signals = _extract_grounded_signals(
        company, funding, layoffs, job_vel, leadership, ai_score, ai_conf
    )
    pitch = pitch_language(segment, ai_score)
    gap_snippet = _gap_snippet(comp_gap, ai_score)

    # Segment-specific hook
    hook = _segment_hook(segment, signals, funding, job_vel, leadership)

    # Build prompt for LLM
    drift_instruction = ""
    if drift_examples:
        drift_instruction = (
            "\n\nAVOID THESE SPECIFIC PHRASINGS (tone violations from prior draft):\n"
            + "\n".join(f"- {d}" for d in drift_examples[:5])
        )

    prompt = f"""Write a cold outreach email for Tenacious Intelligence Corporation.

{_STYLE_SNIPPET}

CONTEXT:
Company: {company}
Contact: {contact_name}{f', {contact_title}' if contact_title else ''}
ICP Segment: {segment} (confidence: {segment_confidence:.0%})
Pitch language: "{pitch}"
AI Maturity score: {ai_score}/3 (confidence: {ai_conf})

VERIFIED SIGNAL BRIEF (only reference what appears here):
{json.dumps(signals, indent=2)}

COMPETITOR GAP (frame as research finding if present):
{gap_snippet}

TASK: Write a cold email that:
1. Opens with the most concrete, verifiable signal from the brief
2. Uses the correct pitch language for this segment
3. Ends with ONE specific ask (15-minute call)
4. Does NOT claim anything not in the signal brief above
5. Stays under 120 words in the body
{drift_instruction}

Return ONLY a JSON object:
{{
  "subject": "...",
  "body": "..."
}}"""

    if llm and llm.is_available():
        result = llm.generate_json(
            prompt,
            temperature=0.4,
            max_tokens=600,
            system="You are a precise B2B outreach writer for a talent outsourcing firm. "
                   "Follow the style guide exactly. Return only valid JSON.",
        )
        parsed = result.get("parsed", {})
        if parsed.get("subject") and parsed.get("body"):
            subject = parsed["subject"]
            body = parsed["body"]
        else:
            subject, body = _fallback_compose(company, contact_name, segment, signals, pitch)
    else:
        subject, body = _fallback_compose(company, contact_name, segment, signals, pitch)

    # Always run bench_gate on the final body
    gate_result = _bench_gate.check_commitment(body)
    if not gate_result["approved"]:
        body = gate_result["rewritten"]
        logger.info(f"Bench gate rewrote body: {gate_result['blocked_claims']}")

    # Inject Cal.com booking link before the signature
    if booking_link:
        body = _inject_booking_link(body, booking_link)

    html = _to_html(body)
    return {
        "subject": subject[:60],  # enforce subject line limit
        "body": body,
        "html": html,
        "bench_approved": gate_result["approved"],
        "bench_blocked_claims": gate_result["blocked_claims"],
        "segment": segment,
        "ai_maturity": ai_score,
    }


def compose_reply(
    thread_messages: List[Dict],
    prospect_reply: str,
    hiring_signal_brief: Dict,
    segment: str,
    stage: str,
    llm: Optional[LLMClient] = None,
) -> Dict:
    """Compose a follow-up reply, advancing toward booking a discovery call."""
    company = hiring_signal_brief.get("company_name", "your company")
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in thread_messages[-6:]
    )

    prompt = f"""You are writing a follow-up reply for Tenacious Intelligence Corporation.

{_STYLE_SNIPPET}

COMPANY: {company}
CONVERSATION STAGE: {stage}
THREAD HISTORY (last 6 messages):
{history_text}

PROSPECT'S LATEST REPLY:
{prospect_reply}

TASK:
- Acknowledge their reply specifically
- Advance toward booking a discovery call (30-min with a Tenacious delivery lead)
- If they show interest, propose a specific time or share the booking link
- Stay under 80 words
- One ask only

Return ONLY a JSON object: {{"subject": "...", "body": "..."}}"""

    if llm and llm.is_available():
        result = llm.generate_json(prompt, temperature=0.4, max_tokens=400)
        parsed = result.get("parsed", {})
        if parsed.get("body"):
            subject = parsed.get("subject", f"Follow-up: {company}")
            body = parsed["body"]
        else:
            subject = f"Follow-up: {company}"
            body = (
                f"Thanks for the reply. Happy to share more on how we've helped similar teams. "
                f"Would you have 30 minutes this week?\n\n"
                f"Alex\nResearch Partner\nTenacious Intelligence Corporation\ngettenacious.com"
            )
    else:
        subject = f"Follow-up: {company}"
        body = (
            f"Thanks for your reply. Would you have 30 minutes this week for a quick call? "
            f"Happy to share specifics on how we've helped similar teams.\n\n"
            f"Alex\nResearch Partner\nTenacious Intelligence Corporation\ngettenacious.com"
        )

    gate_result = _bench_gate.check_commitment(body)
    if not gate_result["approved"]:
        body = gate_result["rewritten"]

    return {
        "subject": subject[:60],
        "body": body,
        "html": _to_html(body),
        "bench_approved": gate_result["approved"],
    }


def _extract_grounded_signals(company, funding, layoffs, job_vel, leadership, ai_score, ai_conf):
    signals = {}
    if funding.get("stage") and funding.get("last_funding_months", 999) <= 12:
        signals["funding"] = (
            f"{funding['stage']} round ~{funding['last_funding_months']} months ago"
        )
    if layoffs.get("event"):
        signals["layoffs"] = (
            f"Layoff event: {layoffs.get('headcount', '?')} affected "
            f"({layoffs.get('percentage', '?')}%) — {layoffs.get('date', 'recent')}"
        )
    open_roles = job_vel.get("open_roles", 0)
    delta = job_vel.get("velocity_delta")
    trend = job_vel.get("velocity_trend", "unknown")
    conf = job_vel.get("confidence", "low")

    if open_roles >= 5:
        delta_str = ""
        if delta is not None and trend not in ("unknown", "stable"):
            direction = "up" if delta > 0 else "down"
            delta_str = f"; {abs(delta)} roles {direction} vs 60-day snapshot ({trend})"
        signals["hiring"] = f"{open_roles} open engineering roles{delta_str} (confidence: {conf})"
    elif open_roles >= 2:
        delta_str = ""
        if delta is not None and delta > 0:
            delta_str = f"; +{delta} vs 60-day snapshot"
        signals["hiring"] = f"{open_roles} open roles — early hiring signal{delta_str} (confidence: {conf})"
    if leadership.get("event"):
        signals["leadership"] = leadership.get("headline") or leadership["event"]
    if ai_score > 0:
        signals["ai_maturity"] = (
            f"AI maturity {ai_score}/3 (confidence: {ai_conf}); "
            f"signals: {', '.join(job_vel.get('raw_titles', [])[:3])}"
        )
    return signals


def _gap_snippet(comp_gap: Dict, ai_score: int) -> str:
    if ai_score < 1 or not comp_gap:
        return "No competitor gap brief available."
    top_gap = comp_gap.get("top_gap", {})
    if not top_gap:
        return "No specific gap identified."
    return (
        f"Gap: {top_gap.get('practice', '')}. "
        f"Why it matters: {top_gap.get('why', '')}. "
        f"Confidence: {comp_gap.get('confidence', 'low')}."
    )


def _segment_hook(segment, signals, funding, job_vel, leadership):
    if segment == "segment_1_series_a_b":
        return f"closed {signals.get('funding', 'a recent round')} and {signals.get('hiring', 'has open roles')}"
    if segment == "segment_2_mid_market_restructuring":
        return f"{signals.get('layoffs', 'went through a restructure')} while {signals.get('hiring', 'still hiring')}"
    if segment == "segment_3_leadership_transitions":
        return signals.get("leadership", "recently named new engineering leadership")
    if segment == "segment_4_capability_gaps":
        return f"AI maturity {signals.get('ai_maturity', 'score')} with specific capability signals"
    return "shows signals relevant to Tenacious's services"


def _inject_booking_link(body: str, booking_link: str) -> str:
    """Insert the Cal.com booking link on its own line before the closing signature."""
    sig_markers = ["Alex\n", "Best,\n", "Thanks,\n", "Regards,\n"]
    for marker in sig_markers:
        idx = body.find(marker)
        if idx != -1:
            return body[:idx] + f"Book a 30-min discovery call: {booking_link}\n\n" + body[idx:]
    # Fallback: append before last line
    return body.rstrip() + f"\n\nBook a 30-min discovery call: {booking_link}"


def _fallback_compose(company, contact_name, segment, signals, pitch):
    """Rule-based fallback when LLM is unavailable."""
    funding_line = signals.get("funding", "")
    hiring_line = signals.get("hiring", "")
    hook = funding_line or hiring_line or "your recent public signals"

    body = (
        f"Hi {contact_name},\n\n"
        f"{company} {hook} — the pattern we often see is that {pitch}.\n\n"
        f"Would you have 15 minutes to explore whether there's a fit?\n\n"
        f"Alex\nResearch Partner\nTenacious Intelligence Corporation\ngettenacious.com"
    )
    subject = f"Context: {company} + Tenacious"
    return subject, body


def _to_html(body: str) -> str:
    """Convert plain text to minimal HTML."""
    lines = body.replace("\r\n", "\n").split("\n")
    html_lines = [f"<p>{l}</p>" if l.strip() else "<br>" for l in lines]
    return (
        "<html><body style='font-family:sans-serif;max-width:600px'>"
        + "".join(html_lines)
        + "</body></html>"
    )
