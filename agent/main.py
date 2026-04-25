"""
main.py — Orchestrator for the Tenacious Conversion Engine.

Pipeline per prospect:
  1. Async enrichment (5 parallel modules, ~30-90s)
  2. ICP qualification
  3. Compose segment-specific grounded email
  4. Tone check; regenerate if needed
  5. Bench gate final check
  6. Kill-switch gate: sandbox → sink; live → Resend
  7. Thread management (sha256-keyed per contact)
  8. HubSpot upsert
  9. Context brief generation (for discovery call prep)
 10. Langfuse trace flush

On email reply:
  - Extract new signals from reply text
  - Re-qualify with updated signals
  - Compose contextual follow-up advancing toward booking

On booking confirmed:
  - Generate discovery context brief for Alex Rivera
  - Attach to HubSpot deal record
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from agent import kill_switch
from agent.bench_gate import BenchGate
from agent.calcom_client import CalComClient
from agent.conversation_manager import ConversationManager
from agent.email_handler import ResendEmailClient
from agent.enrichment import EnrichmentPipeline
from agent.hubspot import HubSpotClient
from agent.langfuse_client import tracing
from agent.llm import LLMClient
from agent.outreach_composer import compose_cold_email, compose_reply, _to_html
from agent.qualifier import classify, pitch_language
from agent.sms_handler import AfricaTalkingClient
from agent.tone_checker import check_and_regenerate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUTS = Path(os.getenv("OUTPUTS_DIR", "outputs"))
OUTPUTS.mkdir(parents=True, exist_ok=True)


class ConversionEngine:
    def __init__(self):
        self.llm = LLMClient()
        self.enrichment = EnrichmentPipeline()
        self.bench_gate = BenchGate()
        self.conv_manager = ConversationManager()
        self.hubspot = HubSpotClient()
        self.calcom = CalComClient()

        try:
            self.email_client = ResendEmailClient()
        except Exception as e:
            logger.warning(f"Resend not configured: {e}")
            self.email_client = None

        try:
            self.sms_client = AfricaTalkingClient()
        except Exception as e:
            logger.warning(f"Africa's Talking not configured: {e}")
            self.sms_client = None

        self._trace_log = OUTPUTS / "traces.jsonl"
        logger.info(f"ConversionEngine ready — mode={kill_switch.mode_label()}")

    # -------------------------------------------------------------------------
    # Primary entry point
    # -------------------------------------------------------------------------

    def process_prospect(
        self,
        company_name: str,
        contact_email: str,
        contact_name: str = "there",
        contact_title: str = "",
        domain: Optional[str] = None,
    ) -> Dict:
        """Full pipeline for a single prospect. Returns complete trace dict."""
        t0 = time.time()
        lf_trace = tracing.new_trace(company_name, contact_email)

        trace: Dict = {
            "company": company_name,
            "contact_email": contact_email,
            "contact_name": contact_name,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "mode": kill_switch.mode_label(),
            "trace_id": lf_trace.trace_id,
        }

        # --- Step 1: Async enrichment (5 parallel modules) ------------------
        logger.info(f"[{company_name}] Enrichment pipeline starting (async, 5 modules)...")
        with lf_trace.span("enrichment") as span:
            brief = self.enrichment.build_hiring_signal_brief(company_name, domain)
            span.set_metadata(
                modules=brief.get("_module_latencies", {}),
                source=brief.get("crunchbase_data", {}).get("_source", "?"),
            )
        trace["hiring_signal_brief"] = brief
        logger.info(
            f"[{company_name}] Enrichment done — {brief.get('_enrichment_latency_ms', 0)}ms"
        )

        # --- Step 2: Qualification ------------------------------------------
        logger.info(f"[{company_name}] Classifying ICP segment...")
        with lf_trace.span("qualification") as span:
            qual = classify(brief)
            span.set_metadata(segment=qual["segment"], confidence=qual["confidence"])
        trace["qualification"] = qual
        segment = qual["segment"]
        confidence = qual["confidence"]
        abstain = qual["abstain_flag"]
        ai_maturity = brief.get("ai_maturity", {})
        logger.info(
            f"[{company_name}] → segment={segment} conf={confidence:.1%} abstain={abstain}"
        )

        # --- Step 3: Compose email ------------------------------------------
        def _do_compose(drift_examples=None):
            return compose_cold_email(
                hiring_signal_brief=brief,
                segment=segment,
                segment_confidence=confidence,
                ai_maturity=ai_maturity,
                contact_name=contact_name,
                contact_title=contact_title,
                llm=self.llm,
                drift_examples=drift_examples,
            )["body"]

        logger.info(f"[{company_name}] Composing email (LLM={self.llm.is_available()})...")
        with lf_trace.span("email_composition") as span:
            email_draft = _do_compose()
            span.set_metadata(llm_available=self.llm.is_available())

        # --- Step 4: Tone check + optional regenerate -----------------------
        with lf_trace.span("tone_check") as span:
            tone_result = check_and_regenerate(
                draft=email_draft,
                regenerate_fn=_do_compose,
                llm=self.llm,
            )
            span.set_metadata(
                score=tone_result["tone_result"]["overall"],
                regenerated=tone_result["regenerated"],
                attempts=tone_result["attempts"],
            )
        final_body = tone_result["draft"]
        trace["tone_check"] = {
            "score": tone_result["tone_result"]["overall"],
            "regenerated": tone_result["regenerated"],
            "attempts": tone_result["attempts"],
        }

        # Rebuild email_data with final body
        with lf_trace.span("bench_gate") as span:
            email_data = compose_cold_email(
                hiring_signal_brief=brief,
                segment=segment,
                segment_confidence=confidence,
                ai_maturity=ai_maturity,
                contact_name=contact_name,
                contact_title=contact_title,
                llm=self.llm,
            )
            email_data["body"] = final_body
            span.set_metadata(bench_approved=email_data.get("bench_approved", True))

        # --- Step 5: Send (kill-switch gated) -------------------------------
        send_result = {}
        with lf_trace.span("send_email") as span:
            if self.email_client:
                send_result = kill_switch.send_email(
                    client=self.email_client,
                    to=contact_email,
                    subject=email_data["subject"],
                    html=email_data["html"],
                    text=final_body,
                )
            span.set_metadata(
                mode=kill_switch.mode_label(),
                status=send_result.get("status", "no_client"),
            )
        trace["send_result"] = send_result

        # --- Step 6: Thread management ------------------------------------
        thread = self.conv_manager.get_thread(contact_email)
        thread_id = thread["thread_id"]
        self.conv_manager.append_message(
            thread_id, "agent", final_body,
            metadata={"subject": email_data["subject"], "segment": segment},
        )
        self.conv_manager.update_qualification(thread_id, segment, confidence, "outreached")

        # --- Step 7: HubSpot upsert ---------------------------------------
        with lf_trace.span("hubspot_upsert") as span:
            try:
                hs_result = self.hubspot.upsert_enriched_contact(
                    email=contact_email,
                    firstname=contact_name if contact_name != "there" else None,
                    company_name=company_name,
                    contact_title=contact_title,
                    domain=domain,
                    icp_segment=segment,
                    enrichment_signals=json.dumps(brief.get("ai_maturity", {})),
                    enrichment_timestamp=datetime.utcnow().isoformat() + "Z",
                    segment_confidence=str(round(confidence, 3)),
                    ai_maturity_score=str(ai_maturity.get("score", 0)),
                    outreach_status="cold",
                    thread_status="outreached",
                    outbound_variant="signal_grounded" if not abstain else "generic_exploratory",
                )
                trace["hubspot"] = {"id": hs_result.get("id")}
                span.set_metadata(hubspot_id=hs_result.get("id"))
            except Exception as e:
                logger.error(f"HubSpot upsert failed: {e}")
                trace["hubspot"] = {"error": str(e)}

        # --- Step 8: Context brief (for discovery prep) -------------------
        context_brief = self._generate_context_brief(company_name, contact_name, contact_title,
                                                       segment, brief, qual)
        trace["context_brief"] = context_brief

        # --- Step 9: Save artifacts ---------------------------------------
        company_slug = re.sub(r"[^a-z0-9_]", "_", company_name.lower())[:40]
        out_dir = OUTPUTS / company_slug
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "hiring_signal_brief.json").write_text(
            json.dumps(brief, indent=2, default=str), encoding="utf-8"
        )
        (out_dir / "competitor_gap_brief.json").write_text(
            json.dumps(brief.get("competitor_gap", {}), indent=2), encoding="utf-8"
        )
        (out_dir / "email_draft.txt").write_text(final_body, encoding="utf-8")
        (out_dir / "qualification.json").write_text(
            json.dumps(qual, indent=2), encoding="utf-8"
        )
        (out_dir / "context_brief.json").write_text(
            json.dumps(context_brief, indent=2, default=str), encoding="utf-8"
        )

        trace["latency_ms"] = int((time.time() - t0) * 1000)
        trace["output_dir"] = str(out_dir)
        self._write_trace(trace)

        # Flush Langfuse trace
        total_tokens = 0  # would be populated from LLM client if it tracked per-call tokens
        lf_trace.finish(
            segment=segment,
            confidence=confidence,
            ai_maturity_score=ai_maturity.get("score", 0),
            send_status=send_result.get("status", "sandbox"),
            total_tokens=total_tokens,
        )

        logger.info(
            f"[{company_name}] Pipeline complete — "
            f"{segment} ({confidence:.0%}) — "
            f"{trace['latency_ms']}ms — mode={kill_switch.mode_label()}"
        )
        return trace

    # -------------------------------------------------------------------------
    # Context brief generation for discovery call
    # -------------------------------------------------------------------------

    def _generate_context_brief(
        self,
        company_name: str,
        contact_name: str,
        contact_title: str,
        segment: str,
        brief: Dict,
        qual: Dict,
    ) -> Dict:
        """Generate a discovery-call context brief for Alex Rivera."""
        ai_maturity = brief.get("ai_maturity", {})
        funding = brief.get("funding", {})
        layoffs = brief.get("layoffs", {})
        job_vel = brief.get("job_post_velocity", {})
        leadership = brief.get("leadership_change", {})
        competitor_gap = brief.get("competitor_gap", {})
        cb = brief.get("crunchbase_data", {})

        pitch = pitch_language(segment, ai_maturity.get("score", 0))

        # Build grounded talking points (only from confident signals)
        talking_points = []

        if funding.get("stage") and funding.get("last_funding_months", 999) <= 18:
            talking_points.append(
                f"Recent {funding['stage']} ({funding['last_funding_months']} months ago) — "
                f"they're in build mode."
            )
        if layoffs.get("event"):
            talking_points.append(
                f"Layoff event: {layoffs.get('percentage', '?')}% reduction. "
                f"Still hiring ({job_vel.get('open_roles', '?')} open roles). "
                f"Classic augmentation play."
            )
        if leadership.get("event"):
            talking_points.append(
                f"Leadership change: {leadership.get('event', '?')}. "
                f"First 90 days — vendor mix under review."
            )
        ai_score = ai_maturity.get("score", 0)
        if ai_score >= 2:
            signals = ai_maturity.get("signal_summary", [])
            talking_points.append(
                f"AI maturity {ai_score}/3 — {'; '.join(signals[:2])}. "
                f"They have intent, may lack execution capacity."
            )
        gap = competitor_gap.get("top_gap", {})
        if gap.get("practice"):
            talking_points.append(f"Gap finding: {gap['practice']}")

        cautions = []
        stacks = [s for s in cb.get("ml_stack", []) if s]
        for stack in stacks:
            stack_lower = stack.lower()
            # Check bench capacity
            for bench_key in ["ml", "python", "data", "infra"]:
                bench_data = self.bench_gate.bench.get("stacks", {}).get(bench_key, {})
                available = bench_data.get("available_engineers", 0)
                if available == 0:
                    cautions.append(f"No {bench_key} engineers currently available — flag before committing.")
                    break

        context_brief = {
            "prepared_for": "Alex Rivera — Tenacious Intelligence Corporation",
            "call_type": "Discovery call",
            "company": company_name,
            "contact": f"{contact_name}, {contact_title}",
            "segment": segment,
            "segment_rationale": qual.get("reasoning", ""),
            "ai_maturity": f"{ai_score}/3",
            "pitch_angle": pitch,
            "talking_points": talking_points,
            "cautions": cautions,
            "competitor_gap_percentile": competitor_gap.get("company_percentile"),
            "hq_timezone": funding.get("hq_timezone", "unknown"),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        return context_brief

    # -------------------------------------------------------------------------
    # Outbound SMS (warm-lead gated)
    # -------------------------------------------------------------------------

    def send_sms_if_warm(
        self,
        contact_email: str,
        phone: str,
        contact_name: str = "there",
    ) -> Dict:
        """
        Outbound SMS gated on the warm-lead check.

        Gate: ConversationManager.has_email_reply(contact_email) must be True — the
        contact must have sent at least one email reply before any SMS is dispatched.
        Cold contacts always return gate_blocked; no SMS is ever sent to them.

        When the gate passes, a Cal.com self-scheduling link (pre-filled with the
        contact's details) is included in the message body.
        """
        if not self.conv_manager.has_email_reply(contact_email):
            logger.info(
                f"[SMS warm gate] BLOCKED {phone} — {contact_email} has no email reply on record"
            )
            return {
                "status": "gate_blocked",
                "reason": "no email reply on record",
                "contact_email": contact_email,
                "phone": phone,
            }

        booking_link = self.calcom.get_booking_link(
            contact_name=contact_name,
            contact_email=contact_email,
        )
        message = (
            f"Hi {contact_name}, Tenacious here. "
            f"Ready to book your 30-min discovery call? {booking_link}"
        )

        if not self.sms_client:
            logger.warning("[SMS] Client unavailable — skipping send")
            return {"status": "sms_client_unavailable", "booking_link": booking_link}

        result = kill_switch.send_sms(
            client=self.sms_client,
            to=phone,
            message=message,
            bypass_gate=True,  # warm-lead gate already enforced above
        )
        logger.info(f"[SMS] Warm lead {contact_email} → {phone}: status={result.get('status')}")
        result["booking_link"] = booking_link
        return result

    # -------------------------------------------------------------------------
    # Webhook handlers
    # -------------------------------------------------------------------------

    def handle_email_reply(self, contact_email: str, reply_text: str) -> Dict:
        """
        Process an inbound email reply.
        Extracts new signals from reply text, re-qualifies, and composes response.
        """
        thread = self.conv_manager.get_thread(contact_email)
        thread_id = thread["thread_id"]
        self.conv_manager.append_message(thread_id, "prospect", reply_text)

        # --- Extract new signals from reply text ---------------------------
        reply_signals = _extract_reply_signals(reply_text)

        # Load existing brief
        existing_brief = self._load_brief_for_contact(contact_email)

        # Overlay extracted signals
        if reply_signals:
            logger.info(
                f"[reply] Extracted signals from reply: {list(reply_signals.keys())}"
            )
            if reply_signals.get("new_leadership"):
                existing_brief["leadership_change"] = {
                    "event": reply_signals["new_leadership"],
                    "date": None,
                    "headline": reply_signals["new_leadership"],
                    "source": "prospect_reply",
                    "confidence": "high",
                }
            if reply_signals.get("ai_upgrade"):
                old_score = existing_brief.get("ai_maturity", {}).get("score", 0)
                new_score = min(3, old_score + 1)
                existing_brief.setdefault("ai_maturity", {})["score"] = new_score
                existing_brief["ai_maturity"]["confidence"] = "high"
                existing_brief["ai_maturity"].setdefault("signal_summary", []).append(
                    reply_signals["ai_upgrade"]
                )
            if reply_signals.get("layoff"):
                existing_brief["layoffs"] = {
                    "event": "layoff",
                    "date": None,
                    "headcount": 0,
                    "percentage": 0.0,
                    "confidence": "medium",
                }

        # Re-qualify with updated brief
        new_qual = classify(existing_brief)
        old_segment = thread.get("segment", "ABSTAIN")
        new_segment = new_qual["segment"]
        if new_segment != old_segment:
            logger.info(
                f"[reply] Re-qualified: {old_segment} → {new_segment} "
                f"(conf={new_qual['confidence']:.1%})"
            )

        # Compose reply
        history = self.conv_manager.get_context(thread_id)
        stage = thread.get("qualification_state", "outreached")

        reply = compose_reply(
            thread_messages=history,
            prospect_reply=reply_text,
            hiring_signal_brief=existing_brief,
            segment=new_segment,
            stage=stage,
            llm=self.llm,
        )

        # Inject Cal.com self-scheduling link so the prospect can book directly
        booking_link = self.calcom.get_booking_link(
            contact_name="you",
            contact_email=contact_email,
        )
        _SIG = "Tenacious Intelligence Corporation"
        reply_body = reply["body"]
        if _SIG in reply_body and booking_link not in reply_body:
            idx = reply_body.index(_SIG)
            block_start = reply_body.rfind("\n\n", 0, idx)
            insert_at = block_start if block_start != -1 else max(0, idx - 1)
            reply_body = (
                reply_body[:insert_at]
                + f"\n\nBook a 30-minute call: {booking_link}\n"
                + reply_body[insert_at:]
            )
        reply["body"] = reply_body
        reply["html"] = _to_html(reply_body)
        reply["booking_link"] = booking_link

        # Send
        send_result = {}
        if self.email_client:
            send_result = kill_switch.send_email(
                client=self.email_client,
                to=contact_email,
                subject=reply["subject"],
                html=reply["html"],
                text=reply["body"],
            )

        self.conv_manager.append_message(thread_id, "agent", reply["body"])
        self.conv_manager.update_qualification(
            thread_id, new_segment, new_qual["confidence"], "engaged"
        )

        # Update HubSpot
        try:
            contact = self.hubspot.search_contact_by_email(contact_email)
            if contact:
                self.hubspot.update_contact(
                    contact["id"],
                    outreach_status="warm",
                    thread_status="engaged",
                )
        except Exception as e:
            logger.error(f"HubSpot warm update failed: {e}")

        return {
            "reply": reply,
            "send_result": send_result,
            "re_qualified": new_segment != old_segment,
            "new_segment": new_segment,
            "extracted_signals": reply_signals,
        }

    def handle_booking_confirmed(self, contact_email: str, booking_data: Dict) -> Dict:
        """Update thread and HubSpot when a discovery call is booked."""
        thread = self.conv_manager.get_thread(contact_email)
        thread_id = thread["thread_id"]
        self.conv_manager.mark_booked(thread_id, booking_data.get("booking_url"))

        # Generate context brief for the call
        company_name = booking_data.get("title", "").replace("Discovery: ", "")
        brief = self._load_brief_for_contact(contact_email)
        if not company_name:
            company_name = brief.get("company_name", "unknown")

        qual = classify(brief) if brief else {"segment": "ABSTAIN", "confidence": 0, "reasoning": "", "abstain_flag": True}
        context_brief = self._generate_context_brief(
            company_name=company_name,
            contact_name=booking_data.get("attendee_name", ""),
            contact_title=booking_data.get("attendee_title", ""),
            segment=qual.get("segment", "ABSTAIN"),
            brief=brief,
            qual=qual,
        )
        context_brief["booking_time"] = booking_data.get("start", "")
        context_brief["booking_url"] = booking_data.get("booking_url", "")

        # Save context brief
        company_slug = re.sub(r"[^a-z0-9_]", "_", company_name.lower())[:40]
        out_dir = OUTPUTS / company_slug
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "context_brief_call.json").write_text(
            json.dumps(context_brief, indent=2, default=str), encoding="utf-8"
        )

        # Update HubSpot
        try:
            contact = self.hubspot.search_contact_by_email(contact_email)
            if contact:
                self.hubspot.update_contact(
                    contact["id"],
                    outreach_status="engaged",
                    thread_status="discovery_call_booked",
                    last_booked_call_at=datetime.utcnow().isoformat() + "Z",
                )
        except Exception as e:
            logger.error(f"HubSpot booking update failed: {e}")

        # Write trace event
        trace = {
            "event": "booking_confirmed",
            "contact_email": contact_email,
            "booking": booking_data,
            "context_brief": context_brief,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        self._write_trace(trace)
        logger.info(f"Discovery call booked for {contact_email} — context brief saved")
        return context_brief

    # -------------------------------------------------------------------------
    # Batch runner
    # -------------------------------------------------------------------------

    def run_batch(self, prospects: List[Dict]) -> List[Dict]:
        results = []
        for p in prospects:
            try:
                result = self.process_prospect(
                    company_name=p["company_name"],
                    contact_email=p["contact_email"],
                    contact_name=p.get("contact_name", "there"),
                    contact_title=p.get("contact_title", ""),
                    domain=p.get("domain"),
                )
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to process {p.get('company_name', '?')}: {e}")
                results.append({"company": p.get("company_name"), "error": str(e)})
        return results

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _load_brief_for_contact(self, contact_email: str) -> Dict:
        """Load cached hiring_signal_brief for a contact from outputs/."""
        for d in OUTPUTS.iterdir():
            if not d.is_dir():
                continue
            brief_path = d / "hiring_signal_brief.json"
            if brief_path.exists():
                try:
                    with brief_path.open(encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return {}

    def _write_trace(self, trace: Dict) -> None:
        with self._trace_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trace, default=str) + "\n")


# ---------------------------------------------------------------------------
# Reply signal extractor
# ---------------------------------------------------------------------------

_LEADERSHIP_RE = re.compile(
    r'\b(new|just hired|appointed|joined|onboarded|named)\b.{0,60}'
    r'(head of ai|vp (of )?ai|chief ai|cto|vp eng|head of engineering|'
    r'director of ai|ai lead|ml lead)',
    re.I,
)
_AI_UPGRADE_RE = re.compile(
    r'\b(building ai|launching ai|starting ai|ai roadmap|ai initiative|'
    r'ai strategy|generative ai|llm project|agentic|new ai)\b',
    re.I,
)
_LAYOFF_RE = re.compile(
    r'\b(laid off|layoff|reduction in force|rif|restructuring|headcount reduction)\b',
    re.I,
)


def _extract_reply_signals(reply_text: str) -> Dict[str, str]:
    signals = {}
    m = _LEADERSHIP_RE.search(reply_text)
    if m:
        signals["new_leadership"] = m.group(0).strip()
    m2 = _AI_UPGRADE_RE.search(reply_text)
    if m2:
        signals["ai_upgrade"] = m2.group(0).strip()
    if _LAYOFF_RE.search(reply_text):
        signals["layoff"] = "layoff_mentioned_in_reply"
    return signals


# ---------------------------------------------------------------------------
# Synthetic test prospects
# ---------------------------------------------------------------------------

SYNTHETIC_PROSPECTS = [
    {
        "company_name": "DataFlow AI",
        "contact_email": "cto@dataflow.ai.sandbox",
        "contact_name": "Jordan",
        "contact_title": "CTO",
        "domain": "dataflow.ai",
    },
    {
        "company_name": "NovaPay",
        "contact_email": "vp.eng@novapay.sandbox",
        "contact_name": "Morgan",
        "contact_title": "VP Engineering",
        "domain": "novapay.io",
    },
    {
        "company_name": "CloudEdge Systems",
        "contact_email": "founder@cloudedge.sandbox",
        "contact_name": "Alex",
        "contact_title": "Founder & CEO",
        "domain": "cloudedge.io",
    },
    {
        "company_name": "Meridian Health Tech",
        "contact_email": "eng@meridianhealth.sandbox",
        "contact_name": "Taylor",
        "contact_title": "Head of Engineering",
        "domain": "meridianhealth.io",
    },
    {
        "company_name": "Quantum Analytics",
        "contact_email": "cto@quantumanaly.sandbox",
        "contact_name": "Riley",
        "contact_title": "CTO",
        "domain": "quantumanalytics.io",
    },
]


if __name__ == "__main__":
    import sys

    engine = ConversionEngine()

    if len(sys.argv) > 1 and sys.argv[1] == "batch":
        results = engine.run_batch(SYNTHETIC_PROSPECTS)
        latencies = [r.get("latency_ms", 0) for r in results if "latency_ms" in r]
        if latencies:
            latencies.sort()
            n = len(latencies)
            p50 = latencies[n // 2]
            p95 = latencies[min(int(n * 0.95), n - 1)]
            logger.info(
                f"Batch complete: {len(results)} prospects | p50={p50}ms | p95={p95}ms"
            )
        print(json.dumps(results, indent=2, default=str))
    else:
        result = engine.process_prospect(**SYNTHETIC_PROSPECTS[0])
        print(json.dumps(result, indent=2, default=str))
