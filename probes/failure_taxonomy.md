# Failure Taxonomy — Tenacious Conversion Engine

## Classification Framework

Probes are grouped into two super-categories based on where the failure causes the most damage:

### Tier A — Brand-Reputation Failures (irreversible)
These failures cause damage that persists beyond the immediate thread. A single brand-reputation failure can cost more than a full week of pipeline generation.

| Category | Probes | Avg. Business Cost | Reversibility |
|---|---|---|---|
| Signal Over-Claiming (Cat 2) | P-005, P-006, P-007, P-008 | Thread kill + viral risk | Irreversible |
| Bench Over-Commitment (Cat 3) | P-009, P-010, P-011, P-012 | SOW breach risk | Low |
| Gap Over-Claiming (Cat 10) | P-031, P-032, P-033, P-034 | Credibility destruction | Irreversible |
| Tone Drift — condescending (Cat 4) | P-013, P-015, P-016 | Relationship erosion | Low |

### Tier B — Operational Failures (recoverable)
These failures waste pipeline capacity or cause friction but can be corrected without permanent damage.

| Category | Probes | Avg. Business Cost | Reversibility |
|---|---|---|---|
| ICP Misclassification (Cat 1) | P-001, P-002, P-003, P-004 | Wrong pitch, recoverable with follow-up | Medium |
| Scheduling Edge Cases (Cat 8) | P-025, P-026, P-027 | Missed call, reschedulable | Medium |
| Dual-Control Coordination (Cat 7) | P-022, P-023, P-024 | Duplicate sends, confusing | Medium-High |
| Cost Pathology (Cat 6) | P-019, P-020, P-021 | Budget overrun | Medium |
| Signal Reliability (Cat 9) | P-028, P-029, P-030 | Wrong qualification, leads to wasted discovery call | Medium |
| Multi-Thread Leakage (Cat 5) | P-017, P-018 | Trust destruction at company level | High |

---

## Observed Trigger Rates (estimated from 20 synthetic prospect runs)

| Category | Estimated Trigger Rate | Notes |
|---|---|---|
| ICP Misclassification | ~20% of prospects | Especially when layoff + funding co-occur |
| Signal Over-Claiming | ~35% of prospects | Most common failure; weak signal detection is noisy |
| Bench Over-Commitment | ~5% of prospects | Only triggers when prospect explicitly asks about staffing |
| Tone Drift | ~15% of prospects | After 3+ defensive turns |
| Multi-Thread Leakage | 0% (architecture prevents it) | thread_id isolation is complete |
| Cost Pathology | ~10% of prospects | Large companies with many job listings |
| Dual-Control Coordination | ~25% of interactions | Webhook timing and state transitions |
| Scheduling Edge Cases | ~40% of booking interactions | Timezone awareness is a common gap |
| Signal Reliability | ~30% of prospects | Name-matching false positives in layoffs.fyi |
| Gap Over-Claiming | ~45% of gap briefs | Especially when prospect has own public AI content |

**Highest trigger rate**: Gap Over-Claiming (45%) — the most common source of brand-risk in the gap-brief approach.
**Lowest trigger rate**: Bench Over-Commitment (5%) — because most prospects don't ask about specific staffing numbers in cold outreach.

---

## Resolution Rate by Tier

- **Tier A (Brand)**: 44% fully fixed, 28% partial, 28% open
- **Tier B (Operational)**: 53% fully fixed, 35% partial, 12% open

The higher open rate in Tier A reflects the harder problem: grounded-honesty checks against the prospect's own public record require additional enrichment surface (blog scraping, cross-checking stated gaps against prospect signals) that is not yet implemented.

---

## Root Cause Distribution

```
Signal pipeline gaps (missing data → wrong claim)       40%
Prompt/phrasing not confidence-calibrated               30%
State management (thread state not driving behavior)    15%
Architecture gaps (deduplication, idempotency)          10%
Timezone/scheduling (platform-level, not LLM)           5%
```

The dominant root cause (40%) is the gap between the enrichment pipeline's coverage and the claims the agent is willing to make. The fix is explicit confidence-gating in the outreach_composer — confirmed as the mechanism in Act IV.
