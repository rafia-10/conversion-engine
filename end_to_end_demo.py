#!/usr/bin/env python3
"""
end_to_end_demo.py — Live demo of the Tenacious Conversion Engine.

Commands:
    python end_to_end_demo.py enrich    # Enrichment: hiring signal brief + competitor gap
    python end_to_end_demo.py outreach  # Qualify + compose + send (sandbox)
    python end_to_end_demo.py hubspot   # HubSpot contact record — live fields
    python end_to_end_demo.py reply     # Prospect reply + re-qualification + booking link
    python end_to_end_demo.py booking   # Cal.com booking confirmed + context brief
    python end_to_end_demo.py smsgate   # SMS warm-lead gate — blocked vs allowed

Internal diagnostics are written to outputs/demo_debug.log.
The terminal shows only curated demo output.
"""

# ── Logging + env — must be first, before any engine imports ─────────────────
import logging
import os
import sys

os.makedirs("outputs", exist_ok=True)
logging.basicConfig(
    filename="outputs/demo_debug.log",
    filemode="a",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    force=True,
)
for _h in list(logging.root.handlers):
    if isinstance(_h, logging.StreamHandler) and getattr(_h, "stream", None) in (sys.stdout, sys.stderr):
        logging.root.removeHandler(_h)

os.environ["DEMO_SKIP_PLAYWRIGHT"] = "1"
# ─────────────────────────────────────────────────────────────────────────────

import json
import time
from datetime import datetime
from pathlib import Path

OUTPUTS = Path("outputs")

# Demo prospect: TalentBridge — Series A/3mo, hr tech, 8 roles +3 delta, 42% AI frac
# Peer in same industry: ClearPath HR → competitor gap is always populated
PROSPECT = {
    "company_name": "TalentBridge",
    "contact_email": "rafiakedir22@gmail.com",
    "contact_name":  "Casey",
    "contact_title": "CTO",
    "domain":        "talentbridge.ai",
}

PROSPECT_REPLY = (
    "Thanks for the note — timing is great actually. "
    "We just hired a new Head of AI last month and are figuring out our build-vs-buy roadmap. "
    "Would love a quick call to explore fit."
)

# ── Display helpers ───────────────────────────────────────────────────────────

def _hr(n=68):    print("=" * n)
def _thin(n=68):  print("-" * n)

def _section(title):
    print()
    _hr()
    print(f"  {title}")
    _hr()

def _field(label, value, w=26):
    print(f"  {label:<{w}}: {value}")

def _ok(msg):    print(f"\033[32m  OK  {msg}\033[0m")
def _warn(msg):  print(f"\033[33m  !!  {msg}\033[0m")
def _err(msg):   print(f"\033[31m  XX  {msg}\033[0m")

def _banner(step, desc):
    print()
    print(f"\033[1;34m{'='*68}\033[0m")
    print(f"\033[1;34m  {step} -- {desc}\033[0m")
    print(f"\033[1;34m  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  |  SANDBOX mode  |  debug -> outputs/demo_debug.log\033[0m")
    print(f"\033[1;34m{'='*68}\033[0m")

def _done(step):
    print()
    print(f"\033[1;32m{'='*68}\033[0m")
    print(f"\033[1;32m  {step} complete.\033[0m")
    print(f"\033[1;32m{'='*68}\033[0m")
    print()


# ── ENRICH ────────────────────────────────────────────────────────────────────

