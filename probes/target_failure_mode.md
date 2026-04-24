# Target Failure Mode — Highest ROI Fix

## Selected Failure: Signal Over-Claiming (Category 2)

Specifically: **the agent asserts facts from the hiring signal brief regardless of the confidence level of the underlying signal.**

---

## Why This Is the Highest-ROI Failure

### Trigger Rate
35% of outbound emails in our synthetic prospect runs contained at least one over-claimed signal. This is the second-highest trigger rate across all categories and the highest among Tier A (brand-damaging) failures.

### Business Cost Derivation

**ACV baseline (from baseline_numbers.md + spec):**
- Talent outsourcing ACV: $240K–$720K
- Project consulting ACV: $80K–$300K
- Weighted average: ~$360K

**Reply rate impact:**
- Signal-grounded outbound (top quartile): 7–12% reply rate
- Generic cold email (no grounding): 1–3% reply rate
- Over-claimed signal email: estimated 0.5–1% reply rate (worse than generic — prospects actively distrust)

At 60 outbound/week (Tenacious target), with 35% contaminated by over-claimed signals:
- 21 emails/week with over-claimed signals
- If those emails had correct grounding: 21 × 10% reply rate = 2.1 replies/week
- With over-claiming: 21 × 0.75% reply rate = 0.16 replies/week
- Weekly opportunity cost: **1.94 fewer warm leads per week**

**Annualized impact:**
- 1.94 × 52 weeks = ~101 fewer warm leads/year
- At 35–50% discovery-to-proposal conversion: 35–50 fewer proposals
- At 25–40% proposal-to-close: 9–20 fewer closed deals
- At $360K ACV: **$3.2M–$7.2M annualized opportunity cost**

**Brand reputation cost:**
- A "viral roast" of a Tenacious over-claiming email (LinkedIn screenshot) could suppress reply rates for 6+ months while the brand recovers. Even one such incident per quarter would cost additional millions in warm-lead erosion.

**vs. Stalled-Thread Rate:**
- Current manual stalled-thread rate: 30–40% (spec)
- Signal over-claiming is a primary contributor to stalling — a thread that opens with a wrong fact can never recover; it stalls or terminates
- Even reducing over-claiming by 50% drops the stall rate by an estimated 8–12 percentage points (from 35% to 23–27%)
- At 60 outbound/week: 5–7 fewer stalled threads/week = 5–7 more live opportunities/week
- Over 30 days: ~20–28 additional live threads carrying ~$7.2M–$10M pipeline value

### Why Other Failures Have Lower ROI

| Failure | Why Lower ROI |
|---|---|
| Bench over-commitment (Cat 3) | 5% trigger rate; rare until late-stage prospect interactions |
| Scheduling edge cases (Cat 8) | Affects only the booking step; doesn't kill the thread entirely |
| ICP misclassification (Cat 1) | Recoverable — wrong pitch can be corrected in follow-up |
| Multi-thread leakage (Cat 5) | Architecture fully fixed; trigger rate is now 0% |
| Dual-control coordination (Cat 7) | Operational friction, not relationship-ending |
| Gap over-claiming (Cat 10) | Also critical, but slightly lower trigger rate (45% of gap briefs vs. 35% of all emails) |

Gap over-claiming (Cat 10) is close in ROI. The reason signal over-claiming is chosen as the primary target:
1. It occurs earlier in the pipeline (cold email level) vs. gap over-claiming (enrichment level)
2. A fix to confidence-calibrated phrasing in outreach_composer.py directly mitigates BOTH signal over-claiming AND gap over-claiming — fixing one mechanism fixes both.

---

## Mechanism Direction

**Confidence-aware phrasing in outreach_composer.py + ai_maturity.py**

Design: Every signal assertion in the composed email must pass through a confidence gate:
- `confidence = "high"` → assert ("you have 8 open engineering roles")
- `confidence = "medium"` → hedge ("we see growing engineering activity across ~5 roles")
- `confidence = "low"` → ask ("is hiring velocity matching your runway?")
- `confidence = "none"` → omit the signal entirely

This gate is explicit in code (not left to the LLM's judgment), so it is deterministic and testable.

**Expected Delta A (baseline improvement):**
- Current pass@1 on tone-check probe for signal-grounding: ~55% (probes P-005, P-006, P-008)
- With confidence-gating mechanism: estimated 80–85% pass@1
- Delta A expected: +25–30 percentage points (p < 0.05 on 30-task dev slice)

See `method.md` for the full mechanism design and ablation plan.
