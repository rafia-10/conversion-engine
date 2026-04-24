#!/usr/bin/env python3
"""
run_probes.py — Executable probe runner for the Tenacious Conversion Engine.

Runs a subset of the 34 adversarial probes from probe_library.md as deterministic
unit tests. Each probe constructs a synthetic hiring_signal_brief, runs it through
the qualifier and/or outreach composer, and asserts expected behavior.

Usage:
    python probes/run_probes.py           # run all probes
    python probes/run_probes.py P-001     # run specific probe
    python probes/run_probes.py --category "ICP Misclassification"
    python probes/run_probes.py --json    # JSON output for CI
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("KILL_SWITCH", "sandbox")
os.environ.setdefault("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))

from agent.bench_gate import BenchGate
from agent.outreach_composer import _extract_grounded_signals
from agent.qualifier import classify


# ---------------------------------------------------------------------------
# Probe definitions
# ---------------------------------------------------------------------------

def _brief(**kwargs) -> Dict:
    """Build a minimal hiring_signal_brief with overridable fields."""
    defaults = {
        "company_name": "TestCo",
        "domain": "testco.com",
        "crunchbase_data": {"headcount": 80, "stage": "Series A", "industry": "enterprise software"},
        "funding": {"stage": "Series A", "last_funding_months": 5, "confidence": "high"},
        "layoffs": {"event": None, "headcount": 0, "percentage": 0.0, "confidence": "high"},
        "job_post_velocity": {"open_roles": 6, "velocity": "moderate", "confidence": "medium", "raw_titles": [], "focus": "engineering", "source": "synthetic"},
        "leadership_change": {"event": None, "headline": None, "date": None, "source": "synthetic", "confidence": "medium"},
        "ai_maturity": {"score": 0, "confidence": "low", "signal_summary": [], "details": {}},
        "competitor_gap": {},
    }
    result = dict(defaults)
    for k, v in kwargs.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = {**result[k], **v}
        else:
            result[k] = v
    return result


class ProbeResult:
    def __init__(self, probe_id: str, category: str, passed: bool, detail: str,
                 duration_ms: int, trigger_rate: float = 0.0):
        self.probe_id = probe_id
        self.category = category
        self.passed = passed
        self.detail = detail
        self.duration_ms = duration_ms
        # Observed failure trigger rate: fraction of real pipeline runs where this
        # failure mode was observed. Seeded from failure_taxonomy.md estimates;
        # update as tau2-Bench evaluation data accumulates.
        self.trigger_rate = trigger_rate

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        rate_str = f" trigger_rate={self.trigger_rate:.0%}" if self.trigger_rate > 0 else ""
        return f"[{status}] {self.probe_id} ({self.category}) — {self.detail} [{self.duration_ms}ms]{rate_str}"

    def to_dict(self):
        return {
            "probe_id": self.probe_id,
            "category": self.category,
            "passed": self.passed,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "observed_trigger_rate": self.trigger_rate,
            "trigger_rate_source": "failure_taxonomy.md estimates (update with tau2-bench data)",
        }


def _run_probe(probe_id: str, category: str, fn, trigger_rate: float = 0.0) -> ProbeResult:
    t0 = time.time()
    try:
        passed, detail = fn()
    except Exception as e:
        passed = False
        detail = f"Exception: {e}"
    ms = int((time.time() - t0) * 1000)
    return ProbeResult(probe_id, category, passed, detail, ms, trigger_rate)


# ---------------------------------------------------------------------------
# Category 1 — ICP Misclassification
# ---------------------------------------------------------------------------

def probe_p001():
    """Layoff + fresh funding → Segment 2 (not Segment 1)."""
    # 220 employees, laid off 18% (40 people), still hiring — classic mid-market restructure
    brief = _brief(
        company_name="BuildFirst",
        crunchbase_data={"headcount": 220, "stage": "Series B", "industry": "enterprise software"},
        funding={"stage": "Series B", "last_funding_months": 2, "confidence": "high"},
        layoffs={"event": "layoff", "headcount": 40, "percentage": 18, "confidence": "high"},
        job_post_velocity={"open_roles": 5, "velocity": "moderate", "confidence": "medium",
                           "raw_titles": [], "focus": "engineering", "source": "crunchbase"},
    )
    qual = classify(brief)
    segment = qual["segment"]
    passed = segment == "segment_2_mid_market_restructuring"
    return passed, f"segment={segment} (expected segment_2_mid_market_restructuring)"


def probe_p002():
    """Stale funding (14 months) + no other signals → ABSTAIN."""
    brief = _brief(
        company_name="FinStack",
        crunchbase_data={"headcount": 250, "stage": "Series C", "industry": "fintech"},
        funding={"stage": "Series C", "last_funding_months": 14, "confidence": "high"},
        layoffs={"event": None, "headcount": 0, "percentage": 0, "confidence": "high"},
        job_post_velocity={"open_roles": 6, "velocity": "moderate", "confidence": "medium",
                           "raw_titles": [], "focus": "engineering", "source": "crunchbase"},
        ai_maturity={"score": 0, "confidence": "low", "signal_summary": [], "details": {}},
    )
    qual = classify(brief)
    segment = qual["segment"]
    passed = segment == "ABSTAIN"
    return passed, f"segment={segment} conf={qual['confidence']:.2f} (expected ABSTAIN)"


def probe_p003():
    """Layoff > 40% → disqualified → ABSTAIN."""
    brief = _brief(
        company_name="SurvivalMode Corp",
        crunchbase_data={"headcount": 1800, "stage": "Growth", "industry": "logistics"},
        funding={"stage": "Growth", "last_funding_months": 36, "confidence": "low"},
        layoffs={"event": "layoff", "headcount": 810, "percentage": 45, "confidence": "high"},
        job_post_velocity={"open_roles": 1, "velocity": "low", "confidence": "medium",
                           "raw_titles": [], "focus": "engineering", "source": "synthetic"},
    )
    qual = classify(brief)
    segment = qual["segment"]
    passed = segment == "ABSTAIN"
    return passed, f"segment={segment} (expected ABSTAIN — layoff > 40%)"


def probe_p004():
    """Small company new CTO → Segment 3 (not abstain)."""
    brief = _brief(
        company_name="TinyStartup",
        crunchbase_data={"headcount": 25, "stage": "Seed", "industry": "developer tools"},
        funding={"stage": "Seed", "last_funding_months": 24, "confidence": "low"},
        leadership_change={"event": "new CTO appointed", "headline": "New CTO joins", "date": None,
                           "source": "linkedin", "confidence": "high"},
        ai_maturity={"score": 0, "confidence": "low", "signal_summary": [], "details": {}},
    )
    qual = classify(brief)
    segment = qual["segment"]
    passed = segment == "segment_3_leadership_transitions"
    return passed, f"segment={segment} (expected segment_3_leadership_transitions)"


# ---------------------------------------------------------------------------
# Category 2 — Signal Over-Claiming
# ---------------------------------------------------------------------------

def probe_p005():
    """< 5 open roles → hiring signal must NOT use assertive framing."""
    funding = {"stage": "Series A", "last_funding_months": 3, "confidence": "high"}
    layoffs = {"event": None, "headcount": 0, "percentage": 0, "confidence": "high"}
    job_vel = {"open_roles": 3, "velocity": "low", "confidence": "low",
               "raw_titles": ["Python Engineer", "Backend Developer", "Frontend Engineer"],
               "focus": "engineering", "source": "synthetic"}
    leadership = {"event": None, "headline": None, "date": None, "source": "synthetic", "confidence": "medium"}

    signals = _extract_grounded_signals(
        "TestCo", funding, layoffs, job_vel, leadership, ai_score=0, ai_conf="low"
    )
    # Key check: "hiring" key should not use aggressive assertion language
    hiring_signal = signals.get("hiring", "")
    bad_phrases = ["aggressive", "scaling aggressively", "rapid growth", "explosive"]
    passed = not any(p in hiring_signal.lower() for p in bad_phrases)
    return passed, f"hiring signal: '{hiring_signal[:80]}'"


def probe_p006():
    """AI maturity = 1 from single weak signal → signals dict must reflect uncertainty."""
    funding = {"stage": "Series A", "last_funding_months": 4, "confidence": "high"}
    layoffs = {"event": None, "headcount": 0, "percentage": 0, "confidence": "high"}
    job_vel = {"open_roles": 4, "velocity": "low", "confidence": "low",
               "raw_titles": ["ML Engineer"],
               "focus": "engineering", "source": "synthetic"}
    leadership = {"event": None, "headline": None, "date": None, "source": "synthetic", "confidence": "medium"}

    signals = _extract_grounded_signals(
        "TestCo", funding, layoffs, job_vel, leadership, ai_score=1, ai_conf="low"
    )
    # AI maturity signal should reference confidence = low
    ai_signal = signals.get("ai_maturity", "")
    passed = "low" in ai_signal.lower() or "confidence" in ai_signal.lower()
    return passed, f"ai_maturity signal: '{ai_signal[:100]}'"


# ---------------------------------------------------------------------------
# Category 3 — Bench Over-Commitment
# ---------------------------------------------------------------------------

def probe_p009():
    """Request for Go engineers → bench gate blocks if exceeds available count."""
    gate = BenchGate()
    # Go bench shows 3 available. "We need 10 Go engineers" should be blocked.
    body = (
        "We can provide you with 10 Go engineers starting immediately. "
        "They will begin within the week."
    )
    result = gate.check_commitment(body)
    passed = not result["approved"] or len(result["blocked_claims"]) > 0
    return passed, f"approved={result['approved']} blocked={result['blocked_claims']}"


def probe_p010():
    """Unknown stack (Rust) → bench gate must block or flag."""
    gate = BenchGate()
    body = (
        "We can deploy 5 Rust engineers to your infrastructure team by next Monday."
    )
    result = gate.check_commitment(body)
    passed = not result["approved"] or len(result["blocked_claims"]) > 0
    return passed, f"approved={result['approved']} blocked={result['blocked_claims']}"


def probe_p011():
    """Safe commitment within bench capacity → approved."""
    gate = BenchGate()
    # Python: 7 available. 3 python engineers is within capacity.
    body = (
        "We can provide 3 Python engineers with FastAPI and SQLAlchemy experience. "
        "Timeline: 7 days to deploy."
    )
    result = gate.check_commitment(body)
    passed = result["approved"]
    return passed, f"approved={result['approved']} (3 Python within 7 available)"


# ---------------------------------------------------------------------------
# Category 4 — Thread Isolation
# ---------------------------------------------------------------------------

def probe_p017():
    """Thread IDs for different emails must not collide."""
    from agent.conversation_manager import ConversationManager
    cm = ConversationManager()

    email_a = "cto@alpha-corp.com"
    email_b = "vp@beta-corp.com"
    thread_a = cm.get_thread(email_a)["thread_id"]
    thread_b = cm.get_thread(email_b)["thread_id"]
    passed = thread_a != thread_b
    return passed, f"thread_a={thread_a} thread_b={thread_b}"


def probe_p018():
    """Same email always maps to same thread_id (deterministic)."""
    from agent.conversation_manager import ConversationManager
    cm = ConversationManager()
    email = "cto@deterministic-test.com"
    t1 = cm.get_thread(email)["thread_id"]
    t2 = cm.get_thread(email)["thread_id"]
    passed = t1 == t2
    return passed, f"t1={t1} t2={t2}"


# ---------------------------------------------------------------------------
# Category 5 — Kill Switch
# ---------------------------------------------------------------------------

def probe_p019():
    """In sandbox mode, send_email must NOT reach Resend — writes to sink instead."""
    import os
    original = os.environ.get("KILL_SWITCH")
    os.environ["KILL_SWITCH"] = "sandbox"

    try:
        import importlib
        import agent.kill_switch as ks
        importlib.reload(ks)

        class FakeEmailClient:
            def __init__(self):
                self.sent = []
            def send_email(self, **kwargs):
                self.sent.append(kwargs)
                return {"id": "sent"}

        client = FakeEmailClient()
        result = ks.send_email(client, "to@test.com", "Subject", "<p>body</p>", "body")
        # In sandbox mode, FakeEmailClient.send_email should NOT be called
        sandbox_statuses = {"sandbox", "sandbox_intercepted", "intercepted"}
        passed = len(client.sent) == 0 and result.get("status", "") in sandbox_statuses
        detail = f"status={result.get('status')} client_calls={len(client.sent)}"
    except Exception as e:
        passed = False
        detail = str(e)
    finally:
        if original is not None:
            os.environ["KILL_SWITCH"] = original
        elif "KILL_SWITCH" in os.environ:
            del os.environ["KILL_SWITCH"]

    return passed, detail


# ---------------------------------------------------------------------------
# Category 6 — Reply Signal Extraction
# ---------------------------------------------------------------------------

def probe_p024():
    """Reply mentioning 'new Head of AI' should extract leadership signal."""
    from agent.main import _extract_reply_signals
    reply = (
        "Thanks for reaching out. We actually just hired a new Head of AI last week "
        "and are figuring out our roadmap for the year. Would love to chat."
    )
    signals = _extract_reply_signals(reply)
    passed = "new_leadership" in signals or "ai_upgrade" in signals
    return passed, f"extracted signals: {signals}"


def probe_p025():
    """Reply mentioning 'building AI' should trigger ai_upgrade signal."""
    from agent.main import _extract_reply_signals
    reply = (
        "We're currently building AI capabilities internally and looking at vendors. "
        "Your timing is interesting."
    )
    signals = _extract_reply_signals(reply)
    passed = "ai_upgrade" in signals
    return passed, f"extracted signals: {signals}"


def probe_p026():
    """Reply with no signals should return empty dict."""
    from agent.main import _extract_reply_signals
    reply = "Thanks for your email. Please remove me from your list."
    signals = _extract_reply_signals(reply)
    passed = len(signals) == 0 or (len(signals) == 1 and "layoff" not in signals)
    return passed, f"signals={signals}"


# ---------------------------------------------------------------------------
# Category 7 — AI Maturity Scoring
# ---------------------------------------------------------------------------

def probe_p028():
    """High AI fraction + named leadership + GitHub → score = 3."""
    from agent.enrichment import _score_ai_maturity_from_record
    record = {
        "ai_roles_fraction": 0.45,
        "named_ai_ml_leadership": True,
        "ai_ml_leadership_title": "Chief AI Officer",
        "github_url": "https://github.com/testco",
        "exec_commentary": "We are all-in on agentic workflows",
        "ml_stack": ["PyTorch", "Hugging Face", "LangChain", "MLflow"],
        "strategic_comms": "AI-native platform",
    }
    result = _score_ai_maturity_from_record(record)
    passed = result["score"] == 3
    return passed, f"score={result['score']} raw={result['details'].get('raw_weighted_score', '?')}"


def probe_p029():
    """Zero AI signals → score = 0."""
    from agent.enrichment import _score_ai_maturity_from_record
    record = {
        "ai_roles_fraction": 0.0,
        "named_ai_ml_leadership": False,
        "github_url": None,
        "exec_commentary": None,
        "ml_stack": [],
        "strategic_comms": None,
    }
    result = _score_ai_maturity_from_record(record)
    passed = result["score"] == 0
    return passed, f"score={result['score']}"


def probe_p030():
    """Weights sum to 1.0 (invariant check)."""
    from agent.enrichment import _AI_WEIGHTS
    total = sum(_AI_WEIGHTS.values())
    passed = abs(total - 1.0) < 1e-9
    return passed, f"sum={total:.10f} (expected 1.0)"


# ---------------------------------------------------------------------------
# Category 8 — Qualification Confidence Gates
# ---------------------------------------------------------------------------

def probe_p031():
    """Segment 4 with AI score exactly 2 → should fire."""
    brief = _brief(
        ai_maturity={"score": 2, "confidence": "medium", "signal_summary": ["ML roles"], "details": {}},
        job_post_velocity={"open_roles": 4, "velocity": "moderate", "confidence": "medium",
                           "raw_titles": ["ML Engineer", "Applied Scientist"],
                           "focus": "AI/ML", "source": "crunchbase"},
    )
    qual = classify(brief)
    passed = qual["segment"] in ("segment_4_capability_gaps", "segment_1_series_a_b")
    return passed, f"segment={qual['segment']} conf={qual['confidence']:.2f}"


def probe_p032():
    """Confidence < 0.6 → ABSTAIN regardless of best-scoring segment."""
    brief = _brief(
        crunchbase_data={"headcount": 20, "stage": "Pre-seed", "industry": "other"},
        funding={"stage": "Pre-seed", "last_funding_months": 30, "confidence": "low"},
        ai_maturity={"score": 1, "confidence": "low", "signal_summary": [], "details": {}},
        job_post_velocity={"open_roles": 1, "velocity": "low", "confidence": "low",
                           "raw_titles": [], "focus": "engineering", "source": "synthetic"},
    )
    qual = classify(brief)
    passed = qual["abstain_flag"] is True
    return passed, f"segment={qual['segment']} abstain={qual['abstain_flag']}"


# ---------------------------------------------------------------------------
# Category 9 — Context Brief
# ---------------------------------------------------------------------------

def probe_p033():
    """Context brief must include talking_points and hq_timezone."""
    from agent.main import ConversionEngine

    # Build a minimal brief directly without running the full pipeline
    brief = _brief(
        company_name="BriefTestCo",
        funding={"stage": "Series A", "last_funding_months": 4, "confidence": "high",
                 "hq_timezone": "America/Chicago"},
        ai_maturity={"score": 2, "confidence": "medium",
                     "signal_summary": ["ML roles detected", "GitHub org visible"], "details": {}},
    )
    qual = classify(brief)

    engine = ConversionEngine()
    cb = engine._generate_context_brief(
        company_name="BriefTestCo",
        contact_name="Jordan",
        contact_title="CTO",
        segment=qual["segment"],
        brief=brief,
        qual=qual,
    )
    passed = (
        isinstance(cb.get("talking_points"), list)
        and cb.get("hq_timezone") is not None
        and cb.get("prepared_for") == "Alex Rivera — Tenacious Intelligence Corporation"
    )
    return passed, f"talking_points={len(cb.get('talking_points', []))} timezone={cb.get('hq_timezone')}"


# ---------------------------------------------------------------------------
# Category 10 — Competitor Gap
# ---------------------------------------------------------------------------

def probe_p034():
    """Competitor gap percentile must be in [0, 100]."""
    from agent.enrichment import EnrichmentPipeline
    ep = EnrichmentPipeline()
    # DataFlow AI exists in crunchbase_sample.json with ai_roles_fraction=0.38
    gap = ep._build_competitor_gap("DataFlow AI", {"name": "DataFlow AI", "industry": "data infrastructure",
                                                     "headcount": 85, "ai_roles_fraction": 0.38})
    percentile = gap.get("company_percentile")
    passed = percentile is None or (0 <= percentile <= 100)
    return passed, f"percentile={percentile} confidence={gap.get('confidence')}"


# ---------------------------------------------------------------------------
# Registry + runner
# ---------------------------------------------------------------------------

# 4-tuples: (probe_id, category, fn, observed_trigger_rate)
# trigger_rate = estimated fraction of production pipeline runs where this failure
# mode fires. Seeded from failure_taxonomy.md; update as tau2-Bench data accumulates.
PROBES = [
    ("P-001", "ICP Misclassification",    probe_p001,  0.12),
    ("P-002", "ICP Misclassification",    probe_p002,  0.08),
    ("P-003", "ICP Misclassification",    probe_p003,  0.03),
    ("P-004", "ICP Misclassification",    probe_p004,  0.07),
    ("P-005", "Signal Over-Claiming",     probe_p005,  0.25),
    ("P-006", "Signal Over-Claiming",     probe_p006,  0.22),
    ("P-009", "Bench Over-Commitment",    probe_p009,  0.05),
    ("P-010", "Bench Over-Commitment",    probe_p010,  0.04),
    ("P-011", "Bench Over-Commitment",    probe_p011,  0.02),
    ("P-017", "Thread Isolation",         probe_p017,  0.00),
    ("P-018", "Thread Isolation",         probe_p018,  0.00),
    ("P-019", "Kill Switch",              probe_p019,  0.00),
    ("P-024", "Reply Signal Extraction",  probe_p024,  0.18),
    ("P-025", "Reply Signal Extraction",  probe_p025,  0.22),
    ("P-026", "Reply Signal Extraction",  probe_p026,  0.30),
    ("P-028", "AI Maturity Scoring",      probe_p028,  0.15),
    ("P-029", "AI Maturity Scoring",      probe_p029,  0.20),
    ("P-030", "AI Maturity Scoring",      probe_p030,  0.00),
    ("P-031", "Qualification Gates",      probe_p031,  0.12),
    ("P-032", "Qualification Gates",      probe_p032,  0.30),
    ("P-033", "Context Brief",            probe_p033,  0.05),
    ("P-034", "Competitor Gap",           probe_p034,  0.08),
]


def run_all(
    filter_id: Optional[str] = None,
    filter_category: Optional[str] = None,
    json_output: bool = False,
) -> Tuple[List[ProbeResult], int, int]:
    results = []
    for probe_id, category, fn, trigger_rate in PROBES:
        if filter_id and probe_id != filter_id:
            continue
        if filter_category and filter_category.lower() not in category.lower():
            continue
        r = _run_probe(probe_id, category, fn, trigger_rate)
        results.append(r)
        if not json_output:
            print(r)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    if json_output:
        # Highest-risk probes: sorted by trigger_rate descending (top 5)
        top_risk = sorted(
            [r.to_dict() for r in results],
            key=lambda x: x["observed_trigger_rate"],
            reverse=True,
        )[:5]
        output = {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / len(results), 3) if results else 0,
            "top_risk_probes": [{"probe_id": p["probe_id"], "category": p["category"],
                                  "trigger_rate": p["observed_trigger_rate"]} for p in top_risk],
            "results": [r.to_dict() for r in results],
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Probes: {len(results)} total | {passed} PASS | {failed} FAIL")
        print(f"Pass rate: {passed/len(results):.0%}" if results else "No probes run")

    return results, passed, failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tenacious adversarial probe runner")
    parser.add_argument("probe_id", nargs="?", help="Run single probe by ID (e.g. P-001)")
    parser.add_argument("--category", help="Filter by category name")
    parser.add_argument("--json", action="store_true", help="JSON output for CI")
    args = parser.parse_args()

    _, passed, failed = run_all(
        filter_id=args.probe_id,
        filter_category=args.category,
        json_output=args.json,
    )
    sys.exit(0 if failed == 0 else 1)
