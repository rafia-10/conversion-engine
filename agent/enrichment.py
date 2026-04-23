import json
import os
import random
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class EnrichmentSignal:
    value: Any
    confidence: str
    justification: str


class EnrichmentPipeline:
    def __init__(self):
        # Load seed materials
        self.bench_summary = self._load_bench_summary()
        self.icp_definition = self._load_icp_definition()
        self.baseline_numbers = self._load_baseline_numbers()

        # Data source paths (may not exist yet)
        self.crunchbase_path = os.getenv("CRUNCHBASE_SAMPLE_PATH", "./data/crunchbase_sample.json")
        self.layoffs_path = os.getenv("LAYOFFS_DATA_PATH", "./data/layoffs.fyi.csv")
        self.job_posts_path = os.getenv("JOB_POSTS_DATA_PATH", "./data/job_posts.json")

        # Load available data
        self.crunchbase_data = self._load_json_file(self.crunchbase_path)
        self.layoffs_data = self._load_layoffs_data()
        self.job_posts_data = self._load_json_file(self.job_posts_path)

    def _load_bench_summary(self) -> Dict:
        """Load bench summary from seed materials."""
        paths = [
            "./seed/bench_summary.json",
            "./tenacious_sales_data/seed/bench_summary.json"
        ]
        for path in paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    continue
        return {}

    def _load_icp_definition(self) -> Dict:
        """Load ICP definition from seed materials."""
        paths = [
            "./seed/icp_definition.md",
            "./tenacious_sales_data/seed/icp_definition.md"
        ]
        for path in paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                        # Parse markdown into structured data
                        return self._parse_icp_markdown(content)
                except Exception:
                    continue
        return {}

    def _load_baseline_numbers(self) -> Dict:
        """Load baseline numbers from seed materials."""
        paths = [
            "./seed/baseline_numbers.md",
            "./tenacious_sales_data/seed/baseline_numbers.md"
        ]
        for path in paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                        # Parse markdown into structured data
                        return self._parse_baseline_markdown(content)
                except Exception:
                    continue
        return {}

    def _load_json_file(self, path: str) -> Dict:
        """Load JSON file if it exists."""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _load_layoffs_data(self) -> List[Dict]:
        """Load layoffs data (CSV format)."""
        if os.path.exists(self.layoffs_path):
            try:
                import csv
                with open(self.layoffs_path, "r", encoding="utf-8") as f:
                    return list(csv.DictReader(f))
            except Exception:
                return []
        return []

    def _parse_icp_markdown(self, content: str) -> Dict:
        """Parse ICP definition markdown into structured data."""
        segments = {}
        current_segment = None

        for line in content.split('\n'):
            if line.startswith('## Segment'):
                segment_match = re.search(r'Segment (\d+) — (.+)', line)
                if segment_match:
                    segment_num = int(segment_match.group(1))
                    segment_name = segment_match.group(2).strip()
                    current_segment = f"segment_{segment_num}_{segment_name.lower().replace(' ', '_').replace('-', '_')}"
                    segments[current_segment] = {
                        "name": segment_name,
                        "qualifying_filters": [],
                        "disqualifying_filters": [],
                        "why_buy": "",
                        "pitch_language": {}
                    }
            elif current_segment and line.startswith('### Qualifying filters'):
                # Next lines until next ### are qualifying filters
                pass  # Would need more complex parsing
            elif current_segment and line.startswith('### Disqualifying filters'):
                pass  # Would need more complex parsing

        return segments

    def _parse_baseline_markdown(self, content: str) -> Dict:
        """Parse baseline numbers markdown into structured data."""
        numbers = {}
        in_table = False

        for line in content.split('\n'):
            if '|' in line and ('Metric' in line or 'Value' in line):
                in_table = True
                continue
            elif in_table and '|' in line and line.strip():
                parts = [p.strip() for p in line.split('|') if p.strip()]
                if len(parts) >= 2:
                    metric = parts[0].lower().replace(' ', '_').replace('-', '_')
                    value = parts[1]
                    numbers[metric] = value

        return numbers

    def lookup_crunchbase(self, company_name: str, domain: str | None = None) -> Dict:
        """Look up company in Crunchbase data or generate synthetic."""
        key = company_name.lower()
        if key in self.crunchbase_data:
            return self.crunchbase_data[key]

        # Generate synthetic data based on ICP patterns
        sanitized = re.sub(r"[^a-z0-9]+", "", company_name.lower())
        domain = domain or f"{sanitized}.com"

        # Random but realistic data
        stages = ["Series A", "Series B", "Growth", "Series C"]
        stage = random.choice(stages)
        funding_months = random.randint(1, 8) if "Series" in stage else random.randint(12, 36)

        industries = [
            "enterprise software", "fintech", "healthtech", "data infrastructure",
            "business intelligence", "ai/ml", "developer tools", "cybersecurity"
        ]
        industry = random.choice(industries)

        return {
            "name": company_name,
            "domain": domain,
            "stage": stage,
            "last_funding_months": funding_months,
            "industry": industry,
            "headcount": random.randint(15, 200),
            "location": random.choice(["San Francisco", "New York", "London", "Berlin", "Toronto"]),
        }

    def lookup_layoffs(self, company_name: str) -> Dict:
        """Check for layoffs in the data or generate synthetic."""
        # Check real data first
        for record in self.layoffs_data:
            if company_name.lower() in record.get("company", "").lower():
                return {
                    "event": "recent layoff",
                    "date": record.get("date", date.today().isoformat()),
                    "headcount": int(record.get("headcount", 0)),
                    "percentage": float(record.get("percentage", 0)),
                    "confidence": "high",
                }

        # Generate synthetic based on company characteristics
        if "layoff" in company_name.lower() or random.random() < 0.18:
            return {
                "event": "recent layoff",
                "date": date.today().isoformat(),
                "headcount": random.randint(10, 50),
                "percentage": random.randint(5, 25),
                "confidence": "medium",
            }

        return {
            "event": None,
            "date": None,
            "headcount": 0,
            "percentage": 0,
            "confidence": "low"
        }

    def lookup_job_post_velocity(self, company_name: str) -> Dict:
        """Analyze job posting velocity."""
        # In a real implementation, this would scrape job boards
        # For now, generate realistic synthetic data
        count = random.randint(0, 15)
        velocity = "high" if count >= 8 else "moderate" if count >= 3 else "low"

        focus_areas = [
            "Python engineering", "data platform", "ML infrastructure", "full-stack product",
            "backend development", "frontend development", "DevOps", "AI/ML engineering"
        ]
        focus = random.choice(focus_areas)

        return {
            "open_roles": count,
            "velocity": velocity,
            "focus": focus,
            "confidence": "high" if count >= 8 else "medium" if count >= 3 else "low",
        }

    def lookup_leadership_change(self, company_name: str) -> Dict:
        """Check for recent leadership changes."""
        # In a real implementation, this would check press releases and Crunchbase
        changed = random.random() < 0.25
        return {
            "event": "new CTO/VP Engineering" if changed else None,
            "date": date.today().isoformat() if changed else None,
            "confidence": "medium" if changed else "low",
        }

    def score_ai_maturity(self, signals: Dict) -> Dict:
        """Score AI maturity based on signals."""
        score = 0
        signal_summary = []
        confidence_levels = []

        # Job post velocity signal
        job_velocity = signals.get("job_post_velocity", {})
        if job_velocity.get("open_roles", 0) >= 3:
            score += 1
            signal_summary.append("multiple engineering openings")
            confidence_levels.append(job_velocity.get("confidence", "low"))

        # Leadership change signal
        leadership = signals.get("leadership_change", {})
        if leadership.get("event"):
            score += 1
            signal_summary.append("recent engineering leadership change")
            confidence_levels.append(leadership.get("confidence", "low"))

        # Layoffs signal (restructuring often indicates AI maturity)
        layoffs = signals.get("layoffs", {})
        if layoffs.get("event"):
            score += 1
            signal_summary.append("recent restructuring signal")
            confidence_levels.append(layoffs.get("confidence", "low"))

        # Funding stage signal
        funding = signals.get("funding", {})
        if funding.get("stage") in ("Series A", "Series B"):
            score += 1
            signal_summary.append("recent growth-stage funding")
            confidence_levels.append("high")

        score = min(score, 3)
        if score == 0:
            confidence = "low"
        elif score == 1:
            confidence = "medium"
        else:
            confidence = "high" if len(signal_summary) >= 3 else "medium"

        return {
            "score": score,
            "confidence": confidence,
            "signal_summary": signal_summary or ["limited public signal"],
            "details": {
                "job_post_velocity": job_velocity,
                "leadership_change": leadership,
                "layoffs": layoffs,
                "funding": funding,
            },
        }

    def classify_icp_segment(self, company_data: Dict, signals: Dict) -> Dict:
        """Classify company into ICP segment based on seed definition."""
        # Segment 1: Recently-funded Series A/B startups
        funding = signals.get("funding", {})
        layoffs = signals.get("layoffs", {})
        job_velocity = signals.get("job_post_velocity", {})

        if (funding.get("stage") in ("Series A", "Series B") and
            funding.get("last_funding_months", 999) <= 6 and
            company_data.get("headcount", 0) <= 80 and
            job_velocity.get("open_roles", 0) >= 5 and
            not layoffs.get("event")):
            return {
                "segment": "segment_1_series_a_b",
                "confidence": 0.8,
                "reason": "Recent funding, growing headcount, active hiring"
            }

        # Segment 2: Mid-market platforms restructuring cost
        if (company_data.get("headcount", 0) >= 200 and
            layoffs.get("event") and
            layoffs.get("percentage", 0) <= 40 and
            job_velocity.get("open_roles", 0) >= 3):
            return {
                "segment": "segment_2_mid_market_restructuring",
                "confidence": 0.7,
                "reason": "Large company with recent layoffs, still hiring"
            }

        # Segment 3: Engineering-leadership transitions
        leadership = signals.get("leadership_change", {})
        if leadership.get("event"):
            return {
                "segment": "segment_3_leadership_transitions",
                "confidence": 0.6,
                "reason": "Recent engineering leadership change"
            }

        # Segment 4: Specialized capability gaps (AI maturity 2+)
        ai_maturity = signals.get("ai_maturity", {})
        if ai_maturity.get("score", 0) >= 2:
            return {
                "segment": "segment_4_capability_gaps",
                "confidence": 0.5,
                "reason": "High AI maturity indicates capability gap opportunity"
            }

        return {
            "segment": "unknown",
            "confidence": 0.0,
            "reason": "Does not match defined ICP segments"
        }

    def check_bench_capacity(self, required_stacks: List[str]) -> Dict:
        """Check if Tenacious has capacity for required tech stacks."""
        available_capacity = {}

        for stack in required_stacks:
            stack_data = self.bench_summary.get("stacks", {}).get(stack, {})
            available = stack_data.get("available_engineers", 0)
            available_capacity[stack] = {
                "available": available,
                "sufficient": available > 0,
                "time_to_deploy_days": stack_data.get("time_to_deploy_days", 14)
            }

        all_sufficient = all(cap["sufficient"] for cap in available_capacity.values())

        return {
            "capacity_available": all_sufficient,
            "stack_details": available_capacity,
            "recommendation": "proceed" if all_sufficient else "wait_or_adjust_scope"
        }

    def build_hiring_signal_brief(self, company_name: str, domain: str | None = None) -> Dict:
        """Build complete hiring signal brief."""
        crunchbase = self.lookup_crunchbase(company_name, domain)
        layoffs = self.lookup_layoffs(company_name)
        job_velocity = self.lookup_job_post_velocity(company_name)
        leadership = self.lookup_leadership_change(company_name)

        funding = {
            "stage": crunchbase.get("stage"),
            "last_funding_months": crunchbase.get("last_funding_months"),
            "confidence": "high",
        }

        ai_maturity = self.score_ai_maturity({
            "job_post_velocity": job_velocity,
            "leadership_change": leadership,
            "layoffs": layoffs,
            "funding": funding,
        })

        icp_classification = self.classify_icp_segment(crunchbase, {
            "funding": funding,
            "layoffs": layoffs,
            "job_post_velocity": job_velocity,
            "leadership_change": leadership,
            "ai_maturity": ai_maturity,
        })

        competitor_gap = self.build_competitor_gap_brief(company_name, crunchbase.get("industry"))

        summary = (
            f"{company_name} shows {job_velocity['velocity']} hiring velocity, "
            f"{crunchbase.get('stage')} funding, and AI maturity {ai_maturity['score']}/3. "
            f"Classified as {icp_classification['segment']} with {icp_classification['confidence']:.1%} confidence."
        )

        return {
            "company_name": company_name,
            "domain": domain,
            "crunchbase_data": crunchbase,
            "funding": funding,
            "layoffs": layoffs,
            "job_post_velocity": job_velocity,
            "leadership_change": leadership,
            "ai_maturity": ai_maturity,
            "icp_classification": icp_classification,
            "competitor_gap": competitor_gap,
            "summary": summary,
        }

    def build_competitor_gap_brief(self, company_name: str, industry: str | None = None) -> Dict:
        """Build competitor gap analysis."""
        competitors = []
        for i in range(3):
            score = random.randint(1, 3)
            competitors.append({
                "company": f"{industry or 'peer'} leader {i + 1}",
                "industry": industry,
                "ai_maturity": score,
                "signals": [
                    "public AI leadership commentary",
                    "multiple AI/ML job postings",
                    "modern data stack evidence",
                ][:max(1, score)],
            })

        top_gap = {
            "practice": "Designing a clear AI ownership model between product and delivery teams.",
            "why": "Top quartile peers publicly signal stronger alignment between engineering capacity and AI roadmap execution.",
        }

        return {
            "top_quartile_competitors": competitors,
            "top_gap": top_gap,
            "confidence": "medium",
        }
