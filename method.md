# Act IV Mechanism: Confidence-Aware Signal Phrasing

## Overview

The target failure mode is signal over-claiming: the agent asserts facts from the hiring signal brief regardless of signal confidence. The mechanism is a deterministic confidence gate applied at the composition layer, forcing language to match the evidential weight of each signal.

## Design

### The Gate

Each signal in `hiring_signal_brief` carries a `confidence` field (`"high"` | `"medium"` | `"low"`). The gate in `outreach_composer.py` maps confidence to language register:

```
confidence = "high"   → assert   "you closed a $14M Series B in February"
confidence = "medium" → hedge    "we see signals of a recent funding event —
                                  around the time you'd expect hiring to accelerate"
confidence = "low"    → ask      "has there been a recent funding event we might
                                  have missed in public filings?"
confidence = None     → omit     the signal does not appear in the email at all
```

This mapping is explicit in `agent/outreach_composer.py` (`_extract_grounded_signals`). It is not a prompt instruction — it is a pre-processing step that runs before the LLM receives the brief. The LLM only sees claims the gate has pre-cleared.

### Why Deterministic (not Prompt-Based)

Prompt-based honesty instructions have been shown to fail under adversarial pressure (see probes P-005, P-006). The LLM may over-interpret weak signals when the prompt context nudges toward a strong pitch. A deterministic pre-filter removes the ambiguity entirely: the LLM cannot assert a "high" claim if the confidence gate has already downgraded the signal to a hedged form.

### Signal-Level Confidence Assignment

Each enrichment module produces a per-output `confidence` field:

| Module | High | Medium | Low |
|---|---|---|---|
| crunchbase (funding) | ODM exact match, round > $3M | Press mention, unconfirmed amount | Sector inference only |
| layoffs.fyi | Exact company name match | Substring match (domain mismatch risk) | No match |
| job_velocity | ratio > 2.0 AND count ≥ 5 | ratio > 1.5 OR count ≥ 3 | count < 3 |
| leadership_change | LinkedIn "started new position" | Press headline | News article keyword match |
| ai_maturity | 3+ independent signals | 2 signals | 1 signal or 0 |

The aggregate confidence for any claim in the brief is the minimum confidence of its component signals.

### Tone Preservation Check Integration

The confidence gate feeds the `tone_checker.py` second LLM call. The tone checker specifically scores `marker_2_grounded` and `marker_3_honest` against the gate-cleared brief. If either marker scores < 0.75, the draft is regenerated with the drift examples as negative examples.

## Ablation Variants

Three ablation conditions were tested on the 30-task dev slice:

### Variant A: Baseline (no confidence gate)
All signals fed to the LLM as-is. LLM decides what to assert. Represents the current system before Act IV.

### Variant B: Prompt-Only Honesty Instruction
Added explicit instructions to the composition prompt: "Only make claims you can verify from the brief. For low-confidence signals, phrase as questions." No pre-filtering.

### Variant C: Mechanism (deterministic confidence gate, this method)
Pre-process signals through the confidence gate. LLM only receives already-hedged text. No assertion/question decision left to the LLM.

## Evaluation Metric

**Grounded-Honesty Pass Rate**: fraction of composed emails that contain 0 over-claimed signals, assessed by a τ²-Bench-style evaluation on the dev slice using a reviewer LLM that scores each email against the hiring_signal_brief + confidence levels.

We proxy this on the τ²-Bench retail domain via the grounded-communication sub-score (the portion of the reward function that penalizes hallucinated information to the user).

## Expected Deltas

| Delta | Measurement | Expected |
|---|---|---|
| Delta A: Mechanism − Baseline | Pass@1 on grounded-honesty evaluation (30-task dev slice) | +25–30 pp |
| Delta B: Mechanism − Prompt-Only | Same evaluation | +12–18 pp |
| Delta C: Mechanism − τ²-Bench reference (~42% baseline) | Pass@1 on held-out slice | +35–40 pp |

## Hyperparameters

| Parameter | Value | Rationale |
|---|---|---|
| confidence gate threshold | medium | Low-confidence signals are never asserted |
| tone_checker threshold | 0.75 | From style_guide.md "score below 4/5 should be regenerated" |
| max regeneration attempts | 2 | Cost ceiling: 2 extra LLM calls max per email |
| temperature (composition) | 0.4 | Balance between consistency and Tenacious voice variation |
| temperature (tone_checker) | 0.1 | Consistent scoring, not creative |

## Statistical Test

Wilson score binomial CI on the proportion of pass@1 successes across the 30-task dev slice × 5 trials. Delta A is declared positive if the lower bound of the mechanism CI is above the upper bound of the baseline CI (non-overlapping at 95% level).

Backup test: two-sample proportions z-test (`scipy.stats.proportions_ztest`) on success counts.

## Known Limitations

1. **Quiet-but-sophisticated prospect**: a company with full AI capability but no public signal still scores low confidence and gets hedged language. The agent may undersell relevance to this prospect. See Skeptic's Appendix in memo.

2. **Signal deduplication**: multiple job postings of the same role (HR artifact) inflate the job_velocity count. The mechanism does not yet deduplicate.

3. **Competitor gap cross-check**: the confidence gate applies to the prospect's own signals but not to the competitor gap brief's claims. Gap over-claiming (P-031, P-034) requires a second gate layer targeting competitor gap assertions specifically.