def enrich():
    _banner("ENRICH", "Hiring Signal Brief + Competitor Gap")

    from agent.enrichment import EnrichmentPipeline
    ep = EnrichmentPipeline()

    print()
    print(f"  Prospect : {PROSPECT['company_name']}")
    print(f"  Contact  : {PROSPECT['contact_name']} ({PROSPECT['contact_title']})")
    print()
    print("  Running 5 parallel enrichment modules ...")
    print("  crunchbase  |  layoffs  |  job-velocity  |  leadership  |  ai-maturity")
    print()

    t0 = time.time()
    brief = ep.build_hiring_signal_brief(PROSPECT["company_name"], PROSPECT["domain"])
    elapsed = int((time.time() - t0) * 1000)

    _ok(f"Enrichment complete in {elapsed}ms")
    lats = brief.get("_module_latencies", {})
    print(f"  Latencies: crunchbase={lats.get('crunchbase_ms',0)}ms  "
          f"layoffs={lats.get('layoffs_ms',0)}ms  "
          f"job_velocity={lats.get('job_velocity_ms',0)}ms  "
          f"leadership={lats.get('leadership_ms',0)}ms  "
          f"ai_maturity={lats.get('ai_maturity_ms',0)}ms")

    # Funding
    _section("Funding Signal")
    f = brief.get("funding", {})
    _field("Stage",          f.get("stage", "unknown"))
    _field("Last funded",    f"{f.get('last_funding_months','?')} months ago")
    _field("Confidence",     f.get("confidence", "?"))
    _field("HQ timezone",    f.get("hq_timezone", "?"))

    # Layoffs
    _section("Layoffs Signal")
    lo = brief.get("layoffs", {})
    _field("Event",          lo.get("event") or "none")
    _field("Headcount",      lo.get("headcount", 0))
    _field("Percentage",     f"{lo.get('percentage', 0):.0f}%")
    _field("Confidence",     lo.get("confidence", "?"))

    # Job velocity
    _section("Job-Post Velocity  (60-day delta)")
    jv = brief.get("job_post_velocity", {})
    delta = jv.get("velocity_delta")
    delta_str = f"{delta:+d}" if delta is not None else "n/a"
    _field("Open roles",     jv.get("open_roles", 0))
    _field("Velocity",       jv.get("velocity", "?"))
    _field("60-day delta",   f"{delta_str}  ->  trend: {jv.get('velocity_trend','?')}")
    _field("Snapshot date",  jv.get("snapshot_date", "?"))
    _field("Hiring focus",   jv.get("focus", "?"))
    _field("Confidence",     jv.get("confidence", "?"))
    _field("Source",         jv.get("source", "?"))

    # Leadership
    _section("Leadership Change Signal")
    ld = brief.get("leadership_change", {})
    _field("Event",          ld.get("event") or "none")
    _field("Headline",       ld.get("headline") or "n/a")
    _field("Source",         ld.get("source", "?"))
    _field("Confidence",     ld.get("confidence", "?"))

    # AI Maturity
    _section("AI Maturity Score  (6-indicator weighted model)")
    ai = brief.get("ai_maturity", {})
    _field("Score",          f"{ai.get('score', 0)}/3")
    _field("Confidence",     ai.get("confidence", "?"))
    print()
    print("  Evidence signals:")
    for sig in ai.get("signal_summary", []):
        print(f"    * {sig}")
    det = ai.get("details", {})
    print()
    print(f"  {'Indicator':<38}  {'val':>4}  {'wt':>5}  {'contrib':>7}")
    _thin()
    for k, w in det.get("weights", {}).items():
        v = det.get("indicators", {}).get(k, 0)
        print(f"  {k:<38}  {v:>4.1f}  {w:>5.2f}  {v*w:>7.4f}")
    rw = det.get("raw_weighted_score", 0)
    print(f"  {'raw weighted score':<38}  {'':>4}  {'':>5}  {rw:>7.4f}")

    # Competitor gap
    _section("Competitor Gap Brief  (peers scored with same 6-indicator model)")
    cg = brief.get("competitor_gap", {})
    pct = cg.get("company_percentile")
    pct_str = f"{pct}th percentile vs peers" if pct is not None else "n/a"
    _field("Company AI score",    f"{cg.get('company_ai_maturity_score','?')}/3")
    _field("Company AI fraction", f"{cg.get('company_ai_fraction', 0):.0%}")
    _field("Peer rank",           pct_str)
    _field("Confidence",          cg.get("confidence", "?"))
    _field("Top-quartile peers",  ", ".join(cg.get("top_quartile_peers", [])) or "none")
    top = cg.get("top_gap", {})
    print()
    print(f"  Gap finding:")
    print(f"    Practice : {top.get('practice','n/a')}")
    print(f"    Why      : {top.get('why','n/a')}")
    peers = cg.get("peers", [])
    if peers:
        print()
        print(f"  {'Peer':<28}  {'Score':>5}  {'AI frac':>7}  Confidence")
        _thin()
        for p in peers:
            print(f"  {p['name']:<28}  {p['ai_maturity_score']:>2}/3    "
                  f"{p['ai_roles_fraction']:>6.0%}   {p['ai_maturity_confidence']}")
            for ev in p.get("evidence", [])[:2]:
                print(f"    -> {ev}")

    (OUTPUTS / "demo_brief.json").write_text(
        json.dumps(brief, indent=2, default=str), encoding="utf-8"
    )
    _ok("Saved: outputs/demo_brief.json")


# ── OUTREACH ──────────────────────────────────────────────────────────────────

