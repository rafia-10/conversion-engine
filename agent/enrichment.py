"""
enrichment.py — Parallel signal enrichment pipeline.

Five modules run concurrently via asyncio.gather():
  1. crunchbase_lookup   — funding stage, headcount, HQ timezone
  2. layoffs_lookup      — recent layoff events from layoffs.fyi sample
  3. job_velocity        — Playwright scrape (falls back to Crunchbase open_roles)
  4. leadership_change   — Playwright scrape (falls back to synthetic)
  5. ai_maturity_score   — Weighted scoring per spec: 6 indicators → integer 0-3

AI maturity weights (from spec):
  ai_adjacent_roles_fraction  0.35
  named_ai_ml_leadership      0.30
  github_signal               0.15
  exec_commentary             0.10
  modern_ml_stack             0.05
  strategic_comms             0.05

Score bands: raw < 0.25 → 0, < 0.50 → 1, < 0.75 → 2, else → 3
"""
import asyncio
import csv
import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=5)

# ---------------------------------------------------------------------------
# Typed schemas
# ---------------------------------------------------------------------------

class JobPostSignal(TypedDict):
    open_roles: int
    velocity: str           # "high" | "moderate" | "low"  (absolute)
    velocity_delta: Optional[int]   # current - 60d_snapshot (None if snapshot missing)
    velocity_trend: str     # "accelerating"|"growing"|"stable"|"decelerating"|"declining"|"unknown"
    snapshot_60d: Optional[int]     # role count from Feb 23 2026 snapshot
    snapshot_date: str      # ISO date of snapshot ("2026-02-23")
    focus: str
    source: str
    confidence: str         # "high" | "medium" | "low"
    raw_titles: List[str]


class LeadershipSignal(TypedDict):
    event: Optional[str]
    date: Optional[str]
    headline: Optional[str]
    source: str
    confidence: str


class FundingSignal(TypedDict):
    stage: Optional[str]
    last_funding_months: Optional[int]
    confidence: str
    hq_timezone: Optional[str]


class LayoffSignal(TypedDict):
    event: Optional[str]
    date: Optional[str]
    headcount: int
    percentage: float
    confidence: str


class AiMaturitySignal(TypedDict):
    score: int             # 0-3
    confidence: str
    signal_summary: List[str]
    details: Dict[str, Any]


@dataclass
class EnrichmentSignal:
    value: Any
    confidence: str
    justification: str


# ---------------------------------------------------------------------------
# AI maturity weights (spec-exact)
# ---------------------------------------------------------------------------

_AI_WEIGHTS = {
    "ai_adjacent_roles_fraction": 0.35,
    "named_ai_ml_leadership":     0.30,
    "github_signal":              0.15,
    "exec_commentary":            0.10,
    "modern_ml_stack":            0.05,
    "strategic_comms":            0.05,
}

_MODERN_ML_STACK = {
    "pytorch", "tensorflow", "jax", "hugging face", "huggingface", "langchain",
    "langgraph", "llm", "mlflow", "weights & biases", "wandb", "triton",
    "pinecone", "weaviate", "openai api", "rag", "lora", "qlora", "mlops",
    "databricks ml", "sagemaker", "vertex ai",
}


