"""
tone_checker.py — Second LLM call that scores a draft against the Tenacious style guide.

Scores 0-1 per marker. Regenerates if score < 0.75. Extra call cost is logged.
"""
import logging
import re
from pathlib import Path
from typing import Dict, Optional

from agent.llm import LLMClient

logger = logging.getLogger(__name__)

_STYLE_PATHS = [
    "tenacious_sales_data/seed/style_guide.md",
    "seed/style_guide.md",
]

REGEN_THRESHOLD = 0.75


def _load_style_guide() -> str:
    for path in _STYLE_PATHS:
        p = Path(path)
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


_STYLE_GUIDE = _load_style_guide()

_HEURISTIC_VIOLATIONS = [
    (r'\b(hey there|hope you\'re doing well|just wanted to|quick question)\b', "marker_1_direct"),
    (r'\b(top talent|world-class|a-players|rockstar|ninja|cost savings of)\b', "marker_4_professional"),
    (r'\b(you\'re clearly|you obviously|you need|you must|you are behind)\b', "marker_5_non_condescending"),
    (r'\b(following up again|circling back|touching base)\b', "marker_1_direct"),
    (r'we can definitely help|we will definitely|100% guarantee', "marker_3_honest"),
    (r'\bemoji|[\U0001F000-\U0001FFFF]', "no_emoji_cold"),
]

MARKER_NAMES = [
    "marker_1_direct",
    "marker_2_grounded",
    "marker_3_honest",
    "marker_4_professional",
    "marker_5_non_condescending",
]


def _heuristic_check(draft: str) -> Dict:
    """Fast rule-based pre-check before the LLM call."""
    violations = {}
    for pattern, marker in _HEURISTIC_VIOLATIONS:
        if re.search(pattern, draft, re.IGNORECASE):
            violations[marker] = violations.get(marker, 0) + 1

    scores = {}
    for m in MARKER_NAMES:
        v = violations.get(m, 0)
        scores[m] = max(0.0, 1.0 - v * 0.3)

    overall = sum(scores.values()) / len(scores)
    return {"scores": scores, "overall": overall, "violations": violations, "method": "heuristic"}


def check(
    draft: str,
    llm: Optional[LLMClient] = None,
    use_llm: bool = True,
) -> Dict:
    """
    Score a draft email against the Tenacious style guide.
    Returns dict with scores per marker, overall score, and specific drift examples.
    """
    heuristic = _heuristic_check(draft)

    if not use_llm or not _STYLE_GUIDE or (llm is None) or (not llm.is_available()):
        return heuristic

    # Only call the LLM if heuristic score suggests potential issues
    if heuristic["overall"] > 0.90:
        heuristic["method"] = "heuristic_passthrough"
        return heuristic

    prompt = f"""You are a tone-quality reviewer for Tenacious, a B2B talent outsourcing firm.

STYLE GUIDE (the five tone markers):
{_STYLE_GUIDE[:3000]}

DRAFT EMAIL TO REVIEW:
---
{draft}
---

Score this draft on each of the 5 tone markers from 0.0 to 1.0.
1. Direct (clear, actionable, no filler)
2. Grounded (every claim grounded in data, weak signals phrased as questions)
3. Honest (no over-claiming, no fabricated signals)
4. Professional (appropriate for CTO/VP Eng, no offshore clichés)
5. Non-condescending (gap framing as research finding, not criticism)

Return ONLY a JSON object like:
{{
  "marker_1_direct": 0.9,
  "marker_2_grounded": 0.8,
  "marker_3_honest": 1.0,
  "marker_4_professional": 0.85,
  "marker_5_non_condescending": 0.9,
  "drift_examples": ["specific phrase that violates marker X..."],
  "overall": 0.89
}}"""

    result = llm.generate_json(prompt, temperature=0.1, max_tokens=500)
    parsed = result.get("parsed", {})

    if not parsed or "marker_1_direct" not in parsed:
        return heuristic

    scores = {m: float(parsed.get(m, heuristic["scores"].get(m, 1.0))) for m in MARKER_NAMES}
    overall = parsed.get("overall", sum(scores.values()) / len(scores))
    drift_examples = parsed.get("drift_examples", [])

    return {
        "scores": scores,
        "overall": overall,
        "drift_examples": drift_examples,
        "method": "llm",
        "llm_tokens": result.get("total_tokens", 0),
        "llm_latency_ms": result.get("latency_ms", 0),
    }


def check_and_regenerate(
    draft: str,
    regenerate_fn,
    llm: Optional[LLMClient] = None,
    max_attempts: int = 2,
) -> Dict:
    """
    Check tone; regenerate with drift examples as negative examples if below threshold.
    Returns: {"draft": str, "tone_result": dict, "regenerated": bool, "attempts": int}
    """
    for attempt in range(max_attempts):
        tone = check(draft, llm=llm)
        if tone["overall"] >= REGEN_THRESHOLD:
            return {
                "draft": draft,
                "tone_result": tone,
                "regenerated": attempt > 0,
                "attempts": attempt + 1,
            }
        if attempt < max_attempts - 1:
            drift = tone.get("drift_examples", [])
            logger.info(f"Tone score {tone['overall']:.2f} < {REGEN_THRESHOLD} — regenerating "
                        f"(attempt {attempt + 1}). Drift: {drift}")
            draft = regenerate_fn(drift_examples=drift)

    return {
        "draft": draft,
        "tone_result": tone,
        "regenerated": True,
        "attempts": max_attempts,
    }