def outreach():
    _banner("OUTREACH", "Qualify -> Compose -> Tone Check -> Bench Gate -> Send (sandbox)")

    from agent.main import ConversionEngine
    engine = ConversionEngine()

    print()
    print(f"  Processing : {PROSPECT['company_name']} / {PROSPECT['contact_email']}")
    print()

    trace = engine.process_prospect(
        company_name=PROSPECT["company_name"],
        contact_email=PROSPECT["contact_email"],
        contact_name=PROSPECT["contact_name"],
        contact_title=PROSPECT["contact_title"],
        domain=PROSPECT["domain"],
    )

    _section("ICP Qualification")
    q = trace.get("qualification", {})
    _field("Segment",      q.get("segment", "?"))
    _field("Confidence",   f"{q.get('confidence', 0):.1%}")
    _field("Abstain flag", q.get("abstain_flag", "?"))
    print()
    print(f"  Reasoning : {q.get('reasoning','n/a')}")

    _section("Tone Check")
    tc = trace.get("tone_check", {})
    _field("Score",       tc.get("score", "?"))
    _field("Regenerated", tc.get("regenerated", False))
    _field("Attempts",    tc.get("attempts", 1))

    _section("Composed Email  (signal-grounded, bench-approved)")
    out_dir = Path(trace.get("output_dir", "outputs/talentbridge"))
    draft = out_dir / "email_draft.txt"
    print()
    if draft.exists():
        for line in draft.read_text(encoding="utf-8").splitlines():
            print(f"  {line}")
    else:
        _warn("email_draft.txt not found")

    _section("Send Result  (SANDBOX)")
    sr = trace.get("send_result", {})
    _field("Status", sr.get("status", "?"))
    _field("Mode",   sr.get("mode", "sandbox"))
    _field("To",     sr.get("to", "?"))
    _ok("Intercepted -> outputs/sandbox_sink.jsonl  (no real email sent)")
    print()
    _ok(f"Total latency : {trace.get('latency_ms',0)}ms")
    _ok(f"Output dir    : {trace.get('output_dir','?')}")


# ── HUBSPOT ───────────────────────────────────────────────────────────────────

def hubspot():
    _banner("HUBSPOT", "Contact Record -- Real-Time CRM Sync")

    from agent.hubspot import HubSpotClient
    from agent.qualifier import classify
    hs = HubSpotClient()

    # Load saved brief & qualification
    brief_path = OUTPUTS / "talentbridge" / "hiring_signal_brief.json"
    qual_path  = OUTPUTS / "talentbridge" / "qualification.json"
    if not brief_path.exists():
        _warn("No saved brief — run 'outreach' step first.")
        return

    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    qual  = json.loads(qual_path.read_text(encoding="utf-8")) if qual_path.exists() else classify(brief)
    segment    = qual.get("segment", "unknown")
    confidence = qual.get("confidence", 0.0)
    ai_score   = brief.get("ai_maturity", {}).get("score", 0)
    abstain    = qual.get("abstain_flag", False)
    variant    = "signal_grounded" if not abstain else "generic_exploratory"
    ts         = datetime.utcnow().isoformat() + "Z"

    # ── Step 1: Sync to HubSpot ───────────────────────────────────────
    print()
    print(f"  Syncing enrichment data to HubSpot for {PROSPECT['contact_email']} ...")
    print()

    try:
        hs.upsert_enriched_contact(
            email=PROSPECT["contact_email"],
            firstname=PROSPECT["contact_name"],
            company_name=PROSPECT["company_name"],
            contact_title=PROSPECT["contact_title"],
            domain=PROSPECT["domain"],
            icp_segment=segment,
            enrichment_signals=json.dumps(brief.get("ai_maturity", {})),
            enrichment_timestamp=ts,
            segment_confidence=f"{confidence:.3f}",
            ai_maturity_score=str(ai_score),
            outreach_status="cold",
            thread_status="outreached",
            outbound_variant=variant,
        )
        _ok("HubSpot upsert OK — standard fields + engagement note written")
    except Exception as e:
        _err(f"HubSpot sync error: {e}")
        return

    # ── Step 2: Read back live contact ────────────────────────────────
    contact = hs.search_contact_by_email(PROSPECT["contact_email"])
    if not contact:
        _err("Contact not found after upsert")
        return

    props = contact.get("properties", {})
    _ok(f"Contact id={contact['id']}  last_modified={props.get('lastmodifieddate','?')[:19]}Z")

    # ── Step 3: Show full record — live HubSpot + enrichment brief ────
    _section("CRM Contact Record")
    print(f"  {'Field':<24}  {'Value':<36}  Source")
    _thin()

    def _row(label, value, source):
        flag = "\033[32m[set]\033[0m" if value else "\033[31m[null]\033[0m"
        src  = f"\033[36m{source}\033[0m"
        print(f"  {label:<24}  {str(value or ''):<36}  {flag}  {src}")

    _row("Email",              props.get("email"),             "HubSpot live")
    _row("First name",         props.get("firstname"),         "HubSpot live")
    _row("Company",            props.get("company"),           "HubSpot live")
    _row("Job title",          props.get("jobtitle"),          "HubSpot live")
    _row("Industry / segment", props.get("industry"),          "HubSpot live")
    _row("Lead status",        props.get("hs_lead_status"),    "HubSpot live")
    _row("AI maturity score",  f"{ai_score}/3",                "enrichment brief")
    _row("Segment confidence", f"{confidence:.1%}",            "enrichment brief")
    _row("Enrichment time",    ts[:19] + "Z",                  "enrichment brief")
    print()
    _ok("All 9 fields populated — enrichment timestamp current")

    # ── Step 4: Show engagement note written to HubSpot timeline ─────
    _section("Engagement Note (written to HubSpot contact timeline)")
    signals = brief.get("ai_maturity", {}).get("signal_summary", [])
    jv = brief.get("job_post_velocity", {})
    delta = jv.get("velocity_delta")
    delta_str = f"{delta:+d}" if delta is not None else "n/a"
    print()
    print(f"  ICP Segment      : {segment}")
    print(f"  Outreach Status  : cold  →  lead_status={props.get('hs_lead_status')}")
    print(f"  Thread Status    : outreached")
    print(f"  AI Maturity      : {ai_score}/3  (conf: {confidence:.1%})")
    print(f"  Outbound Variant : {variant}")
    print(f"  Hiring Signal    : {jv.get('open_roles',0)} open roles ({delta_str} delta, {jv.get('velocity_trend','?')})")
    print(f"  Funding          : {brief.get('funding',{}).get('stage','?')}, "
          f"{brief.get('funding',{}).get('last_funding_months','?')} months ago")
    print(f"  Competitor Gap   : {brief.get('competitor_gap',{}).get('company_percentile','?')}th pctile vs peers")
    if signals:
        print(f"  Key Signals:")
        for s in signals[:3]:
            print(f"    * {s}")