def _score_ai_maturity_from_record(record: Dict) -> AiMaturitySignal:
    """Score AI maturity from a Crunchbase-style record using spec weights."""
    indicators: Dict[str, float] = {}
    evidence: List[str] = []

    # 1. AI-adjacent roles as fraction of total
    frac = float(record.get("ai_roles_fraction", 0) or 0)
    if frac >= 0.30:
        indicators["ai_adjacent_roles_fraction"] = 1.0
        evidence.append(f"{frac:.0%} of roles are AI/ML-adjacent")
    elif frac >= 0.15:
        indicators["ai_adjacent_roles_fraction"] = 0.5
        evidence.append(f"{frac:.0%} AI/ML-adjacent roles (moderate)")
    else:
        indicators["ai_adjacent_roles_fraction"] = 0.0

    # 2. Named AI/ML leadership
    if record.get("named_ai_ml_leadership"):
        title = record.get("ai_ml_leadership_title", "AI/ML leader")
        indicators["named_ai_ml_leadership"] = 1.0
        evidence.append(f"Named {title}")
    else:
        indicators["named_ai_ml_leadership"] = 0.0

    # 3. GitHub signal
    if record.get("github_url"):
        indicators["github_signal"] = 1.0
        evidence.append("Public GitHub org visible")
    else:
        indicators["github_signal"] = 0.0

    # 4. Exec commentary
    commentary = record.get("exec_commentary") or ""
    if commentary.strip():
        indicators["exec_commentary"] = 1.0
        evidence.append("Exec public AI commentary detected")
    else:
        indicators["exec_commentary"] = 0.0

    # 5. Modern ML stack
    stack = [s.lower() for s in (record.get("ml_stack") or [])]
    stack_text = " ".join(stack)
    modern_matches = [k for k in _MODERN_ML_STACK if k in stack_text]
    if len(modern_matches) >= 3:
        indicators["modern_ml_stack"] = 1.0
        evidence.append(f"Modern ML stack: {', '.join(modern_matches[:3])}")
    elif len(modern_matches) >= 1:
        indicators["modern_ml_stack"] = 0.5
        evidence.append(f"Some ML stack signal: {', '.join(modern_matches[:2])}")
    else:
        indicators["modern_ml_stack"] = 0.0

    # 6. Strategic comms
    comms = record.get("strategic_comms") or ""
    ai_comms_re = re.compile(r'\b(ai|ml|machine.?learning|data.?driven|intelligent|agentic|llm)\b', re.I)
    if comms and ai_comms_re.search(comms):
        indicators["strategic_comms"] = 1.0
        evidence.append(f"Strategic comms: '{comms[:60]}'")
    else:
        indicators["strategic_comms"] = 0.0

    raw = sum(_AI_WEIGHTS[k] * indicators.get(k, 0.0) for k in _AI_WEIGHTS)

    if raw < 0.25:
        score = 0
    elif raw < 0.50:
        score = 1
    elif raw < 0.75:
        score = 2
    else:
        score = 3

    confidence = "high" if score >= 2 and len(evidence) >= 3 else ("medium" if score >= 1 else "low")

    return {
        "score": score,
        "confidence": confidence,
        "signal_summary": evidence or ["no AI maturity signal detected"],
        "details": {
            "raw_weighted_score": round(raw, 4),
            "indicators": indicators,
            "weights": _AI_WEIGHTS,
        },
    }


# ---------------------------------------------------------------------------
# EnrichmentPipeline
# ---------------------------------------------------------------------------

