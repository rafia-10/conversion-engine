"""
qualifier.py — ICP segment classifier for Tenacious outbound.

Segment names are fixed for grading. Rules implement the spec exactly.
Returns: segment, confidence [0,1], secondary_segment, abstain_flag.
"""
from typing import Dict, Optional


SEGMENTS = {
    "segment_1_series_a_b": "Recently-funded Series A/B startups",
    "segment_2_mid_market_restructuring": "Mid-market platforms restructuring cost",
    "segment_3_leadership_transitions": "Engineering-leadership transitions",
    "segment_4_capability_gaps": "Specialized capability gaps",
    "ABSTAIN": "No confident segment match — generic exploratory email",
}

ABSTAIN_THRESHOLD = 0.6


def classify(hiring_signal_brief: Dict) -> Dict:
    """
    Classify a prospect into one of the four ICP segments.

    Priority order (from icp_definition.md Classification rules):
      1. Layoff + fresh funding → Segment 2
      2. New CTO/VP Eng in 90 days → Segment 3
      3. Capability gap signal + AI readiness ≥ 2 → Segment 4
      4. Fresh funding in 180 days → Segment 1
      5. Abstain
    """
    funding = hiring_signal_brief.get("funding", {})
    layoffs = hiring_signal_brief.get("layoffs", {})
    job_velocity = hiring_signal_brief.get("job_post_velocity", {})
    leadership = hiring_signal_brief.get("leadership_change", {})
    ai_maturity = hiring_signal_brief.get("ai_maturity", {})
    crunchbase = hiring_signal_brief.get("crunchbase_data", {})

    headcount = crunchbase.get("headcount", 0)
    stage = funding.get("stage", "")
    months_since_funding = funding.get("last_funding_months", 999)
    has_layoff = bool(layoffs.get("event"))
    layoff_pct = float(layoffs.get("percentage", 0))
    open_roles = job_velocity.get("open_roles", 0)
    job_conf = job_velocity.get("confidence", "low")
    leadership_event = bool(leadership.get("event"))
    ai_score = ai_maturity.get("score", 0)

    scores = {}  # segment -> confidence

    # ---- Segment 2: mid-market + layoff + still hiring --------------------
    if (200 <= headcount <= 2000
            and has_layoff
            and layoff_pct <= 40
            and open_roles >= 3):
        conf = 0.75
        if layoff_pct <= 20 and open_roles >= 5:
            conf = 0.85
        scores["segment_2_mid_market_restructuring"] = conf

    # ---- Segment 3: new CTO/VP Eng in 90 days ----------------------------
    if leadership_event and 15 <= headcount <= 500:
        conf = 0.70
        if leadership.get("confidence") == "high":
            conf = 0.85
        elif leadership.get("confidence") == "medium":
            conf = 0.75
        scores["segment_3_leadership_transitions"] = conf

    # ---- Segment 4: capability gap + AI ≥ 2 ------------------------------
    if ai_score >= 2:
        # Check for specialized-role signal in job titles
        raw_titles = job_velocity.get("raw_titles", [])
        specialized = _has_specialized_ai_signal(raw_titles)
        conf = 0.55
        if specialized and ai_score == 3:
            conf = 0.80
        elif specialized or ai_score == 3:
            conf = 0.65
        scores["segment_4_capability_gaps"] = conf

    # ---- Segment 1: fresh Series A/B + hiring + no major layoff ----------
    if (stage in ("Series A", "Series B")
            and months_since_funding <= 6
            and 15 <= headcount <= 200
            and open_roles >= 5
            and not (has_layoff and layoff_pct > 15)):
        conf = 0.70
        if job_conf == "high" and open_roles >= 8:
            conf = 0.85
        elif job_conf == "medium":
            conf = 0.75
        scores["segment_1_series_a_b"] = conf

    # Apply priority ordering from spec: 2 > 3 > 4 > 1
    priority = [
        "segment_2_mid_market_restructuring",
        "segment_3_leadership_transitions",
        "segment_4_capability_gaps",
        "segment_1_series_a_b",
    ]

    primary = None
    primary_conf = 0.0
    for seg in priority:
        if seg in scores and scores[seg] > primary_conf:
            primary = seg
            primary_conf = scores[seg]

    secondary = None
    secondary_conf = 0.0
    for seg in priority:
        if seg != primary and seg in scores and scores[seg] > secondary_conf:
            secondary = seg
            secondary_conf = scores[seg]

    if primary is None or primary_conf < ABSTAIN_THRESHOLD:
        return {
            "segment": "ABSTAIN",
            "confidence": primary_conf if primary else 0.0,
            "secondary_segment": secondary,
            "abstain_flag": True,
            "reasoning": _build_reasoning(scores, priority),
            "all_scores": scores,
        }

    return {
        "segment": primary,
        "confidence": primary_conf,
        "secondary_segment": secondary,
        "abstain_flag": False,
        "reasoning": _build_reasoning(scores, priority),
        "all_scores": scores,
    }


def _has_specialized_ai_signal(raw_titles) -> bool:
    import re
    pattern = re.compile(
        r'\b(ml|machine.?learning|nlp|llm|ai|applied.?scientist|data.?scientist'
        r'|agentic|rag|mlops|ml.?platform|ai.?product|genai)\b',
        re.IGNORECASE,
    )
    return any(pattern.search(t) for t in (raw_titles or []))


def _build_reasoning(scores: Dict, priority) -> str:
    if not scores:
        return "No segment filters fired."
    parts = []
    for seg in priority:
        if seg in scores:
            parts.append(f"{seg}: {scores[seg]:.0%}")
    return "; ".join(parts)


def pitch_language(segment: str, ai_maturity_score: int) -> str:
    """Return the correct pitch language string per segment + AI maturity."""
    if segment == "segment_1_series_a_b":
        if ai_maturity_score >= 2:
            return "scale your AI team faster than in-house hiring can support"
        return "stand up your first AI function with a dedicated squad"
    if segment == "segment_2_mid_market_restructuring":
        if ai_maturity_score >= 2:
            return "preserve your AI delivery capacity while reshaping cost structure"
        return "maintain platform delivery velocity through the restructure"
    if segment == "segment_3_leadership_transitions":
        return ("the first 90 days are typically when vendor mix gets reassessed — "
                "happy to share what peers at your scale have done")
    if segment == "segment_4_capability_gaps":
        return ("three companies in your sector at your stage are doing X and you are not — "
                "here is what the difference looks like")
    return "worth a 15-minute conversation to see if there is a fit"