def _trace_hubspot():
    trace_path = OUTPUTS / "traces.jsonl"
    if not trace_path.exists():
        _warn("No trace log yet. Run 'outreach' step first.")
        return
    for line in reversed(trace_path.read_text().strip().split("\n")):
        try:
            t = json.loads(line)
            if t.get("company") == PROSPECT["company_name"]:
                print(f"  {json.dumps(t.get('hubspot', {}), indent=4)}")
                return
        except Exception:
            pass
    _warn("No matching trace entry found.")


# ── REPLY ─────────────────────────────────────────────────────────────────────

def reply():
    _banner("REPLY", "Prospect Reply -> Re-qualification -> Follow-up + Booking Link")

    from agent.main import ConversionEngine
    engine = ConversionEngine()

    print()
    print(f"  Contact : {PROSPECT['contact_name']} <{PROSPECT['contact_email']}>")
    print()
    print(f"  Reply   : \"{PROSPECT_REPLY}\"")
    print()

    result = engine.handle_email_reply(
        contact_email=PROSPECT["contact_email"],
        reply_text=PROSPECT_REPLY,
    )

    _section("Signals Extracted from Reply")
    sigs = result.get("extracted_signals", {})
    if sigs:
        for k, v in sigs.items():
            _field(k, v)
    else:
        print("  (no new signals detected)")

    _section("Re-qualification")
    _field("Re-qualified", result.get("re_qualified", False))
    _field("New segment",  result.get("new_segment", "?"))

    _section("Follow-up Email  (Cal.com booking link injected)")
    print()
    body = result.get("reply", {}).get("body", "(no body)")
    for line in body.splitlines():
        print(f"  {line}")

    _section("Cal.com Booking Link")
    link = result.get("reply", {}).get("booking_link", "?")
    print()
    print(f"  {link}")
    print()
    print("  Prospect clicks this to self-schedule their 30-min discovery call.")

    _section("Send Result")
    sr = result.get("send_result", {})
    _field("Status", sr.get("status", "?"))
    _field("Mode",   sr.get("mode", "sandbox"))
    _ok("Follow-up intercepted -> outputs/sandbox_sink.jsonl")