class EnrichmentPipeline:
    def __init__(self):
        self.bench_summary = self._load_bench_summary()

        cb_path = os.getenv("CRUNCHBASE_SAMPLE_PATH", "./data/crunchbase_sample.json")
        lo_path = os.getenv("LAYOFFS_DATA_PATH", "./data/layoffs_sample.csv")

        self._crunchbase_index = self._load_crunchbase(cb_path)
        self._layoffs_index = self._load_layoffs(lo_path)

    # -----------------------------------------------------------------------
    # Data loaders
    # -----------------------------------------------------------------------

    def _load_bench_summary(self) -> Dict:
        for p in ["./tenacious_sales_data/seed/bench_summary.json", "./seed/bench_summary.json"]:
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return {}

    def _load_crunchbase(self, path: str) -> Dict[str, Dict]:
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            index = {}
            for rec in data.get("companies", []):
                key = rec["name"].lower().strip()
                index[key] = rec
                # Also index by domain
                domain = (rec.get("domain") or "").lower().split(".")[0]
                if domain:
                    index[domain] = rec
            logger.info(f"Loaded {len(data.get('companies', []))} Crunchbase records")
            return index
        except Exception as e:
            logger.warning(f"Failed to load Crunchbase data: {e}")
            return {}

    def _load_layoffs(self, path: str) -> Dict[str, Dict]:
        if not os.path.exists(path):
            return {}
        try:
            index: Dict[str, Dict] = {}
            with open(path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = row.get("company", "").lower().strip()
                    if key:
                        index[key] = row
            logger.info(f"Loaded {len(index)} layoff records")
            return index
        except Exception as e:
            logger.warning(f"Failed to load layoffs data: {e}")
            return {}

    # -----------------------------------------------------------------------
    # Individual enrichment modules (synchronous — run in thread pool)
    # -----------------------------------------------------------------------

    def _module_crunchbase(self, company_name: str, domain: Optional[str]) -> Dict:
        t0 = time.time()
        key = company_name.lower().strip()
        domain_key = (domain or "").lower().split(".")[0] if domain else None

        record = (
            self._crunchbase_index.get(key)
            or (self._crunchbase_index.get(domain_key) if domain_key else None)
        )

        if record:
            confidence = "high"
            source = "crunchbase_sample"
        else:
            # Realistic synthetic fallback
            stages = ["Series A", "Series B", "Series B", "Series C", "Growth"]
            record = {
                "name": company_name,
                "domain": domain or f"{re.sub(r'[^a-z0-9]','', company_name.lower())}.com",
                "stage": random.choice(stages),
                "last_funding_months": random.randint(2, 10),
                "industry": "enterprise software",
                "headcount": random.randint(25, 300),
                "location": random.choice(["San Francisco, CA", "New York, NY", "Austin, TX"]),
                "hq_timezone": "America/New_York",
                "ai_roles_fraction": round(random.uniform(0.05, 0.35), 2),
                "named_ai_ml_leadership": random.random() < 0.3,
                "github_url": None,
                "exec_commentary": None,
                "ml_stack": [],
                "strategic_comms": None,
                "open_roles": random.randint(2, 10),
                "job_titles_sample": [],
            }
            confidence = "low"
            source = "synthetic"

        latency_ms = int((time.time() - t0) * 1000)
        return {
            "data": record,
            "funding": {
                "stage": record.get("stage"),
                "last_funding_months": record.get("last_funding_months"),
                "confidence": confidence,
                "hq_timezone": record.get("hq_timezone"),
            },
            "_latency_ms": latency_ms,
            "_source": source,
        }

    def _module_layoffs(self, company_name: str) -> LayoffSignal:
        t0 = time.time()
        key = company_name.lower().strip()

        # Exact match
        record = self._layoffs_index.get(key)

        # Fuzzy match
        if not record:
            for k, v in self._layoffs_index.items():
                if k in key or key in k:
                    record = v
                    break

        if record:
            result: LayoffSignal = {
                "event": "layoff",
                "date": record.get("date", date.today().isoformat()),
                "headcount": int(record.get("headcount_laid_off", 0)),
                "percentage": float(record.get("percentage_laid_off", 0)),
                "confidence": "high",
            }
        else:
            result = {
                "event": None,
                "date": None,
                "headcount": 0,
                "percentage": 0.0,
                "confidence": "high",  # high confidence in absence of event
            }

        result["_latency_ms"] = int((time.time() - t0) * 1000)
        return result

    def _module_job_velocity(self, company_name: str, domain: Optional[str],
                              crunchbase_record: Optional[Dict] = None) -> JobPostSignal:
        t0 = time.time()

        # Pull 60d snapshot from Crunchbase record (None if not present)
        snapshot_60d: Optional[int] = None
        snapshot_date = "2026-02-23"
        if crunchbase_record:
            raw_snap = crunchbase_record.get("open_roles_60d_snapshot")
            if raw_snap is not None:
                snapshot_60d = int(raw_snap)

        # Try Playwright first (passes snapshot so scraper can compute delta)
        try:
            from agent.scraper import SignalScraper
            scraper = SignalScraper()
            result = scraper.run(
                scraper.scrape_job_postings(
                    company_name, domain,
                    snapshot_60d=snapshot_60d,
                    snapshot_date=snapshot_date,
                )
            )
            result["_latency_ms"] = int((time.time() - t0) * 1000)
            return result
        except Exception:
            pass

        # Fall back to Crunchbase record data
        if crunchbase_record:
            count = int(crunchbase_record.get("open_roles", 0))
            raw_titles = crunchbase_record.get("job_titles_sample", [])
            confidence = "medium"
            source = "crunchbase_sample"
        else:
            count = random.randint(0, 10)
            raw_titles = []
            confidence = "low"
            source = "synthetic"

        from agent.scraper import _compute_velocity_delta, _infer_focus as _scraper_infer
        delta, trend = _compute_velocity_delta(count, snapshot_60d)
        velocity = "high" if count >= 8 else "moderate" if count >= 3 else "low"
        focus = _scraper_infer(raw_titles) if raw_titles else "engineering"

        return {
            "open_roles": count,
            "velocity": velocity,
            "velocity_delta": delta,
            "velocity_trend": trend,
            "snapshot_60d": snapshot_60d,
            "snapshot_date": snapshot_date,
            "focus": focus,
            "source": source,
            "confidence": confidence,
            "raw_titles": raw_titles,
            "_latency_ms": int((time.time() - t0) * 1000),
        }

    def _module_leadership(self, company_name: str, domain: Optional[str],
                            crunchbase_record: Optional[Dict] = None) -> LeadershipSignal:
        t0 = time.time()

        try:
            from agent.scraper import SignalScraper
            scraper = SignalScraper()
            result = scraper.run(scraper.scrape_leadership_changes(company_name, domain))
            result["_latency_ms"] = int((time.time() - t0) * 1000)
            return result
        except Exception:
            pass

        # Infer from Crunchbase record
        leadership_title = None
        if crunchbase_record and crunchbase_record.get("named_ai_ml_leadership"):
            leadership_title = crunchbase_record.get("ai_ml_leadership_title")

        if leadership_title:
            result: LeadershipSignal = {
                "event": f"named {leadership_title}",
                "date": None,
                "headline": f"{company_name} — {leadership_title} in role",
                "source": "crunchbase_sample",
                "confidence": "medium",
            }
        else:
            result = {
                "event": None,
                "date": None,
                "headline": None,
                "source": "crunchbase_sample",
                "confidence": "medium",
            }

        result["_latency_ms"] = int((time.time() - t0) * 1000)
        return result

    def _module_ai_maturity(self, crunchbase_record: Dict,
                             job_velocity: JobPostSignal) -> AiMaturitySignal:
        t0 = time.time()

        # Augment record with job titles for AI fraction check
        augmented = dict(crunchbase_record)
        raw_titles = job_velocity.get("raw_titles", [])
        if raw_titles:
            ai_title_re = re.compile(
                r'\b(ml|machine.?learning|ai|nlp|llm|data.?sci|applied.?sci|mlops|rag)\b', re.I
            )
            ai_count = sum(1 for t in raw_titles if ai_title_re.search(t))
            if len(raw_titles) > 0:
                observed_fraction = ai_count / len(raw_titles)
                # Blend observed fraction with Crunchbase fraction (give observed slight weight)
                existing = float(augmented.get("ai_roles_fraction", 0) or 0)
                augmented["ai_roles_fraction"] = round(0.6 * existing + 0.4 * observed_fraction, 2)

        result = _score_ai_maturity_from_record(augmented)
        result["_latency_ms"] = int((time.time() - t0) * 1000)
        return result

    # -----------------------------------------------------------------------
    # Async orchestrator — 5 parallel modules
    # -----------------------------------------------------------------------

    async def build_hiring_signal_brief_async(
        self,
        company_name: str,
        domain: Optional[str] = None,
    ) -> Dict:
        t_total = time.time()
        loop = asyncio.get_event_loop()

        # Module 1 + 2 can run fully in parallel (no cross-deps)
        cb_fut = loop.run_in_executor(_EXECUTOR, self._module_crunchbase, company_name, domain)
        lo_fut = loop.run_in_executor(_EXECUTOR, self._module_layoffs, company_name)

        cb_result, lo_result = await asyncio.gather(cb_fut, lo_fut)

        crunchbase_record = cb_result["data"]
        funding = cb_result["funding"]
        layoffs = lo_result

        # Modules 3, 4, 5 can now run in parallel (all have cb_result)
        jv_fut = loop.run_in_executor(
            _EXECUTOR, self._module_job_velocity, company_name, domain, crunchbase_record
        )
        ld_fut = loop.run_in_executor(
            _EXECUTOR, self._module_leadership, company_name, domain, crunchbase_record
        )

        job_velocity, leadership = await asyncio.gather(jv_fut, ld_fut)

        # AI maturity uses job_velocity (fast, no I/O needed)
        ai_maturity = await loop.run_in_executor(
            _EXECUTOR, self._module_ai_maturity, crunchbase_record, job_velocity
        )

        competitor_gap = self._build_competitor_gap(company_name, crunchbase_record)

        total_ms = int((time.time() - t_total) * 1000)

        return {
            "company_name": company_name,
            "domain": domain,
            "crunchbase_data": crunchbase_record,
            "funding": funding,
            "layoffs": {k: v for k, v in layoffs.items() if not k.startswith("_")},
            "job_post_velocity": {k: v for k, v in job_velocity.items() if not k.startswith("_")},
            "leadership_change": {k: v for k, v in leadership.items() if not k.startswith("_")},
            "ai_maturity": {k: v for k, v in ai_maturity.items() if not k.startswith("_")},
            "competitor_gap": competitor_gap,
            "summary": _build_summary(company_name, funding, layoffs, job_velocity, ai_maturity),
            "_enrichment_latency_ms": total_ms,
            "_module_latencies": {
                "crunchbase_ms": cb_result.get("_latency_ms", 0),
                "layoffs_ms": layoffs.get("_latency_ms", 0),
                "job_velocity_ms": job_velocity.get("_latency_ms", 0),
                "leadership_ms": leadership.get("_latency_ms", 0),
                "ai_maturity_ms": ai_maturity.get("_latency_ms", 0),
            },
        }

    def build_hiring_signal_brief(
        self,
        company_name: str,
        domain: Optional[str] = None,
    ) -> Dict:
        """Sync wrapper around the async pipeline (for backward compatibility)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already inside an event loop (e.g. FastAPI/Jupyter) — use run_until_complete
                import concurrent.futures
                future = asyncio.ensure_future(
                    self.build_hiring_signal_brief_async(company_name, domain)
                )
                return loop.run_until_complete(future)
        except RuntimeError:
            pass

        return asyncio.run(self.build_hiring_signal_brief_async(company_name, domain))

    # -----------------------------------------------------------------------
    # Competitor gap analysis
    # -----------------------------------------------------------------------

    def _build_competitor_gap(self, company_name: str, crunchbase_record: Dict) -> Dict:
        industry = crunchbase_record.get("industry", "enterprise software")
        company_headcount = crunchbase_record.get("headcount", 50)
        company_ai_frac = float(crunchbase_record.get("ai_roles_fraction", 0) or 0)

        # Find peers: same industry, similar headcount band, from Crunchbase sample
        headcount_lo = max(0, company_headcount * 0.5)
        headcount_hi = company_headcount * 2.0
        peers = []
        for rec in self._crunchbase_index.values():
            if (rec.get("name") == company_name
                    or rec.get("industry", "").lower() != industry.lower()):
                continue
            hc = rec.get("headcount", 0) or 0
            if headcount_lo <= hc <= headcount_hi:
                peers.append(rec)

        if not peers:
            # Fall back to all same-industry regardless of size
            peers = [
                r for r in self._crunchbase_index.values()
                if r.get("industry", "").lower() == industry.lower()
                and r.get("name") != company_name
            ]

        # Deduplicate (index has domain aliases)
        seen = set()
        unique_peers = []
        for p in peers:
            n = p.get("name", "")
            if n not in seen:
                seen.add(n)
                unique_peers.append(p)
        peers = unique_peers[:6]

        if not peers:
            return {
                "top_gap": {
                    "practice": "Building a clear AI ownership model between product and delivery.",
                    "why": "Top-quartile peers in this sector publicly signal stronger AI alignment.",
                },
                "peer_ai_fractions": [],
                "company_percentile": None,
                "confidence": "low",
            }

        peer_fracs = sorted([float(p.get("ai_roles_fraction", 0) or 0) for p in peers])
        all_fracs = sorted(peer_fracs + [company_ai_frac])
        rank = all_fracs.index(company_ai_frac)
        percentile = round(rank / max(len(all_fracs) - 1, 1) * 100)

        top_quartile_threshold = (
            all_fracs[int(len(all_fracs) * 0.75)] if len(all_fracs) >= 4 else all_fracs[-1]
        )
        top_quartile_peers = [
            p.get("name", "?") for p in peers
            if float(p.get("ai_roles_fraction", 0) or 0) >= top_quartile_threshold
        ]

        if company_ai_frac < top_quartile_threshold:
            gap_practice = (
                f"Top-quartile {industry} peers allocate "
                f"{top_quartile_threshold:.0%}+ of engineering to AI/ML roles; "
                f"{company_name} is at {company_ai_frac:.0%}."
            )
            gap_why = (
                f"Companies like {', '.join(top_quartile_peers[:2] or ['sector leaders'])} "
                f"have built dedicated AI ownership models that accelerate roadmap execution "
                f"without proportionally expanding headcount."
            )
        else:
            gap_practice = "AI team composition is in the top quartile for this sector."
            gap_why = "No material capability gap identified vs. comparable peers."

        return {
            "top_gap": {
                "practice": gap_practice,
                "why": gap_why,
            },
            "peer_ai_fractions": peer_fracs,
            "company_ai_fraction": company_ai_frac,
            "company_percentile": percentile,
            "top_quartile_peers": top_quartile_peers[:3],
            "confidence": "high" if len(peers) >= 3 else "medium",
        }

    # -----------------------------------------------------------------------
    # Legacy sync methods (kept for backward compat in tests)
    # -----------------------------------------------------------------------

    def lookup_crunchbase(self, company_name: str, domain: Optional[str] = None) -> Dict:
        r = self._module_crunchbase(company_name, domain)
        return r["data"]

    def lookup_layoffs(self, company_name: str) -> Dict:
        return {k: v for k, v in self._module_layoffs(company_name).items() if not k.startswith("_")}

    def lookup_job_post_velocity(self, company_name: str, domain: Optional[str] = None) -> JobPostSignal:
        rec = self._crunchbase_index.get(company_name.lower().strip())
        return {k: v for k, v in self._module_job_velocity(company_name, domain, rec).items() if not k.startswith("_")}

    def lookup_leadership_change(self, company_name: str, domain: Optional[str] = None) -> LeadershipSignal:
        rec = self._crunchbase_index.get(company_name.lower().strip())
        return {k: v for k, v in self._module_leadership(company_name, domain, rec).items() if not k.startswith("_")}

    def score_ai_maturity(self, signals: Dict) -> AiMaturitySignal:
        cb_record = signals.get("crunchbase_record", {}) or {}
        job_velocity = signals.get("job_post_velocity", {})
        return {k: v for k, v in self._module_ai_maturity(cb_record, job_velocity).items() if not k.startswith("_")}

    def check_bench_capacity(self, required_stacks: List[str]) -> Dict:
        available_capacity = {}
        for stack in required_stacks:
            stack_data = self.bench_summary.get("stacks", {}).get(stack, {})
            available = stack_data.get("available_engineers", 0)
            available_capacity[stack] = {
                "available": available,
                "sufficient": available > 0,
                "time_to_deploy_days": stack_data.get("time_to_deploy_days", 14),
            }
        all_sufficient = all(cap["sufficient"] for cap in available_capacity.values())
        return {
            "capacity_available": all_sufficient,
            "stack_details": available_capacity,
            "recommendation": "proceed" if all_sufficient else "wait_or_adjust_scope",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_focus(titles: List[str]) -> str:
    text = " ".join(titles).lower()
    if any(k in text for k in ["ml", "ai ", "machine learning", "data sci", "nlp", "llm"]):
        return "AI/ML engineering"
    if any(k in text for k in ["backend", "python", "go ", "java", "node"]):
        return "backend engineering"
    if any(k in text for k in ["infra", "devops", "sre", "platform", "cloud"]):
        return "infrastructure"
    if any(k in text for k in ["frontend", "react", "typescript", "ui "]):
        return "frontend engineering"
    return "engineering"


def _build_summary(company_name, funding, layoffs, job_velocity, ai_maturity) -> str:
    parts = [f"{company_name}:"]
    stage = funding.get("stage", "?") if isinstance(funding, dict) else "?"
    months = funding.get("last_funding_months") if isinstance(funding, dict) else None
    if stage and months:
        parts.append(f"{stage} ({months}mo ago)")
    lo = layoffs.get("event") if isinstance(layoffs, dict) else None
    if lo:
        pct = layoffs.get("percentage", 0) if isinstance(layoffs, dict) else 0
        parts.append(f"layoff {pct:.0f}%")
    vel = job_velocity.get("velocity", "?") if isinstance(job_velocity, dict) else "?"
    n = job_velocity.get("open_roles", 0) if isinstance(job_velocity, dict) else 0
    parts.append(f"hiring={vel} ({n} roles)")
    score = ai_maturity.get("score", 0) if isinstance(ai_maturity, dict) else 0
    parts.append(f"AI_maturity={score}/3")
    return " | ".join(parts)