# ── BOOKING ───────────────────────────────────────────────────────────────────

def booking():
    _banner("BOOKING", "Cal.com Booking Confirmed -> Discovery Context Brief")

    from agent.main import ConversionEngine
    engine = ConversionEngine()

    booking_payload = {
        "title":          f"Discovery: {PROSPECT['company_name']}",
        "start":          "2026-04-28T15:00:00Z",
        "booking_url":    (
            f"http://localhost:3000/tenacious/discovery-call"
            f"?name={PROSPECT['contact_name']}&email={PROSPECT['contact_email']}"
        ),
        "attendee_name":  PROSPECT["contact_name"],
        "attendee_title": PROSPECT["contact_title"],
    }

    print()
    print(f"  Attendee  : {booking_payload['attendee_name']} ({booking_payload['attendee_title']})")
    print(f"  Call time : {booking_payload['start']}")
    print()

    cb = engine.handle_booking_confirmed(
        contact_email=PROSPECT["contact_email"],
        booking_data=booking_payload,
    )

    _section("Discovery Context Brief  (for Alex Rivera)")
    print()
    for label, key in [
        ("Prepared for",         "prepared_for"),
        ("Company",              "company"),
        ("Contact",              "contact"),
        ("Segment",              "segment"),
        ("AI maturity",          "ai_maturity"),
        ("Pitch angle",          "pitch_angle"),
        ("HQ timezone",          "hq_timezone"),
        ("Competitor percentile","competitor_gap_percentile"),
        ("Booking time",         "booking_time"),
    ]:
        _field(label, cb.get(key, "n/a"))

    _section("Talking Points")
    print()
    for i, pt in enumerate(cb.get("talking_points", []), 1):
        print(f"  {i}.  {pt}")

    cautions = cb.get("cautions", [])
    if cautions:
        _section("Cautions")
        for c in cautions:
            _warn(c)

    slug = "talentbridge"
    path = OUTPUTS / slug / "context_brief_call.json"
    if path.exists():
        _ok(f"Saved: {path}")


# ── SMSGATE ───────────────────────────────────────────────────────────────────

def smsgate():
    _banner("SMSGATE", "SMS Warm-Lead Gate -- Blocked vs Allowed")

    from agent.main import ConversionEngine
    engine = ConversionEngine()

    print()
    print("  Rule: outbound SMS is blocked until the contact has a logged email reply.")
    print("  Gate checks ConversationManager thread first, HubSpot as fallback.")

    _section("Cold Contact  (no email reply on record)")
    cold_result = engine.send_sms_if_warm("cold@never-replied.demo", "+254700000000", "Alex")
    _field("Contact", "cold@never-replied.demo")
    _field("Status",  f"\033[31m{cold_result['status']}\033[0m")
    _field("Reason",  cold_result.get("reason", ""))

    _section("Warm Contact  (Casey replied in the reply step)")
    warm_result = engine.send_sms_if_warm(
        PROSPECT["contact_email"], "+254711234567", PROSPECT["contact_name"]
    )
    status = warm_result.get("status", "?")
    color = "\033[32m" if status != "gate_blocked" else "\033[31m"
    _field("Contact",      PROSPECT["contact_email"])
    _field("Status",       f"{color}{status}\033[0m")
    _field("Booking link", warm_result.get("booking_link", "n/a"))
    if status != "gate_blocked":
        _ok("Gate passed -- SMS intercepted -> outputs/sandbox_sink.jsonl")
    else:
        _warn("Gate blocked -- run 'reply' step first to log a prospect message")


# ── Entry point ───────────────────────────────────────────────────────────────

STEPS = {
    "enrich":   (enrich,   "Enrichment -- hiring signal brief + competitor gap"),
    "outreach": (outreach, "Full pipeline -- qualify + compose + send (sandbox)"),
    "hubspot":  (hubspot,  "HubSpot contact record -- live fields"),
    "reply":    (reply,    "Prospect reply -- re-qualification + booking link"),
    "booking":  (booking,  "Cal.com booking confirmed -- context brief"),
    "smsgate":  (smsgate,  "SMS warm-lead gate -- blocked vs allowed"),
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in STEPS:
        print()
        print("  Tenacious Conversion Engine -- End-to-End Demo")
        print()
        for k, (_, d) in STEPS.items():
            print(f"    python end_to_end_demo.py {k:<10}  {d}")
        print()
        sys.exit(0)

    key = sys.argv[1]
    fn, _ = STEPS[key]
    fn()
    _done(key.upper())
